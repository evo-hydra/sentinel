"""Provider factory — creates LLM providers from config without CLI dependencies."""

from __future__ import annotations

from sentinel.core.config import SentinelConfig
from sentinel.core.llm_provider import (
    AnthropicProvider,
    LLMProvider,
    LLMProviderError,
    OllamaProvider,
    OpenAICompatibleProvider,
)

# Providers that use the OpenAI-compatible API
_OPENAI_COMPAT_PROVIDERS = {"openai", "gemini", "grok"}

# The default Ollama model — used to detect "user didn't override model"
_OLLAMA_DEFAULT_MODEL = "deepseek-coder:6.7b-instruct-q4_K_M"


def create_provider(config: SentinelConfig) -> LLMProvider:
    """Create the appropriate LLM provider based on config.

    Supported providers: ollama, anthropic, openai, gemini, grok.
    """
    if config.llm_provider == "ollama":
        return OllamaProvider(
            model=config.llm_model,
            base_url=config.llm_ollama_url,
            timeout=config.llm_timeout,
        )
    elif config.llm_provider == "anthropic":
        try:
            import anthropic  # type: ignore[import-not-found]  # noqa: F401
        except ImportError:
            raise LLMProviderError(
                "LLM support requires the anthropic package. Run: pip install sentinel[llm]"
            ) from None
        return AnthropicProvider(
            model=config.llm_model,
            max_tokens=config.llm_max_tokens,
            timeout=config.llm_timeout,
        )
    elif config.llm_provider in _OPENAI_COMPAT_PROVIDERS:
        # If model is still the Ollama default, let the provider use its own default
        model = config.llm_model if config.llm_model != _OLLAMA_DEFAULT_MODEL else None
        return OpenAICompatibleProvider(
            provider_name=config.llm_provider,
            model=model,
            max_tokens=config.llm_max_tokens,
            timeout=config.llm_timeout,
        )
    else:
        raise LLMProviderError(
            f"Unknown LLM provider: {config.llm_provider}. "
            f"Supported: ollama, anthropic, openai, gemini, grok"
        )
