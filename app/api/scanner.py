"""Portal scanner API - manage tracked companies and trigger scans."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Listing, Profile, TrackedCompany
from app.services.scanner import scan_all_companies, scan_company
from app.services.scanner.parsers import detect_api
from app.utils.session import get_current_profile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scanner", tags=["scanner"])


# ------------------------- Schemas -------------------------


class CompanyResponse(BaseModel):
    id: int
    name: str
    careers_url: str
    api_url: Optional[str] = None
    platform: Optional[str] = None
    notes: Optional[str] = None
    enabled: bool
    last_scanned_at: Optional[datetime] = None
    last_job_count: int = 0
    # AI Company Monitor
    ai_monitor_enabled: bool = False
    last_ai_monitor_at: Optional[datetime] = None
    last_ai_monitor_count: int = 0
    has_query_plan: bool = False


class CompanyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    careers_url: str = Field(..., max_length=1000)
    api_url: Optional[str] = None
    platform: Optional[str] = None
    notes: Optional[str] = None
    enabled: bool = True


class CompanyUpdate(BaseModel):
    name: Optional[str] = None
    careers_url: Optional[str] = None
    api_url: Optional[str] = None
    platform: Optional[str] = None
    notes: Optional[str] = None
    enabled: Optional[bool] = None
    ai_monitor_enabled: Optional[bool] = None


class ScanResponse(BaseModel):
    companies_scanned: int
    total_jobs_found: int
    filtered_out: int
    duplicates: int
    smart_dropped: int = 0
    new_listings: int
    new_listing_ids: list[int]
    errors: list[dict]


class TitleFilterResponse(BaseModel):
    positive: list[str]
    negative: list[str]


class TitleFilterUpdate(BaseModel):
    positive: Optional[list[str]] = None
    negative: Optional[list[str]] = None


# ------------------------- Helpers -------------------------


def _to_company_response(c: TrackedCompany) -> CompanyResponse:
    return CompanyResponse(
        id=c.id,
        name=c.name,
        careers_url=c.careers_url,
        api_url=c.api_url,
        platform=c.platform,
        notes=c.notes,
        enabled=c.enabled,
        last_scanned_at=c.last_scanned_at,
        last_job_count=c.last_job_count,
        ai_monitor_enabled=bool(getattr(c, "ai_monitor_enabled", False)),
        last_ai_monitor_at=getattr(c, "last_ai_monitor_at", None),
        last_ai_monitor_count=int(getattr(c, "last_ai_monitor_count", 0) or 0),
        has_query_plan=bool(getattr(c, "query_plan", None)),
    )


def _load_default_companies() -> list[dict]:
    """Load the preloaded company list from YAML."""
    path = settings.templates_dir / "default_companies.yml"
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data.get("companies", [])
    except Exception as exc:
        logger.warning(f"Could not load default companies: {exc}")
        return []


# ------------------------- Company CRUD -------------------------


@router.get("/companies", response_model=list[CompanyResponse])
def list_companies(
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    companies = (
        db.query(TrackedCompany)
        .filter(TrackedCompany.profile_id == profile.id)
        .order_by(TrackedCompany.name)
        .all()
    )
    return [_to_company_response(c) for c in companies]


@router.post("/companies", response_model=CompanyResponse)
def create_company(
    data: CompanyCreate,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    # Infer API endpoint if not provided
    api = detect_api(data.careers_url, data.api_url)
    platform = data.platform or (api.provider if api else "custom")
    api_url = data.api_url or (api.url if api else None)

    company = TrackedCompany(
        profile_id=profile.id,
        name=data.name,
        careers_url=data.careers_url,
        api_url=api_url,
        platform=platform,
        notes=data.notes,
        enabled=data.enabled,
    )
    db.add(company)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=400, detail="A company with that name already exists")
    db.refresh(company)
    return _to_company_response(company)


@router.put("/companies/{company_id}", response_model=CompanyResponse)
def update_company(
    company_id: int,
    data: CompanyUpdate,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    company = db.get(TrackedCompany, company_id)
    if not company or company.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Company not found")
    update_dict = data.model_dump(exclude_unset=True)
    for k, v in update_dict.items():
        setattr(company, k, v)
    # Re-detect API if careers_url changed and api_url not explicitly set
    if "careers_url" in update_dict and "api_url" not in update_dict:
        api = detect_api(company.careers_url)
        if api:
            company.api_url = api.url
            company.platform = api.provider
    db.commit()
    db.refresh(company)
    return _to_company_response(company)


@router.delete("/companies/{company_id}")
def delete_company(
    company_id: int,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    company = db.get(TrackedCompany, company_id)
    if not company or company.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Company not found")
    db.delete(company)
    db.commit()
    return {"deleted": True}


@router.post("/companies/load-defaults")
def load_default_companies(
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Preload 20+ AI companies. Skips any already tracked by name."""
    defaults = _load_default_companies()
    existing_names = {
        n for (n,) in db.query(TrackedCompany.name)
        .filter(TrackedCompany.profile_id == profile.id)
    }
    added = 0
    for entry in defaults:
        if entry["name"] in existing_names:
            continue
        api = detect_api(entry["careers_url"])
        company = TrackedCompany(
            profile_id=profile.id,
            name=entry["name"],
            careers_url=entry["careers_url"],
            api_url=api.url if api else None,
            platform=entry.get("platform") or (api.provider if api else "custom"),
            enabled=True,
        )
        db.add(company)
        added += 1
    db.commit()
    return {"added": added, "total_defaults": len(defaults)}


class TrackByNameRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    hint_url: Optional[str] = None
    enable_ai_monitor: bool = False


class TrackByNameResponse(BaseModel):
    company: CompanyResponse
    created: bool
    careers_source: str  # "derived_from_url" | "llm_web_search" | "existing"
    ai_monitor_bootstrap_run_id: Optional[int] = None


@router.post("/companies/track-by-name", response_model=TrackByNameResponse)
async def track_company_by_name(
    data: TrackByNameRequest,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Quick-add a company by name.

    Resolves the careers URL via:
      1. Deterministic inference from `hint_url` (job URL, careers URL) — free
      2. LLM web search — incremental cost, only when step 1 fails

    Creates a TrackedCompany row. If `enable_ai_monitor=true`, additionally
    bootstraps the AI Monitor (generate plan + run first scan).

    Idempotent: if a company with the same name already exists (case-insensitive),
    returns it instead of creating a duplicate. If `enable_ai_monitor=true` and
    the company already exists with monitor OFF, flips monitor ON and bootstraps.
    """
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Company name cannot be empty")

    # Already tracked? Return existing row (maybe flip monitor on)
    existing = (
        db.query(TrackedCompany)
        .filter(TrackedCompany.profile_id == profile.id)
        .filter(TrackedCompany.name.ilike(name))
        .first()
    )
    if existing is not None:
        run_id = None
        if data.enable_ai_monitor and not existing.ai_monitor_enabled:
            if not profile.llm_api_key_enc:
                raise HTTPException(
                    status_code=400,
                    detail="Add an LLM API key in Settings first to enable AI Monitor",
                )
            existing.ai_monitor_enabled = True
            db.commit()
            try:
                from app.services.ai_company_monitor import run_ai_monitor_for_company
                from app.services.query_planner import ensure_query_plan
                await ensure_query_plan(db, profile, existing)
                run = await run_ai_monitor_for_company(
                    db, profile, existing, trigger="bootstrap",
                )
                run_id = run.id
            except Exception as exc:
                # Don't fail the whole call if AI bootstrap errors — user can retry from the UI
                logger.warning(f"AI bootstrap on existing company {existing.name} failed: {exc}")
        return TrackByNameResponse(
            company=_to_company_response(existing),
            created=False,
            careers_source="existing",
            ai_monitor_bootstrap_run_id=run_id,
        )

    # Resolve careers URL
    from app.services.careers_url_resolver import resolve_careers_url
    resolved = await resolve_careers_url(db, profile, name, hint_url=data.hint_url)
    if resolved is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Could not locate a careers URL for this company. Add it manually "
                "via the Scanner page."
            ),
        )

    # Detect ATS API if applicable
    api = detect_api(resolved.careers_url)
    platform = resolved.platform
    if api and platform == "custom":
        platform = api.provider  # prefer the more specific ATS detection

    company = TrackedCompany(
        profile_id=profile.id,
        name=name,
        careers_url=resolved.careers_url,
        api_url=api.url if api else None,
        platform=platform,
        notes=resolved.notes,
        enabled=True,
        ai_monitor_enabled=bool(data.enable_ai_monitor),
    )
    db.add(company)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Could not create company row")
    db.refresh(company)

    run_id = None
    if data.enable_ai_monitor:
        if not profile.llm_api_key_enc:
            raise HTTPException(
                status_code=400,
                detail="Add an LLM API key in Settings first to enable AI Monitor",
            )
        try:
            from app.services.ai_company_monitor import run_ai_monitor_for_company
            from app.services.query_planner import ensure_query_plan
            await ensure_query_plan(db, profile, company)
            run = await run_ai_monitor_for_company(db, profile, company, trigger="bootstrap")
            run_id = run.id
        except Exception as exc:
            logger.warning(f"AI bootstrap on newly-tracked company {name} failed: {exc}")
            # Keep the company created; user can retry from UI

    return TrackByNameResponse(
        company=_to_company_response(company),
        created=True,
        careers_source=resolved.source,
        ai_monitor_bootstrap_run_id=run_id,
    )


# ------------------------- Scan triggers -------------------------


@router.post("/scan", response_model=ScanResponse)
async def scan_now(
    auto_evaluate: Optional[bool] = None,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Run a scan across all enabled tracked companies right now.

    If auto_evaluate is not provided, the profile's auto_evaluate setting is used.
    """
    effective = profile.auto_evaluate if auto_evaluate is None else auto_evaluate
    result = await scan_all_companies(db, profile, auto_evaluate=effective)
    return ScanResponse(
        companies_scanned=result.companies_scanned,
        total_jobs_found=result.total_jobs_found,
        filtered_out=result.filtered_out,
        duplicates=result.duplicates,
        smart_dropped=getattr(result, "smart_dropped", 0),
        new_listings=result.new_listings,
        new_listing_ids=result.new_listing_ids,
        errors=result.errors,
    )


@router.post("/companies/{company_id}/scan")
async def scan_single_company(
    company_id: int,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    company = db.get(TrackedCompany, company_id)
    if not company or company.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Company not found")
    jobs, err = await scan_company(db, profile, company)
    if err:
        raise HTTPException(status_code=500, detail=err)
    company.last_scanned_at = datetime.utcnow()
    company.last_job_count = len(jobs)
    db.commit()
    return {"jobs_found": len(jobs), "company": company.name}


# ------------------------- Title Filter -------------------------


@router.get("/title-filter", response_model=TitleFilterResponse)
def get_title_filter(profile: Profile = Depends(get_current_profile)):
    return TitleFilterResponse(
        positive=profile.title_positive_keywords or [],
        negative=profile.title_negative_keywords or [],
    )


@router.put("/title-filter", response_model=TitleFilterResponse)
def update_title_filter(
    data: TitleFilterUpdate,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    if data.positive is not None:
        profile.title_positive_keywords = data.positive
    if data.negative is not None:
        profile.title_negative_keywords = data.negative
    db.commit()
    return TitleFilterResponse(
        positive=profile.title_positive_keywords,
        negative=profile.title_negative_keywords,
    )


# ------------------------- AI Company Monitor -------------------------


class AIMonitorRunSummary(BaseModel):
    id: int
    tracked_company_id: int
    company_name: str
    trigger: str
    total_found: int
    kept_count: int
    filtered_count: int
    deduped_count: int
    created_listing_ids: list[int]
    started_at: datetime
    finished_at: Optional[datetime] = None
    error: Optional[str] = None


class AIMonitorRunDetail(AIMonitorRunSummary):
    queries_used: list[dict]
    kept_listings: list[dict]
    filtered_listings: list[dict]
    deduped_listings: list[dict]


class QueryPlanResponse(BaseModel):
    company_id: int
    company_name: str
    has_plan: bool
    generated_at: Optional[datetime] = None
    strategy: Optional[str] = None
    careers_site: Optional[str] = None
    level_mapping_notes: Optional[str] = None
    queries: list[dict] = []
    estimated_yield: Optional[int] = None


def _plan_to_response(company: TrackedCompany) -> QueryPlanResponse:
    plan = company.query_plan or {}
    return QueryPlanResponse(
        company_id=company.id,
        company_name=company.name,
        has_plan=bool(company.query_plan),
        generated_at=company.query_plan_generated_at,
        strategy=plan.get("strategy"),
        careers_site=plan.get("careers_site"),
        level_mapping_notes=plan.get("level_mapping_notes"),
        queries=plan.get("queries") or [],
        estimated_yield=plan.get("estimated_yield"),
    )


@router.get("/companies/{company_id}/query-plan", response_model=QueryPlanResponse)
def get_company_query_plan(
    company_id: int,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    company = db.get(TrackedCompany, company_id)
    if not company or company.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Company not found")
    return _plan_to_response(company)


@router.post("/companies/{company_id}/query-plan/regenerate", response_model=QueryPlanResponse)
async def regenerate_company_query_plan(
    company_id: int,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    company = db.get(TrackedCompany, company_id)
    if not company or company.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Company not found")
    if not profile.llm_api_key_enc:
        raise HTTPException(status_code=400, detail="Add an LLM API key in Settings first")

    from app.services.query_planner import ensure_query_plan
    try:
        await ensure_query_plan(db, profile, company, force=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not regenerate plan: {exc}")
    db.refresh(company)
    return _plan_to_response(company)


@router.put("/companies/{company_id}/query-plan")
def edit_company_query_plan(
    company_id: int,
    plan: dict,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Let the user hand-edit the query plan (remove a query, tweak one, etc.)."""
    company = db.get(TrackedCompany, company_id)
    if not company or company.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Company not found")
    queries = plan.get("queries") if isinstance(plan, dict) else None
    if not isinstance(queries, list) or not queries:
        raise HTTPException(status_code=400, detail="Plan must include at least one query")
    cleaned = []
    for q in queries:
        if not isinstance(q, dict) or not q.get("q"):
            continue
        q_text = str(q["q"]).strip()
        if "site:" not in q_text.lower():
            raise HTTPException(status_code=400, detail=f"Query missing site: operator: {q_text!r}")
        cleaned.append({"q": q_text, "rationale": str(q.get("rationale") or "")})
    if len(cleaned) > 5:
        raise HTTPException(status_code=400, detail="Max 5 queries per plan")
    new_plan = dict(company.query_plan or {})
    new_plan["queries"] = cleaned
    # Keep the strategy/careers_site/level_mapping_notes fields the user didn't edit
    for k in ("strategy", "careers_site", "level_mapping_notes", "estimated_yield"):
        if k in plan:
            new_plan[k] = plan[k]
    company.query_plan = new_plan
    company.query_plan_generated_at = datetime.utcnow()
    db.commit()
    db.refresh(company)
    return _plan_to_response(company)


@router.post("/companies/{company_id}/ai-scan", response_model=AIMonitorRunDetail)
async def run_ai_scan_for_company(
    company_id: int,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Run AI Company Monitor for a single company right now."""
    company = db.get(TrackedCompany, company_id)
    if not company or company.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Company not found")
    if not profile.llm_api_key_enc:
        raise HTTPException(status_code=400, detail="Add an LLM API key in Settings first")

    from app.services.ai_company_monitor import run_ai_monitor_for_company
    run = await run_ai_monitor_for_company(db, profile, company, trigger="manual")
    return AIMonitorRunDetail(
        id=run.id,
        tracked_company_id=run.tracked_company_id,
        company_name=company.name,
        trigger=run.trigger,
        total_found=run.total_found,
        kept_count=run.kept_count,
        filtered_count=run.filtered_count,
        deduped_count=run.deduped_count,
        created_listing_ids=run.created_listing_ids or [],
        queries_used=run.queries_used or [],
        kept_listings=run.kept_listings or [],
        filtered_listings=run.filtered_listings or [],
        deduped_listings=run.deduped_listings or [],
        started_at=run.started_at,
        finished_at=run.finished_at,
        error=run.error,
    )


@router.post("/companies/{company_id}/ai-bootstrap", response_model=AIMonitorRunDetail)
async def ai_monitor_bootstrap(
    company_id: int,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """First-time bootstrap: generate a plan (if missing) and run a scan synchronously.

    Called immediately after the user flips ai_monitor_enabled from false→true so
    they see results right away instead of waiting for the hourly tick. Same
    response shape as `/ai-scan` so the UI can open the run-detail modal directly.
    """
    company = db.get(TrackedCompany, company_id)
    if not company or company.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Company not found")
    if not profile.llm_api_key_enc:
        raise HTTPException(status_code=400, detail="Add an LLM API key in Settings first")
    if not company.ai_monitor_enabled:
        # Auto-enable if caller forgot; keeps the flow idempotent
        company.ai_monitor_enabled = True
        db.commit()

    from app.services.ai_company_monitor import run_ai_monitor_for_company
    from app.services.query_planner import ensure_query_plan
    # Ensure plan exists (generates if missing) — the scan function does this
    # internally too, but calling it up front makes the intent explicit.
    try:
        await ensure_query_plan(db, profile, company)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not generate plan: {exc}")

    run = await run_ai_monitor_for_company(db, profile, company, trigger="bootstrap")
    return AIMonitorRunDetail(
        id=run.id,
        tracked_company_id=run.tracked_company_id,
        company_name=company.name,
        trigger=run.trigger,
        total_found=run.total_found,
        kept_count=run.kept_count,
        filtered_count=run.filtered_count,
        deduped_count=run.deduped_count,
        created_listing_ids=run.created_listing_ids or [],
        queries_used=run.queries_used or [],
        kept_listings=run.kept_listings or [],
        filtered_listings=run.filtered_listings or [],
        deduped_listings=run.deduped_listings or [],
        started_at=run.started_at,
        finished_at=run.finished_at,
        error=run.error,
    )


@router.post("/ai-scan")
async def run_ai_scan_for_profile(
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Run AI Monitor for every company that has it enabled."""
    if not profile.llm_api_key_enc:
        raise HTTPException(status_code=400, detail="Add an LLM API key in Settings first")
    from app.services.ai_company_monitor import run_ai_monitor_for_profile
    runs = await run_ai_monitor_for_profile(db, profile, trigger="manual")
    return {
        "companies_scanned": len(runs),
        "total_created": sum(r.kept_count for r in runs),
        "run_ids": [r.id for r in runs],
    }


@router.get("/ai-runs", response_model=list[AIMonitorRunSummary])
def list_ai_monitor_runs(
    limit: int = 50,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Recent AI monitor runs across all companies (for the UI activity list)."""
    from app.models import AIMonitorRun
    rows = (
        db.query(AIMonitorRun, TrackedCompany.name)
        .join(TrackedCompany, AIMonitorRun.tracked_company_id == TrackedCompany.id)
        .filter(AIMonitorRun.profile_id == profile.id)
        .order_by(AIMonitorRun.started_at.desc())
        .limit(max(1, min(limit, 200)))
        .all()
    )
    return [
        AIMonitorRunSummary(
            id=r.id,
            tracked_company_id=r.tracked_company_id,
            company_name=name,
            trigger=r.trigger,
            total_found=r.total_found,
            kept_count=r.kept_count,
            filtered_count=r.filtered_count,
            deduped_count=r.deduped_count,
            created_listing_ids=r.created_listing_ids or [],
            started_at=r.started_at,
            finished_at=r.finished_at,
            error=r.error,
        )
        for (r, name) in rows
    ]


@router.get("/ai-runs/{run_id}", response_model=AIMonitorRunDetail)
def get_ai_monitor_run(
    run_id: int,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    from app.models import AIMonitorRun
    row = (
        db.query(AIMonitorRun, TrackedCompany.name)
        .join(TrackedCompany, AIMonitorRun.tracked_company_id == TrackedCompany.id)
        .filter(AIMonitorRun.id == run_id, AIMonitorRun.profile_id == profile.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    r, name = row
    return AIMonitorRunDetail(
        id=r.id,
        tracked_company_id=r.tracked_company_id,
        company_name=name,
        trigger=r.trigger,
        total_found=r.total_found,
        kept_count=r.kept_count,
        filtered_count=r.filtered_count,
        deduped_count=r.deduped_count,
        created_listing_ids=r.created_listing_ids or [],
        queries_used=r.queries_used or [],
        kept_listings=r.kept_listings or [],
        filtered_listings=r.filtered_listings or [],
        deduped_listings=r.deduped_listings or [],
        started_at=r.started_at,
        finished_at=r.finished_at,
        error=r.error,
    )


class PromoteFilteredRequest(BaseModel):
    index: int


@router.post("/ai-runs/{run_id}/promote-filtered")
def promote_filtered_listing(
    run_id: int,
    data: PromoteFilteredRequest,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """User manually promotes a filter-dropped listing into the pipeline.

    Moves the entry from filtered_listings → kept_listings and creates a Listing.
    """
    from app.models import AIMonitorRun
    run = db.get(AIMonitorRun, run_id)
    if not run or run.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Run not found")
    filtered = list(run.filtered_listings or [])
    if not (0 <= data.index < len(filtered)):
        raise HTTPException(status_code=400, detail="Bad index")
    entry = filtered.pop(data.index)

    # Check dedupe again — the user might have added it separately in the meantime
    from app.services.ai_company_monitor import canonical_url_key
    existing_keys = {
        canonical_url_key(u) for (u,) in db.query(Listing.url)
        .filter(Listing.profile_id == profile.id, Listing.url.isnot(None))
        .all()
    }
    key = canonical_url_key(entry.get("url") or "")
    if key in existing_keys:
        raise HTTPException(status_code=409, detail="Already in your pipeline")

    listing = Listing(
        profile_id=profile.id,
        url=entry["url"],
        source="ai_monitor",
        source_detail=(f"{entry.get('company') or 'unknown'} (promoted from filter)"),
        company=entry.get("company") or "unknown",
        role_title=entry.get("role_title") or "(untitled)",
        location=entry.get("location"),
        status="new",
    )
    db.add(listing)
    db.flush()

    # Update the run record
    kept = list(run.kept_listings or [])
    kept.append({
        "company": entry.get("company"),
        "role_title": entry.get("role_title"),
        "url": entry.get("url"),
        "location": entry.get("location"),
        "source_query": entry.get("source_query"),
        "promoted_from_filter_reason": entry.get("reason"),
    })
    run.kept_listings = kept
    run.kept_count = len(kept)
    run.filtered_listings = filtered
    run.filtered_count = len(filtered)
    run.created_listing_ids = (run.created_listing_ids or []) + [listing.id]
    db.commit()
    return {"promoted_listing_id": listing.id}


# ------------------------- Company Suggestions -------------------------


class CompanySuggestionResponse(BaseModel):
    id: int
    name: str
    careers_url: Optional[str] = None
    platform_guess: Optional[str] = None
    why_relevant: Optional[str] = None
    source: str = "adjacent"


class SuggestionsListResponse(BaseModel):
    suggestions: list[CompanySuggestionResponse]
    refreshed_at: Optional[datetime] = None
    cooldown_remaining_seconds: int = 0


def _suggestion_to_response(s) -> CompanySuggestionResponse:
    return CompanySuggestionResponse(
        id=s.id,
        name=s.name,
        careers_url=s.careers_url,
        platform_guess=s.platform_guess,
        why_relevant=s.why_relevant,
        source=s.source,
    )


@router.get("/suggestions", response_model=SuggestionsListResponse)
def get_suggestions(
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    from app.services.company_suggester import cooldown_remaining, list_active_suggestions
    rows = list_active_suggestions(db, profile)
    return SuggestionsListResponse(
        suggestions=[_suggestion_to_response(r) for r in rows],
        refreshed_at=profile.company_suggestions_refreshed_at,
        cooldown_remaining_seconds=cooldown_remaining(profile),
    )


@router.post("/suggestions/refresh", response_model=SuggestionsListResponse)
async def refresh_suggestions_endpoint(
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    from app.services.company_suggester import (
        cooldown_remaining, list_active_suggestions, refresh_suggestions,
    )
    if not profile.llm_api_key_enc:
        raise HTTPException(status_code=400, detail="Add an LLM API key in Settings first")
    remaining = cooldown_remaining(profile)
    if remaining > 0:
        raise HTTPException(
            status_code=429,
            detail=f"Cooldown active — {remaining // 60}m {remaining % 60}s remaining",
        )
    try:
        await refresh_suggestions(db, profile, force=False)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Refresh failed: {exc}")
    rows = list_active_suggestions(db, profile)
    return SuggestionsListResponse(
        suggestions=[_suggestion_to_response(r) for r in rows],
        refreshed_at=profile.company_suggestions_refreshed_at,
        cooldown_remaining_seconds=cooldown_remaining(profile),
    )


@router.post("/suggestions/{suggestion_id}/add", response_model=CompanyResponse)
def add_suggestion(
    suggestion_id: int,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Quick-add: converts a suggestion into a TrackedCompany and removes it."""
    from app.models import CompanySuggestion
    s = db.get(CompanySuggestion, suggestion_id)
    if not s or s.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    if s.added:
        raise HTTPException(status_code=409, detail="Already added")

    # Dedup-check against tracked list one more time before committing
    existing = (
        db.query(TrackedCompany)
        .filter(TrackedCompany.profile_id == profile.id)
        .filter(TrackedCompany.name.ilike(s.name))
        .first()
    )
    if existing:
        # Already tracked, just delete the suggestion and return the existing row
        db.delete(s)
        db.commit()
        return _to_company_response(existing)

    api = detect_api(s.careers_url) if s.careers_url else None
    platform = s.platform_guess or (api.provider if api else "custom")
    api_url = api.url if api else None

    company = TrackedCompany(
        profile_id=profile.id,
        name=s.name,
        careers_url=s.careers_url or "",
        api_url=api_url,
        platform=platform,
        notes=s.why_relevant,
        enabled=True,
    )
    db.add(company)
    # Remove the suggestion so it doesn't come back
    db.delete(s)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Could not add company")
    db.refresh(company)
    return _to_company_response(company)


@router.post("/suggestions/{suggestion_id}/dismiss")
def dismiss_suggestion(
    suggestion_id: int,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Mark a suggestion dismissed so the LLM avoids re-suggesting it."""
    from app.models import CompanySuggestion
    s = db.get(CompanySuggestion, suggestion_id)
    if not s or s.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    if s.dismissed:
        return {"already_dismissed": True}
    s.dismissed = True
    s.dismissed_at = datetime.utcnow()
    db.commit()
    return {"dismissed": True}
