"""Core data models for the runstate runtime.

A Run is the primitive of the system: a budgeted, resumable, receipted unit
of agent work. Budgets bound what a run may consume; Usage records what it
has consumed so far.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

#: All statuses a Run may carry. Terminal statuses end a run's lifecycle.
RUN_STATUSES = frozenset(
    {
        "pending",
        "running",
        "success",
        "unverified",
        "exhausted",
        "failed",
        "cancelled",
    }
)

#: Statuses after which a run accepts no further steps.
TERMINAL_STATUSES = frozenset(
    {"success", "unverified", "exhausted", "failed", "cancelled"}
)


def utcnow_iso() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Budget:
    """Resource bounds for a single run.

    A run is considered exhausted once any consumed dimension reaches its
    bound (see :mod:`runstate.runtime.budgets`).
    """

    max_steps: int = 50
    max_wall_seconds: float = 600.0
    max_cost_usd: float = 1.0

    def __post_init__(self) -> None:
        if self.max_steps <= 0:
            raise ValueError("max_steps must be positive")
        if self.max_wall_seconds <= 0:
            raise ValueError("max_wall_seconds must be positive")
        if self.max_cost_usd <= 0:
            raise ValueError("max_cost_usd must be positive")


@dataclass
class Usage:
    """What a run has consumed so far."""

    steps: int = 0
    wall_seconds: float = 0.0
    cost_usd: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def __post_init__(self) -> None:
        for name in (
            "steps",
            "wall_seconds",
            "cost_usd",
            "prompt_tokens",
            "completion_tokens",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")

    def add_step(
        self,
        wall_seconds: float = 0.0,
        cost_usd: float = 0.0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        """Accumulate the consumption of one agent step into this usage."""
        if wall_seconds < 0 or cost_usd < 0 or prompt_tokens < 0 or completion_tokens < 0:
            raise ValueError("step consumption must be non-negative")
        self.steps += 1
        self.wall_seconds += wall_seconds
        self.cost_usd += cost_usd
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens


@dataclass
class Run:
    """A single unit of agent work.

    Attributes:
        run_id: Unique identifier, e.g. ``run-9f3c1a2b4d5e6f70``.
        objective: The natural-language objective of the run.
        status: One of :data:`RUN_STATUSES`.
        budget: The resource bounds for this run.
        usage: What the run has consumed so far.
        created_at: ISO-8601 creation timestamp (UTC).
        updated_at: ISO-8601 last-update timestamp (UTC).
    """

    run_id: str
    objective: str
    status: str = "pending"
    budget: Budget = field(default_factory=Budget)
    usage: Usage = field(default_factory=Usage)
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ValueError("run_id must be non-empty")
        if not self.objective or not self.objective.strip():
            raise ValueError("objective must be non-empty")
        if self.status not in RUN_STATUSES:
            raise ValueError(
                f"status must be one of {sorted(RUN_STATUSES)}, got {self.status!r}"
            )

    @property
    def is_terminal(self) -> bool:
        """True when the run has reached a terminal status."""
        return self.status in TERMINAL_STATUSES

    def touch(self) -> None:
        """Refresh the ``updated_at`` timestamp to now."""
        self.updated_at = utcnow_iso()
