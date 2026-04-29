"""Track LLM API usage for cost monitoring."""

from sqlalchemy.orm import Session

from app.models import Usage
from app.services.llm.base import LLMResponse


def log_usage(
    db: Session,
    profile_id: int,
    action: str,
    response: LLMResponse,
) -> Usage:
    """Record an LLM call in the usage table."""
    usage = Usage(
        profile_id=profile_id,
        action=action,
        provider=response.provider,
        model=response.model,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        cost_usd=response.cost_usd,
    )
    db.add(usage)
    db.commit()
    return usage
