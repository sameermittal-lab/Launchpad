"""Generate a tailored resume as markdown + rendered PDF."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.models import HistoryEvent, Listing, Profile
from app.prompts import render_prompt
from app.services.evaluation import _load_cv
from app.services.llm import get_provider
from app.services.markdown_resume_parser import parse_resume_md
from app.services.pdf_generator import generate_resume_pdf
from app.services.usage_tracker import log_usage

logger = logging.getLogger(__name__)

VALID_INTENSITIES = {"light", "medium", "heavy"}


def _slug(s: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return s[:max_len] or "unknown"


def _strip_code_fences(text: str) -> str:
    """Remove accidental markdown code fence wrappers from LLM output."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:markdown|md)?\s*\n", "", text)
        text = re.sub(r"\n```\s*$", "", text)
    return text.strip()


async def _generate_tailored_markdown(
    db: Session,
    profile: Profile,
    listing: Listing,
    intensity: str,
) -> str:
    """Call the LLM to generate the tailored resume as markdown text."""
    cv_text = _load_cv(profile)
    if not cv_text:
        raise ValueError(
            "No base resume found. Upload a PDF or create cv.md in Resume Builder first."
        )

    prompt = render_prompt(
        "resume_tailor.md.j2",
        listing=listing,
        cv_text=cv_text,
        intensity=intensity,
    )
    provider = get_provider(profile)
    response = await provider.complete(
        system=(
            "You tailor resumes by reformulating real experience with JD language. "
            "You never invent skills, metrics, or achievements. "
            "Output ONLY the resume as markdown - no JSON, no code fences, no preamble."
        ),
        user=prompt,
        max_tokens=4000,
        temperature=0.3,
    )
    log_usage(db, profile.id, "resume_tailor", response)

    markdown = _strip_code_fences(response.text)
    if not markdown or not markdown.startswith("#"):
        raise ValueError("LLM did not return valid markdown output")
    return markdown


def _render_pdf_from_markdown(
    md_text: str,
    output_path: Path,
    paper_size: str,
) -> None:
    """Convert markdown -> template data -> PDF."""
    import asyncio
    template_data = parse_resume_md(md_text)
    asyncio.get_event_loop().run_until_complete(
        generate_resume_pdf(template_data, output_path, paper_size=paper_size)
    )


async def _render_pdf_async(
    md_text: str,
    output_path: Path,
    paper_size: str,
) -> None:
    template_data = parse_resume_md(md_text)
    await generate_resume_pdf(template_data, output_path, paper_size=paper_size)


async def tailor_resume(
    db: Session,
    profile: Profile,
    listing: Listing,
    intensity: str | None = None,
) -> dict:
    """Generate (or regenerate) a tailored resume. Saves markdown + PDF to the listing."""
    effective_intensity = (intensity or listing.tailoring_intensity or "medium").lower()
    if effective_intensity not in VALID_INTENSITIES:
        effective_intensity = "medium"

    markdown = await _generate_tailored_markdown(db, profile, listing, effective_intensity)

    # First-time generation sets _original; subsequent regenerations don't
    if not listing.tailored_resume_md_original:
        listing.tailored_resume_md_original = markdown
    listing.tailored_resume_md = markdown
    listing.tailoring_intensity = effective_intensity

    # Render PDF
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    filename = f"cv-{_slug(listing.company)}-{date_str}.pdf"
    output_path: Path = (
        settings.resolved_data_dir / str(profile.id) / "generated_resumes" / filename
    )
    await _render_pdf_async(markdown, output_path, profile.paper_size)

    listing.tailored_resume_path = str(
        output_path.resolve().relative_to(settings.base_dir.resolve())
    )

    db.add(HistoryEvent(
        profile_id=profile.id,
        listing_id=listing.id,
        event_type="resume_generated",
        event_data={
            "path": listing.tailored_resume_path,
            "intensity": effective_intensity,
        },
    ))
    db.commit()
    db.refresh(listing)
    return {
        "path": listing.tailored_resume_path,
        "intensity": effective_intensity,
        "markdown": markdown,
    }


async def rerender_resume_from_markdown(
    profile: Profile,
    listing: Listing,
) -> str:
    """Regenerate the PDF from the current tailored_resume_md (no LLM call).

    Used when the user edits the markdown and clicks Save.
    """
    if not listing.tailored_resume_md:
        raise ValueError("No tailored markdown to render. Generate first.")
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    filename = f"cv-{_slug(listing.company)}-{date_str}.pdf"
    output_path: Path = (
        settings.resolved_data_dir / str(profile.id) / "generated_resumes" / filename
    )
    await _render_pdf_async(listing.tailored_resume_md, output_path, profile.paper_size)
    rel = str(output_path.resolve().relative_to(settings.base_dir.resolve()))
    listing.tailored_resume_path = rel
    return rel
