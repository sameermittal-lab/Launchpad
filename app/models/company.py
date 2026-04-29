"""Company model - cached research data per profile."""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, DateTime, Float, Integer, JSON, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (UniqueConstraint("profile_id", "name", name="uq_profile_company"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), nullable=False)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    valuation: Mapped[Optional[str]] = mapped_column(String(50))
    employee_count: Mapped[Optional[str]] = mapped_column(String(50))
    glassdoor_rating: Mapped[Optional[float]] = mapped_column(Float)
    tech_stack: Mapped[Optional[str]] = mapped_column(String(500))
    research_data: Mapped[Optional[dict]] = mapped_column(JSON)

    refreshed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    profile = relationship("Profile", back_populates="companies")
