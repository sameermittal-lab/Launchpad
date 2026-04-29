"""Company suggester — LLM-generated companies for the user to consider tracking.

Lifecycle:
- Daily scheduler tick regenerates for every profile that has an LLM key and has
  any tracked companies at all (or has used AI Monitor recently — signal that
  they're active on the scanner feature).
- Manual refresh via `POST /api/scanner/suggestions/refresh`, guarded by 4-hour
  cooldown.
- Adding a suggestion → creates a TrackedCompany, deletes the suggestion.
- Dismissing a suggestion → marks dismissed; row stays so next LLM call sees it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models import CompanySuggestion, Profile, TrackedCompany
from app.prompts import render_prompt
from app.services.evaluation import _extract_json
from app.services.llm import get_provider
from app.services.usage_tracker import log_usage

logger = logging.getLogger(__name__)

COOLDOWN = timedelta(hours=4)
VALID_PLATFORMS = {"greenhouse", "ashby", "lever", "workday", "smartrecruiters", "custom"}


def _load_cv(profile: Profile) -> Optional[str]:
    cv_path: Path = settings.resolved_data_dir / str(profile.id) / "cv.md"
    if cv_path.exists():
        try:
            return cv_path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning(f"Could not read cv.md for profile {profile.id}: {exc}")
    return None


def _target_roles_str(profile: Profile) -> str:
    pd = profile.profile_data or {}
    tr = pd.get("target_roles")
    if isinstance(tr, list) and tr:
        return ", ".join(str(t) for t in tr if t)
    if isinstance(tr, str) and tr.strip():
        return tr.strip()
    return "(not specified)"


def _target_locations_str(profile: Profile) -> str:
    pd = profile.profile_data or {}
    locs = pd.get("target_locations") or pd.get("location")
    if isinstance(locs, list):
        return ", ".join(str(l) for l in locs if l) or "(any)"
    if isinstance(locs, str) and locs.strip():
        return locs.strip()
    return "(any)"


def cooldown_remaining(profile: Profile) -> int:
    """Seconds remaining until next manual refresh is allowed. 0 if no cooldown."""
    ts = getattr(profile, "company_suggestions_refreshed_at", None)
    if ts is None:
        return 0
    elapsed = datetime.utcnow() - ts
    if elapsed >= COOLDOWN:
        return 0
    return int((COOLDOWN - elapsed).total_seconds())


async def refresh_suggestions(
    db: Session,
    profile: Profile,
    *,
    force: bool = False,
) -> list[CompanySuggestion]:
    """Regenerate the suggestion list for one profile.

    `force=True` bypasses the 4-hour cooldown (used by the daily scheduler).
    Returns the updated list of active (non-added, non-dismissed) suggestions.
    """
    if not profile.llm_api_key_enc:
        raise ValueError("No LLM API key configured")

    if not force:
        remaining = cooldown_remaining(profile)
        if remaining > 0:
            raise ValueError(
                f"Cooldown active — try again in {remaining // 60}m {remaining % 60}s"
            )

    logger.info(f"Refreshing company suggestions for profile {profile.id}")

    # Gather signal: current tracked companies, and dismissed-history names
    tracked = [
        n for (n,) in db.query(TrackedCompany.name)
        .filter(TrackedCompany.profile_id == profile.id)
        .all()
    ]
    tracked_lower = {n.strip().lower() for n in tracked if n}

    dismissed = [
        n for (n,) in db.query(CompanySuggestion.name)
        .filter(
            CompanySuggestion.profile_id == profile.id,
            CompanySuggestion.dismissed.is_(True),
        )
        .all()
    ]

    cv_text = _load_cv(profile)

    prompt = render_prompt(
        "company_suggester.md.j2",
        profile=profile,
        target_roles=_target_roles_str(profile),
        target_locations=_target_locations_str(profile),
        cv_text=cv_text,
        tracked_names=sorted(tracked),
        dismissed_names=sorted(dismissed),
    )

    provider = get_provider(profile)
    try:
        response = await provider.complete_with_search(
            system=(
                "You are a career scout who suggests companies a candidate should track. "
                "Use web search to verify careers URLs and current hiring status. Respond "
                "with JSON only."
            ),
            user=prompt,
            max_tokens=2500,
            temperature=0.4,
        )
    except Exception as exc:
        logger.warning(
            f"Web-grounded suggester failed for profile {profile.id}, falling back: {exc}"
        )
        response = await provider.complete(
            system=(
                "You are a career scout who suggests companies a candidate should track. "
                "Respond with JSON only."
            ),
            user=prompt,
            max_tokens=2500,
            temperature=0.4,
        )
    log_usage(db, profile.id, "company_suggester", response)

    try:
        parsed = _extract_json(response.text)
    except Exception as exc:
        logger.exception(f"Company suggester JSON parse failed: {exc}")
        raise ValueError("LLM returned unparseable JSON")

    raw = parsed.get("suggestions") if isinstance(parsed, dict) else parsed
    if not isinstance(raw, list):
        raise ValueError("LLM returned no suggestions")

    # Replace the current un-added suggestions with the fresh set.
    # Preserve dismissed rows so they continue informing future calls.
    db.query(CompanySuggestion).filter(
        CompanySuggestion.profile_id == profile.id,
        CompanySuggestion.dismissed.is_(False),
    ).delete(synchronize_session=False)

    new_rows: list[CompanySuggestion] = []
    seen_names: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if not name:
            continue
        nl = name.lower()
        if nl in tracked_lower or nl in seen_names:
            continue
        platform = (item.get("platform_guess") or "custom").strip().lower()
        if platform not in VALID_PLATFORMS:
            platform = "custom"
        url = (item.get("careers_url") or "").strip() or None
        src = (item.get("source") or "adjacent").strip().lower()
        if src not in ("adjacent", "discovery"):
            src = "adjacent"
        reason = (item.get("why_relevant") or "").strip()[:500] or None

        # Check for an existing dismissed row — reactivate it so we don't
        # violate the UniqueConstraint(profile_id, name).
        existing = (
            db.query(CompanySuggestion)
            .filter(
                CompanySuggestion.profile_id == profile.id,
                CompanySuggestion.name == name,
            )
            .first()
        )
        if existing is not None:
            # Dismissed name came back from the LLM — skip it to honor the
            # dismissal signal. Belt-and-suspenders; the prompt already lists it.
            if existing.dismissed:
                continue
            # Otherwise update the existing row in-place
            existing.careers_url = url
            existing.platform_guess = platform
            existing.why_relevant = reason
            existing.source = src
            existing.added = False
            existing.created_at = datetime.utcnow()
            new_rows.append(existing)
        else:
            row = CompanySuggestion(
                profile_id=profile.id,
                name=name,
                careers_url=url,
                platform_guess=platform,
                why_relevant=reason,
                source=src,
                added=False,
                dismissed=False,
            )
            db.add(row)
            new_rows.append(row)
        seen_names.add(nl)
        if len(new_rows) >= 10:
            break

    profile.company_suggestions_refreshed_at = datetime.utcnow()
    db.commit()
    for r in new_rows:
        db.refresh(r)

    logger.info(
        f"Generated {len(new_rows)} suggestions for profile {profile.id} "
        f"(excluded {len(tracked_lower)} tracked, {len(dismissed)} dismissed)"
    )
    return new_rows


def list_active_suggestions(db: Session, profile: Profile) -> list[CompanySuggestion]:
    """Return non-added, non-dismissed suggestions sorted by source then name."""
    rows = (
        db.query(CompanySuggestion)
        .filter(
            CompanySuggestion.profile_id == profile.id,
            CompanySuggestion.added.is_(False),
            CompanySuggestion.dismissed.is_(False),
        )
        .order_by(CompanySuggestion.source, CompanySuggestion.name)
        .all()
    )
    return rows
