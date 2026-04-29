"""Authentication endpoints."""

from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from app.database import get_db
from app.models import Profile, UserSession
from app.utils.auth import verify_pin
from app.utils.session import (
    SESSION_COOKIE_NAME,
    clear_session_cookie,
    create_session,
    get_current_profile,
    set_session_cookie,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    profile_id: int
    pin: Optional[str] = None


class LoginResponse(BaseModel):
    id: int
    name: str
    role_title: Optional[str]


@router.post("/login", response_model=LoginResponse)
def login(
    data: LoginRequest,
    request: Request,
    response: Response,
    db: DBSession = Depends(get_db),
):
    """Log in to a profile with optional PIN."""
    profile = db.get(Profile, data.profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    if not verify_pin(data.pin or "", profile.pin_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect PIN",
        )

    session = create_session(db, profile.id, request)
    set_session_cookie(response, session.id)

    return LoginResponse(
        id=profile.id,
        name=profile.name,
        role_title=profile.role_title,
    )


@router.post("/logout")
def logout(
    response: Response,
    launchpad_session: Optional[str] = Cookie(None),
    db: DBSession = Depends(get_db),
):
    """Log out, clearing the session."""
    if launchpad_session:
        session = db.get(UserSession, launchpad_session)
        if session:
            db.delete(session)
            db.commit()
    clear_session_cookie(response)
    return {"logged_out": True}


@router.get("/me", response_model=LoginResponse)
def me(profile: Profile = Depends(get_current_profile)):
    """Return the currently logged-in profile."""
    return LoginResponse(
        id=profile.id,
        name=profile.name,
        role_title=profile.role_title,
    )
