"""Gmail OAuth flow - per-profile credentials, per-account tokens.

Each user provides their own Google Cloud project credentials.json
(one-time setup), then uses it to authorize up to 3 Gmail accounts.
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from sqlalchemy.orm import Session

from app.models import GmailAccount, Profile
from app.services.secrets import decrypt, encrypt

logger = logging.getLogger(__name__)

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]


def has_client_credentials(profile: Profile) -> bool:
    return bool(profile.gmail_client_credentials_enc)


def _write_credentials_to_temp(profile: Profile) -> Path:
    """Google libraries expect a credentials.json on disk.

    Decrypt the stored JSON and write it to a temp file that we delete after.
    """
    if not profile.gmail_client_credentials_enc:
        raise RuntimeError("No Gmail client credentials uploaded for this profile")
    credentials_json = decrypt(profile.gmail_client_credentials_enc)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    )
    tmp.write(credentials_json)
    tmp.close()
    return Path(tmp.name)


def save_client_credentials(
    db: Session, profile: Profile, credentials_json: str
) -> None:
    """Validate and save the uploaded credentials.json for this profile."""
    try:
        parsed = json.loads(credentials_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Not valid JSON: {exc}")

    # Basic shape check - accept 'installed' or 'web' OAuth client types
    root_key = next(iter(parsed.keys()), None)
    if root_key not in ("installed", "web"):
        raise ValueError(
            "Expected an OAuth client JSON with 'installed' or 'web' at the top level"
        )
    section = parsed[root_key]
    required = {"client_id", "client_secret", "auth_uri", "token_uri"}
    missing = required - set(section.keys())
    if missing:
        raise ValueError(f"Missing required fields: {missing}")

    profile.gmail_client_credentials_enc = encrypt(credentials_json)
    db.commit()


def clear_client_credentials(db: Session, profile: Profile) -> None:
    profile.gmail_client_credentials_enc = None
    db.commit()


def get_auth_url(profile: Profile, redirect_uri: str, state_secret: str) -> str:
    """Build the Google OAuth authorization URL for this profile."""
    creds_path = _write_credentials_to_temp(profile)
    try:
        flow = Flow.from_client_secrets_file(
            str(creds_path),
            scopes=GMAIL_SCOPES,
            redirect_uri=redirect_uri,
        )
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
            state=f"{profile.id}:{state_secret}",
        )
        return auth_url
    finally:
        try:
            creds_path.unlink()
        except Exception:
            pass


def handle_callback(
    db: Session,
    profile: Profile,
    code: str,
    state: str,
    expected_state_secret: str,
    redirect_uri: str,
    max_accounts: int,
) -> GmailAccount:
    """Exchange auth code for credentials, save per-account token."""
    try:
        profile_id_str, secret = state.split(":", 1)
        if int(profile_id_str) != profile.id:
            raise ValueError("State profile mismatch")
    except Exception as exc:
        raise ValueError(f"Invalid state: {exc}")
    if secret != expected_state_secret:
        raise ValueError("State mismatch - possible CSRF")

    creds_path = _write_credentials_to_temp(profile)
    try:
        flow = Flow.from_client_secrets_file(
            str(creds_path),
            scopes=GMAIL_SCOPES,
            redirect_uri=redirect_uri,
        )
        flow.fetch_token(code=code)
    finally:
        try:
            creds_path.unlink()
        except Exception:
            pass

    creds: Credentials = flow.credentials
    user_svc = build("oauth2", "v2", credentials=creds)
    info = user_svc.userinfo().get().execute()
    email = info.get("email")
    if not email:
        raise ValueError("Could not determine Gmail address from OAuth response")

    existing = (
        db.query(GmailAccount)
        .filter(
            GmailAccount.profile_id == profile.id,
            GmailAccount.email == email,
        )
        .first()
    )
    token_encrypted = encrypt(creds.to_json())

    if existing:
        existing.oauth_token_enc = token_encrypted
        existing.is_active = True
        db.commit()
        db.refresh(existing)
        return existing

    count = (
        db.query(GmailAccount)
        .filter(GmailAccount.profile_id == profile.id)
        .count()
    )
    if count >= max_accounts:
        raise ValueError(f"Max {max_accounts} Gmail accounts per profile")

    account = GmailAccount(
        profile_id=profile.id,
        email=email,
        oauth_token_enc=token_encrypted,
        is_active=True,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    logger.info(f"Connected Gmail account {email} for profile {profile.id}")
    return account


def load_credentials(account: GmailAccount) -> Credentials:
    token_data = json.loads(decrypt(account.oauth_token_enc))
    return Credentials.from_authorized_user_info(token_data, GMAIL_SCOPES)


def save_credentials_if_refreshed(
    db: Session, account: GmailAccount, creds: Credentials
) -> None:
    account.oauth_token_enc = encrypt(creds.to_json())
    db.commit()


def revoke_account(db: Session, account: GmailAccount) -> None:
    account.is_active = False
    account.oauth_token_enc = ""
    db.commit()
