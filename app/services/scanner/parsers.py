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
        if "myworkdayjobs.com" in api_override or "myworkdaysite.com" in api_override:
            return _detect_workday(api_override)

    if not careers_url:
        return None

    # Workday: {tenant}.wd{N}.myworkdayjobs.com/{locale}/{site}
    # or jobs.myworkdaysite.com/recruiting/{tenant}/{site}
    wd = _detect_workday(careers_url)
    if wd:
        return wd

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

# Workday is added after its parser function is defined (below)


def _detect_workday(url: str) -> Optional[APIEndpoint]:
    """Detect Workday careers URL and build the CXS API endpoint.

    Workday URL patterns:
      - https://{tenant}.wd{N}.myworkdayjobs.com/{locale}/{site}
      - https://{tenant}.wd{N}.myworkdayjobs.com/{site}
      - https://jobs.myworkdaysite.com/recruiting/{tenant}/{site}
    """
    # Pattern 1: {tenant}.wd{N}.myworkdayjobs.com
    m = re.search(
        r"([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)",
        url,
    )
    if m:
        tenant, wd_server, site = m.group(1), m.group(2), m.group(3)
        api_url = f"https://{tenant}.{wd_server}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
        return APIEndpoint("workday", api_url)

    # Pattern 2: jobs.myworkdaysite.com/recruiting/{tenant}/{site}
    m = re.search(
        r"jobs\.myworkdaysite\.com/recruiting/([\w-]+)/([\w-]+)",
        url,
    )
    if m:
        tenant, site = m.group(1), m.group(2)
        # myworkdaysite uses wd5 by default
        api_url = f"https://{tenant}.wd5.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
        return APIEndpoint("workday", api_url)

    return None


def parse_workday(data: dict, company_name: str) -> list[ScannedJob]:
    """Parse Workday CXS API response into ScannedJob list."""
    postings = data.get("jobPostings", [])
    if not isinstance(postings, list):
        return []

    jobs: list[ScannedJob] = []
    for p in postings:
        title = (p.get("title") or "").strip()
        external_path = p.get("externalPath") or ""
        location = p.get("locationsText") or None

        if not title:
            continue

        # Build the full URL from the API URL + externalPath
        # The externalPath looks like "/en-US/job/Senior-PM/JR-12345"
        # We need to reconstruct the full URL from the API base
        url = p.get("externalUrl") or ""
        if not url and external_path:
            # We'll fix this up in fetch_company_jobs where we have the base URL
            url = external_path

        jobs.append(ScannedJob(
            title=title,
            url=url,
            company=company_name,
            location=location,
        ))
    return jobs


async def _fetch_workday_jobs(api_url: str) -> dict:
    """Fetch jobs from Workday CXS API (POST with JSON body, paginated)."""
    all_postings: list[dict] = []
    offset = 0
    batch_size = 20

    # Extract referer from API URL for the headers
    # API: https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
    # Referer: https://{tenant}.{wd}.myworkdayjobs.com/en-US/{site}
    m = re.match(r"(https://[\w-]+\.wd\d+\.myworkdayjobs\.com)/wday/cxs/[\w-]+/([\w-]+)/jobs", api_url)
    if m:
        referer = f"{m.group(1)}/en-US/{m.group(2)}"
        base_url = f"{m.group(1)}/en-US/{m.group(2)}"
    else:
        referer = api_url
        base_url = ""

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Accept-Language": "en-US",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": referer,
    }

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=FETCH_TIMEOUT,
    ) as client:
        while True:
            payload = {
                "appliedFacets": {},
                "limit": batch_size,
                "offset": offset,
                "searchText": "",
            }
            resp = await client.post(api_url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

            postings = data.get("jobPostings") or []
            total = data.get("total", 0)

            if not postings:
                break

            # Fix up URLs — convert externalPath to full URL
            for p in postings:
                ext_path = p.get("externalPath") or ""
                if ext_path and not p.get("externalUrl"):
                    p["externalUrl"] = f"{base_url}{ext_path}" if base_url else ext_path

            all_postings.extend(postings)

            # Safety cap: don't fetch more than 200 jobs per company
            if len(all_postings) >= 200 or offset + batch_size >= total:
                break
            offset += batch_size

    return {"jobPostings": all_postings, "total": total}


# Register Workday parser now that it's defined
PARSERS["workday"] = parse_workday


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
        # Workday uses a POST-based paginated API — special handling
        if api.provider == "workday":
            data = await _fetch_workday_jobs(api.url)
        else:
            data = await fetch_json(api.url)
    except Exception as exc:
        return [], f"API fetch failed: {exc}"

    parser = PARSERS.get(api.provider)
    if not parser:
        return [], f"No parser for {api.provider}"

    return parser(data, company_name), None
