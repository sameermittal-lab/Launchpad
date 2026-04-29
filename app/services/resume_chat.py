"""Resume / cover-letter conversational editing service.

Shared thread per listing (Option C): one chat_history spans both documents,
each turn/edit tagged with target scope. Separate apply calls mutate one
document at a time and push to the undo stack (chat_edit_log).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Listing, Profile, HistoryEvent
from app.prompts import render_prompt
from app.services.evaluation import _extract_json, _load_cv
from app.services.llm import get_provider
from app.services.usage_tracker import log_usage

logger = logging.getLogger(__name__)

VALID_SCOPES = {"resume", "cover_letter", "both"}

# Max edits we'll accept per LLM turn (cap so a bad response can't dump 50 diffs).
MAX_PROPOSED_EDITS = 8

# Max conversation turns we pass to the LLM (oldest trimmed first). Keeps
# prompts bounded while we retain the full history in the DB.
MAX_HISTORY_FOR_PROMPT = 30


def _word_swap_pattern_hint(user_message: str) -> Optional[str]:
    """Detect word-swap-style requests so the backend can flag them for the UI.

    Returns a human-readable hint string if the message looks like a trivial
    word replacement (which the user should do directly in the markdown), else
    None. The frontend ALSO does this check so it can show a soft nudge BEFORE
    firing the LLM, but we keep the server check as a safety net for scripted
    integrations.
    """
    msg = (user_message or "").strip()
    if not msg or len(msg) > 200:
        return None
    patterns = [
        r"\b(change|replace|swap|rename)\s+['\"]?(\w+)['\"]?\s+(to|with|for)\s+['\"]?(\w+)['\"]?",
        r"['\"](\w+)['\"]\s*(?:->|→|to|instead of)\s*['\"](\w+)['\"]",
    ]
    for p in patterns:
        if re.search(p, msg, re.IGNORECASE):
            return "looks-like-word-swap"
    return None


async def chat_turn(
    db: Session,
    profile: Profile,
    listing: Listing,
    user_message: str,
    user_scope: str,
) -> dict:
    """Run one chat turn. Appends the turn to listing.chat_history, calls the LLM,
    appends the assistant reply (with proposed edits), and returns the updated
    history tail for the frontend.

    Returns: {"reply": str, "proposed_edits": [...], "turn_index": int, "word_swap_hint": bool}
    """
    if user_scope not in VALID_SCOPES:
        raise ValueError(f"Invalid scope: {user_scope}. Must be one of {VALID_SCOPES}")
    if not user_message.strip():
        raise ValueError("Empty message")
    if not profile.llm_api_key_enc:
        raise ValueError("No LLM API key configured")

    cv_text = _load_cv(profile)
    pd = profile.profile_data or {}
    target_roles = pd.get("target_roles") or []
    if isinstance(target_roles, list):
        target_roles_str = ", ".join(target_roles) if target_roles else None
    else:
        target_roles_str = str(target_roles) or None

    # Build the history to show the LLM — cap at last N turns so the prompt
    # doesn't balloon. Always include any "system" marker turns (e.g.
    # regeneration / revert notices) because they're short and informative.
    full_history = list(listing.chat_history or [])
    trimmed: list[dict] = []
    for t in reversed(full_history):
        if len(trimmed) >= MAX_HISTORY_FOR_PROMPT and t.get("role") != "system":
            break
        trimmed.append(t)
    trimmed.reverse()

    prompt = render_prompt(
        "resume_chat.md.j2",
        profile=profile,
        listing=listing,
        cv_text=cv_text,
        target_roles=target_roles_str,
        location=pd.get("location"),
        tailored_resume_md=listing.tailored_resume_md or "",
        tailored_cover_letter_md=listing.tailored_cover_letter_md or "",
        chat_history=trimmed,
        user_message=user_message,
        user_scope=user_scope,
    )

    provider = get_provider(profile)
    response = await provider.complete(
        system=(
            "You edit resumes and cover letters for a specific job candidate. "
            "You respond ONLY with a JSON object containing a short conversational reply "
            "and a list of surgical proposed edits the UI can Apply/Reject individually. "
            "Never rewrite whole documents unless explicitly asked. Never fabricate facts."
        ),
        user=prompt,
        max_tokens=2500,
        temperature=0.3,
    )
    log_usage(db, profile.id, "resume_chat", response)

    try:
        result = _extract_json(response.text)
    except Exception as exc:
        logger.warning(f"Resume-chat JSON parse failed: {exc}; raw: {response.text[:300]}")
        result = {
            "reply": "I couldn't structure my response properly — try rephrasing your request.",
            "proposed_edits": [],
        }

    reply = (result.get("reply") or "").strip() or "(no reply)"
    raw_edits = result.get("proposed_edits") or []
    proposed_edits = []
    for edit in raw_edits[:MAX_PROPOSED_EDITS]:
        if not isinstance(edit, dict):
            continue
        target = edit.get("target")
        if target not in ("resume", "cover_letter"):
            continue
        # Scope-enforce: if the user's scope was a single doc, drop edits
        # for the other one (the LLM sometimes wanders).
        if user_scope == "resume" and target != "resume":
            continue
        if user_scope == "cover_letter" and target != "cover_letter":
            continue
        before = str(edit.get("before") or "")
        after = str(edit.get("after") or "")
        if not before or not after or before == after:
            continue
        # Validate the "before" actually exists in the current doc; if not,
        # mark the edit as non-applicable but still surface it for debugging.
        doc = listing.tailored_resume_md if target == "resume" else listing.tailored_cover_letter_md
        applicable = bool(doc and before in doc)
        proposed_edits.append({
            "id": f"e{len(proposed_edits) + 1}",
            "target": target,
            "section": str(edit.get("section") or "").strip(),
            "rationale": str(edit.get("rationale") or "").strip(),
            "before": before,
            "after": after,
            "applicable": applicable,
        })

    # Append both user + assistant turns to history
    now_iso = datetime.utcnow().isoformat() + "Z"
    new_turns: list[dict] = [
        {
            "role": "user",
            "scope": user_scope,
            "content": user_message,
            "timestamp": now_iso,
        },
        {
            "role": "assistant",
            "scope": user_scope,
            "content": reply,
            "proposed_edits": proposed_edits,
            "timestamp": now_iso,
            "cost_usd": response.cost_usd,
            "model": response.model,
        },
    ]
    listing.chat_history = (listing.chat_history or []) + new_turns
    db.commit()
    db.refresh(listing)

    return {
        "reply": reply,
        "proposed_edits": proposed_edits,
        "turn_index": len(listing.chat_history) - 1,  # index of assistant turn
        "word_swap_hint": _word_swap_pattern_hint(user_message) is not None,
    }


async def apply_edit(
    db: Session,
    profile: Profile,
    listing: Listing,
    turn_index: int,
    edit_id: str,
) -> dict:
    """Apply a specific proposed edit from the chat history to the target document.

    Re-renders the relevant PDF, pushes an entry onto chat_edit_log for undo,
    and marks the edit as applied in chat_history so the UI shows ✓ Applied.
    """
    history = list(listing.chat_history or [])
    if turn_index < 0 or turn_index >= len(history):
        raise ValueError(f"Invalid turn_index {turn_index}")
    turn = history[turn_index]
    if turn.get("role") != "assistant":
        raise ValueError("Can only apply edits from assistant turns")
    edits = turn.get("proposed_edits") or []
    matched = next((e for e in edits if e.get("id") == edit_id), None)
    if not matched:
        raise ValueError(f"Edit {edit_id} not found in turn {turn_index}")
    if matched.get("applied_at"):
        raise ValueError("Edit already applied")
    if matched.get("rejected_at"):
        raise ValueError("Edit was rejected — cannot apply")

    target = matched.get("target")
    before = matched.get("before") or ""
    after = matched.get("after") or ""
    if target not in ("resume", "cover_letter") or not before or not after:
        raise ValueError("Edit is malformed")

    if target == "resume":
        current = listing.tailored_resume_md or ""
        if before not in current:
            raise ValueError("Original text no longer found in resume — the document changed since this edit was proposed")
        new_md = current.replace(before, after, 1)
        prior_md = current
        listing.tailored_resume_md = new_md
        if not listing.tailored_resume_md_original:
            listing.tailored_resume_md_original = prior_md
    else:
        current = listing.tailored_cover_letter_md or ""
        if before not in current:
            raise ValueError("Original text no longer found in cover letter — the document changed since this edit was proposed")
        new_md = current.replace(before, after, 1)
        prior_md = current
        listing.tailored_cover_letter_md = new_md
        if not listing.tailored_cover_letter_md_original:
            listing.tailored_cover_letter_md_original = prior_md

    # Stamp the edit as applied
    now_iso = datetime.utcnow().isoformat() + "Z"
    matched["applied_at"] = now_iso
    history[turn_index] = turn
    listing.chat_history = history

    # Push onto undo log
    log = list(listing.chat_edit_log or [])
    log.append({
        "target": target,
        "before_md": prior_md,
        "after_md": new_md,
        "applied_at": now_iso,
        "turn_index": turn_index,
        "edit_id": edit_id,
        "note": (matched.get("section") or matched.get("rationale") or "")[:200],
    })
    listing.chat_edit_log = log

    db.commit()

    # Re-render the appropriate PDF
    try:
        if target == "resume":
            from app.services.resume_tailor import rerender_resume_from_markdown
            await rerender_resume_from_markdown(profile, listing)
        else:
            from app.services.cover_letter import rerender_cover_letter_from_markdown
            await rerender_cover_letter_from_markdown(profile, listing)
    except Exception as exc:
        logger.warning(f"PDF re-render after chat edit failed: {exc}")

    db.commit()
    db.refresh(listing)
    return {
        "ok": True,
        "target": target,
        "pdf_path": listing.tailored_resume_path if target == "resume" else listing.cover_letter_path,
        "undo_index": len(log) - 1,
    }


async def reject_edit(
    db: Session,
    listing: Listing,
    turn_index: int,
    edit_id: str,
) -> None:
    """Mark a proposed edit as rejected so the UI can stop showing Apply on it."""
    history = list(listing.chat_history or [])
    if turn_index < 0 or turn_index >= len(history):
        raise ValueError(f"Invalid turn_index {turn_index}")
    turn = history[turn_index]
    edits = turn.get("proposed_edits") or []
    matched = next((e for e in edits if e.get("id") == edit_id), None)
    if not matched:
        raise ValueError(f"Edit {edit_id} not found in turn {turn_index}")
    matched["rejected_at"] = datetime.utcnow().isoformat() + "Z"
    history[turn_index] = turn
    listing.chat_history = history
    db.commit()


async def undo_last_edit(
    db: Session,
    profile: Profile,
    listing: Listing,
) -> dict:
    """Undo the most recent applied edit. Restores the prior markdown for whichever
    document it touched and re-renders that PDF.
    """
    log = list(listing.chat_edit_log or [])
    if not log:
        raise ValueError("Nothing to undo")
    entry = log.pop()
    target = entry.get("target")
    prior = entry.get("before_md")
    if target == "resume":
        listing.tailored_resume_md = prior
    elif target == "cover_letter":
        listing.tailored_cover_letter_md = prior
    listing.chat_edit_log = log

    # Un-stamp in chat_history so Apply button returns
    ti = entry.get("turn_index")
    eid = entry.get("edit_id")
    if ti is not None and eid:
        history = list(listing.chat_history or [])
        if 0 <= ti < len(history):
            turn = history[ti]
            edits = turn.get("proposed_edits") or []
            for e in edits:
                if e.get("id") == eid:
                    e.pop("applied_at", None)
                    break
            history[ti] = turn
            listing.chat_history = history

    db.commit()

    try:
        if target == "resume":
            from app.services.resume_tailor import rerender_resume_from_markdown
            await rerender_resume_from_markdown(profile, listing)
        elif target == "cover_letter":
            from app.services.cover_letter import rerender_cover_letter_from_markdown
            await rerender_cover_letter_from_markdown(profile, listing)
    except Exception as exc:
        logger.warning(f"PDF re-render after undo failed: {exc}")
    db.commit()
    db.refresh(listing)
    return {"ok": True, "target": target, "remaining": len(log)}


def clear_history(db: Session, listing: Listing) -> None:
    """Wipe the chat thread. Does NOT undo applied edits — they stay in the markdown."""
    listing.chat_history = []
    db.commit()


def append_system_note(db: Session, listing: Listing, note: str) -> None:
    """Insert a system-turn into chat_history. Used when the user does something
    out-of-band (e.g., full regenerate, revert to original) so the conversation
    thread stays an accurate record.
    """
    history = list(listing.chat_history or [])
    history.append({
        "role": "system",
        "scope": "both",
        "content": note,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    })
    listing.chat_history = history
    db.commit()
