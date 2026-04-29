"""CompanySuggestion model — LLM-generated companies to consider tracking.

Regenerated daily plus manual refresh (4-hour cooldown). Dismissed rows stay
in the DB so we can feed their names into the next generation prompt as
negative signal.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CompanySuggestion(Base):
    __tablename__ = "company_suggestions"
    __table_args__ = (
        UniqueConstraint("profile_id", "name", name="uq_profile_suggestion"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), nullable=False, index=True)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    careers_url: Mapped[Optional[str]] = mapped_column(String(1000))
    platform_guess: Mapped[Optional[str]] = mapped_column(String(50))  # greenhouse/ashby/lever/custom
    why_relevant: Mapped[Optional[str]] = mapped_column(Text)

    # "adjacent" = similar to what the user already tracks
    # "discovery" = emerging/less-known companies the user might not know
    source: Mapped[str] = mapped_column(String(20), default="adjacent")

    # Has the user added this to tracked_companies? Used for the one-shot UI
    # confirmation animation; once True we delete the row.
    added: Mapped[bool] = mapped_column(Boolean, default=False)
    # Has the user dismissed it? Kept so we can feed it into the next LLM call
    # as "don't suggest these again".
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    dismissed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
