"""Tests for usage metering, the MeteredProvider middleware, and the
Meterless H-MEM memory adapter (all in-memory; no network)."""

import pytest

from runstate.protocol.meterless import (
    Meter,
    MeteredProvider,
    MeterlessMemory,
    MockMeter,
    MockMeterlessMemory,
    UsageEvent,
    get_memory,
    get_meter,
)
from runstate.runtime.budgets import cost_for_usage
from runstate.runtime.providers import ChatResult


class FakeModelProvider:
    name = "fake"

    def complete(self, messages, **kw):
        text = "resp to: " + messages[-1]["content"]
        return ChatResult(text=text, prompt_tokens=12, completion_tokens=8,
                          model="fake/test-model")


class FakeUsage:
    def __init__(self, p, c, cost=0.0):
        self.prompt_tokens = p
        self.completion_tokens = c
        self.cost_usd = cost


def test_meter_records_and_totals():
    meter = Meter()
    meter.record("run-1", FakeUsage(100, 50, 0.01))
    meter.record("run-1", {"prompt_tokens": 200, "completion_tokens": 25})
    meter.record("run-2", FakeUsage(10, 10))
    totals = meter.totals("run-1")
    assert totals == {"events": 2, "prompt_tokens": 300,
                      "completion_tokens": 75, "cost_usd": pytest.approx(0.01)}
    assert meter.totals("run-2")["events"] == 1
    assert meter.totals("run-zzz")["events"] == 0


def test_meter_rejects_empty_run_id():
    with pytest.raises(ValueError):
        Meter().record("", FakeUsage(1, 1))


def test_mock_meter_same_behavior():
    assert isinstance(MockMeter(), Meter)
    m = MockMeter()
    m.record("r", FakeUsage(5, 5))
    assert m.totals("r")["prompt_tokens"] == 5


def test_get_meter_modes(monkeypatch):
    monkeypatch.setenv("METERLESS_MODE", "mock")
    assert isinstance(get_meter(), MockMeter)
    monkeypatch.setenv("METERLESS_MODE", "live")
    assert type(get_meter()) is Meter
    monkeypatch.setenv("METERLESS_MODE", "bogus")
    with pytest.raises(ValueError):
        get_meter()


def test_metered_provider_meters_every_call():
    meter = Meter()
    wrapped = MeteredProvider(FakeModelProvider(), meter, provider_name="fake")
    r1 = wrapped.complete([{"role": "user", "content": "hello"}], run_id="run-9")
    r2 = wrapped.complete([{"role": "user", "content": "hello again"}], run_id="run-9")
    assert r1.text and r2.text
    totals = meter.totals("run-9")
    assert totals["events"] == 2
    assert totals["prompt_tokens"] == 24
    assert totals["completion_tokens"] == 16
    expected_cost = 2 * cost_for_usage("fake/test-model", 12, 8)
    assert totals["cost_usd"] == pytest.approx(expected_cost)
    # passthrough of provider attributes
    assert wrapped.name == "fake"


def test_metered_provider_without_run_id_skips_meter():
    meter = Meter()
    wrapped = MeteredProvider(FakeModelProvider(), meter)
    resp = wrapped.complete([{"role": "user", "content": "hi"}])
    assert resp.text
    assert meter.all_events() == []


def test_memory_remember_and_recall():
    mem = MeterlessMemory()
    mem.remember("the run bills per agent run", tier="working")
    mem.remember("unrelated weather note", tier="working")
    hits = mem.recall("bills per run")
    assert len(hits) == 1
    assert hits[0].tier == "working"


def test_memory_tiers_and_consolidate():
    mem = MeterlessMemory()
    k = mem.remember("fact A", tier="working")
    mem.remember("recent note", tier="short_term")
    assert mem.consolidate() == 1
    snap = mem.snapshot()
    assert len(snap["long_term"]) == 1
    assert len(snap["working"]) == 0
    assert len(snap["short_term"]) == 1
    assert mem.forget(k) is True
    assert mem.forget(k) is False


def test_memory_rejects_bad_tier_and_empty():
    mem = MeterlessMemory()
    with pytest.raises(ValueError):
        mem.remember("x", tier="nope")
    with pytest.raises(ValueError):
        mem.remember("   ")


def test_memory_recall_ranking():
    mem = MeterlessMemory()
    mem.remember("alpha beta", tier="long_term")
    mem.remember("alpha alpha alpha", tier="long_term")
    hits = mem.recall("alpha", limit=2)
    assert hits[0].text == "alpha alpha alpha"  # more keyword hits first


def test_mock_memory_seeded():
    mem = MockMeterlessMemory()
    assert isinstance(mem, MeterlessMemory)
    hits = mem.recall("bills per agent run")
    assert hits, "seed fixture should be recalled"
    assert hits[0].text == "Runstate bills per agent run, not per token."


def test_get_memory_modes(monkeypatch):
    monkeypatch.setenv("METERLESS_MODE", "mock")
    assert isinstance(get_memory(), MockMeterlessMemory)
    monkeypatch.setenv("METERLESS_MODE", "live")
    assert type(get_memory()) is MeterlessMemory


def test_usage_event_shape():
    e = UsageEvent(run_id="r", prompt_tokens=1, completion_tokens=2)
    assert e.ts > 0
