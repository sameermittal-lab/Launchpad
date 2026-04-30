"""Listings CRUD API + evaluation trigger."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import HistoryEvent, Listing, Profile
from app.services.cover_letter import generate_cover_letter
from app.services.evaluation import evaluate_listing, extract_listing_fields
from app.services.resume_tailor import tailor_resume
from app.services.url_fetcher import fetch_url
from app.utils.session import get_current_profile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/listings", tags=["listings"])

VALID_STATUSES = {"new", "evaluated", "applied", "interview", "offer", "rejected", "passed"}


class ListingSummary(BaseModel):
    id: int
    company: str
    role_title: str
    location: Optional[str] = None
    job_type: Optional[str] = None
    salary_range: Optional[str] = None
    archetype: Optional[str] = None
    status: str
    score: Optional[float] = None
    grade: Optional[str] = None
    source: str
    created_at: datetime


class ListingDetail(ListingSummary):
    url: Optional[str] = None
    jd_text: Optional[str] = None
    ai_summary: Optional[str] = None
    sub_scores: Optional[dict] = None
    tailored_resume_path: Optional[str] = None
    cover_letter_path: Optional[str] = None
    keyword_coverage: Optional[float] = None
    rejection_reason: Optional[str] = None
    source_detail: Optional[str] = None
    tailoring_intensity: Optional[str] = None
    cover_letter_tone_override: Optional[str] = None
    # v2 evaluation fields
    evaluation_version: Optional[int] = None
    dimension_rationales: Optional[dict] = None
    take_it_if: Optional[list] = None
    compromises: Optional[list] = None
    blockers: Optional[list] = None
    citations: Optional[list] = None
    # Pass tracking
    pass_reason: Optional[str] = None
    pass_note: Optional[str] = None
    passed_at: Optional[datetime] = None
    use_for_calibration: Optional[bool] = None
    # Concurrency
    evaluation_in_progress: Optional[datetime] = None


class ListingCreate(BaseModel):
    """Create a listing from either a URL or raw JD text."""
    url: Optional[str] = None
    jd_text: Optional[str] = None
    company: Optional[str] = None
    role_title: Optional[str] = None
    auto_evaluate: bool = True


class ListingUpdate(BaseModel):
    status: Optional[str] = None
    rejection_reason: Optional[str] = None
    company: Optional[str] = None
    role_title: Optional[str] = None
    location: Optional[str] = None
    salary_range: Optional[str] = None
    jd_text: Optional[str] = None


class PipelineStats(BaseModel):
    new: int
    evaluated: int
    applied: int
    interview: int
    offer: int
    rejected: int
    total: int
    avg_score: Optional[float] = None


def _to_summary(listing: Listing) -> ListingSummary:
    return ListingSummary(
        id=listing.id,
        company=listing.company,
        role_title=listing.role_title,
        location=listing.location,
        job_type=listing.job_type,
        salary_range=listing.salary_range,
        archetype=listing.archetype,
        status=listing.status,
        score=listing.score,
        grade=listing.grade,
        source=listing.source,
        created_at=listing.created_at,
    )


def _to_detail(listing: Listing) -> ListingDetail:
    return ListingDetail(
        id=listing.id,
        company=listing.company,
        role_title=listing.role_title,
        location=listing.location,
        job_type=listing.job_type,
        salary_range=listing.salary_range,
        archetype=listing.archetype,
        status=listing.status,
        score=listing.score,
        grade=listing.grade,
        source=listing.source,
        source_detail=listing.source_detail,
        created_at=listing.created_at,
        url=listing.url,
        jd_text=listing.jd_text,
        ai_summary=listing.ai_summary,
        sub_scores=listing.sub_scores,
        tailored_resume_path=listing.tailored_resume_path,
        cover_letter_path=listing.cover_letter_path,
        keyword_coverage=listing.keyword_coverage,
        rejection_reason=listing.rejection_reason,
        tailoring_intensity=listing.tailoring_intensity,
        cover_letter_tone_override=listing.cover_letter_tone_override,
        evaluation_version=listing.evaluation_version,
        dimension_rationales=listing.dimension_rationales,
        take_it_if=listing.take_it_if,
        compromises=listing.compromises,
        blockers=listing.blockers,
        citations=listing.citations,
        pass_reason=listing.pass_reason,
        pass_note=listing.pass_note,
        passed_at=listing.passed_at,
        use_for_calibration=listing.use_for_calibration,
        evaluation_in_progress=listing.evaluation_in_progress,
    )


@router.get("/stats", response_model=PipelineStats)
def get_stats(
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(Listing.status, func.count(Listing.id))
        .filter(Listing.profile_id == profile.id)
        .group_by(Listing.status)
        .all()
    )
    counts = {status: 0 for status in VALID_STATUSES}
    for status, count in rows:
        if status in counts:
            counts[status] = count

    avg_score = (
        db.query(func.avg(Listing.score))
        .filter(Listing.profile_id == profile.id, Listing.score.isnot(None))
        .scalar()
    )

    return PipelineStats(
        **counts,
        total=sum(counts.values()),
        avg_score=round(float(avg_score), 2) if avg_score else None,
    )


@router.get("", response_model=list[ListingSummary])
def list_listings(
    status: Optional[str] = None,
    exclude_status: Optional[str] = None,
    min_score: Optional[float] = None,
    order_by: Optional[str] = None,
    limit: int = 200,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    q = db.query(Listing).filter(Listing.profile_id == profile.id)
    if status:
        q = q.filter(Listing.status == status)
    if exclude_status:
        exclude = [s.strip() for s in exclude_status.split(",") if s.strip()]
        if exclude:
            q = q.filter(~Listing.status.in_(exclude))
    if min_score is not None:
        q = q.filter(Listing.score >= min_score)
    # Default ordering = newest first; "-score" = highest-score first (nulls last).
    if order_by == "-score":
        q = q.order_by(Listing.score.desc().nullslast(), Listing.created_at.desc())
    else:
        q = q.order_by(Listing.created_at.desc())
    listings = q.limit(limit).all()
    return [_to_summary(l) for l in listings]


@router.get("/{listing_id}", response_model=ListingDetail)
def get_listing(
    listing_id: int,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    listing = db.get(Listing, listing_id)
    if not listing or listing.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Listing not found")
    return _to_detail(listing)


@router.put("/{listing_id}", response_model=ListingDetail)
def update_listing(
    listing_id: int,
    data: ListingUpdate,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    listing = db.get(Listing, listing_id)
    if not listing or listing.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Listing not found")

    update_dict = data.model_dump(exclude_unset=True)
    if "status" in update_dict and update_dict["status"] not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {sorted(VALID_STATUSES)}",
        )

    old_status = listing.status
    for k, v in update_dict.items():
        setattr(listing, k, v)

    if "status" in update_dict and update_dict["status"] != old_status:
        db.add(HistoryEvent(
            profile_id=profile.id,
            listing_id=listing.id,
            event_type="status_change",
            event_data={"from": old_status, "to": update_dict["status"]},
        ))

    db.commit()
    db.refresh(listing)
    return _to_detail(listing)


@router.delete("/{listing_id}")
def delete_listing(
    listing_id: int,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    listing = db.get(Listing, listing_id)
    if not listing or listing.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Listing not found")
    db.delete(listing)
    db.commit()
    return {"deleted": True}


@router.delete("")
def bulk_delete(
    status: Optional[str] = None,
    source: Optional[str] = None,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Delete listings matching status and/or source filters.

    Example: DELETE /api/listings?status=new&source=scanner
    removes all unprocessed scanner-added listings.
    """
    q = db.query(Listing).filter(Listing.profile_id == profile.id)
    if status:
        q = q.filter(Listing.status == status)
    if source:
        q = q.filter(Listing.source == source)
    count = q.count()
    q.delete(synchronize_session=False)
    db.commit()
    return {"deleted": count}


