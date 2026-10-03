"""SQLite persistence for runs and their step ledgers.

The database path comes from the ``RUNSTATE_DB_PATH`` environment variable,
defaulting to ``~/.runstate/ledger.db``. Two tables:

``runs``
    One row per run: id, objective, status, budget/usage as JSON, timestamps.
``steps``
    Append-only ledger of step records per run, ordered by ``seq``.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from .models import Budget, Run, Usage

DEFAULT_DB_PATH = Path.home() / ".runstate" / "ledger.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    objective   TEXT NOT NULL,
    status      TEXT NOT NULL,
    budget_json TEXT NOT NULL,
    usage_json  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS steps (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    content    TEXT NOT NULL,
    tokens     INTEGER NOT NULL DEFAULT 0,
    cost       REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs (run_id)
);
CREATE INDEX IF NOT EXISTS idx_steps_run_seq ON steps (run_id, seq);
"""


class RunNotFound(Exception):
    """Raised when a run_id has no row in the ledger."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        super().__init__(f"run not found: {run_id}")


def db_path() -> Path:
    """Resolve the ledger database path from the environment."""
    override = os.environ.get("RUNSTATE_DB_PATH")
    return Path(override).expanduser() if override else DEFAULT_DB_PATH


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    return conn


def _run_to_row(run: Run) -> tuple:
    return (
        run.run_id,
        run.objective,
        run.status,
        json.dumps(asdict(run.budget)),
        json.dumps(asdict(run.usage)),
        run.created_at,
        run.updated_at,
    )


def _row_to_run(row: tuple) -> Run:
    run_id, objective, status, budget_json, usage_json, created_at, updated_at = row
    return Run(
        run_id=run_id,
        objective=objective,
        status=status,
        budget=Budget(**json.loads(budget_json)),
        usage=Usage(**json.loads(usage_json)),
        created_at=created_at,
        updated_at=updated_at,
    )


def save_run(run: Run) -> None:
    """Insert or replace the ``runs`` row for ``run``."""
    with _connect() as conn:
        conn.execute(
            """INSERT INTO runs
               (run_id, objective, status, budget_json, usage_json,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(run_id) DO UPDATE SET
                 objective=excluded.objective,
                 status=excluded.status,
                 budget_json=excluded.budget_json,
                 usage_json=excluded.usage_json,
                 updated_at=excluded.updated_at""",
            _run_to_row(run),
        )


def load_run(run_id: str) -> Run:
    """Load a run by id.

    Raises:
        RunNotFound: If no run with ``run_id`` exists.
    """
    with _connect() as conn:
        row = conn.execute(
            """SELECT run_id, objective, status, budget_json, usage_json,
                      created_at, updated_at
               FROM runs WHERE run_id = ?""",
            (run_id,),
        ).fetchone()
    if row is None:
        raise RunNotFound(run_id)
    return _row_to_run(row)


def list_runs(status: Optional[str] = None) -> list[Run]:
    """List runs, newest first, optionally filtered by ``status``."""
    with _connect() as conn:
        if status is None:
            rows = conn.execute(
                """SELECT run_id, objective, status, budget_json, usage_json,
                          created_at, updated_at
                   FROM runs ORDER BY created_at DESC"""
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT run_id, objective, status, budget_json, usage_json,
                          created_at, updated_at
                   FROM runs WHERE status = ? ORDER BY created_at DESC""",
                (status,),
            ).fetchall()
    return [_row_to_run(row) for row in rows]


def append_step(
    run_id: str,
    kind: str,
    content: str,
    tokens: int = 0,
    cost: float = 0.0,
) -> int:
    """Append a step record to a run's ledger. Returns the step ``seq``.

    Raises:
        RunNotFound: If no run with ``run_id`` exists.
        ValueError: If ``kind``/``content`` are empty or tokens/cost negative.
    """
    if not kind or not content:
        raise ValueError("kind and content must be non-empty")
    if tokens < 0 or cost < 0:
        raise ValueError("tokens and cost must be non-negative")
    # Fail fast on unknown runs instead of writing orphan steps.
    load_run(run_id)
    from .models import utcnow_iso

    with _connect() as conn:
        cur = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM steps WHERE run_id = ?", (run_id,)
        )
        seq = cur.fetchone()[0] + 1
        conn.execute(
            """INSERT INTO steps
               (run_id, seq, kind, content, tokens, cost, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run_id, seq, kind, content, tokens, cost, utcnow_iso()),
        )
    return seq


def get_steps(run_id: str) -> list[dict[str, Any]]:
    """Return a run's step ledger in ``seq`` order.

    Raises:
        RunNotFound: If no run with ``run_id`` exists.
    """
    load_run(run_id)
    with _connect() as conn:
        rows = conn.execute(
            """SELECT seq, kind, content, tokens, cost, created_at
               FROM steps WHERE run_id = ? ORDER BY seq""",
            (run_id,),
        ).fetchall()
    return [
        {
            "seq": seq,
            "kind": kind,
            "content": content,
            "tokens": tokens,
            "cost": cost,
            "created_at": created_at,
        }
        for seq, kind, content, tokens, cost, created_at in rows
    ]
