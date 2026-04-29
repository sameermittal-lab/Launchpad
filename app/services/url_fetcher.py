"""Fetch and extract content from a job URL."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx
import trafilatura

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)


@dataclass
class FetchResult:
    url: str
    content: Optional[str]  # Extracted main text
    html: Optional[str]  # Raw HTML
    title: Optional[str]
    success: bool
    error: Optional[str] = None


async def fetch_url(url: str, use_playwright_fallback: bool = True) -> FetchResult:
    """Fetch a URL and extract the main text content.

    Tries httpx first (fast), falls back to Playwright if the page is JS-heavy
    or trafilatura can't extract meaningful content.
    """
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=20.0,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:
        logger.warning(f"httpx fetch failed for {url}: {exc}")
        if use_playwright_fallback:
            return await _fetch_with_playwright(url)
        return FetchResult(
            url=url, content=None, html=None, title=None,
            success=False, error=str(exc),
        )

    extracted = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        favor_precision=True,
    )

    # If trafilatura extracted very little, fall back to Playwright
    if not extracted or len(extracted) < 200:
        if use_playwright_fallback:
            logger.info(f"Weak extraction for {url}, trying Playwright")
            return await _fetch_with_playwright(url)

    # Try to get a title
    title = None
    try:
        meta = trafilatura.extract_metadata(html)
        if meta:
            title = meta.title
    except Exception:
        pass

    return FetchResult(
        url=url, content=extracted, html=html, title=title, success=True,
    )


async def _fetch_with_playwright(url: str) -> FetchResult:
    """Fallback: use Playwright to render JS-heavy pages."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return FetchResult(
            url=url, content=None, html=None, title=None,
            success=False, error="Playwright not installed",
        )

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent=USER_AGENT)
            page = await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                # Wait a moment for JS-heavy SPAs to render
                await page.wait_for_timeout(2000)
                html = await page.content()
                title = await page.title()
            finally:
                await browser.close()

        extracted = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            favor_precision=True,
        )

        return FetchResult(
            url=url, content=extracted, html=html, title=title, success=True,
        )
    except Exception as exc:
        logger.warning(f"Playwright fetch failed for {url}: {exc}")
        return FetchResult(
            url=url, content=None, html=None, title=None,
            success=False, error=str(exc),
        )