@router.post("", response_model=ListingDetail)
async def create_listing(
    data: ListingCreate,
    force_add: bool = False,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Create a listing from a URL or raw JD text, then optionally run evaluation.

    If the extracted role title fails the user's positive/negative keyword filter,
    we return HTTP 409 with a descriptive reason so the frontend can prompt for
    explicit override. Pass `?force_add=1` to bypass the filter.

    Flow:
      1. If URL given: fetch + extract JD text (httpx -> trafilatura -> Playwright)
      2. If raw text given: use as-is
      3. Extract structured fields (company, role, location) via LLM
      4. Check title filter (unless force_add)
      5. Dedup check vs existing listings
      6. Create listing row with status='new'
      7. If auto_evaluate: run full evaluation, update status to 'evaluated'
    """
    if not data.url and not data.jd_text:
        raise HTTPException(
            status_code=400,
            detail="Must provide either 'url' or 'jd_text'",
        )

    if not profile.llm_api_key_enc:
        raise HTTPException(
            status_code=400,
            detail="No LLM API key configured. Go to Settings to add one.",
        )

    jd_text = data.jd_text
    url = data.url

    # Fetch URL if provided
    if url and not jd_text:
        logger.info(f"Fetching URL: {url}")
        fetched = await fetch_url(url)
        if not fetched.success or not fetched.content:
            raise HTTPException(
                status_code=400,
                detail=f"Could not fetch URL: {fetched.error or 'empty content'}",
            )
        jd_text = fetched.content

    # Extract structured fields via LLM (only if we don't have them already)
    try:
        extracted = await extract_listing_fields(profile, db, jd_text)
    except Exception as exc:
        logger.error(f"Field extraction failed: {exc}")
        extracted = {}

    company = data.company or extracted.get("company") or "(unknown)"
    role_title = data.role_title or extracted.get("role_title") or "(unknown)"

    # Title filter: reject (with a helpful error) if the title fails the user's filter.
    # Caller can retry with force_add=1 to override.
    if not force_add:
        from app.services.filters import why_title_fails
        reason = why_title_fails(
            role_title,
            profile.title_positive_keywords or [],
            profile.title_negative_keywords or [],
        )
        if reason is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "title_filter_failed",
                    "reason": reason,
                    "role_title": role_title,
                    "company": company,
                    "message": f"Listing title \"{role_title}\" {reason}. Pass force_add=1 to add anyway.",
                },
            )

    # Dedup: before inserting, check if the URL or (company, role) already exists
    from app.services.gmail.sync import _canonicalize_url, _find_matching_listing
    canonical = _canonicalize_url(url) if url else None
    existing = None
    if url or (company and role_title):
        existing = _find_matching_listing(db, profile.id, company, role_title, canonical, url)
    if existing:
        # Merge source info — manual paste on top of previously-gmail or previously-scanner listing
        detail = (existing.source_detail or "").strip()
        add = "also added manually"
        if add not in detail:
            existing.source_detail = (detail + " · " + add) if detail else add
        db.commit()
        db.refresh(existing)
        # If the dup was still 'new' and auto_evaluate is on, still evaluate it now
        if data.auto_evaluate and profile.auto_evaluate and existing.status == "new":
            try:
                await evaluate_listing(db, profile, existing)
            except Exception as exc:
                logger.error(f"Evaluation failed for listing {existing.id}: {exc}")
                existing.ai_summary = f"Evaluation failed: {exc}"
                db.commit()
        return _to_detail(existing)

    listing = Listing(
        profile_id=profile.id,
        url=url,
        source="manual",
        source_detail="paste",
        company=company,
        role_title=role_title,
        location=extracted.get("location"),
        job_type=extracted.get("job_type"),
        salary_range=extracted.get("salary_range"),
        jd_text=extracted.get("jd_text") or jd_text,
        status="new",
    )
    db.add(listing)
    db.commit()
    db.refresh(listing)

    # Auto-evaluate if enabled
    if data.auto_evaluate and profile.auto_evaluate:
        try:
            await evaluate_listing(db, profile, listing)
        except Exception as exc:
            logger.error(f"Evaluation failed for listing {listing.id}: {exc}")
            listing.ai_summary = f"Evaluation failed: {exc}"
            db.commit()
            db.refresh(listing)

        # After evaluation, optionally auto-generate tailored resume + cover letter
        if profile.auto_generate_assets and listing.status == "evaluated":
            try:
                await tailor_resume(db, profile, listing)
            except Exception as exc:
                logger.warning(f"Resume tailoring failed for listing {listing.id}: {exc}")
            try:
                await generate_cover_letter(db, profile, listing)
            except Exception as exc:
                logger.warning(f"Cover letter failed for listing {listing.id}: {exc}")
            db.refresh(listing)

    return _to_detail(listing)


@router.post("/{listing_id}/tailor", response_model=ListingDetail)
async def tailor(
    listing_id: int,
    intensity: Optional[str] = None,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Generate (or regenerate) the tailored resume for a listing.

    `intensity` can be 'light' | 'medium' | 'heavy'. Defaults to listing's
    last-used intensity or 'medium'.
    """
    listing = db.get(Listing, listing_id)
    if not listing or listing.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Listing not found")
    if not profile.llm_api_key_enc:
        raise HTTPException(status_code=400, detail="No LLM API key configured")
    had_prior = bool(listing.tailored_resume_md)
    try:
        await tailor_resume(db, profile, listing, intensity=intensity)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Record out-of-band change in the chat thread so ongoing conversations
    # stay accurate about which document version we're editing.
    if had_prior and listing.chat_history:
        from app.services.resume_chat import append_system_note
        effective_intensity = intensity or listing.tailoring_intensity or "medium"
        append_system_note(db, listing, f"— Resume regenerated from scratch (intensity={effective_intensity}). Prior chat context still applies but targets the new document. —")
    return _to_detail(listing)


@router.post("/{listing_id}/cover-letter", response_model=ListingDetail)
async def cover_letter(
    listing_id: int,
    tone: Optional[str] = None,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Generate (or regenerate) cover letter. Optional tone override."""
    listing = db.get(Listing, listing_id)
    if not listing or listing.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Listing not found")
    if not profile.llm_api_key_enc:
        raise HTTPException(status_code=400, detail="No LLM API key configured")
    had_prior = bool(listing.tailored_cover_letter_md)
    try:
        await generate_cover_letter(db, profile, listing, tone=tone)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if had_prior and listing.chat_history:
        from app.services.resume_chat import append_system_note
        effective_tone = tone or listing.cover_letter_tone_override or profile.cover_letter_tone or "warm"
        append_system_note(db, listing, f"— Cover letter regenerated from scratch (tone={effective_tone}). —")
    return _to_detail(listing)


# ------------------------- Editing endpoints -------------------------


class TailoredResumeResponse(BaseModel):
    markdown: str
    markdown_original: Optional[str] = None
    intensity: Optional[str] = None
    pdf_path: Optional[str] = None


class MarkdownUpdate(BaseModel):
    markdown: str


class CoverLetterResponse(BaseModel):
    markdown: str
    markdown_original: Optional[str] = None
    tone_override: Optional[str] = None
    profile_default_tone: Optional[str] = None
    pdf_path: Optional[str] = None


@router.get("/{listing_id}/tailored-resume", response_model=TailoredResumeResponse)
def get_tailored_resume_md(
    listing_id: int,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    listing = db.get(Listing, listing_id)
    if not listing or listing.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Listing not found")
    return TailoredResumeResponse(
        markdown=listing.tailored_resume_md or "",
        markdown_original=listing.tailored_resume_md_original,
        intensity=listing.tailoring_intensity,
        pdf_path=listing.tailored_resume_path,
    )


@router.put("/{listing_id}/tailored-resume", response_model=TailoredResumeResponse)
async def update_tailored_resume_md(
    listing_id: int,
    data: MarkdownUpdate,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Save edited markdown and re-render the PDF (no LLM call)."""
    from app.services.resume_tailor import rerender_resume_from_markdown
    listing = db.get(Listing, listing_id)
    if not listing or listing.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Listing not found")
    if not data.markdown.strip():
        raise HTTPException(status_code=400, detail="Markdown is empty")

    listing.tailored_resume_md = data.markdown
    if not listing.tailored_resume_md_original:
        listing.tailored_resume_md_original = data.markdown
    db.commit()
    try:
        await rerender_resume_from_markdown(profile, listing)
    except Exception as exc:
        logger.exception("Re-render resume PDF failed")
        raise HTTPException(status_code=500, detail=f"PDF render failed: {exc}")
    db.commit()
    db.refresh(listing)
    return TailoredResumeResponse(
        markdown=listing.tailored_resume_md,
        markdown_original=listing.tailored_resume_md_original,
        intensity=listing.tailoring_intensity,
        pdf_path=listing.tailored_resume_path,
    )


@router.post("/{listing_id}/tailored-resume/revert", response_model=TailoredResumeResponse)
async def revert_tailored_resume(
    listing_id: int,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    from app.services.resume_tailor import rerender_resume_from_markdown
    listing = db.get(Listing, listing_id)
    if not listing or listing.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Listing not found")
    if not listing.tailored_resume_md_original:
        raise HTTPException(status_code=400, detail="No original version to revert to")
    listing.tailored_resume_md = listing.tailored_resume_md_original
    db.commit()
    await rerender_resume_from_markdown(profile, listing)
    db.commit()
    db.refresh(listing)
    if listing.chat_history:
        from app.services.resume_chat import append_system_note
        append_system_note(db, listing, "— Resume reverted to the original AI-generated version. Chat context retained. —")
    return TailoredResumeResponse(
        markdown=listing.tailored_resume_md,
        markdown_original=listing.tailored_resume_md_original,
        intensity=listing.tailoring_intensity,
        pdf_path=listing.tailored_resume_path,
    )


@router.get("/{listing_id}/cover-letter-md", response_model=CoverLetterResponse)
def get_cover_letter_md(
    listing_id: int,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    listing = db.get(Listing, listing_id)
    if not listing or listing.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Listing not found")
    return CoverLetterResponse(
        markdown=listing.tailored_cover_letter_md or "",
        markdown_original=listing.tailored_cover_letter_md_original,
        tone_override=listing.cover_letter_tone_override,
        profile_default_tone=profile.cover_letter_tone,
        pdf_path=listing.cover_letter_path,
    )


@router.put("/{listing_id}/cover-letter-md", response_model=CoverLetterResponse)
async def update_cover_letter_md(
    listing_id: int,
    data: MarkdownUpdate,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    from app.services.cover_letter import rerender_cover_letter_from_markdown
    listing = db.get(Listing, listing_id)
    if not listing or listing.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Listing not found")
    if not data.markdown.strip():
        raise HTTPException(status_code=400, detail="Markdown is empty")

    listing.tailored_cover_letter_md = data.markdown
    if not listing.tailored_cover_letter_md_original:
        listing.tailored_cover_letter_md_original = data.markdown
    db.commit()
    try:
        await rerender_cover_letter_from_markdown(profile, listing)
    except Exception as exc:
        logger.exception("Re-render cover letter PDF failed")
        raise HTTPException(status_code=500, detail=f"PDF render failed: {exc}")
    db.commit()
    db.refresh(listing)
    return CoverLetterResponse(
        markdown=listing.tailored_cover_letter_md,
        markdown_original=listing.tailored_cover_letter_md_original,
        tone_override=listing.cover_letter_tone_override,
        profile_default_tone=profile.cover_letter_tone,
        pdf_path=listing.cover_letter_path,
    )


@router.post("/{listing_id}/cover-letter-md/revert", response_model=CoverLetterResponse)
async def revert_cover_letter(
    listing_id: int,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    from app.services.cover_letter import rerender_cover_letter_from_markdown
    listing = db.get(Listing, listing_id)
    if not listing or listing.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Listing not found")
    if not listing.tailored_cover_letter_md_original:
        raise HTTPException(status_code=400, detail="No original version to revert to")
    listing.tailored_cover_letter_md = listing.tailored_cover_letter_md_original
    db.commit()
    await rerender_cover_letter_from_markdown(profile, listing)
    db.commit()
    db.refresh(listing)
    if listing.chat_history:
        from app.services.resume_chat import append_system_note
        append_system_note(db, listing, "— Cover letter reverted to the original AI-generated version. Chat context retained. —")
    return CoverLetterResponse(
        markdown=listing.tailored_cover_letter_md,
        markdown_original=listing.tailored_cover_letter_md_original,
        tone_override=listing.cover_letter_tone_override,
        profile_default_tone=profile.cover_letter_tone,
        pdf_path=listing.cover_letter_path,
    )


@router.post("/{listing_id}/evaluate", response_model=ListingDetail)
async def evaluate(
    listing_id: int,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Re-run evaluation on an existing listing.

    Returns 409 if another evaluation is already in progress for the same
    listing (concurrency guard — prevents double-clicks from spending 2x LLM cost).
    """
    listing = db.get(Listing, listing_id)
    if not listing or listing.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Listing not found")
    if not profile.llm_api_key_enc:
        raise HTTPException(status_code=400, detail="No LLM API key configured")

    # Pre-flight concurrency check (evaluate_listing also checks but we want a
    # cleaner HTTP error than a ValueError trace).
    lock = listing.evaluation_in_progress
    if lock is not None and (datetime.utcnow() - lock) < timedelta(minutes=3):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "evaluation_in_progress",
                "started_at": lock.isoformat() + "Z",
                "message": "An evaluation is already running for this listing. Give it ~30-60s.",
            },
        )

    try:
        await evaluate_listing(db, profile, listing)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _to_detail(listing)


# ------------------------- Batch evaluation -------------------------


class BatchEvaluateRequest(BaseModel):
    """Kick off evaluation across a set of unevaluated listings.

    mode:
      "all"         — every listing with score IS NULL (respects `ids` filter if provided)
      "confident"   — only smart_filter_verdict == "yes" (or null if filter was off)
      "maybe_only"  — only smart_filter_verdict == "maybe"
      "keyword_top" — top N by rough keyword overlap with target_roles (for smart-OFF path)
    """
    mode: str = Field(default="all")
    ids: Optional[list[int]] = None
    limit: Optional[int] = None  # cap on how many to evaluate this round
    concurrency: int = Field(default=4, ge=1, le=8)


class BatchEvaluateResponse(BaseModel):
    requested: int
    evaluated: int
    failed: int
    skipped: int
    errors: list[dict]


def _keyword_rank(listing: Listing, target_roles: list[str]) -> int:
    """Cheap rank for the 'keyword_top' mode — count how many target-role tokens
    appear in the listing's role_title (case-insensitive). Ties broken by recency.
    """
    if not target_roles:
        return 0
    title = (listing.role_title or "").lower()
    score = 0
    for role in target_roles:
        for tok in str(role).lower().split():
            if len(tok) >= 3 and tok in title:
                score += 1
    return score


@router.post("/batch-evaluate", response_model=BatchEvaluateResponse)
async def batch_evaluate(
    data: BatchEvaluateRequest,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Evaluate a batch of listings in parallel (bounded by semaphore).

    Always filtered to `score IS NULL` to avoid silently re-running evaluations.
    The client picks which cohort to target via `mode`.
    """
    import asyncio
    if not profile.llm_api_key_enc:
        raise HTTPException(status_code=400, detail="No LLM API key configured")

    mode = (data.mode or "all").strip().lower()
    if mode not in {"all", "confident", "maybe_only", "keyword_top"}:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode '{mode}'. Must be one of: all, confident, maybe_only, keyword_top",
        )

    q = (
        db.query(Listing)
        .filter(Listing.profile_id == profile.id, Listing.score.is_(None))
        .filter(Listing.status.in_(["new", "evaluated"]))
    )
    if data.ids:
        q = q.filter(Listing.id.in_(data.ids))
    if mode == "confident":
        q = q.filter(Listing.smart_filter_verdict == "yes")
    elif mode == "maybe_only":
        q = q.filter(Listing.smart_filter_verdict == "maybe")

    candidates = q.all()

    # "keyword_top" is a post-query rank — reuse the profile's target_roles
    if mode == "keyword_top":
        pd = profile.profile_data or {}
        target_roles = pd.get("target_roles") or []
        if isinstance(target_roles, str):
            target_roles = [target_roles]
        candidates.sort(
            key=lambda l: (_keyword_rank(l, target_roles), l.created_at),
            reverse=True,
        )
        # Default top 10 for this mode if caller didn't specify a limit
        candidates = candidates[: (data.limit or 10)]
    elif data.limit:
        candidates = candidates[: data.limit]

    requested = len(candidates)
    if requested == 0:
        return BatchEvaluateResponse(requested=0, evaluated=0, failed=0, skipped=0, errors=[])

    sem = asyncio.Semaphore(data.concurrency)
    evaluated = 0
    failed = 0
    skipped = 0
    errors: list[dict] = []

    async def _run_one(listing: Listing):
        nonlocal evaluated, failed, skipped
        async with sem:
            # Check the concurrency guard — skip listings already being evaluated
            lock = listing.evaluation_in_progress
            if lock is not None and (datetime.utcnow() - lock) < timedelta(minutes=3):
                skipped += 1
                return
            try:
                await evaluate_listing(db, profile, listing)
                evaluated += 1
            except Exception as exc:
                failed += 1
                errors.append({"listing_id": listing.id, "error": str(exc)[:200]})

    await asyncio.gather(*[_run_one(l) for l in candidates], return_exceptions=True)

    return BatchEvaluateResponse(
        requested=requested,
        evaluated=evaluated,
        failed=failed,
        skipped=skipped,
        errors=errors[:20],  # cap payload size
    )


class BatchEvalCohortStats(BaseModel):
    unevaluated_total: int
    confident: int  # verdict == "yes"
    maybe: int      # verdict == "maybe"
    no_verdict: int  # filter wasn't run (legacy or off)
    smart_filter_enabled: bool


@router.get("/batch-evaluate/cohort", response_model=BatchEvalCohortStats)
def batch_evaluate_cohort(
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Return counts for each cohort so the UI can render the adaptive banner."""
    base = (
        db.query(Listing)
        .filter(Listing.profile_id == profile.id, Listing.score.is_(None))
        .filter(Listing.status.in_(["new", "evaluated"]))
    )
    total = base.count()
    confident = base.filter(Listing.smart_filter_verdict == "yes").count()
    maybe = base.filter(Listing.smart_filter_verdict == "maybe").count()
    no_verdict = base.filter(Listing.smart_filter_verdict.is_(None)).count()
    return BatchEvalCohortStats(
        unevaluated_total=total,
        confident=confident,
        maybe=maybe,
        no_verdict=no_verdict,
        smart_filter_enabled=bool(getattr(profile, "smart_title_filter_enabled", False)),
    )


VALID_PASS_REASONS = {
    "level_mismatch",
    "comp_too_low",
    "stage_mismatch",
    "domain_mismatch",
    "location",
    "culture_fit",
    "scope_too_narrow",
    "founder_market_fit",
    "timing",
    "other",
}


class PassRequest(BaseModel):
    reason: str
    note: Optional[str] = None


@router.post("/{listing_id}/pass", response_model=ListingDetail)
def pass_on_listing(
    listing_id: int,
    data: PassRequest,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Mark a listing as 'passed' with a reason code. Candidate chose not to pursue."""
    if data.reason not in VALID_PASS_REASONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid reason. Must be one of: {sorted(VALID_PASS_REASONS)}",
        )
    listing = db.get(Listing, listing_id)
    if not listing or listing.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Listing not found")

    listing.status = "passed"
    listing.pass_reason = data.reason
    listing.pass_note = (data.note or "").strip() or None
    listing.passed_at = datetime.utcnow()
    listing.use_for_calibration = True

    db.add(HistoryEvent(
        profile_id=profile.id,
        listing_id=listing.id,
        event_type="passed",
        event_data={"reason": data.reason, "note": listing.pass_note},
    ))
    db.commit()
    db.refresh(listing)
    return _to_detail(listing)


@router.post("/{listing_id}/reconsider", response_model=ListingDetail)
def reconsider_listing(
    listing_id: int,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Undo a pass decision — flip the status back to 'evaluated' while keeping the pass record for history.

    The pass reason + note + passed_at are preserved for audit. use_for_calibration
    is set False so this no-longer-a-pass doesn't feed the LLM calibration.
    """
    listing = db.get(Listing, listing_id)
    if not listing or listing.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Listing not found")
    if listing.status != "passed":
        raise HTTPException(status_code=400, detail="Listing is not in 'passed' state")

    listing.status = "evaluated" if listing.score is not None else "new"
    listing.use_for_calibration = False

    db.add(HistoryEvent(
        profile_id=profile.id,
        listing_id=listing.id,
        event_type="reconsidered",
        event_data={"prev_reason": listing.pass_reason, "prev_note": listing.pass_note},
    ))
    db.commit()
    db.refresh(listing)
    return _to_detail(listing)


class PassSummary(BaseModel):
    listing_id: int
    company: str
    role_title: str
    score: Optional[float]
    grade: Optional[str]
    pass_reason: str
    pass_note: Optional[str]
    passed_at: datetime
    use_for_calibration: bool


class PassListResponse(BaseModel):
    total: int
    threshold: int
    calibration_active: bool
    reason_counts: dict[str, int]
    items: list[PassSummary]


def _calibration_active(profile: Profile, pass_count: int) -> bool:
    pref = getattr(profile, "pass_calibration_preference", "auto") or "auto"
    if pref == "on":
        return pass_count > 0
    if pref == "off":
        return False
    # auto
    return pass_count >= int(getattr(profile, "pass_history_threshold", 15) or 15)


@router.get("/passed/list", response_model=PassListResponse)
def list_passed(
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """List all passed listings for the profile, with counts + calibration status."""
    rows = (
        db.query(Listing)
        .filter(Listing.profile_id == profile.id, Listing.status == "passed")
        .order_by(Listing.passed_at.desc().nullslast())
        .all()
    )
    reason_counts: dict[str, int] = {}
    items: list[PassSummary] = []
    for l in rows:
        rc = l.pass_reason or "other"
        reason_counts[rc] = reason_counts.get(rc, 0) + 1
        items.append(PassSummary(
            listing_id=l.id,
            company=l.company,
            role_title=l.role_title,
            score=l.score,
            grade=l.grade,
            pass_reason=rc,
            pass_note=l.pass_note,
            passed_at=l.passed_at or l.updated_at,
            use_for_calibration=bool(l.use_for_calibration),
        ))
    return PassListResponse(
        total=len(items),
        threshold=int(getattr(profile, "pass_history_threshold", 15) or 15),
        calibration_active=_calibration_active(profile, len(items)),
        reason_counts=reason_counts,
        items=items,
    )


class CalibrationToggleRequest(BaseModel):
    use_for_calibration: bool


@router.put("/{listing_id}/pass/calibration", response_model=ListingDetail)
def toggle_pass_calibration(
    listing_id: int,
    data: CalibrationToggleRequest,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Per-pass toggle — exclude a specific pass from calibration without deleting it."""
    listing = db.get(Listing, listing_id)
    if not listing or listing.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Listing not found")
    if listing.status != "passed":
        raise HTTPException(status_code=400, detail="Listing is not in 'passed' state")
    listing.use_for_calibration = bool(data.use_for_calibration)
    db.commit()
    db.refresh(listing)
    return _to_detail(listing)


@router.delete("/passed/all")
def clear_all_passes(
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Delete all pass records and reset affected listings to their prior 'evaluated' state.

    The underlying Listing rows are NOT deleted — they just stop being 'passed'.
    """
    rows = (
        db.query(Listing)
        .filter(Listing.profile_id == profile.id, Listing.status == "passed")
        .all()
    )
    cleared = 0
    for l in rows:
        l.status = "evaluated" if l.score is not None else "new"
        l.pass_reason = None
        l.pass_note = None
        l.passed_at = None
        l.use_for_calibration = True
        cleared += 1
    db.add(HistoryEvent(
        profile_id=profile.id,
        listing_id=None,
        event_type="passes_cleared",
        event_data={"count": cleared},
    ))
    db.commit()
    return {"cleared": cleared}


# ------------------------- Retroactive title-filter cleanup -------------------------


class FilterSweepResult(BaseModel):
    total_scanned: int
    would_fail: list[dict]
    passed_count: int


@router.get("/filter-sweep/preview", response_model=FilterSweepResult)
def preview_filter_sweep(
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Preview which NON-TERMINAL listings would get auto-passed if the current
    title filter were re-applied retroactively.

    Looks at status in {new, evaluated, rejected_by_candidate? no, we exclude those}.
    Returns the list without touching anything.
    """
    from app.services.filters import why_title_fails
    pos = profile.title_positive_keywords or []
    neg = profile.title_negative_keywords or []
    # Only check still-active listings (not already applied/interview/offer/passed/rejected)
    rows = (
        db.query(Listing)
        .filter(Listing.profile_id == profile.id)
        .filter(Listing.status.in_(["new", "evaluated"]))
        .all()
    )
    would_fail: list[dict] = []
    for l in rows:
        reason = why_title_fails(l.role_title, pos, neg)
        if reason is not None:
            would_fail.append({
                "listing_id": l.id,
                "company": l.company,
                "role_title": l.role_title,
                "status": l.status,
                "reason": reason,
            })
    return FilterSweepResult(
        total_scanned=len(rows),
        would_fail=would_fail,
        passed_count=len(rows) - len(would_fail),
    )


class FilterSweepApplyResult(BaseModel):
    passed_now: int
    details: list[dict]


@router.post("/filter-sweep/apply", response_model=FilterSweepApplyResult)
def apply_filter_sweep(
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Apply the current title filter retroactively.

    Listings whose title now fails the filter are marked status='passed' with
    reason='domain_mismatch' and pass_note='auto-filtered: <reason>'. They can
    be reconsidered individually on the Passed page.
    """
    from app.services.filters import why_title_fails
    pos = profile.title_positive_keywords or []
    neg = profile.title_negative_keywords or []
    rows = (
        db.query(Listing)
        .filter(Listing.profile_id == profile.id)
        .filter(Listing.status.in_(["new", "evaluated"]))
        .all()
    )
    details: list[dict] = []
    now = datetime.utcnow()
    for l in rows:
        reason = why_title_fails(l.role_title, pos, neg)
        if reason is None:
            continue
        l.status = "passed"
        l.pass_reason = "domain_mismatch"
        l.pass_note = f"auto-filtered: {reason}"
        l.passed_at = now
        l.use_for_calibration = False  # don't feed auto-filters into calibration
        details.append({
            "listing_id": l.id,
            "company": l.company,
            "role_title": l.role_title,
            "reason": reason,
        })
        db.add(HistoryEvent(
            profile_id=profile.id,
            listing_id=l.id,
            event_type="auto_filtered",
            event_data={"reason": reason},
        ))
    db.commit()
    return FilterSweepApplyResult(
        passed_now=len(details),
        details=details,
    )


# ------------------------- Resume / cover-letter chat editor -------------------------


class ChatTurnRequest(BaseModel):
    message: str
    scope: str  # "resume" | "cover_letter" | "both"


class ChatTurnResponse(BaseModel):
    reply: str
    proposed_edits: list[dict]
    turn_index: int
    word_swap_hint: bool = False


class ChatHistoryResponse(BaseModel):
    history: list[dict]
    edit_log_size: int
    onboarding_dismissed: bool


class ChatApplyRequest(BaseModel):
    turn_index: int
    edit_id: str


@router.get("/{listing_id}/chat", response_model=ChatHistoryResponse)
def get_chat_history(
    listing_id: int,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    listing = db.get(Listing, listing_id)
    if not listing or listing.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Listing not found")
    return ChatHistoryResponse(
        history=listing.chat_history or [],
        edit_log_size=len(listing.chat_edit_log or []),
        onboarding_dismissed=bool(getattr(profile, "chat_onboarding_dismissed", False)),
    )


@router.post("/{listing_id}/chat/turn", response_model=ChatTurnResponse)
async def chat_turn_endpoint(
    listing_id: int,
    data: ChatTurnRequest,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    from app.services.resume_chat import chat_turn
    listing = db.get(Listing, listing_id)
    if not listing or listing.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Listing not found")
    try:
        result = await chat_turn(db, profile, listing, data.message, data.scope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ChatTurnResponse(**result)


@router.post("/{listing_id}/chat/apply")
async def chat_apply_edit(
    listing_id: int,
    data: ChatApplyRequest,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    from app.services.resume_chat import apply_edit
    listing = db.get(Listing, listing_id)
    if not listing or listing.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Listing not found")
    try:
        return await apply_edit(db, profile, listing, data.turn_index, data.edit_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{listing_id}/chat/reject")
async def chat_reject_edit(
    listing_id: int,
    data: ChatApplyRequest,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    from app.services.resume_chat import reject_edit
    listing = db.get(Listing, listing_id)
    if not listing or listing.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Listing not found")
    try:
        await reject_edit(db, listing, data.turn_index, data.edit_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.post("/{listing_id}/chat/undo")
async def chat_undo(
    listing_id: int,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    from app.services.resume_chat import undo_last_edit
    listing = db.get(Listing, listing_id)
    if not listing or listing.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Listing not found")
    try:
        return await undo_last_edit(db, profile, listing)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/{listing_id}/chat")
def chat_clear(
    listing_id: int,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    from app.services.resume_chat import clear_history
    listing = db.get(Listing, listing_id)
    if not listing or listing.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Listing not found")
    clear_history(db, listing)
    return {"ok": True}
