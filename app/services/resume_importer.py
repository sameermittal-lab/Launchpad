"""Convert an uploaded PDF resume into structured cv.md markdown."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Profile
from app.prompts import render_prompt
from app.services.llm import get_provider
from app.services.pdf_extractor import extract_text_from_pdf
from app.services.usage_tracker import log_usage

logger = logging.getLogger(__name__)


async def pdf_to_markdown(
    db: Session,
    profile: Profile,
    pdf_path: Path,
) -> str:
    """Extract PDF text and convert it to structured markdown via LLM."""
    extracted = extract_text_from_pdf(pdf_path)
    if not extracted:
        raise ValueError("PDF contained no extractable text")

    prompt = render_prompt("pdf_to_markdown.md.j2", extracted_text=extracted)
    provider = get_provider(profile)
    response = await provider.complete(
        system="You are a resume parser. Always output clean markdown, nothing else.",
        user=prompt,
        max_tokens=4000,
        temperature=0.1,
    )
    log_usage(db, profile.id, "pdf_to_markdown", response)

    markdown = response.text.strip()
    # Strip accidental code fences
    if markdown.startswith("```"):
        markdown = re.sub(r"^```(?:markdown|md)?\s*\n", "", markdown)
        markdown = re.sub(r"\n```\s*$", "", markdown)

    return markdown.strip()


def save_cv(profile_data_dir: Path, markdown: str) -> Path:
    """Save markdown to cv.md in the profile's data directory."""
    profile_data_dir.mkdir(parents=True, exist_ok=True)
    cv_path = profile_data_dir / "cv.md"
    cv_path.write_text(markdown, encoding="utf-8")
    return cv_path


def load_cv(profile_data_dir: Path) -> str:
    """Load cv.md, return empty string if missing."""
    cv_path = profile_data_dir / "cv.md"
    if cv_path.exists():
        return cv_path.read_text(encoding="utf-8")
    return ""
