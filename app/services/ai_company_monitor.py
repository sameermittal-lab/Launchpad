"""AI Company Monitor — periodically scans a company's careers site via web search.

Design notes (see spec for full context):
- Uses LLM web search to run 3-5 per-company queries produced by query_planner.
- Ingests ALL surfaced listings (no server-side keyword dropping).
- Runs the profile's own title filter afterward and stores BOTH kept + filtered
  items on an AIMonitorRun record so the user can review/override.
- Dedupes against existing Listings by a canonicalized URL key.
- Optionally auto-evaluates kept listings (respects profile.auto_evaluate).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models import AIMonitorRun, Listing, Profile, TrackedCompany
from app.prompts import render_prompt  # noqa: F401 — used downstream
from app.services.evaluation import _extract_json
from app.services.filters import why_title_fails
from app.services.llm import get_provider
from app.services.query_planner import ensure_query_plan
from app.services.usage_tracker import log_usage

logger = logging.getLogger(__name__)

# --- URL canonicalization -----------------------------------------------------

# Stable-ID regexes by host — extracts a canonical key we can dedupe on.
# Add new hosts over time as we grow coverage.
_STABLE_ID_PATTERNS = [
    # Amazon: amazon.jobs/{en/}?jobs/{digits}
    (re.compile(r"amazon\.jobs.*/jobs/(\d+)", re.I), "amazon.jobs"),
    # Greenhouse: boards.greenhouse.io/{org}/jobs/{digits}
    (re.compile(r"greenhouse\.io/[^/]+/jobs/(\d+)", re.I), "greenhouse"),
    # Ashby: jobs.ashbyhq.com/{org}/{uuid}
    (re.compile(r"ashbyhq\.com/[^/]+/([a-f0-9-]{10,})", re.I), "ashby"),
    # Lever: jobs.lever.co/{org}/{uuid}
    (re.compile(r"lever\.co/[^/]+/([a-f0-9-]{10,})", re.I), "lever"),
    # Microsoft careers: jobs.careers.microsoft.com/us/en/job/{digits}
    (re.compile(r"careers\.microsoft\.com/.*?/job/(\d+)", re.I), "microsoft"),
    # Google careers: google.com/about/careers/applications/jobs/results/{digits}
    (re.compile(r"google\.com/.*?/jobs/results/(\d+)", re.I), "google"),
    # Meta: metacareers.com/jobs/{digits}
    (re.compile(r"metacareers\.com/jobs/(\d+)", re.I), "meta"),
]


def canonical_url_key(url: str) -> str:
    """Return a stable dedup key for a job URL.

    If we recognize the platform, use the platform-specific job ID. Otherwise
    fall back to the URL with query string / fragment stripped.
    """
    if not url:
        return ""
    for pat, platform in _STABLE_ID_PATTERNS:
        m = pat.search(url)
        if m:
            return f"{platform}:{m.group(1)}"
    # Generic fallback: scheme+host+path, no query, no fragment
    m = re.match(r"(https?://[^?#]+)", url.strip())
    return (m.group(1) if m else url).rstrip("/").lower()


# --- URL normalization --------------------------------------------------------

# Rewrite known platform URLs to their canonical, working form.
# The LLM often returns localized or www-prefixed variants that 404.
_URL_NORMALIZERS = [
    # amazon.jobs: rewrite any locale prefix to /en/
    # e.g. https://www.amazon.jobs/pt/jobs/3194369/... → https://amazon.jobs/en/jobs/3194369/...
    (re.compile(r"https?://(?:www\.)?amazon\.jobs(?:/[a-z]{2}(?:-[a-z]{2})?)?/jobs/(\d+)(?:/[^?#]*)?", re.I),
     lambda m: f"https://amazon.jobs/en/jobs/{m.group(1)}"),
    # Microsoft careers: normalize locale
    (re.compile(r"https?://jobs\.careers\.microsoft\.com/[^/]+/[^/]+/job/(\d+)(?:/[^?#]*)?", re.I),
     lambda m: f"https://jobs.careers.microsoft.com/us/en/job/{m.group(1)}"),
]


def normalize_listing_url(url: str) -> str:
    """Rewrite a job URL to its canonical, user-clickable form.

    Returns the original URL unchanged if no normalizer matches.
    """
    if not url:
        return url
    for pat, rewriter in _URL_NORMALIZERS:
        m = pat.match(url.strip())
        if m:
            return rewriter(m)
    # Strip tracking params but keep the URL otherwise
    m2 = re.match(r"(https?://[^?#]+)", url.strip())
    return (m2.group(1) if m2 else url).rstrip("/")


# --- Web search call ----------------------------------------------------------

def _run_queries_prompt(queries: list[dict], careers_site: str) -> str:
    """Build the system/user prompt for the web-search LLM to execute a plan.

    The LLM is instructed to run the queries via its native web_search tool and
    return a single JSON array of listings with their source_query tag.
    """
    lines = [
        "Run the following web searches using your web-search tool.",
        "Return ALL results you find, not just obvious matches — filtering happens downstream.",
        "",
        "CRITICAL — RECENCY: Only return job listings that appear to be CURRENTLY ACTIVE.",
        "If a search result page says 'this job is no longer available', 'position filled',",
        "or redirects to a generic search page, do NOT include it. Prefer results with",
        "recent posting dates. If you cannot verify a listing is still live, include it",
        "but add '\"possibly_stale\": true' to the entry.",
        "",
        "For each result, report: company, role_title, url, location (if visible in",
        "the snippet), and which source_query surfaced it. Prefer the canonical",
        "careers URL (e.g. amazon.jobs, careers.microsoft.com) over aggregators.",
        "",
        f"Target careers site (use as {'verification' if careers_site else 'hint'}): {careers_site or '(none)'}",
        "",
        "Queries to run (each uses a site: operator to restrict scope):",
    ]
    for i, q in enumerate(queries, 1):
        lines.append(f"  {i}. {q['q']}")
        if q.get("rationale"):
            lines.append(f"     (rationale: {q['rationale'][:120]})")
    lines.extend([
        "",
        "Output — a single JSON object only, no prose, no markdown fences:",
        "{",
        "  \"listings\": [",
        "    {",
        "      \"company\": \"Amazon\",",
        "      \"role_title\": \"Principal Product Manager, AWS DataSync\",",
        "      \"url\": \"https://amazon.jobs/jobs/3179898\",",
        "      \"location\": \"Seattle, WA or Santa Clara, CA\",",
        "      \"source_query\": \"\\\"Principal Product Manager\\\" \\\"AWS\\\" site:amazon.jobs\"",
        "    }",
        "  ]",
        "}",
    ])
    return "\n".join(lines)


@dataclass
class SearchHit:
    company: str
    role_title: str
    url: str
    location: Optional[str]
    source_query: Optional[str]


async def _execute_query_plan(
    db: Session,
    profile: Profile,
    company: TrackedCompany,
    plan: dict,
) -> list[SearchHit]:
    """Execute the query plan — tries Gemini grounded search first (fresh Google index),
    falls back to the primary LLM's web search if Gemini isn't available.

    Priority:
    1. Gemini google_search grounding (dedicated search key or primary Gemini key)
    2. Primary LLM's built-in web search + liveness HEAD check
    """
    queries = plan.get("queries") or []
    if not queries:
        return []

    # Determine if we can use Gemini grounded search
    from app.services.secrets import decrypt
    gemini_key = None

    # Option 1: dedicated Gemini search key
    gemini_enc = getattr(profile, "gemini_search_api_key_enc", None)
    if gemini_enc:
        try:
            gemini_key = decrypt(gemini_enc)
        except Exception:
            pass

    # Option 2: primary LLM is Gemini — reuse the same key
    if not gemini_key and profile.llm_provider == "google" and profile.llm_api_key_enc:
        try:
            gemini_key = decrypt(profile.llm_api_key_enc)
        except Exception:
            pass

    if gemini_key:
        try:
            return await _execute_via_gemini_search(db, profile, company, plan, gemini_key)
        except Exception as exc:
            logger.warning(
                f"Gemini grounded search failed for {company.name}, falling back to LLM search: {exc}"
            )

    return await _execute_via_llm_search(db, profile, company, plan)


async def _execute_via_gemini_search(
    db: Session,
    profile: Profile,
    company: TrackedCompany,
    plan: dict,
    gemini_key: str,
) -> list[SearchHit]:
    """Gemini grounded search pipeline.

    Instead of sending each query individually (which fails because Gemini
    strips site: operators and struggles with complex boolean queries), we
    combine all queries into a single natural-language search request.
    """
    import httpx

    queries = plan.get("queries") or []
    careers_site = plan.get("careers_site", "")

    # Extract the key title terms from all queries
    import re as _re
    all_terms = set()
    for q in queries:
        q_text = q.get("q") or ""
        # Extract quoted phrases
        phrases = _re.findall(r'"([^"]+)"', q_text)
        for p in phrases:
            # Skip site: values and generic operators
            if "site:" in p or p in ("OR", "AND"):
                continue
            all_terms.add(p)

    if not all_terms:
        all_terms = {"Director", "Product Management"}

    # Build a single natural-language prompt
    # Strip "site:" prefix from careers_site if present
    import re as _re
    clean_site = _re.sub(r'^site:', '', careers_site).strip() if careers_site else company.name
    terms_str = ", ".join(sorted(all_terms))
    prompt = (
        f"Search {clean_site} for all currently open job positions "
        f"matching these titles/keywords: {terms_str}.\n\n"
        f"Return EVERY job listing you find as a JSON array. Each item must have:\n"
        f"- title: the job title\n"
        f"- url: the full URL to the job posting\n"
        f"- location: the job location (or null)\n\n"
        f"Output ONLY the JSON array, no other text."
    )

    api_url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    logger.info(f"Gemini search prompt for {company.name}: {prompt[:200]}")
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"maxOutputTokens": 4000, "temperature": 0.1},
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(api_url, params={"key": gemini_key}, json=body)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning(f"Gemini search failed for {company.name}: {exc}")
            return []

    # Extract text
    text = ""
    for cand in data.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            if "text" in part:
                text += part["text"]

    if not text.strip():
        logger.warning(f"Gemini search returned empty text for {company.name}")
        return []

    logger.info(f"Gemini search raw response for {company.name} ({len(text)} chars): {text[:200]}")

    # Parse JSON
    try:
        parsed = _extract_json(text)
    except Exception:
        import re as _re2
        json_match = _re2.search(r'\[[\s\S]*\]', text)
        if json_match:
            try:
                import json as _json
                parsed = _json.loads(json_match.group())
            except Exception:
                logger.warning(f"Gemini search JSON parse failed for {company.name}")
                return []
        else:
            logger.warning(f"Gemini search returned no JSON for {company.name}. Response text: {text[:500]}")
            return []

    items = parsed if isinstance(parsed, list) else (parsed.get("listings") or parsed.get("items") or []) if isinstance(parsed, dict) else []

    hits = _parse_hits(items, company)

    # Track usage
    log_usage(db, profile.id, "ai_monitor_gemini_search", type("R", (), {
        "text": text, "provider": "google", "model": "gemini-2.5-flash",
        "input_tokens": 0, "output_tokens": 0, "prompt_tokens": 0,
        "completion_tokens": 0, "cost_usd": 0.01,
    })())

    logger.info(f"Gemini grounded search for {company.name}: {len(hits)} hits")
    return hits
    from app.services.google_search import google_search_multi

    queries = plan.get("queries") or []
    query_strings = [q["q"] for q in queries if q.get("q")]

    # Step 1: Google search (parallel)
    raw_results = await google_search_multi(google_key, google_cx, query_strings)

    # Flatten into a single list with source_query tags
    all_results: list[dict] = []
    for query_str, results in raw_results:
        for r in results:
            if not r.url:
                continue
            all_results.append({
                "title": r.title,
                "url": r.url,
                "snippet": r.snippet,
                "source_query": query_str,
            })

    if not all_results:
        logger.info(f"Google search returned 0 results for {company.name}")
        return []

    logger.info(f"Google search returned {len(all_results)} raw results for {company.name}")

    # Step 2: LLM structuring — extract company, role_title, url, location
    prompt_lines = [
        "I have raw Google search results for job listings. Extract structured data from each.",
        "",
        f"Company being searched: {company.name}",
        f"Careers site: {plan.get('careers_site', '(unknown)')}",
        "",
        "For each result, extract: company, role_title, url, location (if visible).",
        "Only include results that are actual job postings (not blog posts, news, etc.).",
        "Return a JSON object with shape: {\"listings\": [{\"company\": ..., \"role_title\": ..., \"url\": ..., \"location\": ..., \"source_query\": ...}]}",
        "",
        "Raw search results:",
    ]
    for i, r in enumerate(all_results, 1):
        prompt_lines.append(f"  {i}. Title: {r['title']}")
        prompt_lines.append(f"     URL: {r['url']}")
        prompt_lines.append(f"     Snippet: {r['snippet'][:300]}")
        prompt_lines.append(f"     Source query: {r['source_query']}")
        prompt_lines.append("")

    prompt = "\n".join(prompt_lines)
    provider = get_provider(profile)

    response = await provider.complete(
        system=(
            "You extract structured job listing data from raw search results. "
            "Output a single JSON object. Do NOT invent or modify URLs — use "
            "exactly the URLs from the search results. Only include actual job "
            "postings, not news articles or blog posts."
        ),
        user=prompt,
        max_tokens=4000,
        temperature=0.1,
    )
    log_usage(db, profile.id, "ai_monitor_google_parse", response)

    try:
        parsed = _extract_json(response.text)
    except Exception as exc:
        logger.warning(f"Google result parsing failed for {company.name}: {exc}")
        # Fall back to raw results — use title as role_title directly
        return _raw_results_to_hits(all_results, company)

    raw = parsed.get("listings") if isinstance(parsed, dict) else parsed
    if not isinstance(raw, list):
        return _raw_results_to_hits(all_results, company)

    return _parse_hits(raw, company)


def _raw_results_to_hits(results: list[dict], company: TrackedCompany) -> list[SearchHit]:
    """Fallback: convert raw Google results directly to SearchHits without LLM parsing."""
    hits: list[SearchHit] = []
    seen_keys: set[str] = set()
    for r in results:
        url = normalize_listing_url(r.get("url") or "")
        title = r.get("title") or ""
        if not url or not title:
            continue
        # Strip common suffixes like " - Job ID: 12345" from Google titles
        import re as _re
        clean_title = _re.sub(r"\s*[-–—]\s*Job\s+ID:\s*\d+\s*$", "", title).strip()
        key = canonical_url_key(url)
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        hits.append(SearchHit(
            company=company.name,
            role_title=clean_title,
            url=url,
            location=None,
            source_query=r.get("source_query"),
        ))
    return hits


def _parse_hits(raw: list, company: TrackedCompany) -> list[SearchHit]:
    """Parse LLM-structured results into SearchHits with dedup."""
    hits: list[SearchHit] = []
    seen_keys: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = (item.get("url") or "").strip()
        title = (item.get("role_title") or "").strip()
        company_name = (item.get("company") or company.name).strip()
        if not url or not title:
            continue
        url = normalize_listing_url(url)
        key = canonical_url_key(url)
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        hits.append(SearchHit(
            company=company_name,
            role_title=title,
            url=url,
            location=(item.get("location") or None),
            source_query=(item.get("source_query") or None),
        ))
    return hits


async def _execute_via_llm_search(
    db: Session,
    profile: Profile,
    company: TrackedCompany,
    plan: dict,
) -> list[SearchHit]:
    """Original LLM web search path — used as fallback when Google isn't configured."""
    queries = plan.get("queries") or []
    prompt = _run_queries_prompt(queries, plan.get("careers_site", ""))
    provider = get_provider(profile)

    response = await provider.complete_with_search(
        system=(
            "You run the user-supplied web searches with your native web_search tool "
            "and collect ALL result rows (typically 10-30 per query). Prefer direct "
            "careers-site URLs. Return a single JSON object. Do NOT pre-filter or "
            "score — downstream code handles that."
        ),
        user=prompt,
        max_tokens=4000,
        temperature=0.2,
    )
    log_usage(db, profile.id, "ai_monitor_search", response)

    try:
        parsed = _extract_json(response.text)
    except Exception as exc:
        logger.warning(f"AI monitor search JSON parse failed for {company.name}: {exc}")
        return []

    raw = parsed.get("listings") if isinstance(parsed, dict) else parsed
    if not isinstance(raw, list):
        return []

    hits = _parse_hits(raw, company)

    # Liveness check — quick HEAD requests to drop dead/filled listings.
    # The LLM's search index often lags by weeks, returning positions that
    # have since been filled. A HEAD request catches 404/410/redirect-to-search.
    if hits:
        hits = await _filter_dead_urls(hits)

    return hits


