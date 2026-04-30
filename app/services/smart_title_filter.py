"""Smart title filter — optional LLM pass that classifies job titles as
yes/no/maybe before they reach the expensive full-evaluation step.

Opt-in per profile via `Profile.smart_title_filter_enabled`. When off, this
module's callers should skip invoking it entirely and treat all
keyword-filter-passing titles as yes.

Design notes:
- Batches up to 15 titles per LLM call to keep cost low (~$0.001 per title).
- Returns a dict keyed by a caller-provided index, so caller maps verdicts
  back to whatever listing/hit object they started with.
- If the LLM call or JSON parse fails, all items in the batch default to
  "yes" (fail-open — prefer over-inclusion to silently dropping matches).
- Caller decides what to do with "maybe" — current convention is treat
  "maybe" as "yes" for inclusion, but flag separately for UI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Listing, Profile
from app.prompts import render_prompt
from app.services.evaluation import _extract_json
from app.services.llm import get_provider
from app.services.usage_tracker import log_usage

logger = logging.getLogger(__name__)

BATCH_SIZE = 15


@dataclass
class TitleVerdict:
    index: int
    verdict: str   # "yes" | "no" | "maybe"
    reason: str


def _target_roles_str(profile: Profile) -> str:
    pd = profile.profile_data or {}
    tr = pd.get("target_roles")
    if isinstance(tr, list) and tr:
        return ", ".join(str(t) for t in tr if t)
    if isinstance(tr, str) and tr.strip():
        return tr.strip()
    return "(not specified)"


def _top_pass_reasons(db: Session, profile: Profile, limit: int = 3) -> list[str]:
    """Return the top N pass-reason codes by frequency for this profile."""
    from sqlalchemy import func
    rows = (
        db.query(Listing.pass_reason, func.count(Listing.id))
        .filter(
            Listing.profile_id == profile.id,
            Listing.status == "passed",
            Listing.use_for_calibration.is_(True),
            Listing.pass_reason.isnot(None),
        )
        .group_by(Listing.pass_reason)
        .order_by(func.count(Listing.id).desc())
        .limit(limit)
        .all()
    )
    return [r[0] for r in rows if r[0]]


async def classify_titles(
    db: Session,
    profile: Profile,
    items: list[dict],
) -> dict[int, TitleVerdict]:
    """Classify a list of titles. `items` is a list of {title, company} dicts.

    Returns {index: TitleVerdict} where index is 0-based into the input list.
    Fail-open: on any error, every item returns verdict="yes" with
    reason="filter unavailable".
    """
    if not items:
        return {}

    # Fail-open sentinel — used if LLM unavailable or any batch fails
    def _fail_open() -> dict[int, TitleVerdict]:
        return {
            i: TitleVerdict(index=i, verdict="yes", reason="filter unavailable")
            for i in range(len(items))
        }

    if not profile.llm_api_key_enc:
        return _fail_open()

    target_roles = _target_roles_str(profile)
    current_role = (profile.role_title or "").strip() or None
    pass_reasons = _top_pass_reasons(db, profile)

    verdicts: dict[int, TitleVerdict] = {}
    for start in range(0, len(items), BATCH_SIZE):
        batch = items[start : start + BATCH_SIZE]
        prompt = render_prompt(
            "smart_title_filter.md.j2",
            target_roles=target_roles,
            current_role=current_role,
            pass_reasons=pass_reasons,
            items=batch,
        )
        try:
            provider = get_provider(profile)
            response = await provider.complete(
                system=(
                    "You classify job titles for relevance to a candidate's target "
                    "roles. You always output valid JSON and never explain outside it."
                ),
                user=prompt,
                max_tokens=800,
                temperature=0.1,
            )
            log_usage(db, profile.id, "smart_title_filter", response)
            parsed = _extract_json(response.text)
        except Exception as exc:
            logger.warning(f"Smart title filter batch failed, falling back to yes: {exc}")
            # Fail-open for this batch
            for j, _ in enumerate(batch):
                verdicts[start + j] = TitleVerdict(
                    index=start + j, verdict="yes", reason="filter unavailable"
                )
            continue

        raw = parsed.get("verdicts") if isinstance(parsed, dict) else parsed
        if not isinstance(raw, list):
            for j, _ in enumerate(batch):
                verdicts[start + j] = TitleVerdict(
                    index=start + j, verdict="yes", reason="filter unavailable"
                )
            continue

        # Build a lookup keyed by the 1-based indices the prompt uses
        by_idx = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            if isinstance(idx, int) and 1 <= idx <= len(batch):
                v = str(item.get("verdict", "yes")).strip().lower()
                if v not in ("yes", "no", "maybe"):
                    v = "yes"
                reason = str(item.get("reason") or "").strip()[:200]
                by_idx[idx] = TitleVerdict(index=start + idx - 1, verdict=v, reason=reason)

        # Fill any gaps with fail-open yes so callers never see missing indices
        for j, _ in enumerate(batch):
            global_idx = start + j
            if (j + 1) in by_idx:
                verdicts[global_idx] = by_idx[j + 1]
            else:
                verdicts[global_idx] = TitleVerdict(
                    index=global_idx, verdict="yes", reason="filter missed this row"
                )

    return verdicts


def passes_smart_filter(verdict: Optional[TitleVerdict]) -> bool:
    """Decision rule for 'should this listing flow through to full evaluation?'.

    "yes" and "maybe" pass. Only explicit "no" is dropped.
    Null verdict (filter wasn't run) passes by default — matches legacy behavior.
    """
    if verdict is None:
        return True
    return verdict.verdict != "no"
