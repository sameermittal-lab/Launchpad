"""Listing model - a job opportunity in the pipeline."""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    String, Text, DateTime, Float, Integer, JSON, ForeignKey, Index, Boolean,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (
        Index("ix_listings_profile_status", "profile_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), nullable=False)

    # Source
    url: Mapped[Optional[str]] = mapped_column(String(1000))
    source: Mapped[str] = mapped_column(String(50), default="manual")  # manual | scanner | gmail
    source_detail: Mapped[Optional[str]] = mapped_column(String(200))

    # Basics
    company: Mapped[str] = mapped_column(String(200), nullable=False)
    role_title: Mapped[str] = mapped_column(String(300), nullable=False)
    location: Mapped[Optional[str]] = mapped_column(String(200))
    job_type: Mapped[Optional[str]] = mapped_column(String(20))  # Remote | Hybrid | Onsite
    salary_range: Mapped[Optional[str]] = mapped_column(String(100))
    archetype: Mapped[Optional[str]] = mapped_column(String(100))
    jd_text: Mapped[Optional[str]] = mapped_column(Text)

    # Pipeline status
    status: Mapped[str] = mapped_column(
        String(20), default="new", index=True
    )  # new | evaluated | applied | interview | offer | rejected | passed

    # Evaluation results
    score: Mapped[Optional[float]] = mapped_column(Float)
    sub_scores: Mapped[Optional[dict]] = mapped_column(JSON)
    grade: Mapped[Optional[str]] = mapped_column(String(5))  # A+, A, B+, etc.
    ai_summary: Mapped[Optional[str]] = mapped_column(Text)
    # v2 evaluation fields
    evaluation_version: Mapped[Optional[int]] = mapped_column(Integer, default=1)
    dimension_rationales: Mapped[Optional[dict]] = mapped_column(JSON)  # {dim: "one-sentence explanation"}
    take_it_if: Mapped[Optional[list]] = mapped_column(JSON)  # ["reason 1", "reason 2"]
    compromises: Mapped[Optional[list]] = mapped_column(JSON)  # ["trade 1", "trade 2"]
    blockers: Mapped[Optional[list]] = mapped_column(JSON)  # hard deal-breakers
    citations: Mapped[Optional[list]] = mapped_column(JSON)  # web search citations used
    # Concurrency guard — set at start of evaluate_listing, cleared at end.
    # Stale locks (> 3 min) are ignored so a crashed eval doesn't jam the listing.
    evaluation_in_progress: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Smart title filter verdict (if the feature was on when this listing was added).
    # Values: "yes" | "no" | "maybe" | null (filter didn't run).
    smart_filter_verdict: Mapped[Optional[str]] = mapped_column(String(10))
    smart_filter_reason: Mapped[Optional[str]] = mapped_column(String(200))

    # Per-listing chat for resume/cover-letter editing. Shared thread across
    # both documents (Option C design) — each turn is scoped to resume,
    # cover_letter, or both, and the LLM sees the full history + current docs.
    # Shape:
    #   [{role: "user"|"assistant"|"system", scope: "resume"|"cover_letter"|"both",
    #     content: str, proposed_edits?: [...], timestamp: iso8601}, ...]
    chat_history: Mapped[Optional[list]] = mapped_column(JSON)
    # Undo stack for applied chat edits — oldest first.
    # Shape: [{target: "resume"|"cover_letter", before_md: str, after_md: str,
    #          applied_at: iso8601, note: str}, ...]
    chat_edit_log: Mapped[Optional[list]] = mapped_column(JSON)

    # Generated assets
    tailored_resume_path: Mapped[Optional[str]] = mapped_column(String(500))
    cover_letter_path: Mapped[Optional[str]] = mapped_column(String(500))
    keyword_coverage: Mapped[Optional[float]] = mapped_column(Float)

    # Editable source markdown for tailored assets
    tailored_resume_md: Mapped[Optional[str]] = mapped_column(Text)
    tailored_resume_md_original: Mapped[Optional[str]] = mapped_column(Text)
    tailored_cover_letter_md: Mapped[Optional[str]] = mapped_column(Text)
    tailored_cover_letter_md_original: Mapped[Optional[str]] = mapped_column(Text)

    # User-tuned settings per listing
    tailoring_intensity: Mapped[Optional[str]] = mapped_column(String(20))  # light | medium | heavy
    cover_letter_tone_override: Mapped[Optional[str]] = mapped_column(String(50))

    # Rejection tracking
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text)

    # Candidate pass tracking — candidate decided not to pursue (distinct from rejection)
    pass_reason: Mapped[Optional[str]] = mapped_column(String(50))  # level_mismatch | comp_too_low | ...
    pass_note: Mapped[Optional[str]] = mapped_column(Text)
    passed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    use_for_calibration: Mapped[bool] = mapped_column(Boolean, default=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    profile = relationship("Profile", back_populates="listings")
    applications = relationship("Application", back_populates="listing", cascade="all, delete-orphan")
    reminders = relationship("Reminder", back_populates="listing", cascade="all, delete-orphan")
