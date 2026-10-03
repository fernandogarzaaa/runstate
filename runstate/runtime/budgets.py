"""Budget enforcement and token-cost accounting for runstate.

Budgets bound steps, wall-clock time, and USD cost per run. Cost is derived
from token usage via a per-model price table; models not listed use a
documented conservative default rate.
"""

from __future__ import annotations

from .models import Run


class BudgetExhausted(Exception):
    """Raised when a run has consumed its budget on some dimension.

    Attributes:
        run_id: The run that exhausted its budget.
        dimension: One of ``"steps"``, ``"wall_seconds"``, ``"cost_usd"``.
        used: Amount consumed.
        allowed: The budget bound that was reached.
    """

    def __init__(self, run_id: str, dimension: str, used: float, allowed: float):
        self.run_id = run_id
        self.dimension = dimension
        self.used = used
        self.allowed = allowed
        super().__init__(
            f"run {run_id}: budget exhausted on {dimension} "
            f"(used {used}, allowed {allowed})"
        )


#: Price per 1M tokens as (prompt_usd, completion_usd).
#:
#: The Nemotron 3 Nano entry reflects Nebius Token Factory catalog pricing
#: observed 2026-10-03. Other entries are intentionally absent: unknown
#: models use DEFAULT_PRICE_PER_1M below rather than guessed values.
PRICE_TABLE: dict[str, tuple[float, float]] = {
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": (0.06, 0.24),
}

#: Conservative default price per 1M (prompt, completion) tokens, in USD,
#: applied to any model not present in PRICE_TABLE. This is a deliberate
#: overestimate so unlisted models fail safe (exhaust sooner, never silently
#: under-bill). Document any production change to this rate.
DEFAULT_PRICE_PER_1M: tuple[float, float] = (0.50, 1.50)


def _lookup_price(model: str) -> tuple[float, float]:
    """Return (prompt_per_1m, completion_per_1m) for ``model``.

    Matches the full model id first, then the trailing segment after the
    last ``/`` (so ``openai/gpt-4o-mini`` style ids resolve). Falls back to
    :data:`DEFAULT_PRICE_PER_1M` for unknown models.
    """
    if model in PRICE_TABLE:
        return PRICE_TABLE[model]
    short = model.rsplit("/", 1)[-1]
    for key, price in PRICE_TABLE.items():
        if key.rsplit("/", 1)[-1] == short:
            return price
    return DEFAULT_PRICE_PER_1M


def cost_for_usage(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Compute the USD cost of a completion for ``model``.

    Raises:
        ValueError: If token counts are negative.
    """
    if prompt_tokens < 0 or completion_tokens < 0:
        raise ValueError("token counts must be non-negative")
    prompt_rate, completion_rate = _lookup_price(model)
    return (prompt_tokens * prompt_rate + completion_tokens * completion_rate) / 1_000_000


def check_budget(run: Run) -> None:
    """Raise :class:`BudgetExhausted` if ``run`` has consumed its budget.

    A dimension counts as exhausted once usage *reaches* its bound, i.e.
    there is no capacity left for another step. Checks steps first, then
    wall time, then cost.
    """
    budget = run.budget
    usage = run.usage
    if usage.steps >= budget.max_steps:
        raise BudgetExhausted(run.run_id, "steps", usage.steps, budget.max_steps)
    if usage.wall_seconds >= budget.max_wall_seconds:
        raise BudgetExhausted(
            run.run_id, "wall_seconds", usage.wall_seconds, budget.max_wall_seconds
        )
    if usage.cost_usd >= budget.max_cost_usd:
        raise BudgetExhausted(
            run.run_id, "cost_usd", usage.cost_usd, budget.max_cost_usd
        )
