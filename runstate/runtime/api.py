"""Public run-lifecycle API for runstate.

Every function persists through :mod:`runstate.runtime.ledger`, so runs
survive process restarts and can be resumed later.
"""

from __future__ import annotations

import secrets
from typing import Optional

from .ledger import RunNotFound, list_runs as _list_runs, load_run, save_run
from .models import RUN_STATUSES, Budget, Run

__all__ = ["create_run", "get_run", "cancel_run", "list_runs", "RunNotFound"]


def _new_run_id() -> str:
    return f"run-{secrets.token_hex(8)}"


def create_run(objective: str, budget: Optional[Budget] = None) -> Run:
    """Create a new run in ``pending`` status and persist it.

    Args:
        objective: Non-empty natural-language objective for the run.
        budget: Optional custom :class:`Budget`; defaults apply otherwise.

    Returns:
        The persisted :class:`Run`.

    Raises:
        ValueError: If ``objective`` is empty.
    """
    run = Run(
        run_id=_new_run_id(),
        objective=objective,
        budget=budget if budget is not None else Budget(),
    )
    save_run(run)
    return run


def get_run(run_id: str) -> Run:
    """Load a run by id.

    Raises:
        RunNotFound: If no run with ``run_id`` exists.
    """
    return load_run(run_id)


def cancel_run(run_id: str) -> Run:
    """Move a run to ``cancelled`` status.

    Idempotent: cancelling an already-terminal run returns it unchanged.

    Raises:
        RunNotFound: If no run with ``run_id`` exists.
    """
    run = load_run(run_id)
    if run.is_terminal:
        return run
    run.status = "cancelled"
    run.touch()
    save_run(run)
    return run


def list_runs(status: Optional[str] = None) -> list[Run]:
    """List runs, newest first, optionally filtered by ``status``.

    Raises:
        ValueError: If ``status`` is not a known run status.
    """
    if status is not None and status not in RUN_STATUSES:
        raise ValueError(
            f"status must be one of {sorted(RUN_STATUSES)}, got {status!r}"
        )
    return _list_runs(status)
