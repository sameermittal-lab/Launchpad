"""Profile management API."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Profile
from app.utils.auth import hash_pin

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


class ProfileSummary(BaseModel):
    """Lightweight profile info for the login screen (no secrets)."""
    id: int
    name: str
    role_title: Optional[str] = None
    has_pin: bool = False
    listing_count: int = 0


class ProfileCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    role_title: Optional[str] = Field(None, max_length=200)
    pin: Optional[str] = Field(None, min_length=4, max_length=20)
    email: Optional[str] = None
    phone: Optional[str] = None
    linkedin: Optional[str] = None
    location: Optional[str] = None
    target_roles: Optional[list[str]] = None
    target_salary: Optional[str] = None


class ProfileDetail(BaseModel):
    id: int
    name: str
    role_title: Optional[str]
    has_pin: bool
    llm_provider: str
    llm_model: str
    profile_data: dict
    scoring_weights: dict
    min_submit_score: float
    cover_letter_tone: str
    paper_size: str
    scan_interval_hours: int
    auto_evaluate: bool
    auto_generate_assets: bool


@router.get("", response_model=list[ProfileSummary])
def list_profiles(db: Session = Depends(get_db)):
    """List all profiles for the login screen."""
    profiles = db.query(Profile).order_by(Profile.id).all()
    return [
        ProfileSummary(
            id=p.id,
            name=p.name,
            role_title=p.role_title,
            has_pin=bool(p.pin_hash),
            listing_count=len(p.listings),
        )
        for p in profiles
    ]


@router.post("", response_model=ProfileSummary)
def create_profile(data: ProfileCreate, db: Session = Depends(get_db)):
    """Create a new profile. Enforces max_profiles limit."""
    count = db.query(Profile).count()
    if count >= settings.max_profiles:
        raise HTTPException(
            status_code=400,
            detail=f"Max {settings.max_profiles} profiles allowed on this server.",
        )

    profile_data = {
        "email": data.email,
        "phone": data.phone,
        "linkedin": data.linkedin,
        "location": data.location,
        "target_roles": data.target_roles or [],
        "target_salary": data.target_salary,
    }

    profile = Profile(
        name=data.name,
        role_title=data.role_title,
        pin_hash=hash_pin(data.pin) if data.pin else None,
        profile_data=profile_data,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)

    return ProfileSummary(
        id=profile.id,
        name=profile.name,
        role_title=profile.role_title,
        has_pin=bool(profile.pin_hash),
        listing_count=0,
    )


@router.get("/{profile_id}", response_model=ProfileDetail)
def get_profile(profile_id: int, db: Session = Depends(get_db)):
    profile = db.get(Profile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    return ProfileDetail(
        id=profile.id,
        name=profile.name,
        role_title=profile.role_title,
        has_pin=bool(profile.pin_hash),
        llm_provider=profile.llm_provider,
        llm_model=profile.llm_model,
        profile_data=profile.profile_data or {},
        scoring_weights=profile.scoring_weights or {},
        min_submit_score=profile.min_submit_score,
        cover_letter_tone=profile.cover_letter_tone,
        paper_size=profile.paper_size,
        scan_interval_hours=profile.scan_interval_hours,
        auto_evaluate=profile.auto_evaluate,
        auto_generate_assets=profile.auto_generate_assets,
    )


@router.delete("/{profile_id}")
def delete_profile(profile_id: int, db: Session = Depends(get_db)):
    profile = db.get(Profile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    db.delete(profile)
    db.commit()
    return {"deleted": True}
