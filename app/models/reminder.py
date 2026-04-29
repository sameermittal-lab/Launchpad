"""Reminder model - follow-ups, interviews, offer deadlines."""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, DateTime, Boolean, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), nullable=False)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), nullable=False)

    reminder_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # followup_7d | interview | offer_deadline
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)

    trigger_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    profile = relationship("Profile", back_populates="reminders")
    listing = relationship("Listing", back_populates="reminders")
