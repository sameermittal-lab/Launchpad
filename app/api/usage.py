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
    breakdown_by_feature: dict  # {feature_label: {cost: x, calls: y}}
    breakdown_by_provider: dict  # {provider: {cost: x, calls: y}}


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

    # Cost by feature — group related actions into user-friendly categories
    FEATURE_MAP = {
        "evaluation": "Evaluation",
        "resume_tailor": "Resume Tailoring",
        "cover_letter": "Cover Letters",
        "email_classify": "Gmail Classification",
        "extract_jobs_from_email": "Gmail Extraction",
        "company_research": "Company Research",
        "ai_monitor_search": "AI Monitor (LLM search)",
        "ai_monitor_google_parse": "AI Monitor (Google parse)",
        "ai_monitor_gemini_search": "AI Monitor (Gemini search)",
        "smart_title_filter": "Smart Title Filter",
        "query_planner": "Query Planner",
        "resume_chat": "Resume Chat Editor",
        "interview_prep": "Interview Prep",
        "company_suggestions": "Company Suggestions",
        "careers_url_resolver": "URL Resolution",
    }
    by_feature: dict[str, dict] = {}
    for action, data in breakdown.items():
        label = FEATURE_MAP.get(action, action.replace("_", " ").title())
        if label in by_feature:
            by_feature[label]["cost"] += data["cost"]
            by_feature[label]["calls"] += data["calls"]
        else:
            by_feature[label] = {"cost": data["cost"], "calls": data["calls"]}
    # Sort by cost descending
    by_feature = dict(sorted(by_feature.items(), key=lambda x: x[1]["cost"], reverse=True))

    # Cost by provider
    provider_rows = db.query(
        Usage.provider,
        func.sum(Usage.cost_usd),
        func.count(Usage.id),
    ).filter(Usage.profile_id == profile.id).group_by(Usage.provider).all()

    by_provider = {
        provider: {"cost": round(float(cost or 0), 4), "calls": calls}
        for provider, cost, calls in provider_rows
    }

    return UsageSummary(
        month_cost_usd=round(float(month_cost), 4),
        month_calls=month_calls,
        all_time_cost_usd=round(float(all_cost), 4),
        all_time_calls=all_calls,
        breakdown_by_action=breakdown,
        breakdown_by_feature=by_feature,
        breakdown_by_provider=by_provider,
    )
