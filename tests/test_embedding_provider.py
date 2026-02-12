"""Tests for core/embedding_provider.py — EmbeddingProvider, OllamaEmbeddingProvider, OpenAIEmbeddingProvider."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from sentinel.core.embedding_provider import (
    EmbeddingProvider,
    EmbeddingProviderError,
    OllamaEmbeddingProvider,
    OpenAIEmbeddingProvider,
)


def test_embedding_provider_is_abc() -> None:
    """EmbeddingProvider cannot be instantiated directly."""
    with pytest.raises(TypeError):
        EmbeddingProvider()  # type: ignore[abstract]


# --- Ollama Embedding Provider ---


def _mock_ollama_tags(models: list[str]) -> bytes:
    """Build a fake /api/tags response."""
    return json.dumps({
        "models": [{"name": m} for m in models]
    }).encode()


def _mock_ollama_embed(embeddings: list[list[float]]) -> bytes:
    """Build a fake /api/embed response."""
    return json.dumps({"embeddings": embeddings}).encode()


def test_ollama_embedding_connection_error() -> None:
    """Should raise EmbeddingProviderError when Ollama is unreachable."""
    with pytest.raises(EmbeddingProviderError, match="Cannot connect to Ollama"):
        OllamaEmbeddingProvider(base_url="http://localhost:99999")


def test_ollama_embedding_model_not_found() -> None:
    """Should raise EmbeddingProviderError when model isn't available."""
    tags_response = MagicMock()
    tags_response.read.return_value = _mock_ollama_tags(["llama3:latest"])
    tags_response.__enter__ = lambda s: s
    tags_response.__exit__ = MagicMock(return_value=False)

    with (
        patch("sentinel.core.embedding_provider.urlopen", return_value=tags_response),
        pytest.raises(EmbeddingProviderError, match="not found in Ollama"),
    ):
        OllamaEmbeddingProvider(model="nonexistent:model")


def test_ollama_embedding_init_success() -> None:
    """Should initialize when model is available."""
    tags_response = MagicMock()
    tags_response.read.return_value = _mock_ollama_tags(["nomic-embed-text:latest"])
    tags_response.__enter__ = lambda s: s
    tags_response.__exit__ = MagicMock(return_value=False)

    with patch("sentinel.core.embedding_provider.urlopen", return_value=tags_response):
        provider = OllamaEmbeddingProvider(model="nomic-embed-text")
        assert provider.model_name == "nomic-embed-text"


def test_ollama_embedding_embed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test single text embedding via Ollama."""
    tags_response = MagicMock()
    tags_response.read.return_value = _mock_ollama_tags(["nomic-embed-text:latest"])
    tags_response.__enter__ = lambda s: s
    tags_response.__exit__ = MagicMock(return_value=False)

    embed_response = MagicMock()
    embed_response.read.return_value = _mock_ollama_embed([[0.1, 0.2, 0.3]])
    embed_response.__enter__ = lambda s: s
    embed_response.__exit__ = MagicMock(return_value=False)

    call_count = 0

    def mock_urlopen(req, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return tags_response
        return embed_response

    with patch("sentinel.core.embedding_provider.urlopen", side_effect=mock_urlopen):
        provider = OllamaEmbeddingProvider(model="nomic-embed-text")
        result = provider.embed("test text")

    assert result == [0.1, 0.2, 0.3]


def test_ollama_embedding_embed_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test batch embedding via Ollama."""
    tags_response = MagicMock()
    tags_response.read.return_value = _mock_ollama_tags(["nomic-embed-text:latest"])
    tags_response.__enter__ = lambda s: s
    tags_response.__exit__ = MagicMock(return_value=False)

    embed_response = MagicMock()
    embed_response.read.return_value = _mock_ollama_embed([[0.1, 0.2], [0.3, 0.4]])
    embed_response.__enter__ = lambda s: s
    embed_response.__exit__ = MagicMock(return_value=False)

    call_count = 0

    def mock_urlopen(req, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return tags_response
        return embed_response

    with patch("sentinel.core.embedding_provider.urlopen", side_effect=mock_urlopen):
        provider = OllamaEmbeddingProvider(model="nomic-embed-text")
        result = provider.embed_batch(["text 1", "text 2"])

    assert len(result) == 2
    assert result[0] == [0.1, 0.2]
    assert result[1] == [0.3, 0.4]


def test_ollama_embedding_model_name() -> None:
    """model_name property should return the configured model."""
    tags_response = MagicMock()
    tags_response.read.return_value = _mock_ollama_tags(["nomic-embed-text:latest"])
    tags_response.__enter__ = lambda s: s
    tags_response.__exit__ = MagicMock(return_value=False)

    with patch("sentinel.core.embedding_provider.urlopen", return_value=tags_response):
        provider = OllamaEmbeddingProvider(model="nomic-embed-text")
        assert provider.model_name == "nomic-embed-text"


# --- OpenAI Embedding Provider ---


def test_openai_embedding_no_api_key() -> None:
    """Should raise EmbeddingProviderError when no API key is available."""
    with patch.dict("os.environ", {}, clear=True), pytest.raises(
        EmbeddingProviderError, match="OPENAI_API_KEY"
    ):
        OpenAIEmbeddingProvider()


def test_openai_embedding_missing_package(monkeypatch: pytest.MonkeyPatch) -> None:
    """Should raise EmbeddingProviderError when openai package not installed."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    with patch.dict("sys.modules", {"openai": None}), pytest.raises(
        EmbeddingProviderError, match="openai package"
    ):
        OpenAIEmbeddingProvider()


def test_openai_embedding_embed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test single text embedding via OpenAI."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    mock_client = MagicMock()
    mock_item = MagicMock()
    mock_item.embedding = [0.1, 0.2, 0.3]
    mock_response = MagicMock()
    mock_response.data = [mock_item]
    mock_client.embeddings.create.return_value = mock_response

    with patch.object(OpenAIEmbeddingProvider, "_create_client", return_value=mock_client):
        provider = OpenAIEmbeddingProvider()
        result = provider.embed("test text")

    assert result == [0.1, 0.2, 0.3]
    mock_client.embeddings.create.assert_called_once()


def test_openai_embedding_embed_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test batch embedding via OpenAI."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    mock_client = MagicMock()
    mock_item_1 = MagicMock()
    mock_item_1.embedding = [0.1, 0.2]
    mock_item_2 = MagicMock()
    mock_item_2.embedding = [0.3, 0.4]
    mock_response = MagicMock()
    mock_response.data = [mock_item_1, mock_item_2]
    mock_client.embeddings.create.return_value = mock_response

    with patch.object(OpenAIEmbeddingProvider, "_create_client", return_value=mock_client):
        provider = OpenAIEmbeddingProvider()
        result = provider.embed_batch(["text 1", "text 2"])

    assert len(result) == 2
    assert result[0] == [0.1, 0.2]
    assert result[1] == [0.3, 0.4]


def test_openai_embedding_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """API errors should be wrapped in EmbeddingProviderError."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    mock_client = MagicMock()
    mock_client.embeddings.create.side_effect = RuntimeError("connection failed")

    with patch.object(OpenAIEmbeddingProvider, "_create_client", return_value=mock_client):
        provider = OpenAIEmbeddingProvider()
        with pytest.raises(EmbeddingProviderError, match="OpenAI embedding error"):
            provider.embed("test")


def test_openai_embedding_model_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """model_name property should return the configured model."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    with patch.object(OpenAIEmbeddingProvider, "_create_client", return_value=MagicMock()):
        provider = OpenAIEmbeddingProvider(model="text-embedding-3-large")
        assert provider.model_name == "text-embedding-3-large"
