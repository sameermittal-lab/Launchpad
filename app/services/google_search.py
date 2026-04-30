"""Google Custom Search API client for the AI Company Monitor.

Uses Google's Programmable Search Engine (free tier: 100 queries/day) to get
fresh, real search results instead of relying on the LLM's built-in web search
which often returns stale/filled positions from its cached index.

Setup (one-time, ~5 minutes):
  1. Go to https://programmablesearchengine.google.com → Create a search engine
     - Search the entire web (or restrict to specific sites)
     - Copy the "Search engine ID" (cx)
  2. Go to https://console.cloud.google.com/apis/credentials → Create API key
     - Enable "Custom Search API" in the API library
  3. Paste both into LaunchPad Settings → Google Search section
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

GOOGLE_CSE_URL = "https://www.googleapis.com/customsearch/v1"


@dataclass
class GoogleSearchResult:
    title: str
    url: str
    snippet: str


async def google_search(
    api_key: str,
    cx: str,
    query: str,
    num: int = 10,
) -> list[GoogleSearchResult]:
    """Run a single Google Custom Search query.

    Returns up to `num` results (max 10 per call per Google's API).
    Raises on HTTP errors so callers can fall back to LLM search.
    """
    params = {
        "key": api_key,
        "cx": cx,
        "q": query,
        "num": min(num, 10),  # Google CSE max per request
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(GOOGLE_CSE_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    results: list[GoogleSearchResult] = []
    for item in data.get("items") or []:
        results.append(GoogleSearchResult(
            title=item.get("title") or "",
            url=item.get("link") or "",
            snippet=item.get("snippet") or "",
        ))
    return results


async def google_search_multi(
    api_key: str,
    cx: str,
    queries: list[str],
    num_per_query: int = 10,
) -> list[tuple[str, list[GoogleSearchResult]]]:
    """Run multiple queries in parallel. Returns [(query, results), ...]."""
    import asyncio

    async def _run(q: str) -> tuple[str, list[GoogleSearchResult]]:
        try:
            results = await google_search(api_key, cx, q, num=num_per_query)
            return q, results
        except Exception as exc:
            logger.warning(f"Google search failed for query '{q}': {exc}")
            return q, []

    return await asyncio.gather(*[_run(q) for q in queries])
