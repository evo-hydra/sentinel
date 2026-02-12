"""Provider factory — creates LLM/embedding providers from config without CLI dependencies."""

from __future__ import annotations

from sentinel.core.config import SentinelConfig
from sentinel.core.embedding_provider import (
    EmbeddingProvider,
    EmbeddingProviderError,
    OllamaEmbeddingProvider,
    OpenAIEmbeddingProvider,
)
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


def _create_for(provider_name: str, model: str, max_tokens: int, timeout: float) -> LLMProvider:
    """Internal helper: create a provider by name/model."""
    if provider_name == "ollama":
        return OllamaProvider(model=model, timeout=timeout)
    elif provider_name == "anthropic":
        try:
            import anthropic  # type: ignore[import-not-found]  # noqa: F401
        except ImportError:
            raise LLMProviderError(
                "LLM support requires the anthropic package. Run: pip install sentinel[llm]"
            ) from None
        return AnthropicProvider(model=model, max_tokens=max_tokens, timeout=timeout)
    elif provider_name in _OPENAI_COMPAT_PROVIDERS:
        resolved_model = model if model != _OLLAMA_DEFAULT_MODEL else None
        return OpenAICompatibleProvider(
            provider_name=provider_name, model=resolved_model,
            max_tokens=max_tokens, timeout=timeout,
        )
    else:
        raise LLMProviderError(
            f"Unknown LLM provider: {provider_name}. "
            f"Supported: ollama, anthropic, openai, gemini, grok"
        )


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
    return _create_for(
        config.llm_provider, config.llm_model,
        config.llm_max_tokens, config.llm_timeout,
    )


def create_enrich_provider(config: SentinelConfig) -> LLMProvider:
    """Create an LLM provider for enrichment (knowledge extraction).

    Uses enrich_provider/enrich_model from config, which default to
    anthropic/claude-haiku-4-5 for fast+cheap extraction.
    """
    return _create_for(
        config.enrich_provider, config.enrich_model,
        max_tokens=4096, timeout=config.llm_timeout,
    )


def create_embedding_provider(config: SentinelConfig) -> EmbeddingProvider:
    """Create the appropriate embedding provider based on config.

    Supported providers: ollama, openai.
    """
    if config.embed_provider == "ollama":
        return OllamaEmbeddingProvider(
            model=config.embed_model,
            base_url=config.llm_ollama_url,
            timeout=config.llm_timeout,
        )
    elif config.embed_provider == "openai":
        return OpenAIEmbeddingProvider(
            model=config.embed_model,
            timeout=config.llm_timeout,
        )
    else:
        raise EmbeddingProviderError(
            f"Unknown embedding provider: {config.embed_provider}. "
            f"Supported: ollama, openai"
        )
