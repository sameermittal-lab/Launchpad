"""LaunchPad - FastAPI server entry point.

Run:
    ./start.sh        (macOS/Linux)
    start.bat         (Windows)

Or directly:
    python -m uvicorn server:app --host 0.0.0.0 --port 5000
"""

import json
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from fastapi.encoders import ENCODERS_BY_TYPE
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import settings
from app.database import init_db
from app.utils.logging import setup_logging
from app.utils.network import generate_qr_data_url, get_startup_url

logger = logging.getLogger(__name__)


def _datetime_to_utc_z(dt: datetime) -> str:
    """Serialize datetime to ISO 8601 with Z suffix.

    All LaunchPad timestamps are stored as naive UTC (datetime.utcnow()). JSON
    ISO strings without tzinfo are interpreted by JavaScript as local time,
    which is the bug this fixes. Treat naive as UTC and always emit Z.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


# Patch FastAPI's global datetime encoder (handles raw dict responses and
# jsonable_encoder fallbacks).
ENCODERS_BY_TYPE[datetime] = _datetime_to_utc_z


# Pydantic v2 bypasses ENCODERS_BY_TYPE for model fields — it serializes
# datetimes directly via its own core-schema. We apply a custom BaseModel
# base class with a @field_serializer at definition time, BUT retrofitting
# that across ~20 response models is noisy.
#
# Pragmatic global fix: a response-phase regex that rewrites naive ISO
# datetime strings to carry a Z suffix. Matches "YYYY-MM-DDTHH:MM:SS[.ffffff]"
# WITHOUT an existing Z or +HH:MM offset, then appends Z. Applied inside a
# custom JSONResponse so every endpoint is covered without touching models.

_NAIVE_ISO_RE = re.compile(
    r'("(?:\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)")'
)


def _z_fix(match: "re.Match") -> str:
    # Strip the closing quote, append Z, re-add quote
    inner = match.group(1)
    # inner is a quoted string; confirm no existing timezone designator inside
    if inner.endswith('Z"') or re.search(r'[+-]\d{2}:\d{2}"$', inner):
        return inner
    return inner[:-1] + 'Z"'


class UTCZJSONResponse(JSONResponse):
    """Response that rewrites naive ISO datetime strings to carry a Z suffix.

    This is a belt-and-braces fix that catches Pydantic's own datetime
    output, since Pydantic v2 doesn't honor fastapi.encoders globally.
    """

    def render(self, content: Any) -> bytes:
        raw = super().render(content)
        return _NAIVE_ISO_RE.sub(_z_fix, raw.decode("utf-8")).encode("utf-8")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown hooks."""
    setup_logging()
    settings.ensure_dirs()

    logger.info("=" * 60)
    logger.info(f"LaunchPad v{__version__}")
    logger.info("=" * 60)

    # Initialize database
    init_db()
    logger.info(f"Database ready: {settings.db_path}")

    # Print URLs
    url = get_startup_url(settings.host, settings.port)
    logger.info("")
    logger.info(f"  Local:    http://localhost:{settings.port}")
    logger.info(f"  Network:  {url}")
    logger.info("")
    logger.info("  Press Ctrl+C to stop")
    logger.info("=" * 60)

    # Make QR data URL available globally (served via /api/network)
    app.state.network_url = url
    app.state.qr_data_url = generate_qr_data_url(url)

    # Start the background scheduler
    from app import scheduler as sched_module
    sched_module.start()

    yield

    sched_module.shutdown()
    logger.info("LaunchPad shutting down")


app = FastAPI(
    title="LaunchPad",
    version=__version__,
    lifespan=lifespan,
    default_response_class=UTCZJSONResponse,
)

# CORS - permissive for local network use
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routers
from app.api import (
    health, profiles, auth,
    settings as settings_api,
    listings as listings_api,
    usage as usage_api,
    resumes as resumes_api,
    scanner as scanner_api,
    gmail as gmail_api,
    history as history_api,
    reminders as reminders_api,
    companies as companies_api,
    interview_prep as interview_prep_api,
    negotiation as negotiation_api,
    scheduler as scheduler_api,
    backup as backup_api,
)

app.include_router(health.router, prefix="/api")
app.include_router(profiles.router)
app.include_router(auth.router)
app.include_router(settings_api.router)
app.include_router(listings_api.router)
app.include_router(usage_api.router)
app.include_router(resumes_api.router)
app.include_router(scanner_api.router)
app.include_router(gmail_api.router)
app.include_router(history_api.router)
app.include_router(reminders_api.router)
app.include_router(companies_api.router)
app.include_router(interview_prep_api.router)
app.include_router(negotiation_api.router)
app.include_router(scheduler_api.router)
app.include_router(backup_api.router)


@app.get("/api/network")
async def network_info():
    """Return local network URL and QR code for mobile access."""
    return {
        "url": app.state.network_url,
        "qr_data_url": app.state.qr_data_url,
    }


# Serve frontend static files
# Mount the whole frontend directory at root so /app.js, /styles.css, /js/api.js etc all work
if settings.frontend_dir.exists():
    @app.get("/")
    async def index():
        return FileResponse(
            settings.frontend_dir / "index.html",
            headers={"Cache-Control": "no-store, must-revalidate"},
        )

    # StaticFiles subclass that disables caching so browsers always get the
    # latest frontend assets after a server update. This is a local-network
    # tool where fetch latency is negligible.
    class NoCacheStaticFiles(StaticFiles):
        async def get_response(self, path, scope):
            response = await super().get_response(path, scope)
            response.headers["Cache-Control"] = "no-store, must-revalidate"
            return response

    # Catch-all for static assets (styles.css, app.js, js/*.js, assets/*)
    # Must be mounted AFTER /api routes (which are registered above)
    app.mount(
        "/",
        NoCacheStaticFiles(directory=settings.frontend_dir, html=True),
        name="frontend",
    )
