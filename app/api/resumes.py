"""Resume management API - upload PDF, edit cv.md, list generated PDFs."""

from __future__ import annotations

import logging
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Profile
from app.services.resume_importer import pdf_to_markdown, save_cv
from app.utils.session import get_current_profile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/resumes", tags=["resumes"])


class CVResponse(BaseModel):
    markdown: str
    has_cv: bool
    updated_at: Optional[datetime] = None


class CVUpdate(BaseModel):
    markdown: str


class PDFConvertResponse(BaseModel):
    markdown: str
    original_text_length: int


class AnalysisRequest(BaseModel):
    """Optional - if markdown not given, uses saved cv.md."""
    markdown: Optional[str] = None


class AnalysisResponse(BaseModel):
    career_stage: Optional[str] = None
    years_experience: Optional[int] = None
    primary_domain: Optional[str] = None
    archetypes: list[str] = []
    target_roles: list[str] = []
    title_positive_keywords: list[str] = []
    title_negative_keywords: list[str] = []
    target_salary_range: Optional[str] = None
    location_preferences: Optional[str] = None
    preferred_companies: Optional[str] = None
    scoring_weights: dict = {}
    reasoning: Optional[str] = None


class ApplySettingsRequest(BaseModel):
    """Apply selected recommendations to the profile.

    Frontend sends only fields the user approved. Null/missing = skip.
    """
    target_roles: Optional[list[str]] = None
    title_positive_keywords: Optional[list[str]] = None
    title_negative_keywords: Optional[list[str]] = None
    target_salary_range: Optional[str] = None
    location: Optional[str] = None
    scoring_weights: Optional[dict] = None


@router.get("/cv", response_model=CVResponse)
def get_cv(profile: Profile = Depends(get_current_profile)):
    profile_dir = settings.resolved_data_dir / str(profile.id)
    cv_path = profile_dir / "cv.md"
    if not cv_path.exists():
        return CVResponse(markdown="", has_cv=False)
    return CVResponse(
        markdown=cv_path.read_text(encoding="utf-8"),
        has_cv=True,
        updated_at=datetime.fromtimestamp(cv_path.stat().st_mtime),
    )


@router.put("/cv", response_model=CVResponse)
def update_cv(
    data: CVUpdate,
    profile: Profile = Depends(get_current_profile),
):
    profile_dir = settings.resolved_data_dir / str(profile.id)
    save_cv(profile_dir, data.markdown)
    cv_path = profile_dir / "cv.md"
    return CVResponse(
        markdown=data.markdown,
        has_cv=True,
        updated_at=datetime.fromtimestamp(cv_path.stat().st_mtime),
    )


