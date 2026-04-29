"""Background scheduler for periodic tasks.

Uses APScheduler in background mode. Runs these recurring jobs per profile:
- Portal scan every scan_interval_hours (profile setting, default 6h)
- Gmail sync every 30 minutes (if any Gmail accounts connected)
- Reminder regeneration once a day
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from app.database import db_session
from app.models import Profile, GmailAccount

logger = logging.getLogger(__name__)

_scheduler: Optional[AsyncIOScheduler] = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler


def start() -> None:
    """Start the scheduler and register all jobs."""
    sched = get_scheduler()
    if sched.running:
        return

    # Core jobs
    sched.add_job(
        run_all_scheduled_scans,
        trigger=IntervalTrigger(minutes=30),
        id="scan_tick",
        replace_existing=True,
        misfire_grace_time=300,
    )
    sched.add_job(
        run_all_gmail_syncs,
        trigger=IntervalTrigger(minutes=30),
        id="gmail_tick",
        replace_existing=True,
        misfire_grace_time=300,
    )
    sched.add_job(
        run_all_reminder_generations,
        trigger=CronTrigger(hour=6, minute=0),  # Daily at 6am
        id="reminders_daily",
        replace_existing=True,
    )
    sched.add_job(
        run_all_ai_monitors,
        trigger=IntervalTrigger(hours=1),
        id="ai_monitor_tick",
        replace_existing=True,
        misfire_grace_time=600,
    )
    sched.add_job(
        run_all_company_suggestions,
        trigger=CronTrigger(hour=5, minute=0),  # Daily 5am
        id="company_suggestions_daily",
        replace_existing=True,
    )

    sched.start()
    logger.info(
        "Scheduler started with jobs: scan_tick, gmail_tick, reminders_daily, "
        "ai_monitor_tick, company_suggestions_daily"
    )


def shutdown() -> None:
    sched = get_scheduler()
    if sched.running:
        sched.shutdown(wait=False)
        logger.info("Scheduler stopped")


# ------------------------- Tick functions -------------------------


async def run_all_scheduled_scans() -> None:
    """For each profile, run a scan if enough time has passed since last scan.

    We store the "last scan time" in the profile's most recent TrackedCompany
    last_scanned_at. If the oldest last_scanned_at is older than
    scan_interval_hours, we trigger a new scan.
    """
    try:
        with db_session() as db:
            profiles = db.query(Profile).all()
            for profile in profiles:
                if not _should_scan_now(db, profile):
                    continue
                try:
                    from app.services.scanner import scan_all_companies
                    result = await scan_all_companies(
                        db, profile, auto_evaluate=profile.auto_evaluate
                    )
                    logger.info(
                        f"[sched] scan for profile {profile.id}: "
                        f"{result.new_listings} new, {result.total_jobs_found} found, "
                        f"auto_evaluate={profile.auto_evaluate}"
                    )
                except Exception as exc:
                    logger.exception(f"Scheduled scan failed for profile {profile.id}: {exc}")
    except Exception:
        logger.exception("run_all_scheduled_scans top-level error")


def _should_scan_now(db, profile: Profile) -> bool:
    """Check if enough time has passed since this profile's last scan."""
    from app.models import TrackedCompany
    from sqlalchemy import func as sa_func

    # Look at the most recent last_scanned_at across all tracked companies
    last = (
        db.query(sa_func.max(TrackedCompany.last_scanned_at))
        .filter(
            TrackedCompany.profile_id == profile.id,
            TrackedCompany.enabled.is_(True),
        )
        .scalar()
    )
    if last is None:
        return True  # never scanned
    hours_ago = (datetime.utcnow() - last).total_seconds() / 3600
    return hours_ago >= (profile.scan_interval_hours or 6)


async def run_all_gmail_syncs() -> None:
    """Sync every profile's connected Gmail accounts every 30 minutes."""
    try:
        with db_session() as db:
            profiles = db.query(Profile).all()
            for profile in profiles:
                accounts = (
                    db.query(GmailAccount)
                    .filter(
                        GmailAccount.profile_id == profile.id,
                        GmailAccount.is_active.is_(True),
                    )
                    .count()
                )
                if accounts == 0:
                    continue
                try:
                    from app.services.gmail.sync import sync_all_accounts
                    results = await sync_all_accounts(db, profile)
                    total_new = sum(r.new for r in results)
                    if total_new > 0:
                        logger.info(
                            f"[sched] gmail sync for profile {profile.id}: {total_new} new messages"
                        )
                except Exception as exc:
                    logger.exception(f"Scheduled Gmail sync failed for profile {profile.id}: {exc}")
    except Exception:
        logger.exception("run_all_gmail_syncs top-level error")


