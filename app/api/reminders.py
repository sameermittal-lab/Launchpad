"""Reminders API."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Listing, Profile, Reminder
from app.services.reminder_engine import generate_reminders
from app.utils.session import get_current_profile

router = APIRouter(prefix="/api/reminders", tags=["reminders"])


class ReminderResponse(BaseModel):
    id: int
    reminder_type: str
    title: str
    description: Optional[str] = None
    trigger_at: datetime
    dismissed: bool
    listing_id: int
    listing_company: Optional[str] = None
    listing_role: Optional[str] = None


@router.get("", response_model=list[ReminderResponse])
def list_reminders(
    include_dismissed: bool = False,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    q = db.query(Reminder, Listing).join(
        Listing, Reminder.listing_id == Listing.id
    ).filter(Reminder.profile_id == profile.id)
    if not include_dismissed:
        q = q.filter(Reminder.dismissed.is_(False))
    q = q.order_by(Reminder.trigger_at.desc())
    out = []
    for rem, listing in q.all():
        out.append(ReminderResponse(
            id=rem.id,
            reminder_type=rem.reminder_type,
            title=rem.title,
            description=rem.description,
            trigger_at=rem.trigger_at,
            dismissed=rem.dismissed,
            listing_id=rem.listing_id,
            listing_company=listing.company if listing else None,
            listing_role=listing.role_title if listing else None,
        ))
    return out


@router.post("/regenerate")
def regenerate(
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    count = generate_reminders(db, profile)
    return {"generated": count}


@router.post("/{reminder_id}/dismiss")
def dismiss(
    reminder_id: int,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    rem = db.get(Reminder, reminder_id)
    if not rem or rem.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Reminder not found")
    rem.dismissed = True
    db.commit()
    return {"dismissed": True}
