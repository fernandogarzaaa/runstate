"""Tests for runstate.runtime.api (run lifecycle)."""

import pytest

from runstate.runtime.api import cancel_run, create_run, get_run, list_runs
from runstate.runtime.ledger import RunNotFound
from runstate.runtime.models import Budget, Run


@pytest.fixture(autouse=True)
def db(monkeypatch, tmp_path):
    monkeypatch.setenv("RUNSTATE_DB_PATH", str(tmp_path / "ledger.db"))


def test_create_run_persists_and_returns_pending():
    run = create_run("summarize the repo")
    assert isinstance(run, Run)
    assert run.run_id.startswith("run-")
    assert run.status == "pending"
    assert run.objective == "summarize the repo"
    assert get_run(run.run_id) == run


def test_create_run_ids_are_unique():
    assert create_run("a").run_id != create_run("b").run_id


def test_create_run_custom_budget():
    budget = Budget(max_steps=5)
    run = create_run("x", budget=budget)
    assert get_run(run.run_id).budget.max_steps == 5


def test_create_run_rejects_empty_objective():
    with pytest.raises(ValueError):
        create_run("   ")


def test_get_run_missing():
    with pytest.raises(RunNotFound):
        get_run("run-does-not-exist")


def test_cancel_run_transitions_to_cancelled():
    run = create_run("do work")
    cancelled = cancel_run(run.run_id)
    assert cancelled.status == "cancelled"
    assert get_run(run.run_id).status == "cancelled"


def test_cancel_run_is_idempotent_on_terminal():
    run = create_run("do work")
    cancel_run(run.run_id)
    again = cancel_run(run.run_id)
    assert again.status == "cancelled"


def test_cancel_run_missing():
    with pytest.raises(RunNotFound):
        cancel_run("run-does-not-exist")


def test_list_runs_and_status_filter():
    r1 = create_run("first")
    r2 = create_run("second")
    cancel_run(r1.run_id)
    ids = [r.run_id for r in list_runs()]
    assert set(ids) == {r1.run_id, r2.run_id}
    assert [r.run_id for r in list_runs(status="cancelled")] == [r1.run_id]
    assert [r.run_id for r in list_runs(status="pending")] == [r2.run_id]


def test_list_runs_rejects_bad_status():
    with pytest.raises(ValueError):
        list_runs(status="bogus")
