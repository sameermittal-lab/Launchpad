"""EmailMessage model - classified emails from connected Gmail accounts."""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, DateTime, Boolean, Integer, JSON, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class EmailMessage(Base):
    __tablename__ = "email_messages"
    __table_args__ = (
        Index("ix_email_profile_received", "profile_id", "received_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), nullable=False)
    gmail_account_id: Mapped[int] = mapped_column(ForeignKey("gmail_accounts.id"), nullable=False)

    gmail_message_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    from_email: Mapped[Optional[str]] = mapped_column(String(200))
    from_name: Mapped[Optional[str]] = mapped_column(String(200))
    subject: Mapped[Optional[str]] = mapped_column(String(500))
    snippet: Mapped[Optional[str]] = mapped_column(Text)
    body_text: Mapped[Optional[str]] = mapped_column(Text)

    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)

    # linkedin_alert | recruiter | app_update | rejection | other
    category: Mapped[str] = mapped_column(String(50), default="other")
    extracted_listings: Mapped[Optional[list]] = mapped_column(JSON)
    # Listings the LLM proposed but our filters rejected, with per-item reasons.
    # Shape: [{"company", "role_title", "url", "reason"}, ...]
    filtered_listings: Mapped[Optional[list]] = mapped_column(JSON)
    # Post-processing metadata: {"llm_claimed": int, "kept": int, "filtered_by_policy": int, "no_url": int, "dupes": int}
    extraction_meta: Mapped[Optional[dict]] = mapped_column(JSON)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)

    # 1-sentence LLM summary of what this email is about (generated during
    # extraction for linkedin_alert / recruiter emails; null otherwise).
    ai_summary: Mapped[Optional[str]] = mapped_column(Text)

    # Relationships
    gmail_account = relationship("GmailAccount", back_populates="messages")
