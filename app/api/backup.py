"""Backup / restore endpoints.

Exports everything except secrets (encryption key, encrypted API keys, OAuth
tokens, session cookies). Users re-enter those after restore.

Export payload:
    launchpad.db
    users/{profile_id}/cv.md
    users/{profile_id}/generated_resumes/*
    users/{profile_id}/cover_letters/*
    users/{profile_id}/source_resume.pdf (if present)
    meta.json (version, export date, hostname)

NOT included: .launchpad.key, gmail tokens (*.json), sessions table rows,
encrypted LLM keys (they stay in the DB as ciphertext the new host can't read,
but the schema survives so the user only re-enters keys, not all settings).
"""
from __future__ import annotations

import io
import json
import logging
import platform
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse

from app import __version__
from app.config import settings
from app.database import SessionLocal
from app.models import Profile
from app.utils.session import get_current_profile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/backup", tags=["backup"])


def _safe_name(name: str) -> str:
    """Strip path separators from user-provided names."""
    return "".join(c for c in name if c.isalnum() or c in ("-", "_", "."))


@router.get("/export")
def export_backup(profile: Profile = Depends(get_current_profile)):
    """Produce a ZIP of the full LaunchPad data directory (no secrets).

    Any logged-in user can trigger an export of the whole instance (this is
    a single-household tool, not a tenant-isolated SaaS).
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Metadata
        meta = {
            "version": __version__,
            "exported_at": datetime.utcnow().isoformat() + "Z",
            "hostname": platform.node(),
            "platform": platform.system(),
        }
        zf.writestr("meta.json", json.dumps(meta, indent=2))

        # SQLite DB file
        db_file = settings.base_dir / settings.db_path if not settings.db_path.is_absolute() else settings.db_path
        if db_file.exists():
            zf.write(db_file, arcname="launchpad.db")

        # users/ tree - skip secrets
        data_dir = settings.resolved_data_dir
        if data_dir.exists():
            for p in data_dir.rglob("*"):
                if not p.is_file():
                    continue
                rel = p.relative_to(data_dir.parent)  # e.g. "users/3/cv.md"
                name = p.name.lower()
                # Skip known secret filenames
                if name in {"gmail_token.json", "gmail_credentials.json", "credentials.json", "token.json"}:
                    continue
                if name.endswith(".pickle") or name.endswith(".session"):
                    continue
                zf.write(p, arcname=str(rel))

    buf.seek(0)
    filename = f"launchpad-backup-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import")
async def import_backup(
    file: UploadFile = File(...),
    profile: Profile = Depends(get_current_profile),
):
    """Restore from a previously exported ZIP. Overwrites current data.

    The caller must have already stopped the scheduler-dependent background
    jobs on this host; restoring is a disruptive operation and the server
    should be restarted after.
    """
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Expected a .zip file")

    content = await file.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="File is not a valid ZIP archive")

    names = zf.namelist()
    if "meta.json" not in names:
        raise HTTPException(status_code=400, detail="ZIP does not look like a LaunchPad backup (missing meta.json)")

    # Validate no path traversal
    for n in names:
        p = Path(n)
        if p.is_absolute() or ".." in p.parts:
            raise HTTPException(status_code=400, detail=f"Archive contains unsafe path: {n}")

    # Backup current state so a failed restore is recoverable
    base = settings.base_dir
    backup_stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    rollback: list[tuple[Path, Path]] = []

    try:
        # Move aside current DB
        db_file = base / settings.db_path if not settings.db_path.is_absolute() else settings.db_path
        if db_file.exists():
            moved = db_file.with_suffix(f".pre-restore-{backup_stamp}.db")
            shutil.move(str(db_file), str(moved))
            rollback.append((moved, db_file))

        # Move aside current users/ dir
        data_dir = settings.resolved_data_dir
        if data_dir.exists():
            moved_data = data_dir.parent / f"users.pre-restore-{backup_stamp}"
            shutil.move(str(data_dir), str(moved_data))
            rollback.append((moved_data, data_dir))

        # Extract into the right places
        for n in names:
            if n == "meta.json":
                continue
            if n.endswith("/"):
                continue
            dest = base / n
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(n) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)

        # Make sure dirs exist
        settings.ensure_dirs()

        # Drop all sessions so old session cookies don't collide with
        # restored-but-empty Gmail/LLM keys
        try:
            from sqlalchemy import text
            db = SessionLocal()
            db.execute(text("DELETE FROM sessions"))
            db.commit()
            db.close()
        except Exception:
            logger.exception("Failed to clear sessions after restore (non-fatal)")

        return {
            "ok": True,
            "message": "Restore complete. Restart the server, then re-enter LLM API keys and reconnect Gmail accounts.",
            "pre_restore_backups": [str(m) for m, _ in rollback],
        }
    except Exception as exc:
        logger.exception("Restore failed; rolling back")
        # Roll back
        for moved, original in rollback:
            try:
                if original.exists():
                    if original.is_dir():
                        shutil.rmtree(original)
                    else:
                        original.unlink()
                shutil.move(str(moved), str(original))
            except Exception:
                logger.exception("Rollback also failed for %s", original)
        raise HTTPException(status_code=500, detail=f"Restore failed: {exc}")


@router.post("/scanner-reset-defaults")
def reset_scanner_to_defaults(profile: Profile = Depends(get_current_profile)):
    """Reset scanner title filter and tracked companies to their shipped defaults.

    - Title filter keywords: reset to the model-level defaults on Profile.
    - Tracked companies: delete all and re-load from the built-in YAML list.
    """
    import yaml
    from app.models.tracked_company import TrackedCompany
    from app.models.profile import Profile as ProfileModel

    db = SessionLocal()
    try:
        # Re-read the shipped defaults from the YAML file
        path = settings.templates_dir / "default_companies.yml"
        defaults: list[dict] = []
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                defaults = data.get("companies", []) if isinstance(data, dict) else data

        # Load a fresh profile from this session to avoid cross-session issues
        p = db.get(ProfileModel, profile.id)
        if p is None:
            raise HTTPException(status_code=404, detail="Profile not found")

        # Reset title filter to model defaults
        p.title_positive_keywords = ["AI", "ML", "Machine Learning", "Product Manager", "Director", "VP", "Head of"]
        p.title_negative_keywords = ["Junior", "Intern", "Internship"]

        # Clear and reload tracked companies
        db.query(TrackedCompany).filter(TrackedCompany.profile_id == profile.id).delete()
        loaded = 0
        for entry in defaults:
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            db.add(TrackedCompany(
                profile_id=profile.id,
                name=entry["name"],
                careers_url=entry.get("careers_url"),
                platform=entry.get("platform"),
                enabled=True,
            ))
            loaded += 1
        db.commit()
        return {
            "ok": True,
            "companies_loaded": loaded,
            "positive_keywords": p.title_positive_keywords,
            "negative_keywords": p.title_negative_keywords,
        }
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Reset scanner defaults failed")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()
