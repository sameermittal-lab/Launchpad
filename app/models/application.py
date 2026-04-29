"""Application model - audit trail of submitted applications."""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, DateTime, Boolean, Integer, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), nullable=False)

    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    submission_success: Mapped[bool] = mapped_column(Boolean, default=False)

    # What was submitted
    resume_version: Mapped[Optional[str]] = mapped_column(String(500))
    cover_letter_version: Mapped[Optional[str]] = mapped_column(String(500))
    fields_submitted: Mapped[Optional[dict]] = mapped_column(JSON)
    screenshot_path: Mapped[Optional[str]] = mapped_column(String(500))

    # Error info if failed
    error_log: Mapped[Optional[str]] = mapped_column(Text)

    # Relationships
    listing = relationship("Listing", back_populates="applications")
