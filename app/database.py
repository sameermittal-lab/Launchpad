"""SQLAlchemy database setup."""

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


engine = create_engine(
    settings.database_url,
    echo=False,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def db_session() -> Generator[Session, None, None]:
    """Context manager for database sessions (non-FastAPI code)."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """Create all tables and apply any lightweight migrations."""
    # Import all models so they're registered with Base.metadata
    from app.models import (  # noqa: F401
        profile,
        listing,
        application,
        gmail_account,
        company,
        tracked_company,
        history_event,
        email_message,
        usage,
        reminder,
        session,
        ai_monitor_run,
        company_suggestion,
    )
    Base.metadata.create_all(bind=engine)
    _apply_simple_migrations()


def _apply_simple_migrations() -> None:
    """Add missing columns to existing tables.

    This is a lightweight substitute for full Alembic migrations - sufficient
    for the simple column-add operations we do during early development. Any
    complex schema change should use a proper migration.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)

    def ensure_columns(table_name: str, columns: dict[str, str]) -> None:
        if not inspector.has_table(table_name):
            return
        existing = {c["name"] for c in inspector.get_columns(table_name)}
        with engine.begin() as conn:
            for col_name, col_type in columns.items():
                if col_name not in existing:
                    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}"))

    ensure_columns("profiles", {
        "title_positive_keywords": "JSON",
        "title_negative_keywords": "JSON",
        "gmail_client_credentials_enc": "TEXT",
        "web_grounded_eval": "BOOLEAN DEFAULT 1",
        "pass_history_threshold": "INTEGER DEFAULT 15",
        "pass_calibration_preference": "VARCHAR(10) DEFAULT 'auto'",
        "chat_onboarding_dismissed": "BOOLEAN DEFAULT 0",
        "job_alert_senders": "JSON",
        "ai_monitor_interval_hours": "INTEGER DEFAULT 24",
        "company_suggestions_refreshed_at": "DATETIME",
        "smart_title_filter_enabled": "BOOLEAN DEFAULT 0",
        "google_search_api_key_enc": "TEXT",
        "google_search_cx": "VARCHAR(100)",
        "gemini_search_api_key_enc": "TEXT",
    })
    ensure_columns("listings", {
        "tailored_resume_md": "TEXT",
        "tailored_resume_md_original": "TEXT",
        "tailored_cover_letter_md": "TEXT",
        "tailored_cover_letter_md_original": "TEXT",
        "tailoring_intensity": "VARCHAR(20)",
        "cover_letter_tone_override": "VARCHAR(50)",
        "evaluation_version": "INTEGER DEFAULT 1",
        "dimension_rationales": "JSON",
        "take_it_if": "JSON",
        "compromises": "JSON",
        "blockers": "JSON",
        "citations": "JSON",
        "pass_reason": "VARCHAR(50)",
        "pass_note": "TEXT",
        "passed_at": "DATETIME",
        "use_for_calibration": "BOOLEAN DEFAULT 1",
        "evaluation_in_progress": "DATETIME",
        "chat_history": "JSON",
        "chat_edit_log": "JSON",
        "smart_filter_verdict": "VARCHAR(10)",
        "smart_filter_reason": "VARCHAR(200)",
    })
    ensure_columns("email_messages", {
        "ai_summary": "TEXT",
        "filtered_listings": "JSON",
        "extraction_meta": "JSON",
    })
    ensure_columns("gmail_accounts", {
        "last_extraction_at": "DATETIME",
        "last_extraction_count": "INTEGER DEFAULT 0",
        "lifetime_listings_extracted": "INTEGER DEFAULT 0",
    })
    ensure_columns("tracked_companies", {
        "ai_monitor_enabled": "BOOLEAN DEFAULT 0",
        "query_plan": "JSON",
        "query_plan_generated_at": "DATETIME",
        "last_ai_monitor_at": "DATETIME",
        "last_ai_monitor_count": "INTEGER DEFAULT 0",
    })

    # Upgrade any legacy 6-dim scoring_weights to the new 8-dim equal split.
    # Old profiles created before v2 eval will have weights like
    # {role_match, skills, comp, growth, culture, location}. Migrate them
    # to the equal-weighted 8-dim set that includes seniority_match + s_curve.
    try:
        with engine.begin() as conn:
            rows = conn.execute(text("SELECT id, scoring_weights FROM profiles")).fetchall()
            for row in rows:
                import json as _json
                try:
                    w = row[1] if isinstance(row[1], dict) else _json.loads(row[1] or "{}")
                except Exception:
                    w = {}
                needs_upgrade = (
                    not w
                    or "seniority_match" not in w
                    or "s_curve" not in w
                )
                if needs_upgrade:
                    new_w = {
                        "role_match": 0.125,
                        "seniority_match": 0.125,
                        "skills": 0.125,
                        "comp": 0.125,
                        "growth": 0.125,
                        "s_curve": 0.125,
                        "culture": 0.125,
                        "location": 0.125,
                    }
                    conn.execute(
                        text("UPDATE profiles SET scoring_weights = :w WHERE id = :id"),
                        {"w": _json.dumps(new_w), "id": row[0]},
                    )
    except Exception:
        # Don't block startup on migration issues
        import logging
        logging.getLogger(__name__).exception("scoring_weights upgrade skipped")

    # Backfill job_alert_senders for existing profiles (newly-added column will
    # be NULL for rows that existed before this version).
    try:
        with engine.begin() as conn:
            import json as _json
            default_senders = [
                "@indeed.com",
                "jobs-listings@linkedin.com",
                "jobs-noreply@linkedin.com",
                "@ziprecruiter.com",
                "@glassdoor.com",
            ]
            rows = conn.execute(text("SELECT id, job_alert_senders FROM profiles")).fetchall()
            for row in rows:
                raw = row[1]
                needs_fill = raw is None or raw == "" or raw == "null"
                if needs_fill:
                    conn.execute(
                        text("UPDATE profiles SET job_alert_senders = :s WHERE id = :id"),
                        {"s": _json.dumps(default_senders), "id": row[0]},
                    )
    except Exception:
        import logging
        logging.getLogger(__name__).exception("job_alert_senders backfill skipped")
