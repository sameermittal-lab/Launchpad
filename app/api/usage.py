"""Usage / cost tracking endpoints."""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Profile, Usage
from app.utils.session import get_current_profile

router = APIRouter(prefix="/api/usage", tags=["usage"])


class UsageSummary(BaseModel):
    month_cost_usd: float
    month_calls: int
    all_time_cost_usd: float
    all_time_calls: int
    breakdown_by_action: dict  # {action: {cost: x, calls: y}}


@router.get("/summary", response_model=UsageSummary)
def get_summary(
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    month_cost, month_calls = db.query(
        func.coalesce(func.sum(Usage.cost_usd), 0.0),
        func.count(Usage.id),
    ).filter(
        Usage.profile_id == profile.id,
        Usage.created_at >= month_start,
    ).one()

    all_cost, all_calls = db.query(
        func.coalesce(func.sum(Usage.cost_usd), 0.0),
        func.count(Usage.id),
    ).filter(Usage.profile_id == profile.id).one()

    rows = db.query(
        Usage.action,
        func.sum(Usage.cost_usd),
        func.count(Usage.id),
    ).filter(Usage.profile_id == profile.id).group_by(Usage.action).all()

    breakdown = {action: {"cost": float(cost or 0), "calls": calls} for action, cost, calls in rows}

    return UsageSummary(
        month_cost_usd=round(float(month_cost), 4),
        month_calls=month_calls,
        all_time_cost_usd=round(float(all_cost), 4),
        all_time_calls=all_calls,
        breakdown_by_action=breakdown,
    )
