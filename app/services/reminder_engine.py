"""Generate follow-up reminders based on listing status and dates."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import Listing, Profile, Reminder

logger = logging.getLogger(__name__)


def generate_reminders(db: Session, profile: Profile) -> int:
    """Generate reminders for listings that need follow-up.

    Rules:
    - Listings in "applied" status for 7+ days with no update -> followup_7d
    - Listings in "interview" status -> keep an interview reminder
    - Listings in "offer" status -> offer_deadline reminder (3 days default)

    Returns count of reminders created (skips ones that already exist).
    """
    now = datetime.utcnow()
    created = 0

    # Clear out dismissed reminders older than 30 days
    db.query(Reminder).filter(
        Reminder.profile_id == profile.id,
        Reminder.dismissed.is_(True),
        Reminder.created_at < now - timedelta(days=30),
    ).delete(synchronize_session=False)

    # 7-day follow-up on "applied" listings
    cutoff = now - timedelta(days=7)
    applied_listings = (
        db.query(Listing)
        .filter(
            Listing.profile_id == profile.id,
            Listing.status == "applied",
            Listing.updated_at <= cutoff,
        )
        .all()
    )
    for listing in applied_listings:
        existing = db.query(Reminder).filter(
            Reminder.profile_id == profile.id,
            Reminder.listing_id == listing.id,
            Reminder.reminder_type == "followup_7d",
            Reminder.dismissed.is_(False),
        ).first()
        if existing:
            continue
        days_ago = (now - listing.updated_at).days
        db.add(Reminder(
            profile_id=profile.id,
            listing_id=listing.id,
            reminder_type="followup_7d",
            title=f"Follow up with {listing.company}?",
            description=f"Applied {days_ago} days ago, no response. Consider sending a polite check-in.",
            trigger_at=now,
            dismissed=False,
        ))
        created += 1

    # Interview reminders (just track that interviews exist)
    interview_listings = (
        db.query(Listing)
        .filter(
            Listing.profile_id == profile.id,
            Listing.status == "interview",
        )
        .all()
    )
    for listing in interview_listings:
        existing = db.query(Reminder).filter(
            Reminder.profile_id == profile.id,
            Reminder.listing_id == listing.id,
            Reminder.reminder_type == "interview",
            Reminder.dismissed.is_(False),
        ).first()
        if existing:
            continue
        db.add(Reminder(
            profile_id=profile.id,
            listing_id=listing.id,
            reminder_type="interview",
            title=f"{listing.company} interview prep",
            description=f"Review your STAR stories and company research before the interview.",
            trigger_at=now,
            dismissed=False,
        ))
        created += 1

    # Offer deadlines
    offer_listings = (
        db.query(Listing)
        .filter(
            Listing.profile_id == profile.id,
            Listing.status == "offer",
        )
        .all()
    )
    for listing in offer_listings:
        existing = db.query(Reminder).filter(
            Reminder.profile_id == profile.id,
            Reminder.listing_id == listing.id,
            Reminder.reminder_type == "offer_deadline",
            Reminder.dismissed.is_(False),
        ).first()
        if existing:
            continue
        db.add(Reminder(
            profile_id=profile.id,
            listing_id=listing.id,
            reminder_type="offer_deadline",
            title=f"Respond to {listing.company} offer",
            description=f"Review the counter-offer helper to prepare your response.",
            trigger_at=now,
            dismissed=False,
        ))
        created += 1

    db.commit()
    logger.info(f"Generated {created} reminders for profile {profile.id}")
    return created
