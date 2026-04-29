"""Session dependencies for FastAPI."""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.database import get_db
from app.models import Profile, UserSession
from app.utils.auth import generate_session_token

SESSION_COOKIE_NAME = "launchpad_session"


def create_session(
    db: DBSession,
    profile_id: int,
    request: Request,
) -> UserSession:
    """Create a new session for a profile."""
    token = generate_session_token()
    user_session = UserSession(
        id=token,
        profile_id=profile_id,
        expires_at=datetime.utcnow() + timedelta(days=settings.session_lifetime_days),
        user_agent=request.headers.get("user-agent", "")[:500],
        ip_address=request.client.host if request.client else None,
    )
    db.add(user_session)
    db.commit()
    return user_session


def set_session_cookie(response: Response, token: str) -> None:
    """Attach the session cookie to a response."""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.session_lifetime_days * 86400,
        httponly=True,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")


def get_current_profile(
    launchpad_session: Optional[str] = Cookie(None),
    db: DBSession = Depends(get_db),
) -> Profile:
    """FastAPI dependency that returns the current logged-in profile or 401."""
    if not launchpad_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    session = db.get(UserSession, launchpad_session)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session",
        )
    if session.expires_at < datetime.utcnow():
        db.delete(session)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired",
        )
    profile = db.get(Profile, session.profile_id)
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Profile not found",
        )
    # Refresh last activity
    session.last_activity_at = datetime.utcnow()
    db.commit()
    return profile
