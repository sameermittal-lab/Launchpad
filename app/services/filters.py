"""Shared title filter used consistently across Scanner, Gmail sync, Gmail extraction,
and manual listing creation.

Single source of truth: `title_passes_filter(title, positive, negative)`.

Semantics (same as the original scanner implementation, just lifted to shared code):
- Case-insensitive substring matching.
- If ANY negative keyword is a substring of the title -> reject.
- If positive list is non-empty, at least one must be a substring -> accept.
- If positive list is empty, any title that doesn't hit a negative is accepted.
"""
from __future__ import annotations

from typing import Iterable, Optional


def _matched_kw(title: str, keywords: Iterable[str]) -> Optional[str]:
    """Return the first keyword that is a case-insensitive substring of title, else None."""
    if not title or not keywords:
        return None
    low = title.lower()
    for kw in keywords:
        if kw and kw.lower() in low:
            return kw
    return None


def title_passes_filter(
    title: str,
    positive: Optional[list[str]],
    negative: Optional[list[str]],
) -> bool:
    """Return True if the title passes the user's filter configuration."""
    if not title:
        return False
    pos = positive or []
    neg = negative or []
    if _matched_kw(title, neg) is not None:
        return False
    if not pos:
        return True
    return _matched_kw(title, pos) is not None


def why_title_fails(
    title: str,
    positive: Optional[list[str]],
    negative: Optional[list[str]],
) -> Optional[str]:
    """Human-readable reason a title failed the filter, or None if it passes.

    Useful for building UI messages and history-event notes.
    """
    if not title:
        return "empty title"
    neg_match = _matched_kw(title, negative or [])
    if neg_match:
        return f"matched negative keyword '{neg_match}'"
    if positive:
        pos_match = _matched_kw(title, positive)
        if pos_match is None:
            return "no positive keyword matched"
    return None


def partition_by_filter(
    items: list,
    get_title,
    positive: Optional[list[str]],
    negative: Optional[list[str]],
) -> tuple[list, list[tuple[object, str]]]:
    """Split a list of items into (kept, dropped) based on the filter.

    `get_title(item)` extracts the title string from each item.
    `dropped` is a list of (item, reason_string) so callers can log/report.
    """
    kept: list = []
    dropped: list[tuple[object, str]] = []
    for it in items or []:
        title = get_title(it) or ""
        reason = why_title_fails(title, positive, negative)
        if reason is None:
            kept.append(it)
        else:
            dropped.append((it, reason))
    return kept, dropped
