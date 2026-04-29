"""Analyze a candidate's resume and recommend job search settings."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models import Profile
from app.prompts import render_prompt
from app.services.evaluation import _extract_json
from app.services.llm import get_provider
from app.services.usage_tracker import log_usage

logger = logging.getLogger(__name__)


async def analyze_resume_for_settings(
    db: Session,
    profile: Profile,
    cv_text: str,
) -> dict[str, Any]:
    """Run an LLM pass over the user's cv.md to suggest personalized settings.

    Returns a dict matching the structure in analyze_resume.md.j2. Does not
    persist anything - that's the frontend's call to make after user reviews.
    """
    prompt = render_prompt("analyze_resume.md.j2", cv_text=cv_text)
    provider = get_provider(profile)
    response = await provider.complete(
        system=(
            "You are a career coach who analyzes resumes and recommends "
            "personalized job search filters. Always output valid JSON only."
        ),
        user=prompt,
        max_tokens=2500,
        temperature=0.2,
    )
    log_usage(db, profile.id, "analyze_resume", response)
    return _extract_json(response.text)
