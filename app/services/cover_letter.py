"""Cover letter service - generates markdown + renders PDF."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session

from app.config import settings
from app.models import HistoryEvent, Listing, Profile
from app.prompts import render_prompt
from app.services.evaluation import _load_cv
from app.services.llm import get_provider
from app.services.pdf_generator import PAPER_SIZES, html_to_pdf
from app.services.usage_tracker import log_usage

logger = logging.getLogger(__name__)

VALID_TONES = {"warm", "formal", "enthusiastic", "concise"}

_template_env = Environment(
    loader=FileSystemLoader(str(settings.templates_dir)),
    autoescape=select_autoescape(["html"]),
)


def _slug(s: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return s[:max_len] or "unknown"


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:markdown|md)?\s*\n", "", text)
        text = re.sub(r"\n```\s*$", "", text)
    return text.strip()


async def _render_pdf_from_markdown(
    md: str,
    profile: Profile,
    listing: Listing,
) -> Path:
    """Render cover letter markdown into a PDF."""
    pd = profile.profile_data or {}
    page_width, page_height = PAPER_SIZES.get(
        profile.paper_size.lower(), PAPER_SIZES["letter"]
    )
    template = _template_env.get_template("cover-letter-template.html")
    html = template.render(
        name=profile.name,
        email=pd.get("email") or "",
        phone=pd.get("phone") or "",
        linkedin=pd.get("linkedin") or "",
        company=listing.company,
        date=datetime.utcnow().strftime("%B %d, %Y"),
        body_text=md,
        page_width=page_width,
        page_height=page_height,
        lang="en",
    )
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    filename = f"cl-{_slug(listing.company)}-{date_str}.pdf"
    output_path: Path = (
        settings.resolved_data_dir / str(profile.id) / "cover_letters" / filename
    )
    await html_to_pdf(html, output_path, paper_format=profile.paper_size)
    return output_path


async def generate_cover_letter(
    db: Session,
    profile: Profile,
    listing: Listing,
    tone: str | None = None,
) -> dict:
    """Generate (or regenerate) a tailored cover letter."""
    cv_text = _load_cv(profile)
    if not cv_text:
        raise ValueError("No base resume found. Upload one in Resume Builder first.")

    effective_tone = (
        tone
        or listing.cover_letter_tone_override
        or profile.cover_letter_tone
        or "warm"
    )
    if effective_tone not in VALID_TONES:
        effective_tone = "warm"

    prompt = render_prompt(
        "cover_letter.md.j2",
        listing=listing,
        cv_text=cv_text,
        tone=effective_tone,
    )
    provider = get_provider(profile)
    response = await provider.complete(
        system=(
            "You write thoughtful, specific cover letters in plain markdown. "
            "Output ONLY the letter body, no JSON, no code fences, no preamble."
        ),
        user=prompt,
        max_tokens=1500,
        temperature=0.6,
    )
    log_usage(db, profile.id, "cover_letter", response)

    markdown = _strip_code_fences(response.text)
    if not markdown:
        raise ValueError("LLM returned empty cover letter")

    # First-time generation sets _original
    if not listing.tailored_cover_letter_md_original:
        listing.tailored_cover_letter_md_original = markdown
    listing.tailored_cover_letter_md = markdown
    if tone and tone != profile.cover_letter_tone:
        listing.cover_letter_tone_override = effective_tone

    # Render PDF
    output_path = await _render_pdf_from_markdown(markdown, profile, listing)
    listing.cover_letter_path = str(
        output_path.resolve().relative_to(settings.base_dir.resolve())
    )

    db.add(HistoryEvent(
        profile_id=profile.id,
        listing_id=listing.id,
        event_type="cover_letter_generated",
        event_data={
            "path": listing.cover_letter_path,
            "tone": effective_tone,
        },
    ))
    db.commit()
    db.refresh(listing)
    return {
        "path": listing.cover_letter_path,
        "tone": effective_tone,
        "markdown": markdown,
    }


async def rerender_cover_letter_from_markdown(
    profile: Profile,
    listing: Listing,
) -> str:
    """Regenerate cover letter PDF from the current tailored_cover_letter_md (no LLM)."""
    if not listing.tailored_cover_letter_md:
        raise ValueError("No cover letter markdown to render.")
    output_path = await _render_pdf_from_markdown(
        listing.tailored_cover_letter_md, profile, listing
    )
    rel = str(output_path.resolve().relative_to(settings.base_dir.resolve()))
    listing.cover_letter_path = rel
    return rel
