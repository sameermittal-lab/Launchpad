"""Time utilities — single source of truth for datetime handling.

All stored datetimes in LaunchPad are naive UTC (datetime.datetime objects
with no tzinfo). This works fine for internal comparisons. The bug was that
the API serialized them as ISO strings without a Z suffix, causing JavaScript
clients to interpret them as local time.

Fix: helper that normalizes naive datetimes to tz-aware UTC at the JSON
response boundary, so ISO output always carries an explicit "Z" (or +00:00).

Internal code keeps using naive utcnow() via `utcnow_naive()` — this is just
a named wrapper that preserves current behavior and lets us sweep the codebase
in one swap. The important fix is `iso_utc_z()`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def utcnow_naive() -> datetime:
    """Naive UTC "now" — same value as the legacy datetime.utcnow().

    Use this everywhere internal code compares timestamps or stores new ones.
    Keeps internal math stable because every datetime in DB is naive UTC.
    """
    return datetime.utcnow()


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Return a tz-aware UTC datetime, treating naive inputs as UTC.

    - None → None (pass-through, useful for nullable columns)
    - Naive datetime → attach UTC tzinfo (we know all naive values in the app are UTC)
    - Aware datetime → convert to UTC if not already
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_utc_z(dt: Optional[datetime]) -> Optional[str]:
    """Serialize a datetime to ISO 8601 with a Z suffix (UTC-aware JSON convention).

    None → None. Naive datetimes are treated as UTC. Aware datetimes are
    converted to UTC. The result always ends in Z (not +00:00) so it's
    unambiguously parseable by JavaScript's `new Date(...)`.
    """
    aware = ensure_utc(dt)
    if aware is None:
        return None
    # isoformat() produces "+00:00"; normalize to Z for cleanness
    return aware.isoformat(timespec="seconds").replace("+00:00", "Z")
