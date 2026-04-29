"""Scheduler status API."""

from fastapi import APIRouter, Depends

from app.models import Profile
from app.scheduler import get_status
from app.utils.session import get_current_profile

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


@router.get("/status")
def status(profile: Profile = Depends(get_current_profile)):
    return get_status()
