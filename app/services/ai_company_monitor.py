"""AI Company Monitor — periodically scans a company's careers site via web search.

Design notes (see spec for full context):
- Uses LLM web search to run 3-5 per-company queries produced by query_planner.
- Ingests ALL surfaced listings (no server-side keyword dropping).
- Runs the profile's own title filter afterward and stores BOTH kept + filtered
  items on an AIMonitorRun record so the user can review/override.
- Dedupes against existing Listings by a canonicalized URL key.
- Optionally auto-evaluates kept listings (respects profile.auto_evaluate).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models import AIMonitorRun, Listing, Profile, TrackedCompany
from app.prompts import render_prompt  # noqa: F401 — used downstream
from app.services.evaluation import _extract_json
from app.services.filters import why_title_fails
from app.services.llm import get_provider
from app.services.query_planner import ensure_query_plan
from app.services.usage_tracker import log_usage

logger = logging.getLogger(__name__)

# --- URL canonicalization -----------------------------------------------------

# Stable-ID regexes by host — extracts a canonical key we can dedupe on.
# Add new hosts over time as we grow coverage.
_STABLE_ID_PATTERNS = [
    # Amazon: amazon.jobs/{en/}?jobs/{digits}
    (re.compile(r"amazon\.jobs.*/jobs/(\d+)", re.I), "amazon.jobs"),
    # Greenhouse: boards.greenhouse.io/{org}/jobs/{digits}
    (re.compile(r"greenhouse\.io/[^/]+/jobs/(\d+)", re.I), "greenhouse"),
    # Ashby: jobs.ashbyhq.com/{org}/{uuid}
    (re.compile(r"ashbyhq\.com/[^/]+/([a-f0-9-]{10,})", re.I), "ashby"),
    # Lever: jobs.lever.co/{org}/{uuid}
    (re.compile(r"lever\.co/[^/]+/([a-f0-9-]{10,})", re.I), "lever"),
    # Microsoft careers: jobs.careers.microsoft.com/us/en/job/{digits}
    (re.compile(r"careers\.microsoft\.com/.*?/job/(\d+)", re.I), "microsoft"),
    # Google careers: google.com/about/careers/applications/jobs/results/{digits}
    (re.compile(r"google\.com/.*?/jobs/results/(\d+)", re.I), "google"),
    # Meta: metacareers.com/jobs/{digits}
    (re.compile(r"metacareers\.com/jobs/(\d+)", re.I), "meta"),
]


def canonical_url_key(url: str) -> str:
    """Return a stable dedup key for a job URL.

    If we recognize the platform, use the platform-specific job ID. Otherwise
    fall back to the URL with query string / fragment stripped.
    """
    if not url:
        return ""
    for pat, platform in _STABLE_ID_PATTERNS:
        m = pat.search(url)
        if m:
            return f"{platform}:{m.group(1)}"
    # Generic fallback: scheme+host+path, no query, no fragment
    m = re.match(r"(https?://[^?#]+)", url.strip())
    return (m.group(1) if m else url).rstrip("/").lower()


# --- Web search call ----------------------------------------------------------

def _run_queries_prompt(queries: list[dict], careers_site: str) -> str:
    """Build the system/user prompt for the web-search LLM to execute a plan.

    The LLM is instructed to run the queries via its native web_search tool and
    return a single JSON array of listings with their source_query tag.
    """
    lines = [
        "Run the following web searches using your web-search tool.",
        "Return ALL results you find, not just obvious matches — filtering happens downstream.",
        "",
        "For each result, report: company, role_title, url, location (if visible in",
        "the snippet), and which source_query surfaced it. Prefer the canonical",
        "careers URL (e.g. amazon.jobs, careers.microsoft.com) over aggregators.",
        "",
        f"Target careers site (use as {'verification' if careers_site else 'hint'}): {careers_site or '(none)'}",
        "",
        "Queries to run (each uses a site: operator to restrict scope):",
    ]
    for i, q in enumerate(queries, 1):
        lines.append(f"  {i}. {q['q']}")
        if q.get("rationale"):
            lines.append(f"     (rationale: {q['rationale'][:120]})")
    lines.extend([
        "",
        "Output — a single JSON object only, no prose, no markdown fences:",
        "{",
        "  \"listings\": [",
        "    {",
        "      \"company\": \"Amazon\",",
        "      \"role_title\": \"Principal Product Manager, AWS DataSync\",",
        "      \"url\": \"https://amazon.jobs/jobs/3179898\",",
        "      \"location\": \"Seattle, WA or Santa Clara, CA\",",
        "      \"source_query\": \"\\\"Principal Product Manager\\\" \\\"AWS\\\" site:amazon.jobs\"",
        "    }",
        "  ]",
        "}",
    ])
    return "\n".join(lines)


@dataclass
class SearchHit:
    company: str
    role_title: str
    url: str
    location: Optional[str]
    source_query: Optional[str]


async def _execute_query_plan(
    db: Session,
    profile: Profile,
    company: TrackedCompany,
    plan: dict,
) -> list[SearchHit]:
    """Single LLM round-trip that runs all queries in the plan via web search."""
    queries = plan.get("queries") or []
    if not queries:
        return []
    prompt = _run_queries_prompt(queries, plan.get("careers_site", ""))
    provider = get_provider(profile)

    response = await provider.complete_with_search(
        system=(
            "You run the user-supplied web searches with your native web_search tool "
            "and collect ALL result rows (typically 10-30 per query). Prefer direct "
            "careers-site URLs. Return a single JSON object. Do NOT pre-filter or "
            "score — downstream code handles that."
        ),
        user=prompt,
        max_tokens=4000,
        temperature=0.2,
    )
    log_usage(db, profile.id, "ai_monitor_search", response)

    try:
        parsed = _extract_json(response.text)
    except Exception as exc:
        logger.warning(f"AI monitor search JSON parse failed for {company.name}: {exc}")
        return []

    raw = parsed.get("listings") if isinstance(parsed, dict) else parsed
    if not isinstance(raw, list):
        return []

    hits: list[SearchHit] = []
    seen_keys: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = (item.get("url") or "").strip()
        title = (item.get("role_title") or "").strip()
        company_name = (item.get("company") or company.name).strip()
        if not url or not title:
            continue
        key = canonical_url_key(url)
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        hits.append(SearchHit(
            company=company_name,
            role_title=title,
            url=url,
            location=(item.get("location") or None),
            source_query=(item.get("source_query") or None),
        ))
    return hits


# --- Main entry point ---------------------------------------------------------

def _detect_job_type(location: Optional[str]) -> str:
    if not location:
        return "Onsite"
    low = location.lower()
    if "remote" in low:
        return "Remote"
    if "hybrid" in low:
        return "Hybrid"
    return "Onsite"


async def run_ai_monitor_for_company(
    db: Session,
    profile: Profile,
    company: TrackedCompany,
    *,
    trigger: str = "manual",
    auto_evaluate: Optional[bool] = None,
) -> AIMonitorRun:
    """Full pipeline for one company.

    1. Ensure a query plan exists (generate if missing/stale)
    2. Execute the plan via web search
    3. URL-dedupe across queries
    4. Title-filter against profile's positive/negative keywords
    5. DB-dedupe against existing Listings by canonical_url_key
    6. Create new Listings for the survivors
    7. Record everything (kept / filtered / deduped) in an AIMonitorRun row
    8. Optionally auto-evaluate kept listings

    Returns the persisted AIMonitorRun.
    """
    run = AIMonitorRun(
        profile_id=profile.id,
        tracked_company_id=company.id,
        trigger=trigger,
        queries_used=[],
        all_listings=[],
        kept_listings=[],
        filtered_listings=[],
        deduped_listings=[],
    )
    db.add(run)
    db.commit()

    try:
        plan = await ensure_query_plan(db, profile, company)
        run.queries_used = plan.get("queries") or []
        db.commit()

        hits = await _execute_query_plan(db, profile, company, plan)
        run.all_listings = [
            {
                "company": h.company,
                "role_title": h.role_title,
                "url": h.url,
                "location": h.location,
                "source_query": h.source_query,
            }
            for h in hits
        ]
        run.total_found = len(hits)

        # Title filter (same deterministic function used by Gmail + ATS scanner)
        positive = profile.title_positive_keywords or []
        negative = profile.title_negative_keywords or []
        passes: list[SearchHit] = []
        filtered: list[dict] = []
        for h in hits:
            reason = why_title_fails(h.role_title, positive, negative)
            if reason is None:
                passes.append(h)
            else:
                filtered.append({
                    "company": h.company,
                    "role_title": h.role_title,
                    "url": h.url,
                    "location": h.location,
                    "source_query": h.source_query,
                    "reason": reason,
                })
        run.filtered_listings = filtered
        run.filtered_count = len(filtered)

        # DB dedup — build set of existing canonical keys for this profile in one query
        existing_urls = [
            u for (u,) in db.query(Listing.url)
            .filter(Listing.profile_id == profile.id, Listing.url.isnot(None))
            .all()
        ]
        existing_keys: set[str] = {canonical_url_key(u) for u in existing_urls}

        kept: list[SearchHit] = []
        deduped: list[dict] = []
        for h in passes:
            key = canonical_url_key(h.url)
            if key in existing_keys:
                deduped.append({
                    "company": h.company,
                    "role_title": h.role_title,
                    "url": h.url,
                })
                continue
            kept.append(h)
            existing_keys.add(key)
        run.deduped_listings = deduped
        run.deduped_count = len(deduped)

        # Create listings
        created_listings: list[Listing] = []
        for h in kept:
            listing = Listing(
                profile_id=profile.id,
                url=h.url,
                source="ai_monitor",
                source_detail=company.name,
                company=h.company or company.name,
                role_title=h.role_title,
                location=h.location,
                job_type=_detect_job_type(h.location),
                status="new",
            )
            db.add(listing)
            created_listings.append(listing)
        db.commit()

        run.kept_listings = [
            {
                "company": h.company,
                "role_title": h.role_title,
                "url": h.url,
                "location": h.location,
                "source_query": h.source_query,
            }
            for h in kept
        ]
        run.kept_count = len(kept)
        run.created_listing_ids = [l.id for l in created_listings]

        # Update company rollup
        company.last_ai_monitor_at = datetime.utcnow()
        company.last_ai_monitor_count = run.kept_count
        db.commit()

        # Optional auto-evaluate
        should_eval = profile.auto_evaluate if auto_evaluate is None else auto_evaluate
        if should_eval and profile.llm_api_key_enc and created_listings:
            from app.services.evaluation import evaluate_listing
            for listing in created_listings:
                try:
                    await evaluate_listing(db, profile, listing)
                except Exception as exc:
                    logger.warning(
                        f"AI monitor auto-eval failed for listing {listing.id}: {exc}"
                    )

    except Exception as exc:
        logger.exception(f"AI monitor run failed for company {company.name}")
        run.error = str(exc)[:500]
    finally:
        run.finished_at = datetime.utcnow()
        db.commit()
        db.refresh(run)

    logger.info(
        f"AI monitor run for {company.name}: found={run.total_found} "
        f"kept={run.kept_count} filtered={run.filtered_count} deduped={run.deduped_count}"
    )
    return run


async def run_ai_monitor_for_profile(
    db: Session,
    profile: Profile,
    *,
    trigger: str = "scheduled",
    auto_evaluate: Optional[bool] = None,
) -> list[AIMonitorRun]:
    """Run AI monitor across ALL ai_monitor_enabled companies for the profile."""
    companies = (
        db.query(TrackedCompany)
        .filter(
            TrackedCompany.profile_id == profile.id,
            TrackedCompany.enabled.is_(True),
            TrackedCompany.ai_monitor_enabled.is_(True),
        )
        .all()
    )
    if not companies:
        return []
    runs: list[AIMonitorRun] = []
    for c in companies:
        try:
            run = await run_ai_monitor_for_company(
                db, profile, c, trigger=trigger, auto_evaluate=auto_evaluate,
            )
            runs.append(run)
        except Exception as exc:
            logger.exception(f"AI monitor failed for {c.name}: {exc}")
    return runs
