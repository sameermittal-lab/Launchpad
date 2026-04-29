"""Main scanner service - orchestrates fetching, filtering, deduping, creating listings."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Listing, Profile, TrackedCompany
from app.services.scanner.parsers import ScannedJob, fetch_company_jobs

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    companies_scanned: int
    total_jobs_found: int
    filtered_out: int
    duplicates: int
    new_listings: int
    new_listing_ids: list[int]
    errors: list[dict]


def _title_matches_filter(
    title: str,
    positive: list[str],
    negative: list[str],
) -> bool:
    """Deprecated thin wrapper kept for backward compatibility.

    All new code should import `title_passes_filter` from `app.services.filters`.
    """
    from app.services.filters import title_passes_filter
    return title_passes_filter(title, positive, negative)


def _detect_job_type(location: str | None) -> str:
    if not location:
        return "Onsite"
    low = location.lower()
    if "remote" in low:
        return "Remote"
    if "hybrid" in low:
        return "Hybrid"
    return "Onsite"


async def scan_company(
    db: Session,
    profile: Profile,
    company: TrackedCompany,
) -> tuple[list[ScannedJob], Optional[str]]:
    """Scan a single company. Returns (jobs, error)."""
    return await fetch_company_jobs(
        company.name, company.careers_url, company.api_url,
    )


async def scan_all_companies(
    db: Session,
    profile: Profile,
    auto_evaluate: bool = False,
) -> ScanResult:
    """Run a scan across all enabled tracked companies for this profile.

    Optionally auto-evaluate matching listings (uses LLM, costs money).
    """
    companies = (
        db.query(TrackedCompany)
        .filter(
            TrackedCompany.profile_id == profile.id,
            TrackedCompany.enabled.is_(True),
        )
        .all()
    )

    if not companies:
        return ScanResult(0, 0, 0, 0, 0, [], [])

    # Fetch all companies in parallel
    tasks = [fetch_company_jobs(c.name, c.careers_url, c.api_url) for c in companies]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Existing URLs for dedup (per profile)
    existing_urls = {
        u for u, in db.query(Listing.url)
        .filter(Listing.profile_id == profile.id, Listing.url.isnot(None))
        .all()
    }

    positive = profile.title_positive_keywords or []
    negative = profile.title_negative_keywords or []

    total_found = 0
    filtered = 0
    dupes = 0
    new_ids: list[int] = []
    errors: list[dict] = []

    now = datetime.utcnow()

    for company, result in zip(companies, results):
        if isinstance(result, Exception):
            errors.append({"company": company.name, "error": str(result)})
            continue
        jobs, err = result
        if err:
            errors.append({"company": company.name, "error": err})
        if not jobs:
            continue
        total_found += len(jobs)
        company.last_scanned_at = now
        company.last_job_count = len(jobs)

        for job in jobs:
            if not job.url or not job.title:
                continue
            if job.url in existing_urls:
                dupes += 1
                continue
            if not _title_matches_filter(job.title, positive, negative):
                filtered += 1
                continue

            listing = Listing(
                profile_id=profile.id,
                url=job.url,
                source="scanner",
                source_detail=company.name,
                company=job.company or company.name,
                role_title=job.title,
                location=job.location,
                job_type=_detect_job_type(job.location),
                status="new",
            )
            db.add(listing)
            existing_urls.add(job.url)
            new_ids.append(id(listing))  # temp marker, replaced post-commit

    db.commit()

    # Refresh to get real IDs
    new_listings = (
        db.query(Listing)
        .filter(
            Listing.profile_id == profile.id,
            Listing.source == "scanner",
            Listing.created_at >= now,
        )
        .all()
    )
    new_ids = [l.id for l in new_listings]

    # Optionally auto-evaluate (async, fire and forget)
    if auto_evaluate and new_listings and profile.llm_api_key_enc:
        from app.services.evaluation import evaluate_listing
        for listing in new_listings:
            try:
                await evaluate_listing(db, profile, listing)
            except Exception as exc:
                logger.warning(
                    f"Auto-evaluation failed for scanner-added listing {listing.id}: {exc}"
                )

    result = ScanResult(
        companies_scanned=len(companies),
        total_jobs_found=total_found,
        filtered_out=filtered,
        duplicates=dupes,
        new_listings=len(new_listings),
        new_listing_ids=new_ids,
        errors=errors,
    )
    logger.info(
        f"Scan for profile {profile.id}: "
        f"{result.new_listings} new from {result.companies_scanned} companies "
        f"({result.total_jobs_found} found, {result.filtered_out} filtered, {result.duplicates} dupes)"
    )
    return result
