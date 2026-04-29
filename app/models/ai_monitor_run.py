"""AIMonitorRun model — one row per AI-Company-Monitor scan.

Mirrors the shape of EmailMessage.extracted_listings / filtered_listings so the
UI can show "kept vs filtered" identically to how Gmail alert extractions work.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime, ForeignKey, Integer, JSON, String, Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AIMonitorRun(Base):
    __tablename__ = "ai_monitor_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), nullable=False, index=True)
    tracked_company_id: Mapped[int] = mapped_column(
        ForeignKey("tracked_companies.id"), nullable=False, index=True,
    )

    # "scheduled" | "manual"
    trigger: Mapped[str] = mapped_column(String(20), default="manual")

    # Snapshot of the query plan used for this run so we can replay after regen.
    queries_used: Mapped[list] = mapped_column(JSON, default=list)

    # Everything the web search surfaced, after URL dedup across queries but BEFORE
    # the profile's title-keyword filter. Shape:
    #   [{"company","role_title","url","location","snippet","source_query"}]
    all_listings: Mapped[list] = mapped_column(JSON, default=list)

    # Listings that survived both the title filter AND DB-dedup against existing
    # Listing rows — these became real Listings in this run.
    kept_listings: Mapped[list] = mapped_column(JSON, default=list)

    # Listings dropped by the title filter with the human-readable reason.
    # Shape: [{"company","role_title","url","reason","source_query"}]
    filtered_listings: Mapped[list] = mapped_column(JSON, default=list)

    # Listings dropped because they already exist in the DB (dedup).
    # Shape: [{"url","role_title","company"}]
    deduped_listings: Mapped[list] = mapped_column(JSON, default=list)

    # Counts — also derivable but cached for fast UI rendering
    total_found: Mapped[int] = mapped_column(Integer, default=0)
    kept_count: Mapped[int] = mapped_column(Integer, default=0)
    filtered_count: Mapped[int] = mapped_column(Integer, default=0)
    deduped_count: Mapped[int] = mapped_column(Integer, default=0)
    created_listing_ids: Mapped[list] = mapped_column(JSON, default=list)

    # Error string if the whole run failed (e.g. web search unavailable)
    error: Mapped[Optional[str]] = mapped_column(Text)

    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Relationships
    tracked_company = relationship("TrackedCompany")
