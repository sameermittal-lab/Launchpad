"""HistoryEvent model - timeline of all actions."""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, DateTime, Integer, JSON, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class HistoryEvent(Base):
    __tablename__ = "history_events"
    __table_args__ = (
        Index("ix_history_profile_created", "profile_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), nullable=False)
    listing_id: Mapped[Optional[int]] = mapped_column(ForeignKey("listings.id"))

    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # evaluation | status_change | submission | email_received | resume_generated | ...
    event_data: Mapped[Optional[dict]] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
