"""Interview prep API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Profile
from app.services.interview_prep import generate_stories, load_cached_stories
from app.utils.session import get_current_profile

router = APIRouter(prefix="/api/interview-prep", tags=["interview-prep"])


@router.get("/stories")
def get_stories(profile: Profile = Depends(get_current_profile)):
    stories = load_cached_stories(profile)
    return {"stories": stories or [], "has_cache": stories is not None}


@router.post("/stories/generate")
async def generate(
    force_refresh: bool = False,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    if not profile.llm_api_key_enc:
        raise HTTPException(status_code=400, detail="No LLM API key configured")
    try:
        stories = await generate_stories(db, profile, force_refresh=force_refresh)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"stories": stories}
