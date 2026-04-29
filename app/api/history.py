"""History / audit log API."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import HistoryEvent, Listing, Profile
from app.utils.session import get_current_profile

router = APIRouter(prefix="/api/history", tags=["history"])


class HistoryItem(BaseModel):
    id: int
    event_type: str
    event_data: Optional[dict]
    created_at: datetime
    listing_id: Optional[int] = None
    listing_company: Optional[str] = None
    listing_role: Optional[str] = None


@router.get("", response_model=list[HistoryItem])
def list_history(
    limit: int = 100,
    event_type: Optional[str] = None,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    q = db.query(HistoryEvent, Listing).outerjoin(
        Listing, HistoryEvent.listing_id == Listing.id
    ).filter(HistoryEvent.profile_id == profile.id)
    if event_type:
        q = q.filter(HistoryEvent.event_type == event_type)
    q = q.order_by(HistoryEvent.created_at.desc()).limit(limit)

    out: list[HistoryItem] = []
    for ev, listing in q.all():
        out.append(HistoryItem(
            id=ev.id,
            event_type=ev.event_type,
            event_data=ev.event_data,
            created_at=ev.created_at,
            listing_id=ev.listing_id,
            listing_company=listing.company if listing else None,
            listing_role=listing.role_title if listing else None,
        ))
    return out
