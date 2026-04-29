"""Gmail API - credentials upload, OAuth flow, accounts management, feed, sync."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import EmailMessage, GmailAccount, Profile
from app.services.gmail import oauth
from app.services.gmail.sync import sync_all_accounts
from app.utils.session import get_current_profile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/gmail", tags=["gmail"])


# ------------------------- Schemas -------------------------


class GmailStatusResponse(BaseModel):
    has_client_credentials: bool
    accounts: list[dict]
    max_accounts: int
    unprocessed_count: int = 0


class GmailAccountResponse(BaseModel):
    id: int
    email: str
    is_active: bool
    last_synced_at: Optional[datetime] = None
    last_extraction_at: Optional[datetime] = None
    last_extraction_count: int = 0
    lifetime_listings_extracted: int = 0


class SyncResponse(BaseModel):
    results: list[dict]


class MessageSummary(BaseModel):
    id: int
    gmail_message_id: str
    from_email: Optional[str]
    from_name: Optional[str]
    subject: Optional[str]
    snippet: Optional[str]
    received_at: datetime
    category: str
    processed: bool
    gmail_account_email: str
    extracted_listings: Optional[list] = None
    filtered_listings: Optional[list] = None
    extraction_meta: Optional[dict] = None
    ai_summary: Optional[str] = None


class MessageDetail(MessageSummary):
    body_text: Optional[str] = None


# ------------------------- Status + credentials -------------------------


@router.get("/status", response_model=GmailStatusResponse)
def status(
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    accounts = [
        GmailAccountResponse(
            id=a.id,
            email=a.email,
            is_active=a.is_active,
            last_synced_at=a.last_synced_at,
            last_extraction_at=a.last_extraction_at,
            last_extraction_count=a.last_extraction_count or 0,
            lifetime_listings_extracted=a.lifetime_listings_extracted or 0,
        ).model_dump()
        for a in (profile.gmail_accounts or [])
    ]
    # Count emails that haven't been processed (converted to listings / acted on)
    unprocessed = (
        db.query(EmailMessage)
        .filter(EmailMessage.profile_id == profile.id)
        .filter(EmailMessage.processed == False)  # noqa: E712
        .count()
    )
    return GmailStatusResponse(
        has_client_credentials=oauth.has_client_credentials(profile),
        accounts=accounts,
        max_accounts=settings.max_gmail_accounts_per_profile,
        unprocessed_count=unprocessed,
    )


@router.post("/credentials")
async def upload_credentials(
    file: UploadFile = File(...),
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Upload the OAuth client credentials JSON from Google Cloud Console."""
    content = await file.read()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File is not UTF-8 JSON")
    try:
        oauth.save_client_credentials(db, profile, text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"saved": True}


@router.delete("/credentials")
def clear_credentials(
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Clear stored OAuth client credentials. Does not remove connected accounts."""
    oauth.clear_client_credentials(db, profile)
    return {"cleared": True}


# ------------------------- OAuth flow -------------------------


def _redirect_uri(request: Request) -> str:
    """Build the callback URL from the incoming request."""
    scheme = request.url.scheme
    host = request.headers.get("host") or request.url.netloc
    return f"{scheme}://{host}/api/gmail/callback"


@router.get("/connect")
def connect_start(
    request: Request,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Begin OAuth flow. Returns the Google auth URL for the frontend to redirect to."""
    if not oauth.has_client_credentials(profile):
        raise HTTPException(
            status_code=400,
            detail="Upload your Google OAuth credentials.json first",
        )

    # Simple CSRF protection - random secret stored in session (hack: use memory)
    state_secret = secrets.token_urlsafe(16)
    _STATE_SECRETS[profile.id] = state_secret

    try:
        url = oauth.get_auth_url(profile, _redirect_uri(request), state_secret)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"auth_url": url}


# In-memory state store, keyed by profile_id. Good enough for local/single-server.
_STATE_SECRETS: dict[int, str] = {}