async def _filter_dead_urls(hits: list[SearchHit]) -> list[SearchHit]:
    """Parallel HEAD requests to verify URLs are still live.

    Drops hits that return 404, 410, or redirect to a generic search/home page.
    Keeps hits where the check fails (timeout, connection error) — fail-open.
    """
    import httpx

    async def _check(hit: SearchHit) -> tuple[SearchHit, bool]:
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=8.0,
                headers={"User-Agent": "Mozilla/5.0 (compatible; LaunchPad/1.0)"},
            ) as client:
                resp = await client.head(hit.url)
                # 404/410 = definitely dead
                if resp.status_code in (404, 410):
                    logger.info(f"Liveness check: {hit.url} → {resp.status_code} (dead)")
                    return hit, False
                # Some ATS platforms redirect filled jobs to the main search page.
                # Detect by checking if the final URL lost the job-specific path.
                final = str(resp.url)
                if resp.status_code in (301, 302, 303, 307, 308) or final != hit.url:
                    # If redirected to a page that no longer contains the job ID,
                    # it's likely a "this job is no longer available" redirect.
                    job_key = canonical_url_key(hit.url)
                    final_key = canonical_url_key(final)
                    if job_key and final_key and job_key != final_key:
                        # The redirect went somewhere else entirely
                        logger.info(f"Liveness check: {hit.url} → redirected to {final} (likely dead)")
                        return hit, False
                return hit, True
        except Exception as exc:
            # Fail-open: network error, timeout, etc. — keep the hit
            logger.debug(f"Liveness check failed for {hit.url}: {exc}")
            return hit, True

    results = await asyncio.gather(*[_check(h) for h in hits])
    alive = [hit for hit, live in results if live]
    dead_count = len(hits) - len(alive)
    if dead_count:
        logger.info(f"Liveness check: {dead_count}/{len(hits)} URLs appear dead/filled, dropped")
    return alive