@router.post("/upload-pdf", response_model=PDFConvertResponse)
async def upload_pdf(
    file: UploadFile = File(...),
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    if not profile.llm_api_key_enc:
        raise HTTPException(
            status_code=400,
            detail="No LLM API key configured. Add one in Settings first.",
        )
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    try:
        profile_dir = settings.resolved_data_dir / str(profile.id)
        profile_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(tmp_path, profile_dir / "cv-original.pdf")
        markdown = await pdf_to_markdown(db, profile, tmp_path)
        return PDFConvertResponse(markdown=markdown, original_text_length=len(markdown))
    finally:
        try:
            tmp_path.unlink()
        except Exception:
            pass


@router.get("/base-pdf")
async def get_base_pdf(
    profile: Profile = Depends(get_current_profile),
):
    """Return the user's base resume PDF.

    If they uploaded a PDF originally, return that unchanged (their preferred format).
    Otherwise fall back to a minimal rendering of cv.md.
    """
    profile_dir = settings.resolved_data_dir / str(profile.id)

    original = profile_dir / "cv-original.pdf"
    if original.exists():
        filename = f"cv-{profile.name.replace(' ', '-').lower()}-base.pdf"
        return FileResponse(
            original,
            media_type="application/pdf",
            filename=filename,
        )

    cv_path = profile_dir / "cv.md"
    if not cv_path.exists():
        raise HTTPException(status_code=404, detail="No base resume found")

    raise HTTPException(
        status_code=404,
        detail="No original PDF found. Re-upload your resume as PDF to enable this option.",
    )


@router.get("/generated")
def list_generated(profile: Profile = Depends(get_current_profile)):
    gen_dir = settings.resolved_data_dir / str(profile.id) / "generated_resumes"
    if not gen_dir.exists():
        return []
    return [{
        "filename": p.name,
        "size_kb": round(p.stat().st_size / 1024),
        "created_at": datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
    } for p in sorted(gen_dir.glob("*.pdf"), key=lambda x: x.stat().st_mtime, reverse=True)]


@router.get("/generated/{filename}")
def download_generated(
    filename: str,
    inline: bool = False,
    profile: Profile = Depends(get_current_profile),
):
    gen_dir = settings.resolved_data_dir / str(profile.id) / "generated_resumes"
    path = gen_dir / filename
    if not path.resolve().is_relative_to(gen_dir.resolve()):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    # inline=true for <embed>/<iframe> preview; inline=false (default) for download
    if inline:
        return FileResponse(path, media_type="application/pdf")
    return FileResponse(path, media_type="application/pdf", filename=filename)


@router.get("/cover-letters")
def list_cover_letters(profile: Profile = Depends(get_current_profile)):
    gen_dir = settings.resolved_data_dir / str(profile.id) / "cover_letters"
    if not gen_dir.exists():
        return []
    return [{
        "filename": p.name,
        "size_kb": round(p.stat().st_size / 1024),
        "created_at": datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
    } for p in sorted(gen_dir.glob("*.pdf"), key=lambda x: x.stat().st_mtime, reverse=True)]


@router.get("/cover-letters/{filename}")
def download_cover_letter(
    filename: str,
    inline: bool = False,
    profile: Profile = Depends(get_current_profile),
):
    gen_dir = settings.resolved_data_dir / str(profile.id) / "cover_letters"
    path = gen_dir / filename
    if not path.resolve().is_relative_to(gen_dir.resolve()):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if inline:
        return FileResponse(path, media_type="application/pdf")
    return FileResponse(path, media_type="application/pdf", filename=filename)


@router.post("/analyze", response_model=AnalysisResponse)
async def analyze_resume(
    data: AnalysisRequest,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Analyze the user's resume and suggest personalized settings.

    Uses the cv.md on disk unless markdown is provided in the request
    (useful right after PDF conversion, before the user has saved).
    """
    if not profile.llm_api_key_enc:
        raise HTTPException(
            status_code=400,
            detail="No LLM API key configured. Add one in Settings first.",
        )

    cv_text = data.markdown
    if not cv_text:
        profile_dir = settings.resolved_data_dir / str(profile.id)
        cv_path = profile_dir / "cv.md"
        if not cv_path.exists():
            raise HTTPException(
                status_code=404,
                detail="No resume to analyze. Upload your PDF or save cv.md first.",
            )
        cv_text = cv_path.read_text(encoding="utf-8")

    from app.services.profile_analyzer import analyze_resume_for_settings
    try:
        result = await analyze_resume_for_settings(db, profile, cv_text)
    except Exception as exc:
        logger.exception("Resume analysis failed")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}")

    return AnalysisResponse(**result)


@router.post("/apply-suggestions")
def apply_suggestions(
    data: ApplySettingsRequest,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Apply user-approved recommendations from analyze_resume to the profile."""
    update = data.model_dump(exclude_unset=True)
    applied = []

    if "target_roles" in update and update["target_roles"] is not None:
        pd = dict(profile.profile_data or {})
        pd["target_roles"] = update["target_roles"]
        profile.profile_data = pd
        applied.append("target_roles")

    if "title_positive_keywords" in update and update["title_positive_keywords"] is not None:
        profile.title_positive_keywords = update["title_positive_keywords"]
        applied.append("title_positive_keywords")

    if "title_negative_keywords" in update and update["title_negative_keywords"] is not None:
        profile.title_negative_keywords = update["title_negative_keywords"]
        applied.append("title_negative_keywords")

    if "target_salary_range" in update and update["target_salary_range"] is not None:
        pd = dict(profile.profile_data or {})
        pd["target_salary"] = update["target_salary_range"]
        profile.profile_data = pd
        applied.append("target_salary")

    if "location" in update and update["location"]:
        pd = dict(profile.profile_data or {})
        pd["location"] = update["location"]
        profile.profile_data = pd
        applied.append("location")

    if "scoring_weights" in update and update["scoring_weights"] is not None:
        # Validate weights roughly sum to 1
        total = sum(float(v) for v in update["scoring_weights"].values())
        if 0.8 <= total <= 1.2:
            profile.scoring_weights = update["scoring_weights"]
            applied.append("scoring_weights")

    db.commit()
    db.refresh(profile)
    return {"applied": applied}
