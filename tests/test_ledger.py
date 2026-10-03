"""Tests for runstate.runtime.ledger (SQLite persistence)."""

import pytest

from runstate.runtime import ledger
from runstate.runtime.ledger import (
    RunNotFound,
    append_step,
    get_steps,
    list_runs,
    load_run,
    save_run,
)
from runstate.runtime.models import Budget, Run, Usage


@pytest.fixture
def db(monkeypatch, tmp_path):
    """Isolate the ledger to a temp database for each test."""
    monkeypatch.setenv("RUNSTATE_DB_PATH", str(tmp_path / "ledger.db"))
    yield tmp_path / "ledger.db"


def _run(**kw):
    kw.setdefault("run_id", "run-test1")
    kw.setdefault("objective", "test objective")
    return Run(**kw)


def test_save_load_roundtrip(db):
    run = _run(
        status="running",
        budget=Budget(max_steps=10, max_wall_seconds=30.0, max_cost_usd=0.5),
        usage=Usage(steps=3, wall_seconds=4.5, cost_usd=0.01,
                    prompt_tokens=50, completion_tokens=20),
    )
    save_run(run)
    loaded = load_run("run-test1")
    assert loaded == run


def test_load_missing_raises(db):
    with pytest.raises(RunNotFound):
        load_run("run-nope")


def test_save_run_upserts(db):
    run = _run()
    save_run(run)
    run.status = "running"
    run.touch()
    save_run(run)
    assert load_run("run-test1").status == "running"


def test_list_runs_newest_first_and_filter(db):
    r1 = _run(run_id="run-a", created_at="2026-01-01T00:00:00+00:00")
    r2 = _run(run_id="run-b", created_at="2026-01-02T00:00:00+00:00",
              status="success")
    save_run(r1)
    save_run(r2)
    all_runs = list_runs()
    assert [r.run_id for r in all_runs] == ["run-b", "run-a"]
    assert [r.run_id for r in list_runs(status="success")] == ["run-b"]
    assert list_runs(status="failed") == []


def test_append_step_and_get_steps(db):
    save_run(_run())
    seq1 = append_step("run-test1", "thought", "thinking...", tokens=10, cost=0.001)
    seq2 = append_step("run-test1", "tool_call", '{"tool": "x"}')
    assert (seq1, seq2) == (1, 2)
    steps = get_steps("run-test1")
    assert len(steps) == 2
    assert steps[0]["kind"] == "thought"
    assert steps[0]["tokens"] == 10
    assert steps[1]["seq"] == 2
    assert steps[1]["cost"] == 0.0


def test_append_step_validates(db):
    save_run(_run())
    with pytest.raises(ValueError):
        append_step("run-test1", "", "content")
    with pytest.raises(ValueError):
        append_step("run-test1", "kind", "content", tokens=-1)
    with pytest.raises(RunNotFound):
        append_step("run-missing", "kind", "content")


def test_get_steps_missing_run(db):
    with pytest.raises(RunNotFound):
        get_steps("run-missing")


def test_db_path_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("RUNSTATE_DB_PATH", str(tmp_path / "custom.db"))
    assert ledger.db_path() == tmp_path / "custom.db"
    monkeypatch.delenv("RUNSTATE_DB_PATH")
    assert ledger.db_path() == ledger.DEFAULT_DB_PATH
