"""Anthropic Claude provider."""

from __future__ import annotations

import logging

from anthropic import AsyncAnthropic

from app.services.llm.base import Citation, LLMProvider, LLMResponse

logger = logging.getLogger(__name__)


# Pricing per million tokens (USD) as of 2026-04
PRICING = {
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
    "claude-opus-4-20250514": {"input": 15.00, "output": 75.00},
    "claude-3-5-haiku-20241022": {"input": 0.80, "output": 4.00},
    "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00},
    "claude-3-opus-20240229": {"input": 15.00, "output": 75.00},
}

MODELS = [
    {
        "id": "claude-sonnet-4-20250514",
        "name": "Claude Sonnet 4",
        "description": "Best balance of quality and cost (recommended)",
    },
    {
        "id": "claude-3-5-haiku-20241022",
        "name": "Claude Haiku 3.5",
        "description": "Fast and cheap, good for simple tasks",
    },
    {
        "id": "claude-opus-4-20250514",
        "name": "Claude Opus 4",
        "description": "Highest quality, most expensive",
    },
]

# Anthropic web search tool pricing: $10 per 1,000 searches = $0.01/search
WEB_SEARCH_SURCHARGE = 0.01


class AnthropicProvider(LLMProvider):
    provider_name = "anthropic"

    def __init__(self, api_key: str, model: str):
        super().__init__(api_key, model)
        self.client = AsyncAnthropic(api_key=api_key)

    async def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 4096,
        temperature: float = 0.4,
    ) -> LLMResponse:
        resp = await self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(
            block.text for block in resp.content if hasattr(block, "text")
        )
        pt = resp.usage.input_tokens
        ct = resp.usage.output_tokens
        return LLMResponse(
            text=text,
            prompt_tokens=pt,
            completion_tokens=ct,
            cost_usd=self.estimate_cost(pt, ct),
            model=self.model,
            provider=self.provider_name,
        )

    async def complete_with_search(
        self,
        system: str,
        user: str,
        max_tokens: int = 4096,
        temperature: float = 0.4,
    ) -> LLMResponse:
        """Claude Messages API with the web_search tool.

        Raises RuntimeError if the tool fails - never silently falls back
        to non-search output which would return stale training data.
        """
        try:
            resp = await self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=[{"role": "user", "content": user}],
                tools=[{
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 5,
                }],
            )
        except Exception as exc:
            raise RuntimeError(
                f"Anthropic web search failed: {exc}. "
                "Check that your Anthropic account has web_search enabled for this model."
            )

        text_parts: list[str] = []
        citations: list[Citation] = []
        seen_urls: set[str] = set()

        for block in resp.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(getattr(block, "text", "") or "")
                # Citations may be attached to text blocks
                for c in getattr(block, "citations", None) or []:
                    url = getattr(c, "url", "") or ""
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    citations.append(Citation(
                        title=getattr(c, "title", "") or url,
                        url=url,
                        snippet=None,
                    ))
            elif btype == "web_search_tool_result":
                # content is a list of search results
                for result in getattr(block, "content", None) or []:
                    url = getattr(result, "url", "") or ""
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    citations.append(Citation(
                        title=getattr(result, "title", "") or url,
                        url=url,
                        snippet=getattr(result, "encrypted_content", None) and "",
                    ))

        text = "\n".join(text_parts).strip()
        pt = resp.usage.input_tokens
        ct = resp.usage.output_tokens
        # Estimate search count by web_search_tool_result blocks
        search_uses = sum(
            1 for b in resp.content if getattr(b, "type", None) == "web_search_tool_result"
        )
        cost = self.estimate_cost(pt, ct) + WEB_SEARCH_SURCHARGE * max(search_uses, 1)

        return LLMResponse(
            text=text,
            prompt_tokens=pt,
            completion_tokens=ct,
            cost_usd=cost,
            model=self.model,
            provider=self.provider_name,
            citations=citations,
        )

    def estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        prices = PRICING.get(self.model, {"input": 3.00, "output": 15.00})
        return (
            (prompt_tokens / 1_000_000) * prices["input"]
            + (completion_tokens / 1_000_000) * prices["output"]
        )

    @classmethod
    def available_models(cls) -> list[dict]:
        return MODELS
