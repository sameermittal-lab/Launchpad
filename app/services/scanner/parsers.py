"""ATS API parsers - Greenhouse, Ashby, Lever.

Ported from career-ops/scan.mjs. Each parser fetches a public JSON endpoint
and returns a normalized list of job dicts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import httpx


FETCH_TIMEOUT = 20.0


@dataclass
class ScannedJob:
    title: str
    url: str
    company: str
    location: Optional[str] = None


@dataclass
class APIEndpoint:
    provider: str  # greenhouse | ashby | lever
    url: str


def detect_api(careers_url: str, api_override: Optional[str] = None) -> Optional[APIEndpoint]:
    """Infer the ATS API endpoint from a company's careers_url.

    Returns None if no known ATS pattern matches.
    """
    # Explicit override (e.g., user provided the boards-api.greenhouse.io URL)
    if api_override:
        if "greenhouse" in api_override:
            return APIEndpoint("greenhouse", api_override)
        if "ashbyhq" in api_override:
            return APIEndpoint("ashby", api_override)
        if "lever.co" in api_override:
            return APIEndpoint("lever", api_override)

    if not careers_url:
        return None

    # Ashby: https://jobs.ashbyhq.com/{slug}
    m = re.search(r"jobs\.ashbyhq\.com/([^/?#]+)", careers_url)
    if m:
        slug = m.group(1)
        return APIEndpoint(
            "ashby",
            f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true",
        )

    # Lever: https://jobs.lever.co/{slug}
    m = re.search(r"jobs\.lever\.co/([^/?#]+)", careers_url)
    if m:
        slug = m.group(1)
        return APIEndpoint("lever", f"https://api.lever.co/v0/postings/{slug}?mode=json")

    # Greenhouse (US and EU)
    m = re.search(r"job-boards(?:\.eu)?\.greenhouse\.io/([^/?#]+)", careers_url)
    if m:
        slug = m.group(1)
        return APIEndpoint(
            "greenhouse",
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
        )
    m = re.search(r"boards\.greenhouse\.io/([^/?#]+)", careers_url)
    if m:
        slug = m.group(1)
        return APIEndpoint(
            "greenhouse",
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
        )

    return None


async def fetch_json(url: str) -> dict | list:
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=FETCH_TIMEOUT,
        headers={"User-Agent": "LaunchPad/0.1"},
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


def parse_greenhouse(data: dict, company_name: str) -> list[ScannedJob]:
    jobs = data.get("jobs", [])
    return [
        ScannedJob(
            title=j.get("title", ""),
            url=j.get("absolute_url", ""),
            company=company_name,
            location=(j.get("location") or {}).get("name"),
        )
        for j in jobs
    ]


def parse_ashby(data: dict, company_name: str) -> list[ScannedJob]:
    jobs = data.get("jobs", [])
    return [
        ScannedJob(
            title=j.get("title", ""),
            url=j.get("jobUrl", ""),
            company=company_name,
            location=j.get("location"),
        )
        for j in jobs
    ]


def parse_lever(data: list, company_name: str) -> list[ScannedJob]:
    if not isinstance(data, list):
        return []
    return [
        ScannedJob(
            title=j.get("text", ""),
            url=j.get("hostedUrl", "") or j.get("applyUrl", ""),
            company=company_name,
            location=(j.get("categories") or {}).get("location"),
        )
        for j in data
    ]


PARSERS = {
    "greenhouse": parse_greenhouse,
    "ashby": parse_ashby,
    "lever": parse_lever,
}


async def fetch_company_jobs(
    company_name: str,
    careers_url: str,
    api_override: Optional[str] = None,
) -> tuple[list[ScannedJob], Optional[str]]:
    """Fetch jobs for a single company. Returns (jobs, error_message)."""
    api = detect_api(careers_url, api_override)
    if not api:
        return [], f"No ATS API detected for {careers_url}"

    try:
        data = await fetch_json(api.url)
    except Exception as exc:
        return [], f"API fetch failed: {exc}"

    parser = PARSERS.get(api.provider)
    if not parser:
        return [], f"No parser for {api.provider}"

    return parser(data, company_name), None
