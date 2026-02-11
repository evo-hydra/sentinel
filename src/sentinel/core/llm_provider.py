"""LLM provider abstraction — provider-agnostic interface for code analysis."""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


class LLMProviderError(Exception):
    """Raised when an LLM provider encounters an error."""


class LLMProvider(ABC):
    """Abstract base for LLM providers."""

    @abstractmethod
    def analyze(self, system_prompt: str, user_prompt: str) -> str:
        """Send a system + user prompt and return the model's response text."""


class OllamaProvider(LLMProvider):
    """Ollama local model provider. Zero dependencies — uses stdlib urllib."""

    def __init__(
        self,
        model: str = "deepseek-coder:6.7b-instruct-q4_K_M",
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
            raise LLMProviderError(
                f"Cannot connect to Ollama at {self._base_url}. "
                f"Is it running? Start with: ollama serve"
            ) from exc

        available = [m.get("name", "") for m in data.get("models", [])]
        # Check both exact match and base name (e.g. "llama3:latest" matches "llama3")
        if not any(self._model == name or self._model == name.split(":")[0] for name in available):
            raise LLMProviderError(
                f"Model '{self._model}' not found in Ollama. "
                f"Available: {', '.join(available)}. "
                f"Pull with: ollama pull {self._model}"
            )

    def analyze(self, system_prompt: str, user_prompt: str) -> str:
        """Send prompts to Ollama and return the response text."""
        payload = json.dumps({
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 4096},
        }).encode()

        req = Request(
            f"{self._base_url}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )

        try:
            with urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read())
            return str(data.get("message", {}).get("content", ""))
        except (URLError, OSError, json.JSONDecodeError) as exc:
            raise LLMProviderError(f"Ollama API error: {exc}") from exc


class AnthropicProvider(LLMProvider):
    """Anthropic Claude provider."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-5-20250929",
        max_tokens: int = 4096,
        timeout: float = 30.0,
    ) -> None:
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get(
            "SENTINEL_API_KEY"
        )
        if not self._api_key:
            raise LLMProviderError(
                "No API key found. Set ANTHROPIC_API_KEY or SENTINEL_API_KEY environment variable."
            )
        self._model = model
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._client = self._create_client()

    def _create_client(self) -> Any:
        """Create the Anthropic client. Separated for testability."""
        try:
            import anthropic  # type: ignore[import-not-found]
        except ImportError:
            raise LLMProviderError(
                "anthropic package not installed. Run: pip install sentinel[llm]"
            ) from None
        return anthropic.Anthropic(api_key=self._api_key, timeout=self._timeout)

    def analyze(self, system_prompt: str, user_prompt: str) -> str:
        """Send prompts to Claude and return the response text."""
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return str(response.content[0].text)
        except Exception as exc:
            raise LLMProviderError(f"Anthropic API error: {exc}") from exc


# --- Provider defaults for OpenAI-compatible APIs ---

_OPENAI_DEFAULTS: dict[str, dict[str, str]] = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "env_key": "OPENAI_API_KEY",
        "label": "OpenAI",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.0-flash",
        "env_key": "GEMINI_API_KEY",
        "label": "Gemini",
    },
    "grok": {
        "base_url": "https://api.x.ai/v1",
        "model": "grok-2-latest",
        "env_key": "XAI_API_KEY",
        "label": "Grok",
    },
}


_REASONING_MODEL_PREFIXES = ("o1", "o3")


class OpenAICompatibleProvider(LLMProvider):
    """Provider for any OpenAI-compatible chat completions API.

    Covers OpenAI, Gemini (via OpenAI compat endpoint), and Grok (xAI).
    Uses the openai Python SDK — one dependency, three providers.

    Automatically detects OpenAI reasoning models (o1, o3) and adjusts
    parameters: uses max_completion_tokens, drops temperature, and
    folds system prompt into the user message.
    """

    def __init__(
        self,
        provider_name: str = "openai",
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        max_tokens: int = 4096,
        timeout: float = 60.0,
    ) -> None:
        defaults = _OPENAI_DEFAULTS.get(provider_name, _OPENAI_DEFAULTS["openai"])
        self._label = defaults["label"]
        self._base_url = base_url or defaults["base_url"]
        self._model = model or defaults["model"]
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._is_reasoning = any(
            self._model.startswith(p) for p in _REASONING_MODEL_PREFIXES
        )

        self._api_key = api_key or os.environ.get(defaults["env_key"]) or ""
        if not self._api_key:
            raise LLMProviderError(
                f"No API key found for {self._label}. "
                f"Set {defaults['env_key']} environment variable."
            )

        self._client = self._create_client()

    def _create_client(self) -> Any:
        """Create the OpenAI client. Separated for testability."""
        try:
            from openai import OpenAI  # type: ignore[import-not-found]
        except ImportError:
            raise LLMProviderError(
                "openai package not installed. Run: pip install sentinel[llm]"
            ) from None
        return OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._timeout,
        )

    def analyze(self, system_prompt: str, user_prompt: str) -> str:
        """Send prompts and return the response text."""
        try:
            if self._is_reasoning:
                # Reasoning models: no system role, no temperature, use max_completion_tokens
                combined = f"{system_prompt}\n\n{user_prompt}"
                response = self._client.chat.completions.create(
                    model=self._model,
                    max_completion_tokens=self._max_tokens,
                    messages=[{"role": "user", "content": combined}],
                )
            else:
                response = self._client.chat.completions.create(
                    model=self._model,
                    max_tokens=self._max_tokens,
                    temperature=0.1,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                )
            return str(response.choices[0].message.content or "")
        except Exception as exc:
            raise LLMProviderError(f"{self._label} API error: {exc}") from exc