@router.get("/callback", response_class=HTMLResponse)
def oauth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """OAuth callback from Google. State carries profile_id.

    Because this redirect comes from Google (not our frontend), we can't use the
    profile cookie directly - we rely on the state parameter.
    """
    if error:
        return _callback_html(False, f"Google returned: {error}")
    if not code or not state:
        return _callback_html(False, "Missing code or state")
    try:
        profile_id_str, _ = state.split(":", 1)
        profile_id = int(profile_id_str)
    except Exception:
        return _callback_html(False, "Invalid state")

    expected = _STATE_SECRETS.pop(profile_id, None)
    if not expected:
        return _callback_html(False, "Session expired. Please try connecting again.")

    profile = db.get(Profile, profile_id)
    if not profile:
        return _callback_html(False, "Profile not found")

    try:
        account = oauth.handle_callback(
            db=db,
            profile=profile,
            code=code,
            state=state,
            expected_state_secret=expected,
            redirect_uri=_redirect_uri(request),
            max_accounts=settings.max_gmail_accounts_per_profile,
        )
    except Exception as exc:
        return _callback_html(False, str(exc))

    return _callback_html(True, f"Connected {account.email}!")


def _callback_html(success: bool, message: str) -> HTMLResponse:
    emoji = "✨" if success else "⚠️"
    color = "var(--green)" if success else "var(--red)"
    status_text = "Success!" if success else "Connection Failed"
    html = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>Gmail Connection</title>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    body {{
      font-family: 'Plus Jakarta Sans', sans-serif;
      background: linear-gradient(135deg, #eef0ff 0%, #fce7f3 50%, #dbeafe 100%);
      display: flex; align-items: center; justify-content: center;
      min-height: 100vh; margin: 0;
    }}
    .card {{
      background: #fff; border-radius: 24px; padding: 40px;
      box-shadow: 0 30px 80px rgba(0,0,0,.12); text-align: center;
      max-width: 480px;
    }}
    .emoji {{ font-size: 56px; margin-bottom: 16px; }}
    h1 {{ color: #1a1d2e; margin: 0 0 12px; font-size: 24px; font-weight: 800; }}
    p {{ color: #5a6478; margin: 0 0 24px; font-size: 14px; }}
    a {{
      display: inline-block; padding: 12px 24px;
      background: linear-gradient(135deg, #6366f1, #818cf8);
      color: white; text-decoration: none; border-radius: 10px;
      font-weight: 600;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="emoji">{emoji}</div>
    <h1 style="color: {color}">{status_text}</h1>
    <p>{message}</p>
    <a href="/#settings">Return to LaunchPad</a>
  </div>
  <script>
    // Notify the opener tab (if this was opened in a new window)
    if (window.opener) {{
      window.opener.postMessage({{type: 'gmail_connected', success: {str(success).lower()}}}, '*');
      setTimeout(() => window.close(), 2000);
    }}
  </script>
</body>
</html>"""
    return HTMLResponse(content=html)


@router.delete("/accounts/{account_id}")
def disconnect_account(
    account_id: int,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    account = db.get(GmailAccount, account_id)
    if not account or account.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Account not found")
    oauth.revoke_account(db, account)
    # Hard delete so the same email can be re-added
    db.delete(account)
    db.commit()
    return {"removed": True}


# ------------------------- Sync -------------------------


@router.post("/sync", response_model=SyncResponse)
async def sync_now(
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Sync all active Gmail accounts for this profile."""
    results = await sync_all_accounts(db, profile)
    return SyncResponse(
        results=[
            {
                "account_email": r.account_email,
                "fetched": r.fetched,
                "new": r.new,
                "classified": r.classified,
                "listings_extracted": r.listings_extracted,
                "error": r.error,
            }
            for r in results
        ]
    )


# ------------------------- Feed -------------------------


@router.get("/messages", response_model=list[MessageSummary])
def list_messages(
    category: Optional[str] = None,
    limit: int = 100,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    q = db.query(EmailMessage, GmailAccount.email).join(
        GmailAccount, EmailMessage.gmail_account_id == GmailAccount.id
    ).filter(EmailMessage.profile_id == profile.id)
    if category:
        q = q.filter(EmailMessage.category == category)
    q = q.order_by(EmailMessage.received_at.desc()).limit(limit)

    out = []
    for msg, acct_email in q.all():
        out.append(MessageSummary(
            id=msg.id,
            gmail_message_id=msg.gmail_message_id,
            from_email=msg.from_email,
            from_name=msg.from_name,
            subject=msg.subject,
            snippet=msg.snippet,
            received_at=msg.received_at,
            category=msg.category,
            processed=msg.processed,
            gmail_account_email=acct_email,
            extracted_listings=msg.extracted_listings,
            filtered_listings=msg.filtered_listings,
            extraction_meta=msg.extraction_meta,
            ai_summary=msg.ai_summary,
        ))
    return out


@router.get("/messages/{email_id}", response_model=MessageDetail)
def get_message_detail(
    email_id: int,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Return full email including body_text — for the expand-in-place view."""
    msg = db.get(EmailMessage, email_id)
    if not msg or msg.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Email not found")
    acct = db.get(GmailAccount, msg.gmail_account_id) if msg.gmail_account_id else None
    return MessageDetail(
        id=msg.id,
        gmail_message_id=msg.gmail_message_id,
        from_email=msg.from_email,
        from_name=msg.from_name,
        subject=msg.subject,
        snippet=msg.snippet,
        received_at=msg.received_at,
        category=msg.category,
        processed=msg.processed,
        gmail_account_email=acct.email if acct else "",
        extracted_listings=msg.extracted_listings,
        filtered_listings=msg.filtered_listings,
        extraction_meta=msg.extraction_meta,
        ai_summary=msg.ai_summary,
        body_text=msg.body_text,
    )


# ------------------------- Listing extraction from stored emails -------------------------


class ExtractResult(BaseModel):
    email_id: int
    extracted: list[dict]
    new_listings_created: int


class BulkExtractResult(BaseModel):
    processed_emails: int
    total_extracted: int
    total_new_listings: int
    errors: list[dict]


@router.post("/messages/{email_id}/extract", response_model=ExtractResult)
async def extract_listings_from_email(
    email_id: int,
    force: bool = False,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Run (or re-run) listing extraction on a single stored email.

    Pass ?force=1 to reprocess an already-processed email (useful after prompt
    or HTML parser improvements — e.g., retrying old LinkedIn alerts whose URLs
    were stripped by the earlier parser).
    """
    from app.services.gmail.sync import extract_listings_from_stored_message

    msg = db.get(EmailMessage, email_id)
    if not msg or msg.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Email not found")
    if not profile.llm_api_key_enc:
        raise HTTPException(status_code=400, detail="Add an LLM API key in Settings first")

    if force:
        # Reset flags so the existing extract helper will run again
        msg.processed = False
        msg.extracted_listings = None
        db.commit()

    try:
        extracted, created = await extract_listings_from_stored_message(db, profile, msg)
    except Exception as exc:
        logger.exception("extract-single failed")
        raise HTTPException(status_code=500, detail=f"Extraction failed: {exc}")
    return ExtractResult(
        email_id=msg.id,
        extracted=extracted,
        new_listings_created=created,
    )


class PromoteResult(BaseModel):
    email_id: int
    listing_id: int
    promoted_entry: dict


@router.post("/messages/{email_id}/promote-filtered/{idx}", response_model=PromoteResult)
async def promote_filtered_listing(
    email_id: int,
    idx: int,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Take an item from filtered_listings[idx] and add it to the pipeline as a
    real Listing, updating extraction_meta and moving the entry into extracted_listings.

    This is the backing call for the "Add anyway" button in the Gmail expanded view.
    Uses the existing create_listing flow (with force_add=1 semantics) for dedup,
    auto-eval, and source-detail merging.
    """
    from app.models import Listing as ListingModel
    from app.services.gmail.sync import _canonicalize_url, _find_matching_listing

    msg = db.get(EmailMessage, email_id)
    if not msg or msg.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Email not found")
    filtered = list(msg.filtered_listings or [])
    if idx < 0 or idx >= len(filtered):
        raise HTTPException(status_code=404, detail="Filtered entry index out of range")
    entry = filtered[idx]
    url = entry.get("url")
    company = entry.get("company") or "(unknown)"
    role_title = entry.get("role_title") or "(unknown)"
    if not url:
        raise HTTPException(status_code=400, detail="Filtered entry has no URL to add")

    # Account detail for source_detail tag
    acct = db.get(GmailAccount, msg.gmail_account_id) if msg.gmail_account_id else None
    account_email = acct.email if acct else None

    # Dedup — reuse existing matching listing if present
    canonical = _canonicalize_url(url)
    existing = _find_matching_listing(db, profile.id, company, role_title, canonical, url)
    if existing:
        detail = (existing.source_detail or "").strip()
        tag = f"re-added from Gmail ({account_email})" if account_email else "re-added from Gmail (filter override)"
        if tag not in detail:
            existing.source_detail = (detail + " \u00b7 " + tag) if detail else tag
        listing = existing
    else:
        listing = ListingModel(
            profile_id=profile.id,
            url=url,
            source="gmail",
            source_detail=f"{account_email or ''} (filter override)".strip(),
            company=company,
            role_title=role_title,
            location=entry.get("location"),
            status="new",
        )
        db.add(listing)
    db.commit()
    db.refresh(listing)

    # Move the entry from filtered_listings -> extracted_listings
    extracted = list(msg.extracted_listings or [])
    extracted.append({
        "company": company,
        "role_title": role_title,
        "url": url,
        "location": entry.get("location"),
    })
    del filtered[idx]
    msg.extracted_listings = extracted
    msg.filtered_listings = filtered or None

    # Update meta
    meta = dict(msg.extraction_meta or {})
    meta["kept"] = int(meta.get("kept") or 0) + 1
    meta["filtered_by_policy"] = max(0, int(meta.get("filtered_by_policy") or 0) - 1)
    meta["manually_added"] = int(meta.get("manually_added") or 0) + 1
    msg.extraction_meta = meta
    db.commit()

    return PromoteResult(
        email_id=msg.id,
        listing_id=listing.id,
        promoted_entry={"company": company, "role_title": role_title, "url": url},
    )


@router.post("/extract-pending", response_model=BulkExtractResult)
async def extract_pending(
    include_processed_zero: bool = False,
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    """Sweep unprocessed linkedin_alert / recruiter emails and run extraction.

    Useful for catching emails that were classified before auto-extract was enabled
    (or before an LLM key existed), or if extraction failed silently during sync.

    Pass ?include_processed_zero=1 to ALSO retry emails that were processed with
    zero results — useful after prompt or HTML parser improvements (e.g., fixing
    the LinkedIn URL-preservation bug).
    """
    from app.services.gmail.sync import extract_listings_from_stored_message

    if not profile.llm_api_key_enc:
        raise HTTPException(status_code=400, detail="Add an LLM API key in Settings first")

    q = (
        db.query(EmailMessage)
        .filter(EmailMessage.profile_id == profile.id)
        .filter(EmailMessage.category.in_(["linkedin_alert", "job_alert", "recruiter"]))
    )
    if include_processed_zero:
        # include processed emails with empty extracted_listings
        # (either explicitly [] or null from a pre-fix extraction)
        from sqlalchemy import or_, func as sa_func
        q = q.filter(or_(
            EmailMessage.processed.is_(False),
            EmailMessage.extracted_listings.is_(None),
            sa_func.json_array_length(EmailMessage.extracted_listings) == 0,
        ))
    else:
        q = q.filter(EmailMessage.processed.is_(False))

    pending = q.order_by(EmailMessage.received_at.desc()).limit(50).all()

    total_extracted = 0
    total_created = 0
    errors: list[dict] = []
    for msg in pending:
        try:
            # Reset so the helper runs fresh
            if include_processed_zero:
                msg.processed = False
                msg.extracted_listings = None
                db.commit()
            extracted, created = await extract_listings_from_stored_message(db, profile, msg)
            total_extracted += len(extracted)
            total_created += created
        except Exception as exc:
            logger.warning(f"Extract failed for email {msg.id}: {exc}")
            errors.append({"email_id": msg.id, "subject": msg.subject, "error": str(exc)})

    return BulkExtractResult(
        processed_emails=len(pending),
        total_extracted=total_extracted,
        total_new_listings=total_created,
        errors=errors,
    )


# ------------------------- Pending count helper for UI banner -------------------------


class PendingCountResponse(BaseModel):
    count: int


@router.get("/pending-count", response_model=PendingCountResponse)
def pending_count(
    profile: Profile = Depends(get_current_profile),
    db: Session = Depends(get_db),
):
    n = (
        db.query(EmailMessage)
        .filter(EmailMessage.profile_id == profile.id)
        .filter(EmailMessage.processed.is_(False))
        .filter(EmailMessage.category.in_(["linkedin_alert", "job_alert", "recruiter"]))
        .count()
    )
    return PendingCountResponse(count=n)
