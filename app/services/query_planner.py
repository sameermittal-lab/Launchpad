"""Query planner — generates per-company web-search query plans tuned to the candidate."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Profile, TrackedCompany
from app.prompts import render_prompt
from app.services.evaluation import _build_pass_context, _extract_json
from app.services.llm import get_provider
from app.services.usage_tracker import log_usage

logger = logging.getLogger(__name__)

# Query plans are considered fresh for 30 days. After that they need a refresh.
PLAN_TTL = timedelta(days=30)


def _load_cv(profile: Profile) -> Optional[str]:
    """Load profile resume markdown (same shape as evaluation._load_cv)."""
    from pathlib import Path
    from app.config import settings
    cv_path: Path = settings.resolved_data_dir / str(profile.id) / "cv.md"
    if cv_path.exists():
        try:
            return cv_path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning(f"Could not read cv.md for profile {profile.id}: {exc}")
    return None


def _target_locations_str(profile: Profile) -> str:
    pd = profile.profile_data or {}
    locs = pd.get("target_locations") or pd.get("location")
    if isinstance(locs, list):
        return ", ".join(str(l) for l in locs if l) or "(any)"
    if isinstance(locs, str) and locs.strip():
        return locs.strip()
    return "(any)"


def _target_roles_str(profile: Profile) -> str:
    pd = profile.profile_data or {}
    tr = pd.get("target_roles")
    if isinstance(tr, list) and tr:
        return ", ".join(str(t) for t in tr if t)
    if isinstance(tr, str) and tr.strip():
        return tr.strip()
    return "(not specified)"


async def generate_query_plan(
    db: Session,
    profile: Profile,
    company: TrackedCompany,
) -> dict:
    """Generate a web-grounded query plan for one (profile, company) pair.

    Saves the plan onto the TrackedCompany row and returns it.
    Raises on LLM errors (caller decides what to do).
    """
    logger.info(
        f"Generating query plan for profile {profile.id} × company {company.name}"
    )

    cv_text = _load_cv(profile)
    pass_context = _build_pass_context(db, profile)

    prompt = render_prompt(
        "query_planner.md.j2",
        profile=profile,
        target_roles=_target_roles_str(profile),
        target_locations=_target_locations_str(profile),
        positive_keywords=profile.title_positive_keywords or [],
        cv_text=cv_text,
        pass_context=pass_context,
        company={
            "name": company.name,
            "careers_url": company.careers_url,
            "notes": company.notes,
        },
    )

    provider = get_provider(profile)
    try:
        response = await provider.complete_with_search(
            system=(
                "You are a recruiting strategist who generates web-search query plans to "
                "discover open roles at a specific company for a specific candidate. Use "
                "web search to ground level-mapping and careers-site knowledge in current "
                "reality. Respond with valid JSON only."
            ),
            user=prompt,
            max_tokens=1800,
            temperature=0.3,
        )
    except Exception as exc:
        logger.warning(
            f"Web-grounded query plan failed for {company.name}, falling back to training-data plan: {exc}"
        )
        response = await provider.complete(
            system=(
                "You are a recruiting strategist who generates web-search query plans to "
                "discover open roles at a specific company for a specific candidate. "
                "Respond with valid JSON only."
            ),
            user=prompt,
            max_tokens=1800,
            temperature=0.3,
        )

    log_usage(db, profile.id, "query_planner", response)

    try:
        plan = _extract_json(response.text)
    except Exception:
        logger.exception(f"Query plan JSON parse failed for {company.name}")
        raise ValueError(f"Could not parse query plan for {company.name}")

    # Basic sanity checks — reject plans that aren't actionable
    queries = plan.get("queries") if isinstance(plan, dict) else None
    if not isinstance(queries, list) or len(queries) < 1:
        raise ValueError(f"Query plan for {company.name} has no queries")

    cleaned_queries = []
    for q in queries:
        if not isinstance(q, dict) or not q.get("q"):
            continue
        q_text = str(q["q"]).strip()
        if "site:" not in q_text.lower():
            # Drop queries missing site: — they'd blow up the search scope
            logger.warning(f"Dropping query without site: operator: {q_text}")
            continue
        cleaned_queries.append({
            "q": q_text,
            "rationale": str(q.get("rationale") or "").strip(),
        })

    if not cleaned_queries:
        raise ValueError(f"Query plan for {company.name} had no valid queries after cleaning")

    # Cap at 5 queries as promised to user
    cleaned_queries = cleaned_queries[:5]

    plan["queries"] = cleaned_queries
    plan.setdefault("strategy", "scale-wide")
    plan.setdefault("careers_site", "")

    company.query_plan = plan
    company.query_plan_generated_at = datetime.utcnow()
    db.commit()
    db.refresh(company)

    logger.info(
        f"Query plan for {company.name}: {len(cleaned_queries)} queries, "
        f"strategy={plan.get('strategy')}"
    )
    return plan


def plan_needs_refresh(company: TrackedCompany) -> bool:
    """Return True if the company has no plan, or its plan is older than PLAN_TTL."""
    if not company.query_plan or not company.query_plan_generated_at:
        return True
    return (datetime.utcnow() - company.query_plan_generated_at) >= PLAN_TTL


async def ensure_query_plan(
    db: Session,
    profile: Profile,
    company: TrackedCompany,
    *,
    force: bool = False,
) -> dict:
    """Return an up-to-date query plan for the company, generating one if needed."""
    if force or plan_needs_refresh(company):
        return await generate_query_plan(db, profile, company)
    return company.query_plan
