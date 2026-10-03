"""Tests for the MCP protocol server tools.

Runs against the REAL Builder 1 runtime (models/api/providers/budgets/
ledger), with a fake provider monkeypatched in for ``get_provider`` and
the SQLite ledger isolated per test (see conftest).
"""

import asyncio
import json

import pytest

from runstate.protocol import server
from runstate.runtime.providers import ChatResult


class FakeProvider:
    """Deterministic stand-in for a real model provider."""

    name = "fake"

    def complete(self, messages, **kw):
        user_texts = [m["content"] for m in messages if m["role"] == "user"]
        text = "fake-answer to: " + user_texts[-1] if user_texts else "fake-answer"
        prompt = sum(len(m["content"]) for m in messages)
        return ChatResult(text=text, prompt_tokens=prompt,
                          completion_tokens=len(text), model="fake/test-model")


@pytest.fixture(autouse=True)
def _fake_provider(monkeypatch):
    monkeypatch.setattr(server, "get_provider", lambda name=None: FakeProvider())


def run(coro):
    return asyncio.run(coro)


def test_run_start_basic():
    out = run(server.run_start("triage the inbox"))
    assert out["objective"] == "triage the inbox"
    assert out["status"] == "pending"
    assert out["run_id"].startswith("run-")
    assert out["budget"]["max_steps"] == 50


def test_run_start_with_budget_json():
    out = run(server.run_start("x", '{"max_steps": 3, "max_cost_usd": 0.5}'))
    assert out["budget"]["max_steps"] == 3
    assert out["budget"]["max_cost_usd"] == 0.5
    assert out["budget"]["max_wall_seconds"] == 600.0


def test_run_start_bad_inputs():
    assert run(server.run_start(""))["code"] == "bad_request"
    assert run(server.run_start("   "))["code"] == "bad_request"
    assert run(server.run_start("x", "not json"))["code"] == "bad_request"
    assert run(server.run_start("x", '{"nope": 1}'))["code"] == "bad_request"
    assert run(server.run_start("x", "[1,2]"))["code"] == "bad_request"
    assert run(server.run_start("x", '{"max_steps": -1}'))["code"] == "bad_request"


def test_run_step_happy_path():
    start = run(server.run_start("count to three"))
    step = run(server.run_step(start["run_id"], "begin"))
    assert "fake-answer to: begin" in step["text"]
    assert step["status"] == "running"
    assert step["usage"]["steps"] == 1
    assert step["usage"]["prompt_tokens"] > 0
    assert step["usage"]["completion_tokens"] > 0
    assert step["usage"]["cost_usd"] > 0  # real pricing applied
    # second step sees history: provider got more messages
    step2 = run(server.run_step(start["run_id"], "continue"))
    assert step2["usage"]["steps"] == 2
    assert step2["usage"]["prompt_tokens"] > step["usage"]["prompt_tokens"]


def test_run_step_unknown_run():
    assert run(server.run_step("run-nope", "hi"))["code"] == "not_found"


def test_run_step_empty_instruction():
    start = run(server.run_start("x"))
    assert run(server.run_step(start["run_id"], ""))["code"] == "bad_request"


def test_run_step_budget_exhaustion():
    start = run(server.run_start("x", '{"max_steps": 1}'))
    run(server.run_step(start["run_id"], "one"))
    out = run(server.run_step(start["run_id"], "two"))
    assert out["code"] == "budget_exhausted"
    assert out["status"] == "exhausted"
    status = run(server.run_status(start["run_id"]))
    assert status["status"] == "exhausted"


def test_run_step_provider_failure_keeps_ledger_clean(monkeypatch):
    class Boom:
        name = "boom"

        def complete(self, messages, **kw):
            raise RuntimeError("kaput")

    monkeypatch.setattr(server, "get_provider", lambda name=None: Boom())
    start = run(server.run_start("x"))
    out = run(server.run_step(start["run_id"], "work"))
    assert out["code"] == "provider_error"
    # no steps appended, usage untouched, run still active
    status = run(server.run_status(start["run_id"]))
    assert status["steps"] == 0
    assert status["usage"]["steps"] == 0
    assert status["status"] == "pending"


def test_run_step_provider_not_configured(monkeypatch):
    from runstate.runtime.providers import ProviderNotConfigured

    def _raise(name=None):
        raise ProviderNotConfigured("no provider configured")

    monkeypatch.setattr(server, "get_provider", _raise)
    start = run(server.run_start("x"))
    out = run(server.run_step(start["run_id"], "work"))
    assert out["code"] == "provider_not_configured"


def test_run_cancel_and_status():
    start = run(server.run_start("x"))
    run(server.run_step(start["run_id"], "work"))
    cancelled = run(server.run_cancel(start["run_id"]))
    assert cancelled["status"] == "cancelled"
    # cancelled runs refuse new steps but keep their ledger
    out = run(server.run_step(start["run_id"], "more"))
    assert out["code"] == "run_not_active"
    status = run(server.run_status(start["run_id"]))
    assert status["status"] == "cancelled"
    assert status["steps"] == 2  # user + assistant entries of the one step


def test_run_status_unknown():
    assert run(server.run_status("run-nope"))["code"] == "not_found"
    assert run(server.run_cancel("run-nope"))["code"] == "not_found"


def test_run_receipt_full_record():
    start = run(server.run_start("audit me", '{"max_steps": 5}'))
    run(server.run_step(start["run_id"], "first"))
    receipt = run(server.run_receipt(start["run_id"]))
    assert receipt["run_id"] == start["run_id"]
    assert receipt["objective"] == "audit me"
    assert receipt["status"] == "running"
    assert len(receipt["steps"]) == 2
    assert receipt["steps"][0]["kind"] == "user"
    assert receipt["steps"][0]["content"] == "first"
    assert receipt["steps"][1]["kind"] == "assistant"
    assert receipt["steps"][1]["tokens"] > 0
    assert receipt["steps"][1]["cost"] > 0
    assert receipt["usage"]["steps"] == 1
    assert receipt["metered_totals"]["events"] == 1
    assert receipt["metered_totals"]["prompt_tokens"] == \
        receipt["usage"]["prompt_tokens"]
    assert receipt["metered_totals"]["cost_usd"] == \
        pytest.approx(receipt["usage"]["cost_usd"])
    # receipt is JSON-serializable
    json.dumps(receipt)


def test_run_receipt_unknown():
    assert run(server.run_receipt("run-nope"))["code"] == "not_found"


def test_zetaris_query_tool_uses_mock_by_default(monkeypatch):
    monkeypatch.setenv("ZETARIS_MODE", "mock")
    out = run(server.zetaris_query("nemotron"))
    assert out["count"] >= 1
    assert any("Nemotron" in str(r) for r in out["rows"])


def test_zetaris_query_tool_bad_input():
    assert run(server.zetaris_query(""))["code"] == "bad_request"


def test_create_server_registers_all_tools():
    mcp = server.create_server()
    names = sorted(t.name for t in mcp._tool_manager.list_tools())
    assert names == ["run_cancel", "run_receipt", "run_start",
                     "run_status", "run_step", "zetaris_query"]
