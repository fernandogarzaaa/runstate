"""Tests for runstate.runtime.providers (HTTP layer is mocked, logic is real)."""

import pytest

from runstate.runtime import providers
from runstate.runtime.providers import (
    ChatResult,
    OpenAICompatibleProvider,
    ProviderError,
    ProviderNotConfigured,
    get_provider,
)


def _ok_body():
    return {
        "choices": [{"message": {"role": "assistant", "content": "hello there"}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3},
    }


def _patch_http(monkeypatch, body=None, exc=None):
    def fake_post(url, payload, headers, timeout=60.0):
        fake_post.calls.append((url, payload, headers, timeout))
        if exc is not None:
            raise exc
        return body

    fake_post.calls = []
    monkeypatch.setattr(providers, "_http_post", fake_post)
    return fake_post


def test_complete_success(monkeypatch):
    fake = _patch_http(monkeypatch, body=_ok_body())
    p = OpenAICompatibleProvider("https://x.test/v1", "sekret", "m")
    result = p.complete([{"role": "user", "content": "hi"}], temperature=0)
    assert isinstance(result, ChatResult)
    assert result.text == "hello there"
    assert result.prompt_tokens == 12
    assert result.completion_tokens == 3
    assert result.model == "m"
    url, payload, headers, _ = fake.calls[0]
    assert url == "https://x.test/v1/chat/completions"
    assert payload["model"] == "m"
    assert payload["messages"] == [{"role": "user", "content": "hi"}]
    assert payload["temperature"] == 0
    assert headers["Authorization"] == "Bearer sekret"
    assert headers["Content-Type"] == "application/json"


def test_complete_empty_messages(monkeypatch):
    _patch_http(monkeypatch, body=_ok_body())
    p = OpenAICompatibleProvider("https://x.test/v1", "k", "m")
    with pytest.raises(ProviderError):
        p.complete([])


def test_complete_http_error_propagates(monkeypatch):
    _patch_http(monkeypatch, exc=ProviderError("HTTP 401 from https://x: bad key"))
    p = OpenAICompatibleProvider("https://x.test/v1", "k", "m")
    with pytest.raises(ProviderError, match="401"):
        p.complete([{"role": "user", "content": "hi"}])


def test_complete_malformed_payload(monkeypatch):
    _patch_http(monkeypatch, body={"nope": True})
    p = OpenAICompatibleProvider("https://x.test/v1", "k", "m")
    with pytest.raises(ProviderError, match="malformed"):
        p.complete([{"role": "user", "content": "hi"}])


def test_complete_missing_usage_defaults_to_zero(monkeypatch):
    body = {"choices": [{"message": {"content": "t"}}]}
    _patch_http(monkeypatch, body=body)
    p = OpenAICompatibleProvider("https://x.test/v1", "k", "m")
    result = p.complete([{"role": "user", "content": "hi"}])
    assert result.prompt_tokens == 0
    assert result.completion_tokens == 0


def test_provider_rejects_empty_config():
    with pytest.raises(ValueError):
        OpenAICompatibleProvider("", "k", "m")
    with pytest.raises(ValueError):
        OpenAICompatibleProvider("https://x", "", "m")


def test_get_provider_nebius(monkeypatch):
    monkeypatch.setenv("RUNSTATE_PROVIDER", "nebius")
    monkeypatch.setenv("NEBIUS_API_KEY", "nb-key")
    p = get_provider()
    assert isinstance(p, OpenAICompatibleProvider)
    assert p.base_url == providers.NEBIUS_BASE_URL
    assert p.model == providers.NEBIUS_DEFAULT_MODEL


def test_get_provider_nebius_model_override(monkeypatch):
    monkeypatch.setenv("RUNSTATE_PROVIDER", "nebius")
    monkeypatch.setenv("NEBIUS_API_KEY", "nb-key")
    monkeypatch.setenv("RUNSTATE_MODEL", "custom/model")
    assert get_provider().model == "custom/model"


def test_get_provider_nebius_missing_key(monkeypatch):
    monkeypatch.setenv("RUNSTATE_PROVIDER", "nebius")
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    with pytest.raises(ProviderNotConfigured, match="NEBIUS_API_KEY"):
        get_provider()


def test_get_provider_openai(monkeypatch):
    monkeypatch.setenv("RUNSTATE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "oa-key")
    p = get_provider()
    assert p.base_url == providers.OPENAI_BASE_URL


def test_get_provider_openai_missing_key(monkeypatch):
    monkeypatch.setenv("RUNSTATE_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderNotConfigured, match="OPENAI_API_KEY"):
        get_provider()


def test_get_provider_unset_and_unknown(monkeypatch):
    monkeypatch.delenv("RUNSTATE_PROVIDER", raising=False)
    with pytest.raises(ProviderNotConfigured, match="no provider configured"):
        get_provider()
    with pytest.raises(ProviderNotConfigured, match="unknown provider"):
        get_provider("watson")
