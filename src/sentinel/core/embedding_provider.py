"""Embedding provider abstraction — provider-agnostic interface for text embeddings."""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


class EmbeddingProviderError(Exception):
    """Raised when an embedding provider encounters an error."""


class EmbeddingProvider(ABC):
    """Abstract base for embedding providers."""

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Generate an embedding vector for a single text."""

    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embedding vectors for multiple texts."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return the model name used for embeddings."""


class OllamaEmbeddingProvider(EmbeddingProvider):
    """Ollama embedding provider. Zero dependencies — uses stdlib urllib."""

    def __init__(
        self,
        model: str = "nomic-embed-text",
        base_url: str = "http://localhost:11434",
        timeout: float = 120.0,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._verify_connection()

    def _verify_connection(self) -> None:
        """Check that Ollama is running and the model is available."""
        try:
            req = Request(f"{self._base_url}/api/tags")
            with urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
        except (URLError, OSError) as exc:
            raise EmbeddingProviderError(
                f"Cannot connect to Ollama at {self._base_url}. "
                f"Is it running? Start with: ollama serve"
            ) from exc

        available = [m.get("name", "") for m in data.get("models", [])]
        if not any(self._model == name or self._model == name.split(":")[0] for name in available):
            raise EmbeddingProviderError(
                f"Model '{self._model}' not found in Ollama. "
                f"Available: {', '.join(available)}. "
                f"Pull with: ollama pull {self._model}"
            )

    def embed(self, text: str) -> list[float]:
        """Generate embedding via Ollama /api/embed endpoint."""
        result = self.embed_batch([text])
        return result[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts in one request."""
        payload = json.dumps({
            "model": self._model,
            "input": texts,
        }).encode()

        req = Request(
            f"{self._base_url}/api/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
        )

        try:
            with urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read())
            embeddings = data.get("embeddings", [])
            if len(embeddings) != len(texts):
                raise EmbeddingProviderError(
                    f"Expected {len(texts)} embeddings, got {len(embeddings)}"
                )
            return [list(e) for e in embeddings]
        except (URLError, OSError, json.JSONDecodeError) as exc:
            raise EmbeddingProviderError(f"Ollama embedding error: {exc}") from exc

    @property
    def model_name(self) -> str:
        return self._model


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI embedding provider. Uses the openai SDK (part of [llm] extra)."""

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._model = model
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY") or ""
        if not self._api_key:
            raise EmbeddingProviderError(
                "No API key found for OpenAI. Set OPENAI_API_KEY environment variable."
            )
        self._timeout = timeout
        self._client = self._create_client()

    def _create_client(self):  # type: ignore[no-untyped-def]
        """Create the OpenAI client. Separated for testability."""
        try:
            from openai import OpenAI  # type: ignore[import-not-found]
        except ImportError:
            raise EmbeddingProviderError(
                "openai package not installed. Run: pip install sentinel[llm]"
            ) from None
        return OpenAI(api_key=self._api_key, timeout=self._timeout)

    def embed(self, text: str) -> list[float]:
        """Generate embedding via OpenAI API."""
        result = self.embed_batch([text])
        return result[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts via OpenAI batch API."""
        try:
            response = self._client.embeddings.create(
                model=self._model,
                input=texts,
            )
            return [list(item.embedding) for item in response.data]
        except Exception as exc:
            logger.error("OpenAI embedding error: %s", exc)
            raise EmbeddingProviderError(f"OpenAI embedding error: {exc}") from exc

    @property
    def model_name(self) -> str:
        return self._model
