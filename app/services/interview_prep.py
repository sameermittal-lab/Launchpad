"""Generate and cache STAR stories for interview prep."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Profile
from app.prompts import render_prompt
from app.services.evaluation import _extract_json, _load_cv
from app.services.llm import get_provider
from app.services.usage_tracker import log_usage

logger = logging.getLogger(__name__)


def _stories_path(profile: Profile) -> Path:
    return settings.resolved_data_dir / str(profile.id) / "interview_stories.json"


def load_cached_stories(profile: Profile) -> Optional[list]:
    path = _stories_path(profile)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_stories(profile: Profile, stories: list) -> None:
    path = _stories_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stories, indent=2), encoding="utf-8")


async def generate_stories(
    db: Session,
    profile: Profile,
    force_refresh: bool = False,
) -> list:
    if not force_refresh:
        cached = load_cached_stories(profile)
        if cached:
            return cached

    cv_text = _load_cv(profile)
    if not cv_text:
        raise ValueError("No resume found. Upload one in Resume Builder first.")

    target_roles = (profile.profile_data or {}).get("target_roles") or []
    target_roles_str = ", ".join(target_roles) if target_roles else ""

    prompt = render_prompt(
        "interview_prep.md.j2",
        cv_text=cv_text,
        target_roles=target_roles_str,
    )
    provider = get_provider(profile)
    response = await provider.complete(
        system="You are an interview coach. Output JSON array only, nothing else.",
        user=prompt,
        max_tokens=4000,
        temperature=0.4,
    )
    log_usage(db, profile.id, "interview_prep", response)

    try:
        stories = _extract_json(response.text)
    except Exception as exc:
        logger.error(f"Interview prep JSON parse failed: {exc}")
        raise

    if not isinstance(stories, list):
        raise ValueError("Expected JSON array of stories")

    save_stories(profile, stories)
    return stories
