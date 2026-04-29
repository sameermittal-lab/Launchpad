"""Negotiation helper - generate counter-offer scripts for offer-stage listings."""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Listing, Profile
from app.prompts import render_prompt
from app.services.evaluation import _extract_json
from app.services.llm import get_provider
from app.services.usage_tracker import log_usage

logger = logging.getLogger(__name__)


async def generate_counter_offer(
    db: Session,
    profile: Profile,
    listing: Listing,
    offer_details: dict,
) -> dict:
    """Generate a counter-offer email and strategy.

    offer_details should contain:
      base_salary, equity, other, deadline, competing_offers (optional), notes (optional)
    """
    pd = profile.profile_data or {}
    target_salary = pd.get("target_salary") or "(not specified)"
    career_stage = pd.get("career_stage") or "Senior"
    years_experience = pd.get("years_experience") or "10+"

    prompt = render_prompt(
        "negotiation.md.j2",
        profile=profile,
        listing=listing,
        offer=offer_details,
        target_salary=target_salary,
        career_stage=career_stage,
        years_experience=years_experience,
    )
    provider = get_provider(profile)
    response = await provider.complete(
        system="You are a compensation negotiation expert. Output JSON only.",
        user=prompt,
        max_tokens=2500,
        temperature=0.4,
    )
    log_usage(db, profile.id, "negotiation", response)

    try:
        result = _extract_json(response.text)
    except Exception as exc:
        logger.error(f"Negotiation JSON parse failed: {exc}")
        raise

    return result
