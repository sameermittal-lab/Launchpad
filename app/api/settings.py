"""Settings endpoints - profile data, LLM config, toggles."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession

from app.database import get_db
from app.models import Profile
from app.services.llm.factory import get_provider_direct, list_all_models
from app.services.secrets import decrypt, encrypt
from app.utils.session import get_current_profile

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsResponse(BaseModel):
    # Profile basics
    id: int
    name: str
    role_title: Optional[str]
    profile_data: dict

    # LLM
    llm_provider: str
    llm_model: str
    has_llm_api_key: bool  # Don't return the actual key

    # Scoring
    scoring_weights: dict
    min_submit_score: float

    # Submission behavior
    require_approval: bool

    # Evaluation behavior
    web_grounded_eval: bool

    # Pass-history calibration
    pass_history_threshold: int
    pass_calibration_preference: str  # "auto" | "on" | "off"

    # Editor chat onboarding
    chat_onboarding_dismissed: bool

    # Asset generation defaults
    cover_letter_tone: str
    resume_format: str
    paper_size: str
    auto_evaluate: bool
    auto_generate_assets: bool

    # Scanner
    scan_interval_hours: int
    ai_monitor_interval_hours: int

    # Trusted job-alert senders (priority list)
    job_alert_senders: list[str]


class SettingsUpdate(BaseModel):
    # Profile
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    role_title: Optional[str] = Field(None, max_length=200)
    profile_data: Optional[dict] = None

    # LLM
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    llm_api_key: Optional[str] = None  # plaintext - will be encrypted

    # Scoring
    scoring_weights: Optional[dict] = None
    min_submit_score: Optional[float] = Field(None, ge=0, le=5)

    # Submission
    require_approval: Optional[bool] = None

    # Evaluation behavior
    web_grounded_eval: Optional[bool] = None

    # Pass-history calibration
    pass_history_threshold: Optional[int] = Field(None, ge=3, le=100)
    pass_calibration_preference: Optional[str] = None

    # Editor chat onboarding
    chat_onboarding_dismissed: Optional[bool] = None

    # Assets
    cover_letter_tone: Optional[str] = None
    resume_format: Optional[str] = None
    paper_size: Optional[str] = None
    auto_evaluate: Optional[bool] = None
    auto_generate_assets: Optional[bool] = None

    # Scanner
    scan_interval_hours: Optional[int] = Field(None, ge=1, le=168)
    ai_monitor_interval_hours: Optional[int] = Field(None, ge=1, le=168)

    # Trusted job-alert senders (priority list)
    job_alert_senders: Optional[list[str]] = None


class TestConnectionRequest(BaseModel):
    provider: str
    api_key: str
    model: str


@router.get("", response_model=SettingsResponse)
def get_settings(profile: Profile = Depends(get_current_profile)):
    return SettingsResponse(
        id=profile.id,
        name=profile.name,
        role_title=profile.role_title,
        profile_data=profile.profile_data or {},
        llm_provider=profile.llm_provider,
        llm_model=profile.llm_model,
        has_llm_api_key=bool(profile.llm_api_key_enc),
        scoring_weights=profile.scoring_weights or {},
        min_submit_score=profile.min_submit_score,
        require_approval=profile.require_approval,
        web_grounded_eval=bool(getattr(profile, "web_grounded_eval", True)),
        pass_history_threshold=int(getattr(profile, "pass_history_threshold", 15) or 15),
        pass_calibration_preference=str(getattr(profile, "pass_calibration_preference", "auto") or "auto"),
        chat_onboarding_dismissed=bool(getattr(profile, "chat_onboarding_dismissed", False)),
        cover_letter_tone=profile.cover_letter_tone,
        resume_format=profile.resume_format,
        paper_size=profile.paper_size,
        auto_evaluate=profile.auto_evaluate,
        auto_generate_assets=profile.auto_generate_assets,
        scan_interval_hours=profile.scan_interval_hours,
        ai_monitor_interval_hours=int(getattr(profile, "ai_monitor_interval_hours", 24) or 24),
        job_alert_senders=list(getattr(profile, "job_alert_senders", []) or []),
    )


VALID_WEIGHT_KEYS = {
    "role_match", "seniority_match", "skills", "comp",
    "growth", "s_curve", "culture", "location",
}


def _validate_weights(weights: dict) -> dict:
    """Normalize and validate a scoring_weights dict.

    - Unknown keys are rejected (HTTP 400)
    - Missing keys default to 0
    - Weights are re-normalized to sum to 1.0 so small rounding errors don't break math
    """
    cleaned: dict[str, float] = {}
    for k, v in weights.items():
        if k not in VALID_WEIGHT_KEYS:
            raise HTTPException(status_code=400, detail=f"Unknown scoring dimension: {k}")
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"Weight for {k} must be numeric")
        if f < 0:
            raise HTTPException(status_code=400, detail=f"Weight for {k} cannot be negative")
        cleaned[k] = f
    total = sum(cleaned.values())
    if total <= 0:
        raise HTTPException(status_code=400, detail="At least one weight must be > 0")
    # Fill missing dims with 0 and normalize
    for k in VALID_WEIGHT_KEYS:
        cleaned.setdefault(k, 0.0)
    return {k: round(cleaned[k] / total, 4) for k in VALID_WEIGHT_KEYS}


@router.put("", response_model=SettingsResponse)
def update_settings(
    data: SettingsUpdate,
    profile: Profile = Depends(get_current_profile),
    db: DBSession = Depends(get_db),
):
    """Update any subset of profile settings. Only provided fields are updated."""
    update_dict = data.model_dump(exclude_unset=True)

    # API key needs encryption
    if "llm_api_key" in update_dict:
        new_key = update_dict.pop("llm_api_key")
        if new_key:
            profile.llm_api_key_enc = encrypt(new_key)
        else:
            profile.llm_api_key_enc = None

    # Weights: validate + normalize
    if "scoring_weights" in update_dict and update_dict["scoring_weights"] is not None:
        update_dict["scoring_weights"] = _validate_weights(update_dict["scoring_weights"])

    # pass_calibration_preference must be one of {auto, on, off}
    if "pass_calibration_preference" in update_dict and update_dict["pass_calibration_preference"] is not None:
        v = str(update_dict["pass_calibration_preference"]).lower()
        if v not in {"auto", "on", "off"}:
            raise HTTPException(status_code=400, detail="pass_calibration_preference must be auto|on|off")
        update_dict["pass_calibration_preference"] = v

    # job_alert_senders: trim, dedupe, validate each entry has an "@"
    if "job_alert_senders" in update_dict and update_dict["job_alert_senders"] is not None:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in update_dict["job_alert_senders"]:
            if not isinstance(raw, str):
                continue
            entry = raw.strip().lower()
            if not entry or "@" not in entry:
                continue
            if entry in seen:
                continue
            seen.add(entry)
            cleaned.append(entry)
        if len(cleaned) > 50:
            raise HTTPException(status_code=400, detail="Too many job_alert_senders (max 50)")
        update_dict["job_alert_senders"] = cleaned

    # Apply remaining fields
    for field, value in update_dict.items():
        setattr(profile, field, value)

    db.commit()
    db.refresh(profile)

    return SettingsResponse(
        id=profile.id,
        name=profile.name,
        role_title=profile.role_title,
        profile_data=profile.profile_data or {},
        llm_provider=profile.llm_provider,
        llm_model=profile.llm_model,
        has_llm_api_key=bool(profile.llm_api_key_enc),
        scoring_weights=profile.scoring_weights or {},
        min_submit_score=profile.min_submit_score,
        require_approval=profile.require_approval,
        web_grounded_eval=bool(getattr(profile, "web_grounded_eval", True)),
        pass_history_threshold=int(getattr(profile, "pass_history_threshold", 15) or 15),
        pass_calibration_preference=str(getattr(profile, "pass_calibration_preference", "auto") or "auto"),
        chat_onboarding_dismissed=bool(getattr(profile, "chat_onboarding_dismissed", False)),
        cover_letter_tone=profile.cover_letter_tone,
        resume_format=profile.resume_format,
        paper_size=profile.paper_size,
        auto_evaluate=profile.auto_evaluate,
        auto_generate_assets=profile.auto_generate_assets,
        scan_interval_hours=profile.scan_interval_hours,
        ai_monitor_interval_hours=int(getattr(profile, "ai_monitor_interval_hours", 24) or 24),
        job_alert_senders=list(getattr(profile, "job_alert_senders", []) or []),
    )


@router.get("/llm-models")
def get_llm_models():
    """Return available models grouped by provider for settings dropdowns."""
    return list_all_models()


@router.post("/test-llm")
async def test_llm_connection(
    data: TestConnectionRequest,
    profile: Profile = Depends(get_current_profile),
):
    """Test LLM connection with provided credentials.

    Use case: user enters a new key and clicks 'Test'. We don't want to
    save the key yet, so we pass it directly instead of loading from DB.
    If api_key is empty, tries the stored key on the profile.
    """
    api_key = data.api_key
    if not api_key:
        if not profile.llm_api_key_enc:
            raise HTTPException(
                status_code=400,
                detail="No API key provided and none stored",
            )
        api_key = decrypt(profile.llm_api_key_enc)

    try:
        provider = get_provider_direct(data.provider, api_key, data.model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    result = await provider.test_connection()
    return result
