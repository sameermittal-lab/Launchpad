"""Company research service - web-grounded LLM research cached per company per profile."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Company, Profile
from app.prompts import render_prompt
from app.services.evaluation import _extract_json
from app.services.llm import get_provider
from app.services.usage_tracker import log_usage

logger = logging.getLogger(__name__)

CACHE_TTL = timedelta(days=30)


async def research_company(
    db: Session,
    profile: Profile,
    company_name: str,
    careers_url: Optional[str] = None,
    force_refresh: bool = False,
) -> Company:
    """Get or create research for a company using web-grounded LLM search.

    Uses cache unless force_refresh=True or cache is older than 30 days.
    """
    existing = (
        db.query(Company)
        .filter(
            Company.profile_id == profile.id,
            Company.name == company_name,
        )
        .first()
    )
    if existing and not force_refresh:
        if datetime.utcnow() - existing.refreshed_at < CACHE_TTL:
            return existing

    today_str = date.today().isoformat()
    prompt = render_prompt(
        "company_research.md.j2",
        company_name=company_name,
        careers_url=careers_url or "",
        today=today_str,
    )
    provider = get_provider(profile)

    # Use web-grounded search for fresh data
    response = await provider.complete_with_search(
        system=(
            "You research companies with current information for job candidates. "
            "Use web search to find fresh, recent data. Output JSON only, no prose."
        ),
        user=prompt,
        max_tokens=2500,
        temperature=0.2,
    )
    log_usage(db, profile.id, "company_research", response)

    try:
        data = _extract_json(response.text)
    except Exception as exc:
        logger.error(f"Company research JSON parse failed: {exc}")
        raise

    # Attach citations to research_data for the UI
    if response.citations:
        data["sources"] = [
            {"title": c.title, "url": c.url}
            for c in response.citations
        ]

    glassdoor_raw = data.get("glassdoor_rating")
    try:
        glassdoor = float(glassdoor_raw) if glassdoor_raw not in (None, "", "Unknown") else None
    except (TypeError, ValueError):
        glassdoor = None

    if existing:
        existing.description = data.get("description")
        existing.valuation = data.get("valuation")
        existing.employee_count = data.get("employee_count")
        existing.glassdoor_rating = glassdoor
        existing.tech_stack = data.get("tech_stack")
        existing.research_data = data
        existing.refreshed_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return existing

    company = Company(
        profile_id=profile.id,
        name=company_name,
        description=data.get("description"),
        valuation=data.get("valuation"),
        employee_count=data.get("employee_count"),
        glassdoor_rating=glassdoor,
        tech_stack=data.get("tech_stack"),
        research_data=data,
        refreshed_at=datetime.utcnow(),
    )
    db.add(company)
    db.commit()
    db.refresh(company)
    return company