async def run_all_reminder_generations() -> None:
    """Daily reminder refresh across all profiles."""
    try:
        with db_session() as db:
            profiles = db.query(Profile).all()
            for profile in profiles:
                try:
                    from app.services.reminder_engine import generate_reminders
                    count = generate_reminders(db, profile)
                    if count > 0:
                        logger.info(f"[sched] generated {count} reminders for profile {profile.id}")
                except Exception as exc:
                    logger.exception(f"Scheduled reminders failed for profile {profile.id}: {exc}")
    except Exception:
        logger.exception("run_all_reminder_generations top-level error")


async def run_all_ai_monitors() -> None:
    """Hourly tick — runs AI Company Monitor for any (profile, company) pair that's
    overdue. "Overdue" means the company's last_ai_monitor_at is older than the
    profile's scan_interval_hours.

    Per-company throttling prevents bursty cost — we only scan a handful of
    companies per tick even if many are overdue.
    """
    max_per_tick = 10  # cap to keep one hourly tick bounded in cost
    try:
        with db_session() as db:
            from app.models import TrackedCompany
            profiles = db.query(Profile).all()
            for profile in profiles:
                if not profile.llm_api_key_enc:
                    continue
                interval = (
                    profile.ai_monitor_interval_hours
                    if getattr(profile, "ai_monitor_interval_hours", None)
                    else (profile.scan_interval_hours or 6)
                )
                threshold = datetime.utcnow().timestamp() - (interval * 3600)
                companies = (
                    db.query(TrackedCompany)
                    .filter(
                        TrackedCompany.profile_id == profile.id,
                        TrackedCompany.enabled.is_(True),
                        TrackedCompany.ai_monitor_enabled.is_(True),
                    )
                    .all()
                )
                overdue = [
                    c for c in companies
                    if c.last_ai_monitor_at is None
                    or c.last_ai_monitor_at.timestamp() <= threshold
                ]
                # Oldest first
                overdue.sort(
                    key=lambda c: c.last_ai_monitor_at or datetime.min
                )
                overdue = overdue[:max_per_tick]
                for company in overdue:
                    try:
                        from app.services.ai_company_monitor import run_ai_monitor_for_company
                        await run_ai_monitor_for_company(
                            db, profile, company, trigger="scheduled",
                        )
                    except Exception as exc:
                        logger.exception(
                            f"Scheduled AI monitor failed for profile {profile.id} × "
                            f"company {company.name}: {exc}"
                        )
    except Exception:
        logger.exception("run_all_ai_monitors top-level error")


async def run_all_company_suggestions() -> None:
    """Daily tick — refresh the company-suggestions list for each profile that
    has an LLM key. Force=True so we bypass the 4h manual-refresh cooldown.
    """
    try:
        with db_session() as db:
            from app.models import TrackedCompany
            profiles = db.query(Profile).all()
            for profile in profiles:
                if not profile.llm_api_key_enc:
                    continue
                # Only generate if the user is actually using the scanner
                tracked_count = (
                    db.query(TrackedCompany)
                    .filter(TrackedCompany.profile_id == profile.id)
                    .count()
                )
                if tracked_count == 0:
                    continue
                try:
                    from app.services.company_suggester import refresh_suggestions
                    rows = await refresh_suggestions(db, profile, force=True)
                    logger.info(
                        f"[sched] company_suggestions for profile {profile.id}: {len(rows)} suggestions"
                    )
                except Exception as exc:
                    logger.exception(
                        f"Scheduled company suggestions failed for profile {profile.id}: {exc}"
                    )
    except Exception:
        logger.exception("run_all_company_suggestions top-level error")


# ------------------------- Stats -------------------------


def get_status() -> dict:
    """Return scheduler state for UI display."""
    sched = get_scheduler()
    if not sched.running:
        return {"running": False, "jobs": []}
    jobs = []
    for job in sched.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name or job.id,
            "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
        })
    return {"running": True, "jobs": jobs}
