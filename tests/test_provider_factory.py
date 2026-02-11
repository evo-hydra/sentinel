"""Tests for core/provider_factory.py — create_provider."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sentinel.core.config import SentinelConfig
from sentinel.core.llm_provider import (
    LLMProviderError,
    OllamaProvider,
    OpenAICompatibleProvider,
)
from sentinel.core.provider_factory import create_provider


def test_create_provider_unknown_raises() -> None:
    """Unknown provider should raise LLMProviderError."""
    config = SentinelConfig(llm_provider="nonexistent")
    with pytest.raises(LLMProviderError, match="Unknown LLM provider"):
        create_provider(config)


def test_create_provider_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ollama provider creation (mocked to skip connection check)."""
    with patch.object(OllamaProvider, "_verify_connection"):
        config = SentinelConfig(llm_provider="ollama", llm_model="test-model")
        provider = create_provider(config)
        assert isinstance(provider, OllamaProvider)


def test_create_provider_anthropic_missing_package() -> None:
    """Anthropic provider should raise if package not installed."""
    config = SentinelConfig(llm_provider="anthropic")
    with patch.dict("sys.modules", {"anthropic": None}), pytest.raises(LLMProviderError, match="anthropic package"):
        create_provider(config)


def test_create_provider_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenAI provider creation with mocked SDK."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    with patch.object(OpenAICompatibleProvider, "_create_client", return_value=MagicMock()):
        config = SentinelConfig(llm_provider="openai")
        provider = create_provider(config)
        assert isinstance(provider, OpenAICompatibleProvider)
        assert provider._model == "gpt-4o-mini"  # default for openai


def test_create_provider_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gemini provider creation with mocked SDK."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    with patch.object(OpenAICompatibleProvider, "_create_client", return_value=MagicMock()):
        config = SentinelConfig(llm_provider="gemini")
        provider = create_provider(config)
        assert isinstance(provider, OpenAICompatibleProvider)
        assert provider._model == "gemini-2.0-flash"  # default for gemini


def test_create_provider_grok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Grok provider creation with mocked SDK."""
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    with patch.object(OpenAICompatibleProvider, "_create_client", return_value=MagicMock()):
        config = SentinelConfig(llm_provider="grok")
        provider = create_provider(config)
        assert isinstance(provider, OpenAICompatibleProvider)
        assert provider._model == "grok-2-latest"  # default for grok


def test_create_provider_openai_with_custom_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Custom model should override provider default."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    with patch.object(OpenAICompatibleProvider, "_create_client", return_value=MagicMock()):
        config = SentinelConfig(llm_provider="openai", llm_model="gpt-4o")
        provider = create_provider(config)
        assert provider._model == "gpt-4o"


def test_create_provider_openai_missing_key() -> None:
    """OpenAI provider should raise if API key not set."""
    config = SentinelConfig(llm_provider="openai")
    with patch.dict("os.environ", {}, clear=True), pytest.raises(LLMProviderError, match="OPENAI_API_KEY"):
        create_provider(config)


def test_create_provider_gemini_missing_key() -> None:
    """Gemini provider should raise if API key not set."""
    config = SentinelConfig(llm_provider="gemini")
    with patch.dict("os.environ", {}, clear=True), pytest.raises(LLMProviderError, match="GEMINI_API_KEY"):
        create_provider(config)


def test_create_provider_openai_missing_package(monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenAI provider should raise if openai package not installed."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    with patch.dict("sys.modules", {"openai": None}), pytest.raises(LLMProviderError, match="openai package"):
        create_provider(config=SentinelConfig(llm_provider="openai"))
