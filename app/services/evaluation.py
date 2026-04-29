"""AI evaluation service - scores a listing against a profile."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Listing, Profile, HistoryEvent
from app.prompts import render_prompt
from app.services.llm import get_provider
from app.services.usage_tracker import log_usage

logger = logging.getLogger(__name__)


def _load_cv(profile: Profile) -> Optional[str]:
    """Load the profile's base resume markdown, if present."""
    cv_path: Path = settings.resolved_data_dir / str(profile.id) / "cv.md"
    if cv_path.exists():
        try:
            return cv_path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning(f"Could not read cv.md for profile {profile.id}: {exc}")
    return None


def _build_pass_context(db: Session, profile: Profile) -> Optional[dict]:
    """Build a pass-history context block for the evaluation prompt.

    Returns None if calibration isn't active (either <threshold passes or
    explicitly disabled). Otherwise returns a dict with:
      - count: total active pass decisions (use_for_calibration=True)
      - reason_counts: {reason_code: count}
      - examples: list of recent pass examples (company, role, score, reason, note)
    """
    pref = (getattr(profile, "pass_calibration_preference", "auto") or "auto").lower()
    if pref == "off":
        return None
    threshold = int(getattr(profile, "pass_history_threshold", 15) or 15)

    # Count only passes flagged for calibration
    active_passes = (
        db.query(Listing)
        .filter(
            Listing.profile_id == profile.id,
            Listing.status == "passed",
            Listing.use_for_calibration.is_(True),
        )
        .order_by(Listing.passed_at.desc().nullslast())
        .all()
    )

    count = len(active_passes)
    if pref == "auto" and count < threshold:
        return None
    if count == 0:
        return None

    reason_counts: dict[str, int] = {}
    for l in active_passes:
        r = l.pass_reason or "other"
        reason_counts[r] = reason_counts.get(r, 0) + 1

    # Keep the most recent 10 as examples for the prompt
    examples = []
    for l in active_passes[:10]:
        examples.append({
            "company": l.company,
            "role": l.role_title,
            "score": l.score,
            "reason": l.pass_reason or "other",
            "note": (l.pass_note or "")[:200],
        })

    return {
        "count": count,
        "reason_counts": reason_counts,
        "examples": examples,
    }


async def _ensure_company_research(
    db: Session,
    profile: Profile,
    listing: Listing,
) -> Optional[dict]:
    """Ensure the listing's company has up-to-date research, generating it if missing.

    Returns a compact dict suitable for injection into the evaluation prompt, or None
    if research can't be produced (unknown company, web search unavailable, etc.).

    Non-fatal — any error is logged and the function returns None. The evaluation
    pipeline MUST keep going even if research fails.
    """
    from app.models import Company  # local import to avoid circular import
    from app.services.company_research import CACHE_TTL, research_company

    company_name = (listing.company or "").strip()
    if not company_name or company_name.lower() in {"(unknown)", "unknown"}:
        return None

    # See if we already have fresh research. If so, skip the extra LLM call.
    existing = (
        db.query(Company)
        .filter(
            Company.profile_id == profile.id,
            Company.name == company_name,
        )
        .first()
    )
    company: Optional[Company] = existing
    if existing is None or (datetime.utcnow() - existing.refreshed_at) >= CACHE_TTL:
        logger.info(
            f"Company research {'missing' if existing is None else 'stale'} for "
            f"'{company_name}' (profile {profile.id}); running research before eval."
        )
        try:
            company = await research_company(
                db,
                profile,
                company_name,
                careers_url=None,
                force_refresh=False,
            )
        except Exception as exc:
            # Don't fail the eval just because research can't run — log & proceed.
            logger.warning(
                f"Auto company research failed for '{company_name}': {exc}. "
                f"Proceeding with evaluation without company research."
            )
            return None

    if company is None:
        return None

    # Build a compact dict for the prompt — use research_data if present, otherwise
    # fall back to the top-level columns so every code path works.
    data = dict(company.research_data or {})
    data.setdefault("description", company.description)
    data.setdefault("valuation", company.valuation)
    data.setdefault("employee_count", company.employee_count)
    data.setdefault("glassdoor_rating", company.glassdoor_rating)
    data.setdefault("tech_stack", company.tech_stack)
    data["_name"] = company.name
    data["_refreshed_at"] = (
        company.refreshed_at.isoformat() if company.refreshed_at else None
    )
    return data


