"""Factory for getting the right LLM provider instance for a profile."""

from app.services.llm.anthropic import AnthropicProvider
from app.services.llm.base import LLMProvider
from app.services.llm.gemini import GeminiProvider
from app.services.llm.openai import OpenAIProvider
from app.services.secrets import decrypt


_PROVIDERS: dict[str, type[LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
}


def get_provider_class(provider_name: str) -> type[LLMProvider]:
    cls = _PROVIDERS.get(provider_name)
    if not cls:
        raise ValueError(f"Unknown LLM provider: {provider_name}")
    return cls


def get_provider(profile) -> LLMProvider:
    """Instantiate the LLM provider for a profile using its stored settings."""
    if not profile.llm_api_key_enc:
        raise ValueError(
            f"No API key configured for {profile.llm_provider}. "
            "Add one in Settings."
        )
    api_key = decrypt(profile.llm_api_key_enc)
    cls = get_provider_class(profile.llm_provider)
    return cls(api_key=api_key, model=profile.llm_model)


def get_provider_direct(
    provider_name: str,
    api_key: str,
    model: str,
) -> LLMProvider:
    """Instantiate a provider with explicit credentials (used for test-connection)."""
    cls = get_provider_class(provider_name)
    return cls(api_key=api_key, model=model)


def list_all_models() -> dict[str, list[dict]]:
    """Return the model list for each provider, for the settings dropdown."""
    return {name: cls.available_models() for name, cls in _PROVIDERS.items()}
