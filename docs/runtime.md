# runstate.runtime — core runtime

The core runtime module of **runstate**, a stateful agent orchestration
runtime. The primitive of the system is a **Run**: a budgeted, resumable,
receipted unit of agent work that persists in SQLite and survives process
restarts.

Zero third-party dependencies. Python 3.11+.

## Quickstart

```python
from runstate.runtime import (
    Budget, create_run, get_run, cancel_run, list_runs,
    append_step, get_provider, check_budget, cost_for_usage,
)

# Create a run with a custom budget
run = create_run("triage the repo's open issues",
                 budget=Budget(max_steps=20, max_wall_seconds=300, max_cost_usd=0.50))
print(run.run_id, run.status)   # run-9f3c... pending

# Record ledger steps as the agent works
append_step(run.run_id, "thought", "Listing open issues first.", tokens=40)

# Accumulate usage and enforce the budget
run.usage.add_step(wall_seconds=2.1, cost_usd=0.004,
                   prompt_tokens=120, completion_tokens=30)
check_budget(run)               # raises BudgetExhausted when a bound is reached

# Call a model through the configured provider
provider = get_provider()       # RUNSTATE_PROVIDER=nebius|openai
result = provider.complete([{"role": "user", "content": "hi"}])
print(result.text, result.prompt_tokens, result.completion_tokens)

# Lifecycle
cancel_run(run.run_id)          # -> cancelled (idempotent on terminal runs)
get_run(run.run_id)             # raises RunNotFound when unknown
list_runs(status="pending")     # newest first, optional status filter
```

## Modules

| Module | Contents |
|---|---|
| `runstate.runtime.models` | `Run`, `Budget`, `Usage` dataclasses; `RUN_STATUSES`, `TERMINAL_STATUSES` |
| `runstate.runtime.ledger` | SQLite persistence: `save_run`, `load_run`, `list_runs`, `append_step`, `get_steps`, `RunNotFound`, `db_path` |
| `runstate.runtime.providers` | `Provider` ABC, `OpenAICompatibleProvider`, `ChatResult`, `get_provider`, `ProviderError`, `ProviderNotConfigured` |
| `runstate.runtime.budgets` | `check_budget`, `BudgetExhausted`, `cost_for_usage`, `PRICE_TABLE` |
| `runstate.runtime.api` | `create_run`, `get_run`, `cancel_run`, `list_runs` |

All contract names are also re-exported from `runstate.runtime` and `runstate`.

## Run lifecycle

`pending` -> `running` -> one of `success`, `unverified`, `exhausted`,
`failed`, `cancelled`. The last five are terminal: `Run.is_terminal` is
true and `cancel_run` becomes a no-op on them.

## Budgets

`check_budget(run)` raises `BudgetExhausted` (carrying `run_id`,
`dimension`, `used`, `allowed`) once any consumed dimension *reaches* its
bound: steps >= `max_steps`, wall seconds >= `max_wall_seconds`, or cost >=
`max_cost_usd`. Check it before every agent step.

`cost_for_usage(model, prompt_tokens, completion_tokens)` prices token
usage from `PRICE_TABLE` (per-1M-token prompt/completion rates). The only
verified entry is `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` at
$0.06 / $0.24 per 1M tokens (Nebius Token Factory catalog, 2026-10-03).
Unknown models use the documented conservative default
`DEFAULT_PRICE_PER_1M = (0.50, 1.50)` so they fail safe.

## Persistence

Database path from `RUNSTATE_DB_PATH`, default `~/.runstate/ledger.db`.
Two tables: `runs` (one row per run, budget/usage as JSON) and `steps`
(append-only ledger: `seq`, `kind`, `content`, `tokens`, `cost`).

## Providers

`get_provider(name=None)` resolves `RUNSTATE_PROVIDER` (`nebius` or
`openai`). `nebius` needs `NEBIUS_API_KEY` and defaults to
`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` at
`https://api.tokenfactory.nebius.com/v1`; `openai` needs `OPENAI_API_KEY`.
`RUNSTATE_MODEL` overrides the model for either. Missing keys, missing
names, and unknown names raise `ProviderNotConfigured` — there are no
silent fallbacks. Transport/HTTP/payload failures raise `ProviderError`.

## Tests

```bash
python3 -m pytest          # 49 tests, stdlib only
```
