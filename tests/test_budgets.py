"""Tests for runstate.runtime.budgets."""

import pytest

from runstate.runtime.budgets import (
    DEFAULT_PRICE_PER_1M,
    BudgetExhausted,
    check_budget,
    cost_for_usage,
)
from runstate.runtime.models import Budget, Run, Usage


def _run(usage: Usage, budget: Budget | None = None) -> Run:
    return Run(
        run_id="run-b",
        objective="x",
        budget=budget or Budget(),
        usage=usage,
    )


def test_check_budget_passes_when_headroom():
    run = _run(Usage(steps=10, wall_seconds=100.0, cost_usd=0.1))
    check_budget(run)  # must not raise


def test_check_budget_exhausted_on_steps():
    run = _run(Usage(steps=50))
    with pytest.raises(BudgetExhausted) as ei:
        check_budget(run)
    assert ei.value.dimension == "steps"
    assert ei.value.run_id == "run-b"
    assert "steps" in str(ei.value)


def test_check_budget_exhausted_on_wall_time():
    run = _run(Usage(wall_seconds=600.0))
    with pytest.raises(BudgetExhausted) as ei:
        check_budget(run)
    assert ei.value.dimension == "wall_seconds"


def test_check_budget_exhausted_on_cost():
    run = _run(Usage(cost_usd=1.0))
    with pytest.raises(BudgetExhausted) as ei:
        check_budget(run)
    assert ei.value.dimension == "cost_usd"
    assert ei.value.used == 1.0
    assert ei.value.allowed == 1.0


def test_check_budget_respects_custom_budget():
    budget = Budget(max_steps=5, max_wall_seconds=10.0, max_cost_usd=0.01)
    run = _run(Usage(steps=4), budget)
    check_budget(run)
    run.usage.steps = 5
    with pytest.raises(BudgetExhausted):
        check_budget(run)


def test_check_budget_reports_used_and_allowed():
    run = _run(Usage(steps=50, wall_seconds=1.0, cost_usd=0.0))
    with pytest.raises(BudgetExhausted) as ei:
        check_budget(run)
    # steps is checked first
    assert (ei.value.dimension, ei.value.used, ei.value.allowed) == ("steps", 50, 50)


def test_cost_for_known_model():
    cost = cost_for_usage("nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", 1_000_000, 1_000_000)
    assert cost == pytest.approx(0.06 + 0.24)


def test_cost_for_usage_typical_extraction():
    cost = cost_for_usage("nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", 219, 139)
    assert cost == pytest.approx((219 * 0.06 + 139 * 0.24) / 1_000_000)


def test_cost_for_unknown_model_uses_default():
    rate_p, rate_c = DEFAULT_PRICE_PER_1M
    cost = cost_for_usage("some/unknown-model", 1_000_000, 1_000_000)
    assert cost == pytest.approx(rate_p + rate_c)


def test_cost_for_usage_rejects_negative():
    with pytest.raises(ValueError):
        cost_for_usage("m", -1, 0)
