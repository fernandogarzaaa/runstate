# runstate ARCHITECTURE

runstate is a stateful agent orchestration runtime. This document explains how
its five pieces fit together: the run primitive, budgets, the ledger, the MCP
protocol server, and the sponsor adapters.

## The run primitive

Everything in runstate is a **run**: one objective, one budget, one durable
record. The runtime owns this primitive (`runstate.runtime`):

- `create_run(objective, budget=None)` creates a run in `pending` status and
  returns it. The run id is `run_id` (a `run-` prefixed hex string).
- `get_run(run_id)` loads it back from the ledger; `list_runs(status=None)`
  lists runs newest first; `cancel_run(run_id)` cancels a live run and is
  idempotent on terminal ones; `get_run` on an unknown id raises
  `RunNotFound`.
- A run carries a `Budget` (max steps, max wall seconds, max USD cost), a
  `Usage` accumulator (steps taken, wall seconds, USD spent, prompt and
  completion tokens), a status, and timestamps. Statuses are `pending`,
  `running`, `success`, `unverified`, `exhausted`, `failed`, `cancelled`.

The runtime never executes an agent loop. Execution belongs to the runner
(`runstate.runner`), the product surface's driver:

```
check_budget(run)            # raises BudgetExhausted before every step
  -> provider.complete(messages)   # one model call per step
  -> cost_for_usage(model, prompt_tokens, completion_tokens)
  -> append_step(run_id, kind="assistant", content, tokens, cost)
  -> run.usage.add_step(...); save_run(run)   # durable after every step
```

The model signals completion by starting its final message with `FINAL:`
followed by a summary. The runner stores that summary as a ledger step with
`kind="outcome"` (zero tokens, zero cost) and marks the run `success`. A
budget breach appends an `outcome` step describing the exhaustion and marks
the run `exhausted`. A provider transport error appends an `outcome` step and
marks the run `failed`. Every outcome, terminal or not, lives in the ledger;
nothing about a run's ending is kept only in memory.

Because the run is saved after every step, a crashed or timed-out run is
resumable: its status, usage, and history are all in the ledger, and driving
it again continues where it stopped. `runstate run`'s `--timeout` uses this
property: on timeout it raises `DriveTimeout`, leaves the run in its current
state, and the CLI points you at `runstate status <run-id>`.

The CLI (`runstate.cli`, console script `runstate`) is a thin shell over this:
it parses arguments, builds the `Budget`, calls `create_run`, hands the id to
`runner.drive_run`, and renders the result. All runtime imports in the CLI
and runner are lazy, so `runstate doctor` can report a missing runtime as a
checklist failure instead of a traceback.

## Budgets

Budgets are the safety contract. `Budget(max_steps, max_wall_seconds,
max_cost_usd)` is validated at construction (`max_steps >= 1` and friends;
invalid values raise `ValueError` at the CLI with exit code 2, before any run
is created). `check_budget(run)` is called before every step and raises
`BudgetExhausted` naming the breached dimension.

Cost accounting is per step and per model: `cost_for_usage(model,
prompt_tokens, completion_tokens)` prices the step's tokens against the
model's rate card, and the step's cost is stored on the ledger row. The run's
`Usage.cost_usd` is the sum of its priced steps, so the receipt and the budget
check always agree: they read the same numbers.

Defaults are deliberately small: 50 steps, 600 wall seconds, $1.00, tunable
via `RUNSTATE_DEFAULT_MAX_STEPS`, `RUNSTATE_DEFAULT_MAX_COST_USD`, and
`--max-steps` / `--max-cost` on the command line.

## The ledger

The ledger is a SQLite database (`RUNSTATE_DB_PATH`, default
`~/.runstate/ledger.db`) with two tables:

- `runs`: one row per run (id, objective, status, budget/usage as JSON,
  timestamps).
- `steps`: the append-only step log (run id, sequence number, kind,
  content, tokens, cost, timestamp).

"Append-only" is a convention the runtime enforces by only ever inserting:
runs are updated in place for status/usage, but steps are never rewritten.
The receipt (`runstate receipt`) is a pure function of the ledger: per-step
rows plus summed totals. If the ledger says a run cost $0.0001, that is
because two priced step rows say so.

Isolation falls out of the design: point `RUNSTATE_DB_PATH` at a different
file and you get a completely separate runstate. The test suite uses this to
give every test its own ledger.

## The MCP protocol server

`runstate.protocol.server` exposes the run primitive to any MCP client over
Streamable HTTP (`python -m runstate.protocol.server`, default
`http://127.0.0.1:8899`, via `RUNSTATE_HOST` / `RUNSTATE_PORT`). Its tools
mirror the runtime API one-to-one:

- `run_start(objective, budget_json?)` -> run id
- `run_step(run_id, instruction)` -> advances one stateful step
- `run_status(run_id)` -> state, budget, usage
- `run_cancel(run_id)` -> cancel
- `run_receipt(run_id)` -> cost/step breakdown

The design thesis: instead of an agent chaining many thin tools and holding
all state in its own context, one tool call advances a stateful, budgeted run
held by the runtime. The agent stays small; the runtime holds the state, the
budget, and the receipt. The CLI and the MCP server are two interfaces over
the same ledger, so a run started by an MCP client is inspectable with
`runstate status` and vice versa.

## Sponsor adapters

Sponsor integrations plug into the run lifecycle without touching the
primitive:

- **Zetaris** (`runstate.protocol.zetaris`, surfaced as the `zetaris_query`
  MCP tool): query-side adapter. A run can pull governed data through it; the
  adapter is a client of the run, not a change to the run model.
- **Meterless** (`runstate.protocol.meterless`): metering adapter. It reads
  the same per-step token/cost rows the receipt uses, so external metering
  and the local receipt cannot disagree.

Both adapters consume the ledger and the provider layer; neither redefines
what a run is. Adding a new sponsor adapter means adding a module under
`runstate.protocol` plus, if it needs a tool, registering it in
`create_server()`.

## Module map

| Path | Owner | Responsibility |
|---|---|---|
| `runstate/cli.py` | surface | Argument parsing, human/JSON rendering, `doctor` checklist |
| `runstate/runner.py` | surface | The agent loop: budget check, model call, ledger append, usage accumulate |
| `runstate/runtime/` | runtime | `Run`, `Budget`, `Usage`, ledger, provider router. Owns all state. |
| `runstate/protocol/server.py` | integrations | MCP server: `run_start/step/status/cancel/receipt` tools |
| `runstate/protocol/zetaris.py` | integrations | Zetaris sponsor adapter |
| `runstate/protocol/meterless.py` | integrations | Meterless sponsor adapter |
| `tests/` | surface | Full-stack tests: CLI -> runner -> runtime -> ledger -> receipt |

The dependency rule is one-directional: surface depends on runtime and
protocol; runtime depends on nothing product-level; protocol depends on
runtime. Nothing imports the CLI.
