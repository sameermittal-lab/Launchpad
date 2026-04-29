"""Sync emails from a connected Gmail account, classify, optionally extract listings."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models import EmailMessage, GmailAccount, Profile
from app.prompts import render_prompt
from app.services.evaluation import _extract_json
from app.services.gmail.client import GmailClient, ParsedMessage
from app.services.llm import get_provider
from app.services.usage_tracker import log_usage

logger = logging.getLogger(__name__)

VALID_CATEGORIES = {
    "linkedin_alert",
    "job_alert",    # platform-neutral alert category set by the sender fast-path
    "recruiter",
    "app_update",
    "rejection",
    "offer",
    "other",
}


def _sender_matches_priority_list(from_email: Optional[str], senders: list[str]) -> bool:
    """True if the email's From address matches any entry in the trusted senders list.

    Each entry can be:
      - a full email like "alert@indeed.com"     → case-insensitive exact match
      - a domain-prefixed entry like "@indeed.com" → case-insensitive endswith match
    Any entry without an "@" is ignored to prevent silly mistakes like whitelisting
    the entire TLD.
    """
    if not from_email or not senders:
        return False
    f = from_email.strip().lower()
    for raw in senders:
        if not raw or not isinstance(raw, str):
            continue
        entry = raw.strip().lower()
        if "@" not in entry:
            continue
        if entry.startswith("@"):
            if f.endswith(entry):
                return True
        else:
            if f == entry:
                return True
    return False


def _profile_role_context(profile: Profile) -> tuple[Optional[str], Optional[str]]:
    """Extract target-role strings to inject into the extraction prompt."""
    pd = (profile.profile_data or {}) if hasattr(profile, "profile_data") else {}
    tr = pd.get("target_roles")
    if isinstance(tr, list) and tr:
        target_roles = ", ".join(str(t) for t in tr if t)
    elif isinstance(tr, str) and tr.strip():
        target_roles = tr.strip()
    else:
        target_roles = None
    current_role = (profile.role_title or "").strip() or None
    return target_roles, current_role


def _reconcile_summary(llm_summary: Optional[str], meta: dict) -> Optional[str]:
    """Rewrite the LLM's summary so its counts match what actually landed.

    The LLM often over-counts (says "4 relevant" when it emits 3), and its idea
    of "mismatched" doesn't include our deterministic title filter. We keep the
    LLM's descriptive opening if present and append authoritative post-processing
    stats that the user can trust.
    """
    kept = int(meta.get("kept") or 0)
    filtered = int(meta.get("filtered_by_policy") or 0)
    llm_claimed = int(meta.get("llm_claimed") or 0)
    no_url = int(meta.get("no_url") or 0)
    dupes = int(meta.get("dupes") or 0)

    # Start with a short factual sentence about what actually happened.
    if llm_claimed == 0 and kept == 0 and filtered == 0:
        # Not a job-alert at all — keep whatever the LLM said verbatim.
        return (llm_summary or "").strip() or None

    parts: list[str] = []
    if kept > 0:
        parts.append(f"Extracted {kept} matching role{'s' if kept != 1 else ''}")
    else:
        parts.append("No matching roles extracted")
    drop_bits: list[str] = []
    if filtered:
        drop_bits.append(f"{filtered} filtered by your rules")
    if no_url:
        drop_bits.append(f"{no_url} had no URL")
    if dupes:
        drop_bits.append(f"{dupes} already in pipeline")
    if drop_bits:
        parts[0] += " \u00b7 " + "; ".join(drop_bits)

    header = parts[0] + "."

    # Keep a trimmed descriptive tail from the LLM output if available and
    # non-contradictory. We strip any "N relevant / skipped X" phrasing the LLM
    # added since our counts override those claims.
    descriptive: Optional[str] = None
    if llm_summary:
        import re as _re
        txt = llm_summary.strip()
        # Remove any sentence that contains self-reported counts we're overriding.
        # Split on sentence-ish boundaries, drop sentences that talk about counts,
        # keep the rest. This is heavy-handed on purpose — we never want the
        # LLM's numbers shown alongside our authoritative numbers.
        sentences = _re.split(r"(?<=[.!?])\s+", txt)
        kept_sentences = []
        for s in sentences:
            low = s.lower()
            if _re.search(r"\b\d+\s+(?:relevant|matching|mismatched|skipped|filtered|new|roles?|positions?|listings?)\b", low):
                continue
            if _re.search(r"\bskipped\s+\d+", low):
                continue
            kept_sentences.append(s)
        descriptive = " ".join(kept_sentences).strip() or None
    if descriptive:
        return f"{header} {descriptive}".strip()
    return header


@dataclass
class SyncResult:
    account_email: str
    fetched: int
    new: int
    classified: int
    listings_extracted: int
    error: Optional[str] = None


def _build_search_query(last_synced: Optional[datetime]) -> str:
    """Gmail search query for messages to fetch.

    Limits to 7-day window for first sync, else incremental since last.
    """
    if last_synced is None:
        return "newer_than:7d in:inbox"
    days = max(1, min(30, (datetime.utcnow() - last_synced).days + 1))
    return f"newer_than:{days}d in:inbox"


async def _classify_batch(
    db: Session,
    profile: Profile,
    msgs: list[ParsedMessage],
) -> dict[str, str]:
    """Classify messages with the LLM. Returns {gmail_id: category}."""
    if not msgs:
        return {}
    # Build a simplified list for the prompt
    email_summaries = [
        {
            "from_name": m.from_name or m.from_email,
            "from_email": m.from_email,
            "subject": m.subject,
            "snippet": (m.snippet or m.body_text[:300])[:400],
        }
        for m in msgs
    ]
    prompt = render_prompt("email_classify.md.j2", emails=email_summaries)
    provider = get_provider(profile)
    response = await provider.complete(
        system="You classify emails into job-search categories. Output JSON array only.",
        user=prompt,
        max_tokens=1500,
        temperature=0.1,
    )
    log_usage(db, profile.id, "email_classify", response)

    try:
        result = _extract_json(response.text)
    except Exception as exc:
        logger.error(f"Email classification JSON parse failed: {exc}")
        return {}

    # Expecting list of {index: 1, category: "..."}
    mapping: dict[str, str] = {}
    for item in result if isinstance(result, list) else []:
        idx = item.get("index")
        cat = item.get("category", "other")
        if cat not in VALID_CATEGORIES:
            cat = "other"
        if isinstance(idx, int) and 1 <= idx <= len(msgs):
            mapping[msgs[idx - 1].gmail_id] = cat
    return mapping


async def _extract_listings_from_email(
    db: Session,
    profile: Profile,
    msg: ParsedMessage,
) -> tuple[list[dict], Optional[str], list[dict], int, int]:
    """Use LLM to pull (company, role, url) tuples AND a 1-sentence summary.

    Returns (kept, summary, dropped_by_policy, no_url_count, llm_claimed_count).
    """
    target_roles_str, current_role_str = _profile_role_context(profile)
    prompt = render_prompt(
        "extract_jobs_from_email.md.j2",
        email={
            "from_name": msg.from_name,
            "from_email": msg.from_email,
            "subject": msg.subject,
            "body_text": msg.body_text[:8000],
        },
        candidate_target_roles=target_roles_str,
        candidate_current_role=current_role_str,
        positive_keywords=profile.title_positive_keywords or [],
        negative_keywords=profile.title_negative_keywords or [],
    )
    provider = get_provider(profile)
    response = await provider.complete(
        system="You extract structured job listings AND a 1-sentence summary. Filter strictly by the candidate's target roles. Output a single JSON object only.",
        user=prompt,
        max_tokens=1500,
        temperature=0.1,
    )
    log_usage(db, profile.id, "extract_jobs_from_email", response)

    try:
        result = _extract_json(response.text)
    except Exception as exc:
        logger.warning(f"Email listing extraction parse failed: {exc}")
        return [], None, [], 0, 0

    summary = None
    raw_listings = []
    if isinstance(result, dict):
        summary = (result.get("summary") or "").strip() or None
        raw_listings = result.get("listings") or []
    elif isinstance(result, list):
        raw_listings = result

    llm_claimed = len(raw_listings) if isinstance(raw_listings, list) else 0

    from app.services.filters import why_title_fails
    pos = profile.title_positive_keywords or []
    neg = profile.title_negative_keywords or []

    kept = []
    dropped: list[dict] = []
    no_url = 0
    for item in raw_listings if isinstance(raw_listings, list) else []:
        if not isinstance(item, dict):
            continue
        if not item.get("url") or not item.get("company") or not item.get("role_title"):
            no_url += 1
            continue
        reason = why_title_fails(item["role_title"], pos, neg)
        if reason is not None:
            dropped.append({
                "company": item.get("company"),
                "role_title": item.get("role_title"),
                "url": item.get("url"),
                "reason": reason,
            })
            continue
        kept.append({
            "company": item["company"],
            "role_title": item["role_title"],
            "url": item["url"],
            "location": item.get("location"),
        })
    return kept, summary, dropped, no_url, llm_claimed


async def extract_listings_from_stored_message(
    db: Session,
    profile: Profile,
    email_msg: "EmailMessage",
) -> tuple[list[dict], int]:
    """Run listing extraction on an email already stored in the DB.

    Returns (extracted_payloads, new_listings_created).
    """
    from app.models import Listing
    if not profile.llm_api_key_enc:
        raise ValueError("No LLM API key configured for this profile")
    if not email_msg.body_text:
        # Still mark processed so the user doesn't keep clicking Extract on empty emails
        email_msg.extracted_listings = []
        email_msg.processed = True
        db.commit()
        return [], 0

    # Build the same prompt shape as the fresh-sync path, but ask for a summary too
    target_roles_str, current_role_str = _profile_role_context(profile)
    prompt = render_prompt(
        "extract_jobs_from_email.md.j2",
        email={
            "from_name": email_msg.from_name,
            "from_email": email_msg.from_email,
            "subject": email_msg.subject,
            "body_text": (email_msg.body_text or "")[:8000],
        },
        candidate_target_roles=target_roles_str,
        candidate_current_role=current_role_str,
        positive_keywords=profile.title_positive_keywords or [],
        negative_keywords=profile.title_negative_keywords or [],
    )
    provider = get_provider(profile)
    response = await provider.complete(
        system="You extract structured job listings from email text, filtered by the candidate's target roles, AND produce a 1-sentence summary of what the email is about. Output JSON with shape {summary: string, listings: [...]}.",
        user=prompt,
        max_tokens=1500,
        temperature=0.1,
    )
    log_usage(db, profile.id, "extract_jobs_from_email", response)

    try:
        result = _extract_json(response.text)
    except Exception as exc:
        logger.warning(f"Listing extraction parse failed for email {email_msg.id}: {exc}")
        # Mark processed to avoid retries on unparseable emails
        email_msg.extracted_listings = []
        email_msg.processed = True
        db.commit()
        return [], 0

    # Accept either old shape (list) or new shape ({summary, listings})
    raw_listings = []
    summary = None
    if isinstance(result, dict):
        summary = (result.get("summary") or "").strip() or None
        raw_listings = result.get("listings") or []
    elif isinstance(result, list):
        raw_listings = result

    llm_claimed = len(raw_listings) if isinstance(raw_listings, list) else 0

    # Hard deterministic filter using the user's positive/negative title keywords.
    # This is a safety net — the LLM already knows to filter (via prompt), but we
    # enforce here too so a keyword like "engineer" is respected no matter what.
    from app.services.filters import title_passes_filter, why_title_fails
    pos = profile.title_positive_keywords or []
    neg = profile.title_negative_keywords or []

    extracted = []
    auto_dropped: list[dict] = []  # visible in UI as "filtered by your rules"
    no_url_dropped = 0  # LLM proposed but had no URL
    for item in raw_listings if isinstance(raw_listings, list) else []:
        if not isinstance(item, dict):
            continue
        if not item.get("url") or not item.get("company") or not item.get("role_title"):
            # Missing a required field — most commonly url. Track so the UI
            # can show "LLM claimed N, kept M, K had no URL."
            no_url_dropped += 1
            continue
        reason = why_title_fails(item["role_title"], pos, neg)
        if reason is not None:
            auto_dropped.append({
                "company": item.get("company"),
                "role_title": item.get("role_title"),
                "url": item.get("url"),
                "reason": reason,
            })
            continue
        extracted.append({
            "company": item["company"],
            "role_title": item["role_title"],
            "url": item["url"],
            "location": item.get("location"),
        })

    # Persist results — ALWAYS mark processed, even if zero listings
    email_msg.extracted_listings = extracted
    email_msg.filtered_listings = auto_dropped or None
    email_msg.processed = True
    # Note: ai_summary is set below after extraction_meta so we can reconcile
    # the LLM's self-reported counts with the real post-filter numbers.
    if auto_dropped:
        logger.info(
            f"Email {email_msg.id}: auto-filtered {len(auto_dropped)} listings "
            f"(sample: {[d['role_title'] + ' — ' + d['reason'] for d in auto_dropped[:3]]})"
        )

    # Create Listing rows with URL normalization for dedup
    created = 0
    dupes = 0
    account_email = None
    if email_msg.gmail_account_id:
        acct = db.get(GmailAccount, email_msg.gmail_account_id)
        account_email = acct.email if acct else None
    for entry in extracted:
        raw_url = entry["url"]
        canonical = _canonicalize_url(raw_url)
        # Dedup: try canonical match first, fall back to exact string match
        existing = _find_matching_listing(db, profile.id, entry["company"], entry["role_title"], canonical, raw_url)
        if existing:
            dupes += 1
            # Merge source info — if this listing was manual, note it's also in Gmail now
            detail = (existing.source_detail or "").strip()
            add = f"also seen in Gmail ({account_email})" if account_email else "also seen in Gmail"
            if add not in detail:
                existing.source_detail = (detail + " · " + add) if detail else add
            continue
        db.add(Listing(
            profile_id=profile.id,
            url=raw_url,
            source="gmail",
            source_detail=account_email or "",
            company=entry["company"],
            role_title=entry["role_title"],
            location=entry.get("location"),
            status="new",
        ))
        created += 1

    # Capture post-processing metadata so the UI can show "LLM claimed 4, kept 3".
    # This number captures what actually landed in the pipeline (created), plus
    # what was dropped for each reason.
    email_msg.extraction_meta = {
        "llm_claimed": llm_claimed,
        "kept": len(extracted),
        "created": created,
        "dupes": dupes,
        "filtered_by_policy": len(auto_dropped),
        "no_url": no_url_dropped,
    }
    # Rewrite summary with authoritative counts (don't trust the LLM's self-reported numbers).
    email_msg.ai_summary = _reconcile_summary(summary, email_msg.extraction_meta)

    # Update GmailAccount stats
    if email_msg.gmail_account_id:
        acct = db.get(GmailAccount, email_msg.gmail_account_id)
        if acct:
            acct.last_extraction_at = datetime.utcnow()
            acct.last_extraction_count = created
            acct.lifetime_listings_extracted = (acct.lifetime_listings_extracted or 0) + created
    db.commit()
    return extracted, created


def _canonicalize_url(url: Optional[str]) -> Optional[str]:
    """Normalize a URL for dedup: lowercase host, strip tracking params, strip trailing slash."""
    if not url:
        return None
    try:
        from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
        parts = urlsplit(url.strip())
        host = (parts.netloc or "").lower()
        path = (parts.path or "").rstrip("/")
        query_pairs = [
            (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
            if not k.lower().startswith(("utm_", "trk", "_ga", "gh_src", "mc_"))
            and k.lower() not in {"ref", "source", "refid", "source_id", "ref_src"}
        ]
        query = urlencode(sorted(query_pairs))
        return urlunsplit((parts.scheme.lower(), host, path, query, ""))
    except Exception:
        return url.strip().lower()


def _find_matching_listing(
    db: Session,
    profile_id: int,
    company: str,
    role_title: str,
    canonical_url: Optional[str],
    raw_url: Optional[str],
):
    """Find an existing listing that matches by URL or by (company, role) pair."""
    from app.models import Listing
    # 1) exact URL match
    if raw_url:
        hit = (
            db.query(Listing)
            .filter(Listing.profile_id == profile_id, Listing.url == raw_url)
            .first()
        )
        if hit:
            return hit
    # 2) canonical URL match against any stored listing
    if canonical_url:
        for l in (
            db.query(Listing)
            .filter(Listing.profile_id == profile_id, Listing.url.isnot(None))
            .all()
        ):
            if _canonicalize_url(l.url) == canonical_url:
                return l
    # 3) (company, role_title) exact-ish match — case-insensitive
    hit = (
        db.query(Listing)
        .filter(
            Listing.profile_id == profile_id,
            func_lower(Listing.company) == company.strip().lower(),
            func_lower(Listing.role_title) == role_title.strip().lower(),
        )
        .first()
    )
    return hit


def func_lower(col):
    from sqlalchemy import func
    return func.lower(col)


async def sync_account(
    db: Session,
    profile: Profile,
    account: GmailAccount,
    max_messages: int = 50,
    auto_extract_listings: bool = True,
) -> SyncResult:
    """Fetch new messages, classify, extract listings from relevant ones."""
    result = SyncResult(
        account_email=account.email,
        fetched=0,
        new=0,
        classified=0,
        listings_extracted=0,
    )
    try:
        client = GmailClient(db, account)
    except Exception as exc:
        result.error = f"Could not connect Gmail client: {exc}"
        return result

    query = _build_search_query(account.last_synced_at)
    ids = client.search(query, max_results=max_messages)
    result.fetched = len(ids)

    # Dedup against what we already have
    existing_ids = {
        i for (i,) in db.query(EmailMessage.gmail_message_id)
        .filter(EmailMessage.profile_id == profile.id)
        .filter(EmailMessage.gmail_message_id.in_(ids))
        .all()
    }
    new_ids = [i for i in ids if i not in existing_ids]
    if not new_ids:
        account.last_synced_at = datetime.utcnow()
        db.commit()
        return result

    # Fetch full messages
    parsed_msgs: list[ParsedMessage] = []
    for gid in new_ids:
        try:
            parsed_msgs.append(client.get_message(gid))
        except Exception as exc:
            logger.warning(f"Failed to fetch gmail msg {gid}: {exc}")
    result.new = len(parsed_msgs)

    # First pass — sender fast-path. Any message from a trusted sender is
    # promoted straight to "job_alert" without an LLM classifier call.
    trusted_senders = list(getattr(profile, "job_alert_senders", []) or [])
    classifications: dict[str, str] = {}
    fast_path_ids: set[str] = set()
    if trusted_senders:
        for m in parsed_msgs:
            if _sender_matches_priority_list(m.from_email, trusted_senders):
                classifications[m.gmail_id] = "job_alert"
                fast_path_ids.add(m.gmail_id)

    # Classify the remaining messages in one batch (if LLM key exists)
    if profile.llm_api_key_enc and parsed_msgs:
        to_classify = [m for m in parsed_msgs if m.gmail_id not in fast_path_ids]
        if to_classify:
            try:
                llm_classifications = await _classify_batch(db, profile, to_classify)
                classifications.update(llm_classifications)
                result.classified = len(llm_classifications) + len(fast_path_ids)
            except Exception as exc:
                logger.warning(f"Classification failed: {exc}")
                result.classified = len(fast_path_ids)
        else:
            # Everything was sender-matched — no LLM needed
            result.classified = len(fast_path_ids)

    # Save each message
    for msg in parsed_msgs:
        category = classifications.get(msg.gmail_id, "other")
        db_msg = EmailMessage(
            profile_id=profile.id,
            gmail_account_id=account.id,
            gmail_message_id=msg.gmail_id,
            from_email=msg.from_email,
            from_name=msg.from_name,
            subject=msg.subject,
            snippet=msg.snippet,
            body_text=msg.body_text[:10000],
            received_at=msg.received_at,
            category=category,
            processed=False,
        )
        db.add(db_msg)

    db.commit()

    # Extract listings from recruiter / linkedin_alert / job_alert emails (opt-in)
    if auto_extract_listings and profile.llm_api_key_enc:
        for msg in parsed_msgs:
            cat = classifications.get(msg.gmail_id, "other")
            if cat not in ("linkedin_alert", "job_alert", "recruiter"):
                continue
            try:
                extracted, summary, dropped, no_url, llm_claimed = await _extract_listings_from_email(db, profile, msg)
            except Exception as exc:
                logger.warning(f"Listing extraction failed for {msg.gmail_id}: {exc}")
                continue
            # Update the email record (always mark processed so UI doesn't keep nagging)
            stored = (
                db.query(EmailMessage)
                .filter(EmailMessage.gmail_message_id == msg.gmail_id)
                .first()
            )
            created_for_this_email = 0
            dupes_for_this_email = 0
            if not extracted:
                # Even with zero extracted we still want to persist metadata
                if stored:
                    stored.extracted_listings = []
                    stored.filtered_listings = dropped or None
                    stored.processed = True
                    stored.extraction_meta = {
                        "llm_claimed": llm_claimed,
                        "kept": 0,
                        "created": 0,
                        "dupes": 0,
                        "filtered_by_policy": len(dropped),
                        "no_url": no_url,
                    }
                    stored.ai_summary = _reconcile_summary(summary, stored.extraction_meta)
                continue
            result.listings_extracted += len(extracted)
            # Create Listing rows with dedup
            from app.models import Listing
            for entry in extracted:
                canonical = _canonicalize_url(entry["url"])
                existing = _find_matching_listing(
                    db, profile.id, entry["company"], entry["role_title"], canonical, entry["url"]
                )
                if existing:
                    dupes_for_this_email += 1
                    detail = (existing.source_detail or "").strip()
                    add = f"also seen in Gmail ({account.email})"
                    if add not in detail:
                        existing.source_detail = (detail + " · " + add) if detail else add
                    continue
                db.add(Listing(
                    profile_id=profile.id,
                    url=entry["url"],
                    source="gmail",
                    source_detail=f"{account.email}",
                    company=entry["company"],
                    role_title=entry["role_title"],
                    location=entry.get("location"),
                    status="new",
                ))
                created_for_this_email += 1
            if stored:
                stored.extracted_listings = extracted
                stored.filtered_listings = dropped or None
                stored.processed = True
                stored.extraction_meta = {
                    "llm_claimed": llm_claimed,
                    "kept": len(extracted),
                    "created": created_for_this_email,
                    "dupes": dupes_for_this_email,
                    "filtered_by_policy": len(dropped),
                    "no_url": no_url,
                }
                stored.ai_summary = _reconcile_summary(summary, stored.extraction_meta)
        # Update account extraction stats
        if result.listings_extracted > 0:
            account.last_extraction_at = datetime.utcnow()
            account.last_extraction_count = result.listings_extracted
            account.lifetime_listings_extracted = (account.lifetime_listings_extracted or 0) + result.listings_extracted

    account.last_synced_at = datetime.utcnow()
    db.commit()
    return result


async def sync_all_accounts(
    db: Session,
    profile: Profile,
    auto_extract_listings: bool = True,
) -> list[SyncResult]:
    """Sync all active Gmail accounts for a profile."""
    accounts = (
        db.query(GmailAccount)
        .filter(
            GmailAccount.profile_id == profile.id,
            GmailAccount.is_active.is_(True),
        )
        .all()
    )
    results: list[SyncResult] = []
    for account in accounts:
        try:
            r = await sync_account(db, profile, account, auto_extract_listings=auto_extract_listings)
        except Exception as exc:
            logger.exception(f"Sync failed for account {account.email}")
            r = SyncResult(
                account_email=account.email,
                fetched=0, new=0, classified=0, listings_extracted=0,
                error=str(exc),
            )
        results.append(r)
    return results
