"""OpenAI provider."""

from __future__ import annotations

import logging
from typing import Optional

from openai import AsyncOpenAI

from app.services.llm.base import Citation, LLMProvider, LLMResponse

logger = logging.getLogger(__name__)


# Pricing per million tokens (USD) as of 2026-04
PRICING = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    "o1": {"input": 15.00, "output": 60.00},
    "o1-mini": {"input": 3.00, "output": 12.00},
    "gpt-4.1": {"input": 2.00, "output": 8.00},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
}

MODELS = [
    {"id": "gpt-4o", "name": "GPT-4o", "description": "Recommended for quality"},
    {"id": "gpt-4o-mini", "name": "GPT-4o mini", "description": "Fast and cheap"},
    {"id": "gpt-4.1", "name": "GPT-4.1", "description": "Latest flagship"},
    {"id": "gpt-4.1-mini", "name": "GPT-4.1 mini", "description": "Latest flagship, smaller"},
    {"id": "o1-mini", "name": "o1-mini", "description": "Reasoning model, slower but deeper"},
]

# Small per-call surcharge OpenAI adds when web_search tool is used.
# (Low-context preview: $0.025/call; medium: $0.030; high: $0.050.)
WEB_SEARCH_SURCHARGE = 0.03


class OpenAIProvider(LLMProvider):
    provider_name = "openai"

    def __init__(self, api_key: str, model: str):
        super().__init__(api_key, model)
        self.client = AsyncOpenAI(api_key=api_key)

    async def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 4096,
        temperature: float = 0.4,
    ) -> LLMResponse:
        resp = await self.client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        text = resp.choices[0].message.content or ""
        pt = resp.usage.prompt_tokens
        ct = resp.usage.completion_tokens
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
        """Use OpenAI Responses API with the web_search_preview tool.

        The model decides when to search. Returns text + citations extracted
        from the annotations in the response.

        Raises a descriptive error if the Responses API isn't available -
        we refuse to silently fall back to hallucinated non-search output.
        """
        if not hasattr(self.client, "responses"):
            raise RuntimeError(
                "Your OpenAI SDK is too old to support web search. "
                "Upgrade with: pip install --upgrade 'openai>=1.60'"
            )

        try:
            resp = await self.client.responses.create(
                model=self.model,
                instructions=system,
                input=user,
                tools=[{"type": "web_search_preview"}],
                max_output_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as exc:
            raise RuntimeError(
                f"OpenAI web search failed: {exc}. "
                "Check that your OpenAI account has web_search enabled and the model supports it."
            )

        text = self._extract_response_text(resp)
        citations = self._extract_citations(resp)

        if not citations:
            logger.warning(
                "OpenAI web search returned zero citations - model may not have actually searched. "
                "Response may contain stale or hallucinated data."
            )

        usage = getattr(resp, "usage", None)
        pt = getattr(usage, "input_tokens", 0) if usage else 0
        ct = getattr(usage, "output_tokens", 0) if usage else 0
        cost = self.estimate_cost(pt, ct) + WEB_SEARCH_SURCHARGE

        return LLMResponse(
            text=text,
            prompt_tokens=pt,
            completion_tokens=ct,
            cost_usd=cost,
            model=self.model,
            provider=self.provider_name,
            citations=citations,
        )

    @staticmethod
    def _extract_response_text(resp) -> str:
        """Walk the Responses API output to collect plain text."""
        out_parts = []
        output = getattr(resp, "output", None) or []
        for item in output:
            item_type = getattr(item, "type", None)
            if item_type == "message":
                for content in getattr(item, "content", []) or []:
                    if getattr(content, "type", None) in ("output_text", "text"):
                        txt = getattr(content, "text", "") or ""
                        out_parts.append(txt)
        if not out_parts and hasattr(resp, "output_text"):
            # SDK convenience accessor
            return resp.output_text or ""
        return "\n".join(out_parts).strip()

    @staticmethod
    def _extract_citations(resp) -> list[Citation]:
        """Pull URL citations from the annotations in message output."""
        seen: set[str] = set()
        citations: list[Citation] = []
        output = getattr(resp, "output", None) or []
        for item in output:
            if getattr(item, "type", None) != "message":
                continue
            for content in getattr(item, "content", []) or []:
                for ann in getattr(content, "annotations", []) or []:
                    if getattr(ann, "type", None) == "url_citation":
                        url = getattr(ann, "url", "") or ""
                        if not url or url in seen:
                            continue
                        seen.add(url)
                        citations.append(Citation(
                            title=getattr(ann, "title", "") or url,
                            url=url,
                            snippet=None,
                        ))
        return citations

    def estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        prices = PRICING.get(self.model, {"input": 2.50, "output": 10.00})
        return (
            (prompt_tokens / 1_000_000) * prices["input"]
            + (completion_tokens / 1_000_000) * prices["output"]
        )

    @classmethod
    def available_models(cls) -> list[dict]:
        return MODELS
