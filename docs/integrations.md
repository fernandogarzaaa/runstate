# runstate.protocol — MCP protocol server + sponsor integrations

Builder 2 of 3 for **runstate** (Open Agent Hackathon 2026, Track 03
Reasoning Architecture). This package is the APE-pattern embodiment of the
product: one MCP tool call advances a stateful, budgeted agent run held by
Builder 1's runtime.

```
integrations/
  runstate/protocol/
    __init__.py    lazy public API + sibling-runtime namespace merge
    server.py      MCP server (Streamable HTTP), 6 tools
    zetaris.py     Zetaris adapter: real client + deterministic mock
    meterless.py   usage metering + Meterless H-MEM memory adapter
  tests/
    conftest.py        wires the real ../runtime, tmp ledger DB per test
    test_server.py     protocol tools against the real runtime contract
    test_zetaris.py    real client (mocked HTTP) + mock source
    test_meterless.py  meter, middleware, H-MEM memory
    _shim/             legacy test-only contract stand-in (unused while
                       ../runtime is checked out)
```

## MCP tools

| Tool | What it does |
|---|---|
| `run_start(objective, budget_json?)` | Create a run; optional JSON budget (`max_steps`, `max_wall_seconds`, `max_cost_usd`). Returns `run_id`. |
| `run_step(run_id, instruction)` | Run ONE agent loop step: ledger history + instruction -> provider -> append steps -> enforce budget -> persist usage with real model pricing. Returns assistant text + updated usage. |
| `run_status(run_id)` | State, usage, budget, step count. |
| `run_cancel(run_id)` | Cancel; the ledger and receipt survive. |
| `run_receipt(run_id)` | Full run + every step + metered totals (JSON-serializable). |
| `zetaris_query(query)` | SQL against Zetaris (or mock fixtures). |

## Run

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests/ -q
RUNSTATE_PORT=8899 .venv/bin/python -m runstate.protocol.server
```

The server reads `RUNSTATE_HOST` / `RUNSTATE_PORT` (default
`127.0.0.1:8899`) and serves MCP over Streamable HTTP.

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `RUNSTATE_HOST` / `RUNSTATE_PORT` | `127.0.0.1` / `8899` | server bind |
| `RUNSTATE_DB_PATH` | `~/.runstate/ledger.db` | runtime SQLite ledger (Builder 1) |
| `RUNSTATE_PROVIDER` | (unset) | `nebius` or `openai`; required for `run_step` |
| `NEBIUS_API_KEY` / `OPENAI_API_KEY` | (unset) | provider credentials (never logged) |
| `RUNSTATE_MODEL` | provider default (Nemotron 3 Nano 30B on Nebius) | model override |
| `ZETARIS_MODE` | `mock` | `mock` -> `MockZetarisSource` (deterministic fixtures); `live` -> real REST client |
| `ZETARIS_ENDPOINT` | (unset) | e.g. `https://data.example.com`; required for live |
| `ZETARIS_API_KEY` | (unset) | sent as `X-API-Key` (override header via `ZETARIS_API_KEY_HEADER`); `X-Request-ID` + `X-Org-ID` on every call |
| `ZETARIS_ORG_ID` | (unset) | optional org header |
| `METERLESS_MODE` | `mock` | `mock` -> seeded `MockMeter`/`MockMeterlessMemory`; `live` -> empty real ones |

## What "Meterless integration" honestly means here

Meterless is **not a metering SaaS**. Their public repo (verified Oct
2026) is a local-first context layer for agents (H-MEM hierarchical
memory, world model, Markovian reasoning compression; Apache-2.0) with no
hosted usage-events API, so there is nothing to POST usage to and we did
not invent one. This package therefore ships two honest pieces:

1. **`Meter`** — runstate's own internal usage ledger. Every
   `run_step` routes the provider call through `MeteredProvider`
   middleware, which records prompt/completion tokens and USD cost
   (priced with the runtime's real model price table) per run. Totals
   feed `run_receipt`.
2. **`MeterlessMemory`** — the genuine sponsor integration: a Python
   implementation of their H-MEM three-tier memory pattern
   (short-term / working / long-term, capture -> recall -> consolidate),
   kept local-first like the original. Runs can use it as durable
   memory across steps.

`METERLESS_MODE` therefore switches between seeded fixtures and an
empty store; no credentials are involved.

## Zetaris client protocol (documented from Zetaris API docs)

`POST {endpoint}/api/v1.0/query/sql/start` with
`{"select": "<SQL>", "pageLimit": N}` returns the first page plus a
`queryToken`; further pages via `GET .../query/sql/page`
(`queryToken`, `pageNumber`); the token is always released with
`DELETE .../query/sql/close/{queryToken}` in a `finally` block. The API
key header name (`X-API-Key`) is the one unverified assumption and is
overridable via `ZETARIS_API_KEY_HEADER`.

## Verifying live

- **Zetaris live**: set `ZETARIS_MODE=live`, `ZETARIS_ENDPOINT`,
  `ZETARIS_API_KEY`; `zetaris_query("SELECT 1")` should return rows.
  Without credentials the real client raises a clear error at
  construction; tests cover the wire protocol against mocked HTTP.
- **Provider**: set `RUNSTATE_PROVIDER=nebius` + `NEBIUS_API_KEY`
  (or `openai` + `OPENAI_API_KEY`); `run_step` then performs a real
  inference. Without them it returns `provider_not_configured` instead
  of failing silently.
- **Meterless**: nothing to verify remotely; it is local-first by
  design. `METERLESS_MODE=live` gives an empty memory store.