# --- Main entry point ---------------------------------------------------------

def _detect_job_type(location: Optional[str]) -> str:
    if not location:
        return "Onsite"
    low = location.lower()
    if "remote" in low:
        return "Remote"
    if "hybrid" in low:
        return "Hybrid"
    return "Onsite"


async def run_ai_monitor_for_company(
    db: Session,
    profile: Profile,
    company: TrackedCompany,
    *,
    trigger: str = "manual",
    auto_evaluate: Optional[bool] = None,
) -> AIMonitorRun:
    """Full pipeline for one company.

    1. Ensure a query plan exists (generate if missing/stale)
    2. Execute the plan via web search
    3. URL-dedupe across queries
    4. Title-filter against profile's positive/negative keywords
    5. DB-dedupe against existing Listings by canonical_url_key
    6. Create new Listings for the survivors
    7. Record everything (kept / filtered / deduped) in an AIMonitorRun row
    8. Optionally auto-evaluate kept listings

    Returns the persisted AIMonitorRun.
    """
    run = AIMonitorRun(
        profile_id=profile.id,
        tracked_company_id=company.id,
        trigger=trigger,
        queries_used=[],
        all_listings=[],
        kept_listings=[],
        filtered_listings=[],
        deduped_listings=[],
    )
    db.add(run)
    db.commit()

    try:
        plan = await ensure_query_plan(db, profile, company)
        run.queries_used = plan.get("queries") or []
        db.commit()

        hits = await _execute_query_plan(db, profile, company, plan)
        run.all_listings = [
            {
                "company": h.company,
                "role_title": h.role_title,
                "url": h.url,
                "location": h.location,
                "source_query": h.source_query,
            }
            for h in hits
        ]
        run.total_found = len(hits)

        # Title filter (same deterministic function used by Gmail + ATS scanner)
        positive = profile.title_positive_keywords or []
        negative = profile.title_negative_keywords or []
        passes: list[SearchHit] = []
        filtered: list[dict] = []
        for h in hits:
            reason = why_title_fails(h.role_title, positive, negative)
            if reason is None:
                passes.append(h)
            else:
                filtered.append({
                    "company": h.company,
                    "role_title": h.role_title,
                    "url": h.url,
                    "location": h.location,
                    "source_query": h.source_query,
                    "reason": reason,
                })

        # Optional smart-title-filter pass — profile opt-in. Runs after the
        # cheap keyword filter so we never spend LLM cost on already-rejected
        # titles. "no" verdicts join the filtered_listings array with a
        # "smart filter:" prefix so the user can still "Add anyway" from
        # the run detail modal.
        smart_on = bool(getattr(profile, "smart_title_filter_enabled", False))
        smart_verdicts: dict[int, "object"] = {}
        if smart_on and passes and profile.llm_api_key_enc:
            try:
                from app.services.smart_title_filter import classify_titles
                items = [
                    {"title": h.role_title, "company": h.company}
                    for h in passes
                ]
                smart_verdicts = await classify_titles(db, profile, items)
            except Exception as exc:
                logger.warning(
                    f"Smart filter pass failed for {company.name}: {exc}"
                )
                smart_verdicts = {}
        # Attach verdict-survival info to hits so we can persist verdict on the
        # Listing row at create time below.
        smart_attrs_by_idx: dict[int, tuple[Optional[str], Optional[str]]] = {}
        if smart_on and smart_verdicts:
            kept_passes: list[SearchHit] = []
            for idx, h in enumerate(passes):
                v = smart_verdicts.get(idx)
                if v is not None and v.verdict == "no":
                    filtered.append({
                        "company": h.company,
                        "role_title": h.role_title,
                        "url": h.url,
                        "location": h.location,
                        "source_query": h.source_query,
                        "reason": f"smart filter: {v.reason or 'off-target'}",
                    })
                    continue
                if v is not None:
                    smart_attrs_by_idx[len(kept_passes)] = (v.verdict, v.reason)
                kept_passes.append(h)
            passes = kept_passes

        run.filtered_listings = filtered
        run.filtered_count = len(filtered)

        # DB dedup — build set of existing canonical keys for this profile in one query
        existing_urls = [
            u for (u,) in db.query(Listing.url)
            .filter(Listing.profile_id == profile.id, Listing.url.isnot(None))
            .all()
        ]
        existing_keys: set[str] = {canonical_url_key(u) for u in existing_urls}

        kept: list[SearchHit] = []
        deduped: list[dict] = []
        for h in passes:
            key = canonical_url_key(h.url)
            if key in existing_keys:
                deduped.append({
                    "company": h.company,
                    "role_title": h.role_title,
                    "url": h.url,
                })
                continue
            kept.append(h)
            existing_keys.add(key)
        run.deduped_listings = deduped
        run.deduped_count = len(deduped)

        # Create listings
        created_listings: list[Listing] = []
        for h in kept:
            # Recover the smart verdict by locating this hit back in `passes`.
            # passes and smart_attrs_by_idx were aligned BEFORE the DB-dedup
            # step, so we look up by URL to get the right attrs.
            sv_verdict: Optional[str] = None
            sv_reason: Optional[str] = None
            if smart_attrs_by_idx:
                for pi, ph in enumerate(passes):
                    if ph.url == h.url and pi in smart_attrs_by_idx:
                        sv_verdict, sv_reason = smart_attrs_by_idx[pi]
                        break
            listing = Listing(
                profile_id=profile.id,
                url=h.url,
                source="ai_monitor",
                source_detail=company.name,
                company=h.company or company.name,
                role_title=h.role_title,
                location=h.location,
                job_type=_detect_job_type(h.location),
                status="new",
                smart_filter_verdict=sv_verdict,
                smart_filter_reason=sv_reason,
            )
            db.add(listing)
            created_listings.append(listing)
        db.commit()

        run.kept_listings = [
            {
                "company": h.company,
                "role_title": h.role_title,
                "url": h.url,
                "location": h.location,
                "source_query": h.source_query,
            }
            for h in kept
        ]
        run.kept_count = len(kept)
        run.created_listing_ids = [l.id for l in created_listings]

        # Update company rollup
        company.last_ai_monitor_at = datetime.utcnow()
        company.last_ai_monitor_count = run.kept_count
        db.commit()

        # Optional auto-evaluate
        should_eval = profile.auto_evaluate if auto_evaluate is None else auto_evaluate
        if should_eval and profile.llm_api_key_enc and created_listings:
            from app.services.evaluation import evaluate_listing
            for listing in created_listings:
                try:
                    await evaluate_listing(db, profile, listing)
                except Exception as exc:
                    logger.warning(
                        f"AI monitor auto-eval failed for listing {listing.id}: {exc}"
                    )

    except Exception as exc:
        logger.exception(f"AI monitor run failed for company {company.name}")
        run.error = str(exc)[:500]
    finally:
        run.finished_at = datetime.utcnow()
        db.commit()
        db.refresh(run)

    logger.info(
        f"AI monitor run for {company.name}: found={run.total_found} "
        f"kept={run.kept_count} filtered={run.filtered_count} deduped={run.deduped_count}"
    )
    return run


async def run_ai_monitor_for_profile(
    db: Session,
    profile: Profile,
    *,
    trigger: str = "scheduled",
    auto_evaluate: Optional[bool] = None,
) -> list[AIMonitorRun]:
    """Run AI monitor across ALL ai_monitor_enabled companies for the profile."""
    companies = (
        db.query(TrackedCompany)
        .filter(
            TrackedCompany.profile_id == profile.id,
            TrackedCompany.enabled.is_(True),
            TrackedCompany.ai_monitor_enabled.is_(True),
        )
        .all()
    )
    if not companies:
        return []
    runs: list[AIMonitorRun] = []
    for c in companies:
        try:
            run = await run_ai_monitor_for_company(
                db, profile, c, trigger=trigger, auto_evaluate=auto_evaluate,
            )
            runs.append(run)
        except Exception as exc:
            logger.exception(f"AI monitor failed for {c.name}: {exc}")
    return runs
