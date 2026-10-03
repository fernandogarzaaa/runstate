"""MCP protocol server: one tool call advances a stateful, budgeted run.

This is the APE-pattern embodiment for runstate: instead of a host model
chaining thin tools, each tool call drives a stateful agent run held by
the runtime. ``run_step`` runs exactly one agent loop iteration — history
from the run's step ledger plus the new instruction goes to the provider,
the step is appended, the budget is enforced, usage is accumulated with
real model pricing, and the assistant text plus updated usage comes back.

Tools:
  run_start(objective, budget_json)  start a run, optional budget as JSON
  run_step(run_id, instruction)      run one agent loop step
  run_status(run_id)                 run state + usage summary
  run_cancel(run_id)                 cancel a run
  run_receipt(run_id)                full run + steps + cost breakdown (JSON)
  zetaris_query(query)               federated SQL via the Zetaris adapter

Serve with ``python -m runstate.protocol.server`` (``RUNSTATE_HOST`` /
``RUNSTATE_PORT``, default 127.0.0.1:8899).

Runtime surface used (Builder 1's ``runstate`` package):
  - api: create_run / get_run / cancel_run (RunNotFound on unknown ids)
  - ledger: append_step / get_steps / save_run (SQLite step ledger)
  - models: Run (run_id, status, is_terminal), Budget, Usage.add_step
  - providers: get_provider -> complete(messages) -> ChatResult
  - budgets: check_budget (raises BudgetExhausted), cost_for_usage
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from mcp.server.mcpserver import MCPServer

from runstate.runtime.api import cancel_run, create_run, get_run
from runstate.runtime.budgets import (
    BudgetExhausted,
    check_budget,
    cost_for_usage,
)
from runstate.runtime.ledger import RunNotFound, append_step, get_steps, save_run
from runstate.runtime.models import Budget, Run
from runstate.runtime.providers import ProviderNotConfigured, get_provider

from .meterless import Meter, MeteredProvider, get_meter
from .zetaris import ZetarisError, get_zetaris_source

SYSTEM_PROMPT = (
    "You are the reasoning engine of a stateful agent run. "
    "Answer the latest instruction directly and briefly, using the "
    "conversation history for context. Do not restate the history."
)

_meter: Meter = get_meter()


# ---------------------------------------------------------------- helpers

def _usage_dict(run: Run) -> Dict[str, Any]:
    return asdict(run.usage)


def _budget_dict(run: Run) -> Dict[str, Any]:
    return asdict(run.budget)


def _parse_budget(budget_json: Optional[str]) -> Budget:
    if not budget_json:
        return Budget()
    try:
        raw = json.loads(budget_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"budget_json is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("budget_json must be a JSON object")
    allowed = {"max_steps", "max_wall_seconds", "max_cost_usd"}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unknown budget keys: {sorted(unknown)}")
    try:
        return Budget(
            max_steps=int(raw.get("max_steps", 50)),
            max_wall_seconds=float(raw.get("max_wall_seconds", 600.0)),
            max_cost_usd=float(raw.get("max_cost_usd", 1.0)),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid budget values: {exc}") from exc


def _history_messages(run_id: str) -> List[Dict[str, str]]:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for step in get_steps(run_id):
        role = "assistant" if step["kind"] == "assistant" else "user"
        messages.append({"role": role, "content": step["content"]})
    return messages


# ------------------------------------------------------------------ tools

async def run_start(objective: str, budget_json: Optional[str] = None) -> Dict[str, Any]:
    """Start a new stateful agent run.

    :param objective: the run's goal in one sentence.
    :param budget_json: optional JSON object with max_steps,
        max_wall_seconds, max_cost_usd.
    """
    if not objective or not objective.strip():
        return {"error": "objective is required", "code": "bad_request"}
    try:
        budget = _parse_budget(budget_json)
    except ValueError as exc:
        return {"error": str(exc), "code": "bad_request"}
    run = create_run(objective.strip(), budget=budget)
    return {"run_id": run.run_id, "objective": run.objective,
            "status": run.status, "budget": _budget_dict(run)}


async def run_step(run_id: str, instruction: str) -> Dict[str, Any]:
    """Run ONE agent loop step on a run and return the assistant text."""
    if not instruction or not instruction.strip():
        return {"error": "instruction is required", "code": "bad_request"}
    try:
        run = get_run(run_id)
    except RunNotFound:
        return {"error": f"unknown run_id: {run_id}", "code": "not_found"}
    if run.is_terminal:
        return {"error": f"run is {run.status}, no further steps accepted",
                "code": "run_not_active", "status": run.status}
    try:
        check_budget(run)
    except BudgetExhausted as exc:
        run.status = "exhausted"
        save_run(run)
        return {"error": str(exc), "code": "budget_exhausted",
                "run_id": run.run_id, "status": run.status,
                "usage": _usage_dict(run)}

    messages = _history_messages(run_id)
    messages.append({"role": "user", "content": instruction.strip()})

    try:
        provider = get_provider()
    except ProviderNotConfigured as exc:
        return {"error": str(exc), "code": "provider_not_configured",
                "run_id": run.run_id}
    metered = MeteredProvider(provider, _meter,
                              provider_name=getattr(provider, "name", ""))
    started = time.time()
    try:
        result = metered.complete(messages, run_id=run.run_id)
    except Exception as exc:  # provider failure must not corrupt the ledger
        return {"error": f"provider failed: {exc}", "code": "provider_error",
                "run_id": run.run_id}
    elapsed = time.time() - started

    prompt_tokens = int(getattr(result, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(result, "completion_tokens", 0) or 0)
    model = str(getattr(result, "model", "") or "")
    text = str(getattr(result, "text", ""))
    cost = cost_for_usage(model, prompt_tokens, completion_tokens)

    append_step(run_id, "user", instruction.strip())
    append_step(run_id, "assistant", text,
                tokens=prompt_tokens + completion_tokens, cost=cost)

    run.usage.add_step(wall_seconds=elapsed, cost_usd=cost,
                       prompt_tokens=prompt_tokens,
                       completion_tokens=completion_tokens)
    if run.status == "pending":
        run.status = "running"
    save_run(run)

    return {"run_id": run.run_id, "status": run.status, "text": text,
            "model": model, "usage": _usage_dict(run)}


async def run_status(run_id: str) -> Dict[str, Any]:
    """Return a run's state and usage summary."""
    try:
        run = get_run(run_id)
    except RunNotFound:
        return {"error": f"unknown run_id: {run_id}", "code": "not_found"}
    return {"run_id": run.run_id, "objective": run.objective,
            "status": run.status, "usage": _usage_dict(run),
            "budget": _budget_dict(run),
            "steps": len(get_steps(run_id))}


