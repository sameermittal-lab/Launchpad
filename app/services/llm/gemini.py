"""Google Gemini provider."""

from __future__ import annotations

import logging

import google.generativeai as genai

from app.services.llm.base import Citation, LLMProvider, LLMResponse

logger = logging.getLogger(__name__)


# Pricing per million tokens (USD) as of 2026-04
PRICING = {
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
    "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
    "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
}

MODELS = [
    {"id": "gemini-2.0-flash", "name": "Gemini 2.0 Flash", "description": "Fast and cheap, free tier"},
    {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash", "description": "Latest fast model"},
    {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro", "description": "Higher quality"},
    {"id": "gemini-1.5-pro", "name": "Gemini 1.5 Pro", "description": "Previous flagship, 2M context"},
]

# Google search grounding: ~$0.035/request over free quota
WEB_SEARCH_SURCHARGE = 0.035


class GeminiProvider(LLMProvider):
    provider_name = "gemini"

    def __init__(self, api_key: str, model: str):
        super().__init__(api_key, model)
        genai.configure(api_key=api_key)

    async def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 4096,
        temperature: float = 0.4,
    ) -> LLMResponse:
        client = genai.GenerativeModel(self.model)
        full_prompt = f"{system}\n\n{user}"
        resp = await client.generate_content_async(
            full_prompt,
            generation_config={
                "max_output_tokens": max_tokens,
                "temperature": temperature,
            },
        )
        text = resp.text
        usage = getattr(resp, "usage_metadata", None)
        pt = getattr(usage, "prompt_token_count", 0) if usage else 0
        ct = getattr(usage, "candidates_token_count", 0) if usage else 0
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
        """Gemini with Google Search grounding enabled.

        Raises RuntimeError on failure - does NOT silently fall back to
        non-grounded completion.
        """
        try:
            client = genai.GenerativeModel(
                self.model,
                tools=[{"google_search": {}}],
            )
            full_prompt = f"{system}\n\n{user}"
            resp = await client.generate_content_async(
                full_prompt,
                generation_config={
                    "max_output_tokens": max_tokens,
                    "temperature": temperature,
                },
            )
        except Exception as exc:
            raise RuntimeError(
                f"Gemini search grounding failed: {exc}. "
                "Check your API key supports Google Search grounding for this model."
            )

        text = resp.text
        citations = self._extract_citations(resp)
        usage = getattr(resp, "usage_metadata", None)
        pt = getattr(usage, "prompt_token_count", 0) if usage else 0
        ct = getattr(usage, "candidates_token_count", 0) if usage else 0
        surcharge = WEB_SEARCH_SURCHARGE if citations else 0.0
        return LLMResponse(
            text=text,
            prompt_tokens=pt,
            completion_tokens=ct,
            cost_usd=self.estimate_cost(pt, ct) + surcharge,
            model=self.model,
            provider=self.provider_name,
            citations=citations,
        )

    @staticmethod
    def _extract_citations(resp) -> list[Citation]:
        citations: list[Citation] = []
        seen: set[str] = set()
        try:
            candidate = resp.candidates[0]
            metadata = getattr(candidate, "grounding_metadata", None)
            if not metadata:
                return citations
            chunks = getattr(metadata, "grounding_chunks", None) or []
            for chunk in chunks:
                web = getattr(chunk, "web", None)
                if not web:
                    continue
                url = getattr(web, "uri", "") or ""
                if not url or url in seen:
                    continue
                seen.add(url)
                citations.append(Citation(
                    title=getattr(web, "title", "") or url,
                    url=url,
                    snippet=None,
                ))
        except Exception as exc:
            logger.debug(f"Could not extract Gemini citations: {exc}")
        return citations

    def estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        prices = PRICING.get(self.model, {"input": 0.10, "output": 0.40})
        return (
            (prompt_tokens / 1_000_000) * prices["input"]
            + (completion_tokens / 1_000_000) * prices["output"]
        )

    @classmethod
    def available_models(cls) -> list[dict]:
        return MODELS
