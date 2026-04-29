"""Gmail client - search, fetch, parse messages.

Built on top of job-agent's GmailAgent pattern.
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parseaddr
from typing import Optional

from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from sqlalchemy.orm import Session

from app.models import GmailAccount
from app.services.gmail.oauth import load_credentials, save_credentials_if_refreshed

logger = logging.getLogger(__name__)


@dataclass
class ParsedMessage:
    gmail_id: str
    thread_id: str
    from_email: str
    from_name: str
    subject: str
    snippet: str
    body_text: str
    received_at: datetime


class GmailClient:
    """Wraps the Gmail API for a single connected account."""

    def __init__(self, db: Session, account: GmailAccount):
        self.db = db
        self.account = account
        self._creds = load_credentials(account)
        if self._creds.expired and self._creds.refresh_token:
            logger.info(f"Refreshing token for {account.email}")
            self._creds.refresh(Request())
            save_credentials_if_refreshed(db, account, self._creds)
        self.service = build("gmail", "v1", credentials=self._creds, cache_discovery=False)

    def search(self, query: str, max_results: int = 50) -> list[str]:
        """Return message IDs matching a Gmail search query."""
        resp = (
            self.service.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )
        return [m["id"] for m in resp.get("messages", [])]

    def get_message(self, message_id: str) -> ParsedMessage:
        """Fetch a message and parse it into a ParsedMessage."""
        raw = (
            self.service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
        return self._parse(raw)

    def _parse(self, raw: dict) -> ParsedMessage:
        headers = {h["name"].lower(): h["value"] for h in raw.get("payload", {}).get("headers", [])}
        from_raw = headers.get("from", "")
        from_name, from_email = parseaddr(from_raw)
        subject = headers.get("subject", "")

        # received_at from internalDate (ms since epoch)
        ts = int(raw.get("internalDate", "0")) / 1000
        received_at = datetime.utcfromtimestamp(ts)

        body_text = self._extract_body(raw.get("payload", {}))

        return ParsedMessage(
            gmail_id=raw["id"],
            thread_id=raw.get("threadId", ""),
            from_email=from_email or "",
            from_name=from_name or "",
            subject=subject,
            snippet=raw.get("snippet", ""),
            body_text=body_text,
            received_at=received_at,
        )

    def _extract_body(self, payload: dict) -> str:
        """Extract text body from a Gmail payload.

        Prefers text/plain, falls back to text/html with tags stripped.
        """
        # Single-part message
        body_data = (payload.get("body") or {}).get("data")
        mime = payload.get("mimeType", "")
        if body_data:
            decoded = self._b64decode(body_data)
            if mime == "text/html":
                return self._strip_html(decoded)
            return decoded

        # Multipart
        for part in payload.get("parts", []) or []:
            part_mime = part.get("mimeType", "")
            if part_mime in ("text/plain", "text/html"):
                data = (part.get("body") or {}).get("data")
                if data:
                    decoded = self._b64decode(data)
                    if part_mime == "text/html":
                        return self._strip_html(decoded)
                    return decoded
            # Nested multipart
            if "parts" in part:
                nested = self._extract_body(part)
                if nested:
                    return nested
        return ""

    @staticmethod
    def _b64decode(data: str) -> str:
        try:
            return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="ignore")
        except Exception:
            return ""

    @staticmethod
    def _strip_html(html: str) -> str:
        """Convert HTML to plain text while preserving `<a href>` URLs inline.

        Email bodies (especially LinkedIn Job Alerts) hang every job title
        off an `<a href>` — if we just strip tags, the user-visible titles
        survive but the URLs disappear. That's useless for our extraction
        prompt which requires a URL per listing.

        Format we emit: "anchor text (https://full-url)" so downstream text
        processing still sees both the human label and the machine URL.
        """
        # Drop scripts, styles, comments
        text = re.sub(r"<script.*?</script>", "", html, flags=re.S | re.I)
        text = re.sub(r"<style.*?</style>", "", text, flags=re.S | re.I)
        text = re.sub(r"<!--.*?-->", "", text, flags=re.S)

        # Replace <a href="URL">TEXT</a> with "TEXT (URL)"
        def _anchor(m: "re.Match") -> str:
            href = m.group(1).strip()
            inner = re.sub(r"<[^>]+>", " ", m.group(2) or "")
            inner = re.sub(r"\s+", " ", inner).strip()
            # Skip mailto / tel / same-url-as-text to avoid noise
            if href.lower().startswith(("mailto:", "tel:", "#")):
                return inner
            if not inner:
                return f" {href} "
            if inner.strip() == href.strip():
                return f" {href} "
            return f" {inner} ({href}) "

        text = re.sub(
            r"<a\b[^>]*?href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
            _anchor,
            text,
            flags=re.S | re.I,
        )

        # Line breaks for block elements so text stays readable after strip
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
        text = re.sub(r"</(p|div|tr|li|h[1-6])>", "\n", text, flags=re.I)

        # Remaining tags
        text = re.sub(r"<[^>]+>", " ", text)

        # HTML entities
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"&amp;", "&", text)
        text = re.sub(r"&lt;", "<", text)
        text = re.sub(r"&gt;", ">", text)
        text = re.sub(r"&#39;", "'", text)
        text = re.sub(r"&quot;", '"', text)

        # Collapse whitespace, but keep single newlines as separators
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n\n", text)
        return text.strip()

    def extract_verification_code(self, text: str) -> Optional[str]:
        """Extract a 4-8 digit verification code from email text."""
        m = re.search(r"\b(\d{4,8})\b", text)
        return m.group(1) if m else None

    def extract_verification_link(self, text: str) -> Optional[str]:
        m = re.search(r"https?://[^\s>\"']+", text)
        return m.group(0) if m else None
