"""TrackedCompany model - companies whose career pages we monitor."""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    String, Text, DateTime, Boolean, Integer, ForeignKey, UniqueConstraint, JSON,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TrackedCompany(Base):
    __tablename__ = "tracked_companies"
    __table_args__ = (
        UniqueConstraint("profile_id", "name", name="uq_profile_tracked_company"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), nullable=False)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    careers_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    api_url: Mapped[Optional[str]] = mapped_column(String(1000))
    platform: Mapped[Optional[str]] = mapped_column(String(50))  # greenhouse | ashby | lever | custom
    notes: Mapped[Optional[str]] = mapped_column(Text)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_scanned_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_job_count: Mapped[int] = mapped_column(Integer, default=0)

    # --- AI Company Monitor ---
    # When enabled, LaunchPad runs a periodic LLM-web-search scan against this
    # company's careers site using a cached "query plan" tuned to the candidate's
    # resume, target roles, and pass history. Fills the gap for companies that
    # aren't on the public Greenhouse/Ashby/Lever APIs.
    ai_monitor_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Structured query plan produced by services/query_planner.py. Shape:
    # {
    #   "strategy": "scale-wide" | "narrow",
    #   "careers_site": "amazon.jobs",
    #   "queries": [
    #     {"q": "...", "rationale": "..."},
    #     ...
    #   ],
    #   "estimated_yield": 25,
    #   "level_mapping_notes": "free-text from the planner",
    # }
    query_plan: Mapped[Optional[dict]] = mapped_column(JSON)
    query_plan_generated_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_ai_monitor_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_ai_monitor_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
