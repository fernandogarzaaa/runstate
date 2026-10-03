"""Agent-loop driver for runstate.

The runtime (``runstate.runtime``) owns state: runs, budgets, the ledger,
providers. This module owns *execution*: it drives a run's reasoning loop
to a terminal status using only the runtime's public API.

Loop protocol (documented, stable):
  - Each iteration calls ``provider.complete(messages)`` once.
  - The model signals completion by starting its final message with
    ``FINAL:`` (see SYSTEM_PROMPT). Anything else is an intermediate step.
  - Budgets are enforced before every step via ``check_budget``; exhaustion
    ends the run with status ``exhausted``.
  - Every step is appended to the ledger and accumulated into
    ``run.usage``; the run is saved after every step, so runs are resumable
    and survive crashes.
"""

from __future__ import annotations

import time

SYSTEM_PROMPT = (
    "You are runstate, a stateful agent runtime executing one run. "
    "Work toward the objective step by step; each of your responses is one "
    "recorded step with a real cost. Be concise. When the objective is "
    "complete, start your final message with 'FINAL:' followed by a short "
    "summary of the result. Do not use the FINAL: marker before you are done."
)

FINAL_MARKER = "FINAL:"


def is_final(text: str) -> bool:
    """True when the model marked this message as the run's final answer."""
    return any(
        line.strip().upper().startswith(FINAL_MARKER)
        for line in (text or "").splitlines()
    )


def final_text(text: str) -> str:
    """Strip the FINAL: marker, keeping the summary content."""
    out = []
    for line in (text or "").splitlines():
        s = line.strip()
        if s.upper().startswith(FINAL_MARKER):
            s = s[len(FINAL_MARKER):].strip()
        out.append(s)
    return "\n".join(out).strip()


class DriveTimeout(Exception):
    """The driver stopped waiting; the run itself is left untouched."""


def drive_run(run_id, provider=None, timeout=300.0, on_step=None):
    """Drive a pending/running run to a terminal status.

    Args:
        run_id: ID of a run created via ``create_run``.
        provider: provider name for ``get_provider`` (None = configured default).
        timeout: max seconds to block; on expiry raises :class:`DriveTimeout`
            leaving the run resumable in its current state.
        on_step: optional callable ``(seq, kind, text, cost)`` invoked after
            each recorded step (used by the CLI for progress output).

    Returns:
        The terminal :class:`Run`.

    Raises:
        DriveTimeout: if ``timeout`` elapses before a terminal status.
        ProviderNotConfigured / ProviderError: from the provider layer.
    """
    from runstate.runtime import (
        BudgetExhausted,
        append_step,
        check_budget,
        cost_for_usage,
        get_run,
        providers as _providers,
        save_run,
    )

    run = get_run(run_id)
    try:
        provider_obj = _providers.get_provider(provider)
    except _providers.ProviderNotConfigured:
        # Leave the run resumable in pending rather than stuck in running.
        if run.status == "pending":
            save_run(run)
        raise

    if run.status == "pending":
        run.status = "running"
        run.touch()
        save_run(run)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Objective: {run.objective}"},
    ]
    deadline = time.time() + timeout

    while True:
        if time.time() > deadline:
            raise DriveTimeout(
                f"timed out after {timeout:g}s; run {run_id} left "
                f"in status '{run.status}' (resumable)"
            )
        try:
            check_budget(run)
        except BudgetExhausted as exc:
            append_step(run_id, kind="outcome", content=f"exhausted: {exc}")
            run.status = "exhausted"
            run.touch()
            save_run(run)
            return run

        t0 = time.time()
        try:
            result = provider_obj.complete(messages)
        except _providers.ProviderError as exc:
            append_step(run_id, kind="outcome", content=f"failed: provider error: {exc}")
            run.status = "failed"
            run.touch()
            save_run(run)
            return run
        wall = time.time() - t0

        cost = cost_for_usage(
            result.model, result.prompt_tokens, result.completion_tokens
        )
        seq = append_step(
            run_id,
            kind="assistant",
            content=result.text,
            tokens=result.prompt_tokens + result.completion_tokens,
            cost=cost,
        )
        run.usage.add_step(
            wall_seconds=wall,
            cost_usd=cost,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
        )
        run.touch()
        save_run(run)
        if on_step is not None:
            on_step(seq, "assistant", result.text, cost)

        if is_final(result.text):
            summary = final_text(result.text)
            append_step(run_id, kind="outcome", content=summary)
            run.status = "success"
            run.touch()
            save_run(run)
            return run
        messages.append({"role": "assistant", "content": result.text})
