"""Resolve a company's canonical careers URL — by deterministic inference first,
then by LLM web search as a fallback.

Used by the "Track this company" quick-add flow on listings + the Companies page.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.models import Profile
from app.services.evaluation import _extract_json
from app.services.llm import get_provider
from app.services.usage_tracker import log_usage

logger = logging.getLogger(__name__)


@dataclass
class ResolvedCareers:
    careers_url: str
    platform: str  # greenhouse | ashby | lever | custom
    source: str    # "derived_from_url" | "llm_web_search"
    notes: Optional[str] = None


def _derive_from_listing_url(listing_url: Optional[str]) -> Optional[ResolvedCareers]:
    """Try to deterministically build the company's careers URL from a job URL.

    Zero-cost path — works for most listings whose host reveals the ATS platform.
    Returns None if we can't recognize the host.
    """
    if not listing_url:
        return None

    # Greenhouse board: https://job-boards.greenhouse.io/{slug}/jobs/XXXX
    m = re.search(r"job-boards(?:\.eu)?\.greenhouse\.io/([^/?#]+)", listing_url)
    if m:
        slug = m.group(1)
        return ResolvedCareers(
            careers_url=f"https://job-boards.greenhouse.io/{slug}",
            platform="greenhouse",
            source="derived_from_url",
        )
    m = re.search(r"boards\.greenhouse\.io/([^/?#]+)", listing_url)
    if m:
        slug = m.group(1)
        return ResolvedCareers(
            careers_url=f"https://boards.greenhouse.io/{slug}",
            platform="greenhouse",
            source="derived_from_url",
        )

    # Ashby: https://jobs.ashbyhq.com/{slug}/{uuid}
    m = re.search(r"jobs\.ashbyhq\.com/([^/?#]+)", listing_url)
    if m:
        slug = m.group(1)
        return ResolvedCareers(
            careers_url=f"https://jobs.ashbyhq.com/{slug}",
            platform="ashby",
            source="derived_from_url",
        )

    # Lever: https://jobs.lever.co/{slug}/{uuid}
    m = re.search(r"jobs\.lever\.co/([^/?#]+)", listing_url)
    if m:
        slug = m.group(1)
        return ResolvedCareers(
            careers_url=f"https://jobs.lever.co/{slug}",
            platform="lever",
            source="derived_from_url",
        )

    # Custom career portals we recognize — map listing URL to the root
    # careers page. Platform stays "custom" since the ATS scanner can't use it.
    custom_hosts = [
        (r"amazon\.jobs", "https://www.amazon.jobs"),
        (r"careers\.microsoft\.com|jobs\.careers\.microsoft\.com", "https://jobs.careers.microsoft.com"),
        (r"careers\.google\.com|google\.com/about/careers", "https://www.google.com/about/careers/applications"),
        (r"metacareers\.com", "https://www.metacareers.com"),
        (r"jobs\.apple\.com", "https://jobs.apple.com"),
        (r"openai\.com/careers", "https://openai.com/careers"),
        (r"anthropic\.com/careers", "https://www.anthropic.com/careers"),
        (r"nvidia\.com/en-us/about-nvidia/careers", "https://www.nvidia.com/en-us/about-nvidia/careers/"),
    ]
    for pat, root in custom_hosts:
        if re.search(pat, listing_url):
            return ResolvedCareers(
                careers_url=root,
                platform="custom",
                source="derived_from_url",
            )

    # Fall-through: if the URL has a sensible-looking host with "careers" or
    # "jobs" in it, extract the root as a reasonable guess.
    try:
        parsed = urlparse(listing_url)
        if parsed.netloc and ("careers" in parsed.netloc.lower() or "jobs" in parsed.netloc.lower()):
            return ResolvedCareers(
                careers_url=f"{parsed.scheme}://{parsed.netloc}",
                platform="custom",
                source="derived_from_url",
            )
    except Exception:
        pass

    return None


async def _resolve_via_llm(
    db: Session,
    profile: Profile,
    company_name: str,
) -> Optional[ResolvedCareers]:
    """Ask the LLM (web-grounded) to find the company's canonical careers URL.

    Returns None on any failure — callers decide what to do when we can't find one.
    """
    if not company_name or not company_name.strip():
        return None

    prompt = (
        f"Find the official careers page URL for the company: {company_name}\n\n"
        f"Requirements:\n"
        f"- You MUST use web search to verify the URL exists and serves a careers page\n"
        f"- Prefer the company's own domain (e.g. `careers.openai.com`) over aggregators\n"
        f"- If the company uses a public ATS (Greenhouse, Ashby, Lever), return the ATS board URL "
        f"(e.g. `https://job-boards.greenhouse.io/{{slug}}`)\n"
        f"- If the company uses a custom careers portal, return the root careers URL\n"
        f"- Do NOT return a specific job posting — return the careers landing / search page\n\n"
        f"Respond with JSON only:\n"
        f"{{\n"
        f'  "careers_url": "https://...",\n'
        f'  "platform": "greenhouse" | "ashby" | "lever" | "workday" | "smartrecruiters" | "custom",\n'
        f'  "notes": "optional short note"\n'
        f"}}"
    )
    provider = get_provider(profile)
    try:
        response = await provider.complete_with_search(
            system=(
                "You locate companies' official careers pages using web search. "
                "You always respond with valid JSON and never fabricate URLs."
            ),
            user=prompt,
            max_tokens=500,
            temperature=0.1,
        )
    except Exception as exc:
        logger.warning(f"Web-grounded careers URL resolve failed for {company_name}: {exc}")
        return None

    log_usage(db, profile.id, "careers_url_resolver", response)

    try:
        data = _extract_json(response.text)
    except Exception as exc:
        logger.warning(f"Careers URL resolve JSON parse failed: {exc}")
        return None

    url = (data.get("careers_url") or "").strip() if isinstance(data, dict) else ""
    if not url or not url.startswith(("http://", "https://")):
        return None

    platform = (data.get("platform") or "custom").strip().lower()
    if platform not in ("greenhouse", "ashby", "lever", "workday", "smartrecruiters", "custom"):
        platform = "custom"

    return ResolvedCareers(
        careers_url=url,
        platform=platform,
        source="llm_web_search",
        notes=(data.get("notes") or None),
    )


async def resolve_careers_url(
    db: Session,
    profile: Profile,
    company_name: str,
    hint_url: Optional[str] = None,
) -> Optional[ResolvedCareers]:
    """Resolve a company's careers URL.

    Tries cheap deterministic inference from `hint_url` first; falls back to
    web-grounded LLM. Returns None if both paths fail.
    """
    # Step 1: derive from hint (job URL, careers URL, etc.) for free
    derived = _derive_from_listing_url(hint_url)
    if derived:
        return derived

    # Step 2: ask the LLM with web search
    if not profile.llm_api_key_enc:
        return None
    return await _resolve_via_llm(db, profile, company_name)
