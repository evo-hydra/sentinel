"""Tests for core/llm_provider.py — LLMProvider, AnthropicProvider, OllamaProvider."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from sentinel.core.llm_provider import (
    AnthropicProvider,
    LLMProvider,
    LLMProviderError,
    OllamaProvider,
)


def test_llm_provider_is_abc() -> None:
    """LLMProvider cannot be instantiated directly."""
    with pytest.raises(TypeError):
        LLMProvider()  # type: ignore[abstract]


# --- Anthropic Provider ---


def test_anthropic_provider_no_api_key() -> None:
    """Should raise LLMProviderError when no API key is available."""
    env = {k: v for k, v in __import__("os").environ.items()
           if k not in ("ANTHROPIC_API_KEY", "SENTINEL_API_KEY")}
    with patch.dict("os.environ", env, clear=True), pytest.raises(LLMProviderError, match="No API key found"):
        AnthropicProvider()


def test_anthropic_provider_sentinel_api_key() -> None:
    """Should accept SENTINEL_API_KEY as fallback."""
    mock_anthropic = MagicMock()
    with (
        patch.dict("os.environ", {"SENTINEL_API_KEY": "test-key"}, clear=True),
        patch("sentinel.core.llm_provider.AnthropicProvider._create_client",
              return_value=mock_anthropic),
    ):
        provider = AnthropicProvider()
        assert provider._api_key == "test-key"


def test_anthropic_provider_anthropic_api_key_takes_precedence() -> None:
    """ANTHROPIC_API_KEY should take precedence over SENTINEL_API_KEY."""
    mock_anthropic = MagicMock()
    with (
        patch.dict("os.environ", {
            "ANTHROPIC_API_KEY": "primary-key",
            "SENTINEL_API_KEY": "fallback-key",
        }, clear=True),
        patch("sentinel.core.llm_provider.AnthropicProvider._create_client",
              return_value=mock_anthropic),
    ):
        provider = AnthropicProvider()
        assert provider._api_key == "primary-key"


def test_anthropic_provider_explicit_key() -> None:
    """Explicit api_key parameter should be used."""
    mock_anthropic = MagicMock()
    with patch("sentinel.core.llm_provider.AnthropicProvider._create_client",
                return_value=mock_anthropic):
        provider = AnthropicProvider(api_key="explicit-key")
        assert provider._api_key == "explicit-key"


def test_anthropic_provider_analyze_success() -> None:
    """Test successful analyze call."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='[{"line": 5, "severity": "high", "message": "issue"}]')]
    mock_client.messages.create.return_value = mock_response

    with patch("sentinel.core.llm_provider.AnthropicProvider._create_client",
                return_value=mock_client):
        provider = AnthropicProvider(api_key="test-key")
        result = provider.analyze("system", "user")

    assert "issue" in result
    mock_client.messages.create.assert_called_once()


def test_anthropic_provider_analyze_api_error() -> None:
    """API errors should be wrapped in LLMProviderError."""
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = RuntimeError("connection failed")

    with patch("sentinel.core.llm_provider.AnthropicProvider._create_client",
                return_value=mock_client):
        provider = AnthropicProvider(api_key="test-key")
        with pytest.raises(LLMProviderError, match="Anthropic API error"):
            provider.analyze("system", "user")


def test_anthropic_provider_custom_model() -> None:
    """Custom model and params should be stored."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="[]")]
    mock_client.messages.create.return_value = mock_response

    with patch("sentinel.core.llm_provider.AnthropicProvider._create_client",
                return_value=mock_client):
        provider = AnthropicProvider(api_key="key", model="claude-haiku-4-5-20251001", max_tokens=1024)
        provider.analyze("sys", "usr")

    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "claude-haiku-4-5-20251001"
    assert call_kwargs["max_tokens"] == 1024


# --- Ollama Provider ---


def _mock_ollama_tags(models: list[str]) -> bytes:
    """Build a fake /api/tags response."""
    return json.dumps({
        "models": [{"name": m} for m in models]
    }).encode()


def _mock_ollama_chat(content: str) -> bytes:
    """Build a fake /api/chat response."""
    return json.dumps({
        "message": {"role": "assistant", "content": content}
    }).encode()


def test_ollama_provider_connection_error() -> None:
    """Should raise LLMProviderError when Ollama is unreachable."""
    with pytest.raises(LLMProviderError, match="Cannot connect to Ollama"):
        OllamaProvider(base_url="http://localhost:99999")


def test_ollama_provider_model_not_found() -> None:
    """Should raise LLMProviderError when model isn't available."""
    tags_response = MagicMock()
    tags_response.read.return_value = _mock_ollama_tags(["llama3:latest", "mistral:7b"])
    tags_response.__enter__ = lambda s: s
    tags_response.__exit__ = MagicMock(return_value=False)

    with (
        patch("sentinel.core.llm_provider.urlopen", return_value=tags_response),
        pytest.raises(LLMProviderError, match="not found in Ollama"),
    ):
        OllamaProvider(model="nonexistent:model")


def test_ollama_provider_init_success() -> None:
    """Should initialize when model is available."""
    tags_response = MagicMock()
    tags_response.read.return_value = _mock_ollama_tags(["deepseek-coder:6.7b-instruct-q4_K_M"])
    tags_response.__enter__ = lambda s: s
    tags_response.__exit__ = MagicMock(return_value=False)

    with patch("sentinel.core.llm_provider.urlopen", return_value=tags_response):
        provider = OllamaProvider(model="deepseek-coder:6.7b-instruct-q4_K_M")
        assert provider._model == "deepseek-coder:6.7b-instruct-q4_K_M"


def test_ollama_provider_analyze_success() -> None:
    """Test successful analyze call to Ollama."""
    tags_response = MagicMock()
    tags_response.read.return_value = _mock_ollama_tags(["deepseek-coder:6.7b-instruct-q4_K_M"])
    tags_response.__enter__ = lambda s: s
    tags_response.__exit__ = MagicMock(return_value=False)

    chat_response = MagicMock()
    chat_response.read.return_value = _mock_ollama_chat('[{"line": 5, "severity": "high", "message": "issue"}]')
    chat_response.__enter__ = lambda s: s
    chat_response.__exit__ = MagicMock(return_value=False)

    call_count = 0

    def mock_urlopen(req, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return tags_response  # /api/tags
        return chat_response  # /api/chat

    with patch("sentinel.core.llm_provider.urlopen", side_effect=mock_urlopen):
        provider = OllamaProvider(model="deepseek-coder:6.7b-instruct-q4_K_M")
        result = provider.analyze("system prompt", "user prompt")

    assert "issue" in result


def test_ollama_provider_analyze_error() -> None:
    """API errors during chat should be wrapped in LLMProviderError."""
    from urllib.error import URLError

    tags_response = MagicMock()
    tags_response.read.return_value = _mock_ollama_tags(["deepseek-coder:6.7b-instruct-q4_K_M"])
    tags_response.__enter__ = lambda s: s
    tags_response.__exit__ = MagicMock(return_value=False)

    call_count = 0

    def mock_urlopen(req, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return tags_response
        raise URLError("connection reset")

    with patch("sentinel.core.llm_provider.urlopen", side_effect=mock_urlopen):
        provider = OllamaProvider(model="deepseek-coder:6.7b-instruct-q4_K_M")
        with pytest.raises(LLMProviderError, match="Ollama API error"):
            provider.analyze("system", "user")
