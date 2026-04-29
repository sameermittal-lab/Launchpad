"""GmailAccount model - connected Gmail accounts per profile."""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, DateTime, Boolean, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class GmailAccount(Base):
    __tablename__ = "gmail_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), nullable=False)

    email: Mapped[str] = mapped_column(String(200), nullable=False)
    oauth_token_enc: Mapped[str] = mapped_column(Text, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Extraction stats (updated whenever we run listing extraction on this account)
    last_extraction_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_extraction_count: Mapped[int] = mapped_column(Integer, default=0)
    lifetime_listings_extracted: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    profile = relationship("Profile", back_populates="gmail_accounts")
    messages = relationship("EmailMessage", back_populates="gmail_account", cascade="all, delete-orphan")
