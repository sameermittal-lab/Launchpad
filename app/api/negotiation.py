"""Negotiation helper API."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Listing, Profile
from app.services.negotiation import generate_counter_offer
from app.utils.session import get_current_profile

router = APIRouter(prefix="/api/negotiation", tags=["negotiation"])


class OfferDetails(BaseModel):
    base_salary: str
    equity: Optional[str] = None
    other: Optional[str] = None
    deadline: Optional[str] = None
    competing_offers: Optional[str] = None
    notes: Optional[str] = None


class CounterOfferRequest(BaseModel):
    listing_id: int
    offer: OfferDetails


@router.post("/counter")
async def counter_offer(
    data: CounterOfferRequest,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    if not profile.llm_api_key_enc:
        raise HTTPException(status_code=400, detail="No LLM API key configured")
    listing = db.get(Listing, data.listing_id)
    if not listing or listing.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Listing not found")
    try:
        result = await generate_counter_offer(
            db, profile, listing, data.offer.model_dump()
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return result