def _extract_json(text: str) -> dict:
    """Extract the first JSON object from LLM text output.

    Tolerates markdown code fences and leading/trailing prose.
    """
    # Strip code fences
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*\n", "", text)
        text = re.sub(r"\n```\s*$", "", text)

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Find the first {...} block
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"Could not parse JSON from LLM output: {exc}")
    raise ValueError("No JSON object found in LLM output")


async def evaluate_listing(
    db: Session,
    profile: Profile,
    listing: Listing,
) -> Listing:
    """Run AI evaluation on a listing and update it in-place.

    Returns the updated listing. Raises on failure.
    """
    logger.info(
        f"Evaluating listing {listing.id} ({listing.company} - {listing.role_title})"
    )

    # Concurrency guard — same listing shouldn't be evaluated twice in parallel.
    # Stale locks (>3 min old) are treated as crashed runs and overridden.
    from datetime import datetime as _dt, timedelta as _td
    now = _dt.utcnow()
    lock = getattr(listing, "evaluation_in_progress", None)
    if lock is not None and (now - lock) < _td(minutes=3):
        raise ValueError(f"Evaluation already in progress for listing {listing.id} (started {lock.isoformat()}Z)")
    listing.evaluation_in_progress = now
    db.commit()

    try:
        return await _evaluate_listing_locked(db, profile, listing)
    finally:
        # Clear the lock whether the eval succeeded or threw.
        listing.evaluation_in_progress = None
        try:
            db.commit()
        except Exception:
            db.rollback()


