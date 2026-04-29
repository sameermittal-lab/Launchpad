"""Company research API."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Company, Profile
from app.services.company_research import research_company
from app.utils.session import get_current_profile

router = APIRouter(prefix="/api/companies", tags=["companies"])


class CompanyResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    valuation: Optional[str] = None
    employee_count: Optional[str] = None
    glassdoor_rating: Optional[float] = None
    tech_stack: Optional[str] = None
    research_data: Optional[dict] = None
    refreshed_at: datetime


class ResearchRequest(BaseModel):
    name: str
    careers_url: Optional[str] = None


@router.get("", response_model=list[CompanyResponse])
def list_companies(
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    companies = (
        db.query(Company)
        .filter(Company.profile_id == profile.id)
        .order_by(Company.name)
        .all()
    )
    return [_to_response(c) for c in companies]


@router.post("/research", response_model=CompanyResponse)
async def research(
    data: ResearchRequest,
    force_refresh: bool = False,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    if not profile.llm_api_key_enc:
        raise HTTPException(status_code=400, detail="No LLM API key configured")
    try:
        company = await research_company(
            db, profile, data.name, data.careers_url, force_refresh=force_refresh,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return _to_response(company)


@router.post("/{company_id}/refresh", response_model=CompanyResponse)
async def refresh(
    company_id: int,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    company = db.get(Company, company_id)
    if not company or company.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Company not found")
    if not profile.llm_api_key_enc:
        raise HTTPException(status_code=400, detail="No LLM API key configured")
    updated = await research_company(
        db, profile, company.name, None, force_refresh=True,
    )
    return _to_response(updated)


@router.delete("/{company_id}")
def delete_company(
    company_id: int,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    company = db.get(Company, company_id)
    if not company or company.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Company not found")
    db.delete(company)
    db.commit()
    return {"deleted": True}


def _to_response(c: Company) -> CompanyResponse:
    return CompanyResponse(
        id=c.id,
        name=c.name,
        description=c.description,
        valuation=c.valuation,
        employee_count=c.employee_count,
        glassdoor_rating=c.glassdoor_rating,
        tech_stack=c.tech_stack,
        research_data=c.research_data,
        refreshed_at=c.refreshed_at,
    )
