"""Tests for runstate.runtime.models."""

import pytest

from runstate.runtime.models import (
    RUN_STATUSES,
    TERMINAL_STATUSES,
    Budget,
    Run,
    Usage,
    utcnow_iso,
)


def test_budget_defaults():
    b = Budget()
    assert b.max_steps == 50
    assert b.max_wall_seconds == 600.0
    assert b.max_cost_usd == 1.0


def test_budget_rejects_non_positive():
    with pytest.raises(ValueError):
        Budget(max_steps=0)
    with pytest.raises(ValueError):
        Budget(max_wall_seconds=-1)
    with pytest.raises(ValueError):
        Budget(max_cost_usd=0)


def test_usage_defaults_and_add_step():
    u = Usage()
    assert (u.steps, u.wall_seconds, u.cost_usd) == (0, 0.0, 0.0)
    u.add_step(wall_seconds=1.5, cost_usd=0.002, prompt_tokens=100, completion_tokens=40)
    assert u.steps == 1
    assert u.wall_seconds == 1.5
    assert u.cost_usd == pytest.approx(0.002)
    assert u.prompt_tokens == 100
    assert u.completion_tokens == 40


def test_usage_rejects_negative():
    with pytest.raises(ValueError):
        Usage(steps=-1)
    u = Usage()
    with pytest.raises(ValueError):
        u.add_step(cost_usd=-0.1)


def test_run_defaults_and_status_set():
    r = Run(run_id="run-abc", objective="do the thing")
    assert r.status == "pending"
    assert r.status in RUN_STATUSES
    assert not r.is_terminal
    assert r.created_at and r.updated_at


def test_run_rejects_bad_status():
    with pytest.raises(ValueError):
        Run(run_id="run-abc", objective="x", status="bogus")


def test_run_rejects_empty_fields():
    with pytest.raises(ValueError):
        Run(run_id="", objective="x")
    with pytest.raises(ValueError):
        Run(run_id="run-abc", objective="   ")


def test_terminal_statuses():
    for status in ("success", "unverified", "exhausted", "failed", "cancelled"):
        assert Run(run_id="r", objective="x", status=status).is_terminal
    for status in ("pending", "running"):
        assert not Run(run_id="r", objective="x", status=status).is_terminal
    assert TERMINAL_STATUSES < RUN_STATUSES


def test_touch_updates_timestamp():
    r = Run(run_id="r", objective="x", created_at="2000-01-01T00:00:00+00:00",
            updated_at="2000-01-01T00:00:00+00:00")
    r.touch()
    assert r.updated_at > "2000-01-01T00:00:00+00:00"
    assert utcnow_iso().startswith("20")
