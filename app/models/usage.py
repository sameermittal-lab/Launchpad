"""Usage model - LLM API cost tracking."""

from datetime import datetime

from sqlalchemy import String, DateTime, Float, Integer, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Usage(Base):
    __tablename__ = "usage"
    __table_args__ = (
        Index("ix_usage_profile_created", "profile_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), nullable=False)

    action: Mapped[str] = mapped_column(String(50), nullable=False)
    # evaluation | resume_tailor | cover_letter | email_classify | company_research | ...
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)

    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    profile = relationship("Profile", back_populates="usage_records")
