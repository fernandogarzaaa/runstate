"""Model provider abstraction for runstate.

A :class:`Provider` turns a list of chat messages into a :class:`ChatResult`
via a real HTTP call. :func:`get_provider` resolves the configured provider
from the environment with no silent fallbacks: a missing or unknown
configuration raises a clear error.
"""

from __future__ import annotations

import json
import os
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional


class ProviderError(Exception):
    """The provider call failed (transport, HTTP status, or bad payload)."""


class ProviderNotConfigured(Exception):
    """No usable provider configuration was found in the environment."""


@dataclass
class ChatResult:
    """The outcome of one provider completion call."""

    text: str
    prompt_tokens: int
    completion_tokens: int
    model: str


class Provider(ABC):
    """Abstract model provider."""

    @abstractmethod
    def complete(self, messages: list[dict], **kw: Any) -> ChatResult:
        """Run a chat completion over ``messages``.

        Args:
            messages: OpenAI-style message dicts (``role``/``content``).
            **kw: Provider-specific options (e.g. ``temperature``).

        Returns:
            A :class:`ChatResult` with text and token usage.

        Raises:
            ProviderError: If the call fails.
        """
        raise NotImplementedError


def _http_post(
    url: str, payload: dict, headers: dict, timeout: float = 60.0
) -> dict:
    """POST JSON and return the decoded JSON body.

    Separated for testability: tests monkeypatch this function instead of
    the network.
    """
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8")[:500]
        except Exception:  # noqa: BLE001 - best-effort error detail
            detail = "<unreadable>"
        raise ProviderError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ProviderError(f"request to {url} failed: {exc.reason}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProviderError(f"invalid JSON response from {url}: {exc}") from exc


class OpenAICompatibleProvider(Provider):
    """Chat completions against any OpenAI-compatible ``/chat/completions``.

    Args:
        base_url: API base, e.g. ``https://api.tokenfactory.nebius.com/v1``.
        api_key: Bearer credential. Never logged.
        model: Model id to request.
        timeout: HTTP timeout in seconds.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 120.0,
    ):
        if not base_url or not api_key or not model:
            raise ValueError("base_url, api_key, and model must all be non-empty")
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.model = model
        self.timeout = timeout

    def complete(self, messages: list[dict], **kw: Any) -> ChatResult:
        if not messages:
            raise ProviderError("messages must be non-empty")
        payload = {"model": self.model, "messages": messages}
        payload.update(kw)
        body = _http_post(
            f"{self.base_url}/chat/completions",
            payload,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            timeout=self.timeout,
        )
        try:
            choice = body["choices"][0]
            text = choice["message"]["content"]
            usage = body.get("usage", {})
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"malformed chat completion payload: {exc}") from exc
        if not isinstance(text, str):
            raise ProviderError("completion content was not a string")
        return ChatResult(
            text=text,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            model=self.model,
        )


#: Nebius Token Factory endpoint and default model (per the nebius skill).
NEBIUS_BASE_URL = "https://api.tokenfactory.nebius.com/v1"
NEBIUS_DEFAULT_MODEL = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"

#: OpenAI endpoint and default model.
OPENAI_BASE_URL = "https://api.openai.com/v1"
OPENAI_DEFAULT_MODEL = "gpt-4o-mini"


def get_provider(name: Optional[str] = None) -> Provider:
    """Resolve the configured provider.

    Args:
        name: ``"nebius"`` or ``"openai"``; defaults to the
            ``RUNSTATE_PROVIDER`` environment variable.

    Environment:
        RUNSTATE_PROVIDER: provider name when ``name`` is omitted.
        NEBIUS_API_KEY: required for ``nebius``.
        OPENAI_API_KEY: required for ``openai``.
        RUNSTATE_MODEL: overrides the default model for either provider.

    Raises:
        ProviderNotConfigured: If the provider name is missing/unknown or
            its API key environment variable is unset. Never falls back to
            a different provider silently.
    """
    resolved = (name or os.environ.get("RUNSTATE_PROVIDER") or "").strip().lower()
    if resolved == "nebius":
        api_key = os.environ.get("NEBIUS_API_KEY")
        if not api_key:
            raise ProviderNotConfigured(
                "nebius provider selected but NEBIUS_API_KEY is not set"
            )
        model = os.environ.get("RUNSTATE_MODEL") or NEBIUS_DEFAULT_MODEL
        return OpenAICompatibleProvider(NEBIUS_BASE_URL, api_key, model)
    if resolved == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ProviderNotConfigured(
                "openai provider selected but OPENAI_API_KEY is not set"
            )
        model = os.environ.get("RUNSTATE_MODEL") or OPENAI_DEFAULT_MODEL
        return OpenAICompatibleProvider(OPENAI_BASE_URL, api_key, model)
    if not resolved:
        raise ProviderNotConfigured(
            "no provider configured: set RUNSTATE_PROVIDER to 'nebius' or 'openai'"
        )
    raise ProviderNotConfigured(
        f"unknown provider {resolved!r}: expected 'nebius' or 'openai'"
    )