async def run_cancel(run_id: str) -> Dict[str, Any]:
    """Cancel a run; it keeps its ledger and receipt."""
    try:
        run = cancel_run(run_id)
    except RunNotFound:
        return {"error": f"unknown run_id: {run_id}", "code": "not_found"}
    return {"run_id": run.run_id, "status": run.status}


async def run_receipt(run_id: str) -> Dict[str, Any]:
    """Full run record: metadata, every step, and the cost breakdown."""
    try:
        run = get_run(run_id)
    except RunNotFound:
        return {"error": f"unknown run_id: {run_id}", "code": "not_found"}
    steps = get_steps(run_id)
    meter_totals = _meter.totals(run_id)
    return {
        "run_id": run.run_id,
        "objective": run.objective,
        "status": run.status,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "budget": _budget_dict(run),
        "usage": _usage_dict(run),
        "metered_totals": meter_totals,
        "steps": steps,
    }


async def zetaris_query(query: str) -> Dict[str, Any]:
    """Run a SQL query against Zetaris (federated data layer)."""
    if not query or not query.strip():
        return {"error": "query is required", "code": "bad_request"}
    try:
        rows = get_zetaris_source().query(query)
    except ZetarisError as exc:
        return {"error": str(exc), "code": "zetaris_error"}
    except Exception as exc:
        return {"error": f"unexpected error: {exc}", "code": "internal_error"}
    return {"rows": rows, "count": len(rows)}


# ------------------------------------------------------------------ server

def create_server() -> MCPServer:
    """Build the MCP server with all runstate protocol tools registered."""
    mcp = MCPServer("runstate-protocol")
    mcp.tool()(run_start)
    mcp.tool()(run_step)
    mcp.tool()(run_status)
    mcp.tool()(run_cancel)
    mcp.tool()(run_receipt)
    mcp.tool()(zetaris_query)
    return mcp


def main() -> None:
    """Serve the protocol over Streamable HTTP."""
    host = os.environ.get("RUNSTATE_HOST", "127.0.0.1")
    port = int(os.environ.get("RUNSTATE_PORT", "8899"))
    mcp = create_server()
    asyncio.run(mcp.run_streamable_http_async(host=host, port=port))


if __name__ == "__main__":
    main()
