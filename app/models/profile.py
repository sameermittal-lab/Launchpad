"""Profile model - represents a user of the LaunchPad instance."""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, DateTime, Float, Integer, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    role_title: Mapped[Optional[str]] = mapped_column(String(200))
    pin_hash: Mapped[Optional[str]] = mapped_column(String(200))

    # LLM settings (api key encrypted)
    llm_provider: Mapped[str] = mapped_column(String(50), default="anthropic")
    llm_api_key_enc: Mapped[Optional[str]] = mapped_column(Text)
    llm_model: Mapped[str] = mapped_column(String(100), default="claude-sonnet-4-20250514")

    # Gmail OAuth client credentials (from user's Google Cloud project, encrypted)
    gmail_client_credentials_enc: Mapped[Optional[str]] = mapped_column(Text)

    # Profile data (name, email, phone, linkedin, target_roles, salary, location, etc.)
    profile_data: Mapped[dict] = mapped_column(JSON, default=dict)

    # Scoring and submission behavior
    scoring_weights: Mapped[dict] = mapped_column(
        JSON,
        default=lambda: {
            "role_match": 0.125,
            "seniority_match": 0.125,
            "skills": 0.125,
            "comp": 0.125,
            "growth": 0.125,
            "s_curve": 0.125,
            "culture": 0.125,
            "location": 0.125,
        },
    )
    min_submit_score: Mapped[float] = mapped_column(Float, default=4.0)
    require_approval: Mapped[bool] = mapped_column(default=True)
    web_grounded_eval: Mapped[bool] = mapped_column(default=True)

    # Pass-history calibration
    pass_history_threshold: Mapped[int] = mapped_column(Integer, default=15)
    pass_calibration_preference: Mapped[str] = mapped_column(String(10), default="auto")  # auto | on | off

    # Editor chat onboarding flags (per-profile).
    # "chat_onboarding_dismissed" is set once the user clicks "Got it" on the
    # first-open tip. They can re-enable tips in Settings.
    chat_onboarding_dismissed: Mapped[bool] = mapped_column(default=False)

    # Resume / cover letter defaults
    cover_letter_tone: Mapped[str] = mapped_column(String(50), default="warm")
    resume_format: Mapped[str] = mapped_column(String(10), default="pdf")
    paper_size: Mapped[str] = mapped_column(String(10), default="letter")

    # Scan settings
    scan_interval_hours: Mapped[int] = mapped_column(Integer, default=6)
    # AI Company Monitor runs at its own cadence (default 24h — LLM-paid, daily is sane)
    ai_monitor_interval_hours: Mapped[int] = mapped_column(Integer, default=24)
    # Last time company-suggestions were regenerated (for 4h cooldown on manual refresh)
    company_suggestions_refreshed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    auto_evaluate: Mapped[bool] = mapped_column(default=True)
    auto_generate_assets: Mapped[bool] = mapped_column(default=True)

    # Smart title filter — opt-in LLM pass that classifies titles as yes/no/maybe
    # before they reach the expensive full-evaluation step. When OFF (default),
    # the deterministic keyword filter is the only gate.
    smart_title_filter_enabled: Mapped[bool] = mapped_column(default=False)

    # Google Custom Search API credentials (for AI Company Monitor).
    # When set, the AI monitor uses Google's fresh index instead of the LLM's
    # built-in web search, which often returns stale/filled positions.
    # Free tier: 100 queries/day. Setup: https://programmablesearchengine.google.com
    google_search_api_key_enc: Mapped[Optional[str]] = mapped_column(Text)
    google_search_cx: Mapped[Optional[str]] = mapped_column(String(100))

    # Title filter for scanner (JSON list of strings)
    title_positive_keywords: Mapped[list] = mapped_column(
        JSON,
        default=lambda: ["AI", "ML", "Machine Learning", "Product Manager", "Director", "VP", "Head of"],
    )
    title_negative_keywords: Mapped[list] = mapped_column(
        JSON,
        default=lambda: ["Junior", "Intern", "Internship"],
    )

    # Priority list of trusted job-alert sender emails or domains.
    # An entry can be either a full email ("alert@indeed.com") or a domain
    # prefix starting with "@" ("@indeed.com"). Any inbox message whose From
    # matches an entry is fast-pathed to category="job_alert" and goes through
    # the existing LLM listing extractor without a separate classifier call.
    # This is a priority list, not a strict allowlist — the regular classifier
    # still runs on senders not listed.
    job_alert_senders: Mapped[list] = mapped_column(
        JSON,
        default=lambda: [
            "@indeed.com",
            "jobs-listings@linkedin.com",
            "jobs-noreply@linkedin.com",
            "@ziprecruiter.com",
            "@glassdoor.com",
        ],
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    listings = relationship("Listing", back_populates="profile", cascade="all, delete-orphan")
    gmail_accounts = relationship(
        "GmailAccount", back_populates="profile", cascade="all, delete-orphan"
    )
    companies = relationship("Company", back_populates="profile", cascade="all, delete-orphan")
    usage_records = relationship("Usage", back_populates="profile", cascade="all, delete-orphan")
    reminders = relationship("Reminder", back_populates="profile", cascade="all, delete-orphan")
