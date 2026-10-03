"""Tests for the Zetaris adapter (mock backend + real-client unit behavior)."""

import json

import httpx
import pytest

from runstate.protocol.zetaris import (
    MockZetarisSource,
    ZetarisError,
    ZetarisSource,
    get_zetaris_source,
)


def test_mock_returns_fixture_rows():
    src = MockZetarisSource()
    rows = src.query("nemotron")
    assert len(rows) >= 1
    assert rows[0]["model"] == "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"


def test_mock_keyword_filter_is_deterministic():
    src = MockZetarisSource()
    a = src.query("nebius nano")
    b = src.query("nebius nano")
    assert a == b
    # every token must match
    assert src.query("nebius nonexistenttoken") == []


def test_mock_empty_query():
    assert MockZetarisSource().query("") == []
    assert MockZetarisSource().query("   ") == []


def test_mock_does_not_mutate_fixtures():
    src = MockZetarisSource()
    rows = src.query("taxi")
    rows[0]["rows"] = -1
    assert src.query("taxi")[0]["rows"] == 1_742_088


def test_factory_defaults_to_mock(monkeypatch):
    monkeypatch.delenv("ZETARIS_MODE", raising=False)
    assert isinstance(get_zetaris_source(), MockZetarisSource)


def test_factory_rejects_unknown_mode(monkeypatch):
    monkeypatch.setenv("ZETARIS_MODE", "bogus")
    with pytest.raises(ZetarisError):
        get_zetaris_source()


def test_real_client_requires_config(monkeypatch):
    monkeypatch.delenv("ZETARIS_ENDPOINT", raising=False)
    monkeypatch.delenv("ZETARIS_API_KEY", raising=False)
    with pytest.raises(ZetarisError):
        ZetarisSource()


_RealClient = httpx.Client


def _client_with_handler(handler):
    transport = httpx.MockTransport(handler)
    return transport


def test_real_client_query_shape(monkeypatch):
    """The real client speaks the documented start/page/close protocol."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["select"] == "SELECT * FROM t"
            assert 1 <= body["pageLimit"] <= 100
            assert request.headers["X-API-Key"] == "key-123"
            assert "X-Request-ID" in request.headers
            return httpx.Response(200, json={
                "queryToken": "tok-1",
                "rows": [{"a": 1}, {"a": 2}],
            })
        if request.method == "GET":
            return httpx.Response(200, json={"rows": []})
        return httpx.Response(200, json={})

    import runstate.protocol.zetaris as zmod

    monkeypatch.setenv("ZETARIS_ENDPOINT", "https://zetaris.example")
    monkeypatch.setenv("ZETARIS_API_KEY", "key-123")
    monkeypatch.setattr(
        zmod.httpx, "Client",
        lambda **kw: _RealClient(transport=_client_with_handler(handler)),
    )

    src = ZetarisSource()
    rows = src.query("SELECT * FROM t")
    assert rows == [{"a": 1}, {"a": 2}]
    methods = [m for m, _ in calls]
    assert methods[0] == "POST"  # start
    assert "DELETE" in methods    # close called in finally


def test_real_client_401_surfaces_clearly(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    import runstate.protocol.zetaris as zmod
    monkeypatch.setenv("ZETARIS_ENDPOINT", "https://zetaris.example")
    monkeypatch.setenv("ZETARIS_API_KEY", "bad-key")
    monkeypatch.setattr(
        zmod.httpx, "Client",
        lambda **kw: _RealClient(transport=_client_with_handler(handler)),
    )

    with pytest.raises(ZetarisError, match="401"):
        ZetarisSource().query("SELECT 1")


def test_real_client_columnar_rows_zipped(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={
                "queryToken": "tok-2",
                "columns": ["a", "b"],
                "rows": [[1, 2], [3, 4]],
            })
        return httpx.Response(200, json={})

    import runstate.protocol.zetaris as zmod
    monkeypatch.setenv("ZETARIS_ENDPOINT", "https://zetaris.example")
    monkeypatch.setenv("ZETARIS_API_KEY", "key-123")
    monkeypatch.setattr(
        zmod.httpx, "Client",
        lambda **kw: _RealClient(transport=_client_with_handler(handler)),
    )

    rows = ZetarisSource().query("SELECT a, b FROM t")
    assert rows == [{"a": 1, "b": 2}, {"a": 3, "b": 4}]


def test_real_client_rejects_empty_query(monkeypatch):
    monkeypatch.setenv("ZETARIS_ENDPOINT", "https://zetaris.example")
    monkeypatch.setenv("ZETARIS_API_KEY", "key-123")
    with pytest.raises(ZetarisError):
        ZetarisSource().query("  ")
