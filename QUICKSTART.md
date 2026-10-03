# runstate QUICKSTART

Five minutes from zero to your first receipted agent run. Every command below
was executed against the real product; outputs are real (the walkthrough uses
a local deterministic stand-in provider so no API key is needed; with a real
model the shape is identical, only the words differ).

## 0. Prerequisites

Python 3.10 or newer. Check:

```bash
python3 --version
```

## 1. Install

From a checkout:

```bash
pip install .
runstate doctor
```

(Or `./install.sh`, which installs and then runs the doctor. `pip install
runstate` will work once the package is published to PyPI.)

## 2. Run the doctor

```bash
runstate doctor
```

```
[ok]   python >= 3.10: python 3.12.3
[ok]   RUNSTATE_HOME writable: /home/you/.runstate
[ok]   ledger database writable: /home/you/.runstate/ledger.db
[ok]   runtime module importable: runstate.runtime contract ok
[warn] default provider not configured: no provider configured: set RUNSTATE_PROVIDER to 'nebius' or 'openai'; ...
[ok]   MCP protocol server module importable

doctor: all checks passed
```

The provider warning is expected: configure a provider before running agents.

## 3. Configure a provider

```bash
export RUNSTATE_PROVIDER=nebius
export NEBIUS_API_KEY="<redacted>"
```

Or `RUNSTATE_PROVIDER=openai` with `OPENAI_API_KEY`. Optional:
`RUNSTATE_MODEL` overrides the default model id.

If you skip this, `runstate run` fails cleanly and tells you exactly what to
set:

```bash
runstate run "Summarize the quarterly report"
```

```
started run run-39be090885d50010
error: no provider configured: set RUNSTATE_PROVIDER to 'nebius' or 'openai'
hint: set RUNSTATE_PROVIDER=nebius (needs NEBIUS_API_KEY) or RUNSTATE_PROVIDER=openai (needs OPENAI_API_KEY)
```

(The created run stays `pending`, so nothing is lost; configure the provider
and run again.)

## 4. Start a run

```bash
runstate run "Summarize the quarterly report" --max-steps 10 --max-cost 0.50
```

```
started run run-4702142055de87ae
  step 1 [assistant] $0.0001  The quarterly report shows revenue up 12% with churn down to 3%.
  step 2 [assistant] $0.0001  FINAL: Revenue up 12%, churn down to 3%. Summary complete.

Run       run-4702142055de87ae
Status    success
Objective Summarize the quarterly report
Steps     2 (max 10)
Cost      $0.0001 / $0.5000 budget
Tokens    180 prompt + 32 completion
Outcome   Revenue up 12%, churn down to 3%. Summary complete.
```

The model ends the run by starting its final message with `FINAL:` followed
by a summary. That summary becomes the run's outcome, stored in the ledger.

Budgets are hard: `--max-steps` caps reasoning steps, `--max-cost` caps USD
spend, and both are checked before every step. A run that hits a budget ends
as `exhausted`, not `failed`; its partial work stays in the ledger.

## 5. Inspect the run

```bash
runstate status run-4702142055de87ae
runstate status run-4702142055de87ae --json   # machine-readable
```

## 6. Get the receipt

```bash
runstate receipt run-4702142055de87ae
```

```
Receipt for run run-4702142055de87ae
Status: success

seq  kind        tokens      cost  content
------------------------------------------------------------------------------
  1  assistant      138   $0.0001  The quarterly report shows revenue up 12% wi
  2  assistant       74   $0.0001  FINAL: Revenue up 12%, churn down to 3%. Sum
  3  outcome          0   $0.0000  Revenue up 12%, churn down to 3%. Summary co
------------------------------------------------------------------------------
Total steps:  2
Total tokens: 180 prompt + 32 completion
Total cost:   $0.0001 (budget $0.5000)
Wall time:    0.0s (budget 600s)
```

Every line comes from the append-only SQLite ledger (`~/.runstate/ledger.db`
by default, `RUNSTATE_DB_PATH` to move it). The receipt is derived from the
ledger, never reconstructed from memory.

## 7. Manage runs

```bash
runstate list                      # all runs, newest first
runstate list --status success     # filter by status
runstate list --json               # machine-readable
runstate cancel run-4702142055de87ae
```

Cancelling a terminal run is a no-op that reports its status.

## 8. Serve runs over MCP

```bash
python -m runstate.protocol.server
```

Starts the MCP protocol server on `http://127.0.0.1:8899` (override with
`RUNSTATE_HOST` / `RUNSTATE_PORT`). Any MCP client can then call `run_start`,
`run_step`, `run_status`, `run_cancel`, and `run_receipt` to drive stateful,
budgeted runs remotely.

## What to try next

- Set a tiny `--max-cost 0.01` and watch a run end as `exhausted`.
- Point `RUNSTATE_DB_PATH` at a scratch file and inspect the ledger with
  `sqlite3`: tables `runs` and `steps`.
- Read [ARCHITECTURE.md](ARCHITECTURE.md) for how the run primitive,
  budgets, ledger, MCP server, and sponsor adapters fit together.
