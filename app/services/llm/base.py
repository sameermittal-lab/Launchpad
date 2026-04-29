"""Abstract LLM provider interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Citation:
    """One source returned by a web-grounded LLM call."""
    title: str
    url: str
    snippet: Optional[str] = None


@dataclass
class LLMResponse:
    """Normalized response from any LLM provider."""
    text: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    model: str
    provider: str
    citations: list[Citation] = field(default_factory=list)


class LLMProvider(ABC):
    """Base class for LLM providers (Anthropic, OpenAI, Gemini)."""

    provider_name: str = "unknown"

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    @abstractmethod
    async def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 4096,
        temperature: float = 0.4,
    ) -> LLMResponse:
        """Send a completion request without web search."""

    @abstractmethod
    async def complete_with_search(
        self,
        system: str,
        user: str,
        max_tokens: int = 4096,
        temperature: float = 0.4,
    ) -> LLMResponse:
        """Send a completion request with native web search enabled.

        The model may decide to run one or more searches. Citations in
        the response indicate sources the model consulted.
        """

    @abstractmethod
    def estimate_cost(
        self,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> float:
        """Estimate cost in USD for given token counts."""

    @classmethod
    @abstractmethod
    def available_models(cls) -> list[dict]:
        """Return list of {id, name, description} for this provider's models."""

    async def test_connection(self) -> dict:
        """Make a tiny test call to verify credentials work."""
        try:
            import time
            start = time.time()
            resp = await self.complete(
                system="You are a test assistant.",
                user="Reply with just the word: ok",
                max_tokens=5,
            )
            elapsed_ms = int((time.time() - start) * 1000)
            return {
                "success": True,
                "latency_ms": elapsed_ms,
                "model": resp.model,
                "cost_usd": resp.cost_usd,
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}