async def _evaluate_listing_locked(
    db: Session,
    profile: Profile,
    listing: Listing,
) -> Listing:
    """The actual evaluation work — caller holds the `evaluation_in_progress` lock."""
    cv_text = _load_cv(profile)
    pd = profile.profile_data or {}
    target_roles = pd.get("target_roles") or []
    if isinstance(target_roles, list):
        target_roles_str = ", ".join(target_roles) if target_roles else "(not specified)"
    else:
        target_roles_str = str(target_roles) or "(not specified)"

    web_grounded = bool(getattr(profile, "web_grounded_eval", True))

    # Gather pass-history context if calibration is active
    pass_context = _build_pass_context(db, profile)

    # Ensure we have company research for this listing's company.
    # If none exists (or cache is stale), generate it now so the evaluation
    # prompt can use fresh, web-grounded company data.
    # Failures here are non-fatal — we continue with evaluation either way.
    company_research = await _ensure_company_research(db, profile, listing)

    prompt = render_prompt(
        "evaluation.md.j2",
        profile=profile,
        listing=listing,
        cv_text=cv_text,
        target_roles=target_roles_str,
        target_salary=pd.get("target_salary") or "(not specified)",
        location=pd.get("location") or "(not specified)",
        cover_letter_tone=profile.cover_letter_tone,
        scoring_weights=profile.scoring_weights or {},
        web_grounded=web_grounded,
        pass_context=pass_context,
        company_research=company_research,
    )

    provider = get_provider(profile)
    citations = []
    if web_grounded:
        try:
            response = await provider.complete_with_search(
                system="You are a senior career coach who evaluates jobs objectively and uses web search to ground company-specific claims in current reality. Always respond with valid JSON.",
                user=prompt,
                max_tokens=3000,
                temperature=0.3,
            )
            # Extract citations if provider surfaced them
            raw_citations = getattr(response, "citations", None) or []
            citations = [
                {
                    "title": getattr(c, "title", None) or (c.get("title") if isinstance(c, dict) else None),
                    "url": getattr(c, "url", None) or (c.get("url") if isinstance(c, dict) else None),
                }
                for c in raw_citations
                if (getattr(c, "url", None) or (isinstance(c, dict) and c.get("url")))
            ]
        except Exception as exc:
            logger.warning(
                f"Web-grounded evaluation failed, falling back to training data only: {exc}"
            )
            # Fall back to plain completion so the user still gets a score
            response = await provider.complete(
                system="You are a senior career coach who evaluates jobs objectively. Always respond with valid JSON.",
                user=prompt,
                max_tokens=3000,
                temperature=0.3,
            )
    else:
        response = await provider.complete(
            system="You are a senior career coach who evaluates jobs objectively. Always respond with valid JSON.",
            user=prompt,
            max_tokens=3000,
            temperature=0.3,
        )
    log_usage(db, profile.id, "evaluation", response)

    try:
        result = _extract_json(response.text)
    except ValueError as exc:
        logger.error(f"Failed to parse evaluation JSON: {exc}\nRaw: {response.text[:500]}")
        raise

    # Apply core results
    listing.sub_scores = result.get("sub_scores", {})
    listing.score = float(result.get("overall_score", 0))
    listing.grade = result.get("grade", "")
    listing.ai_summary = result.get("ai_summary", "")
    listing.archetype = result.get("archetype", listing.archetype)

    # v2 fields — qualitative breakdown
    listing.evaluation_version = 2
    listing.dimension_rationales = result.get("dimension_rationales") or {}
    listing.take_it_if = result.get("take_it_if") or []
    listing.compromises = result.get("compromises") or []
    listing.blockers = result.get("blockers") or []
    listing.citations = citations or None

    # Use extracted fields to overwrite only if original was missing/default
    if not listing.company or listing.company == "(unknown)":
        listing.company = result.get("company_extracted") or listing.company
    if not listing.role_title or listing.role_title == "(unknown)":
        listing.role_title = result.get("role_extracted") or listing.role_title
    if not listing.location:
        listing.location = result.get("location_extracted")
    if not listing.job_type:
        listing.job_type = result.get("job_type_extracted")
    if not listing.salary_range:
        listing.salary_range = result.get("salary_extracted")

    listing.status = "evaluated"

    # Store the full LLM result in a history event for debugging
    evt = HistoryEvent(
        profile_id=profile.id,
        listing_id=listing.id,
        event_type="evaluation",
        event_data={
            "score": listing.score,
            "grade": listing.grade,
            "strengths": result.get("key_strengths", []),
            "gaps": result.get("gaps_or_risks", []),
            "cost_usd": response.cost_usd,
            "model": response.model,
            "web_grounded": web_grounded,
            "citations_count": len(citations),
            "evaluation_version": 2,
            "pass_context_used": pass_context is not None,
            "pass_context_size": (pass_context or {}).get("count", 0),
            "company_research_used": company_research is not None,
        },
    )
    db.add(evt)
    db.commit()
    db.refresh(listing)

    logger.info(
        f"Evaluated listing {listing.id}: score={listing.score}, grade={listing.grade}, "
        f"web_grounded={web_grounded}, citations={len(citations)}"
    )
    return listing


async def extract_listing_fields(profile: Profile, db: Session, jd_text: str) -> dict:
    """Extract structured fields from raw JD text using LLM."""
    prompt = render_prompt("extract_listing.md.j2", jd_text=jd_text)
    provider = get_provider(profile)
    response = await provider.complete(
        system="You extract structured data from job listings. Always respond with valid JSON only.",
        user=prompt,
        max_tokens=2000,
        temperature=0.1,
    )
    log_usage(db, profile.id, "extract_listing", response)
    return _extract_json(response.text)
