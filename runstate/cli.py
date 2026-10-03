"""runstate CLI: the human interface to the stateful agent orchestration runtime.

Commands:
    runstate run "objective" [--max-steps N] [--max-cost USD] [--provider NAME]
    runstate status <run-id> [--json]
    runstate receipt <run-id> [--json]
    runstate cancel <run-id>
    runstate list [--status STATUS]
    runstate doctor

The CLI drives the runtime's public API and the local runner loop; it never
implements agent logic itself. Runtime imports are lazy so `runstate doctor`
can report a missing runtime instead of tracebacks.
"""

import argparse
import functools
import json
import os
import sys
import time


@functools.lru_cache(maxsize=1)
def _runtime():
    """Import the runtime package or exit with a helpful message."""
    try:
        from runstate import runtime as rt
        return rt
    except ImportError:
        print(
            "error: the runstate runtime is not installed.\n"
            "Install the full runstate distribution (runtime + surface), or run\n"
            "from a checkout where the sibling 'runtime' directory is present.",
            file=sys.stderr,
        )
        sys.exit(2)


def _outcome_of(run_id):
    """Outcome = content of the latest ledger step with kind 'outcome'."""
    rt = _runtime()
    try:
        steps = rt.get_steps(run_id)
    except Exception:  # noqa: BLE001 - ledger unavailable; outcome unknown
        return None
    for s in reversed(steps):
        if s.get("kind") == "outcome":
            return s.get("content")
    return None


def _run_dict(run):
    """Plain-dict view of a Run for JSON output."""
    rt = _runtime()
    return {
        "run_id": run.run_id,
        "objective": run.objective,
        "status": run.status,
        "budget": {
            "max_steps": run.budget.max_steps,
            "max_wall_seconds": run.budget.max_wall_seconds,
            "max_cost_usd": run.budget.max_cost_usd,
        },
        "usage": {
            "steps": run.usage.steps,
            "wall_seconds": round(run.usage.wall_seconds, 3),
            "cost_usd": round(run.usage.cost_usd, 6),
            "prompt_tokens": run.usage.prompt_tokens,
            "completion_tokens": run.usage.completion_tokens,
        },
        "outcome": _outcome_of(run.run_id),
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "steps": rt.get_steps(run.run_id),
    }


def _fmt_money(v):
    try:
        return f"${float(v):.4f}"
    except (TypeError, ValueError):
        return "?"


def _get_run_or_exit(run_id):
    rt = _runtime()
    try:
        return rt.api.get_run(run_id)
    except rt.RunNotFound:
        print(f"error: no such run: {run_id}", file=sys.stderr)
        sys.exit(1)


def _print_run_human(run):
    u, b = run.usage, run.budget
    print(f"Run       {run.run_id}")
    print(f"Status    {run.status}")
    print(f"Objective {run.objective}")
    print(f"Steps     {u.steps} (max {b.max_steps})")
    print(f"Cost      {_fmt_money(u.cost_usd)} / {_fmt_money(b.max_cost_usd)} budget")
    print(f"Tokens    {u.prompt_tokens} prompt + {u.completion_tokens} completion")
    outcome = _outcome_of(run.run_id)
    if outcome:
        print(f"Outcome   {outcome}")


def _print_receipt_human(run):
    rt = _runtime()
    steps = rt.get_steps(run.run_id)
    u, b = run.usage, run.budget
    print(f"Receipt for run {run.run_id}")
    print(f"Status: {run.status}")
    print()
    print(f"{'seq':>3}  {'kind':<10} {'tokens':>7} {'cost':>9}  content")
    print("-" * 78)
    for s in steps:
        content = str(s.get("content", "")).replace("\n", " ")
        print(f"{s.get('seq', '?'):>3}  {str(s.get('kind', '?'))[:10]:<10} "
              f"{s.get('tokens', 0):>7} {_fmt_money(s.get('cost')):>9}  {content[:44]}")
    print("-" * 78)
    print(f"Total steps:  {u.steps}")
    print(f"Total tokens: {u.prompt_tokens} prompt + {u.completion_tokens} completion")
    print(f"Total cost:   {_fmt_money(u.cost_usd)} (budget {_fmt_money(b.max_cost_usd)})")
    print(f"Wall time:    {u.wall_seconds:.1f}s (budget {b.max_wall_seconds:g}s)")


def _on_step_progress(seq, kind, text, cost):
    first = (text or "").strip().splitlines()[0][:72] if text else ""
    print(f"  step {seq} [{kind}] {_fmt_money(cost)}  {first}")


def cmd_run(args):
    from runstate import runner

    rt = _runtime()
    max_steps = args.max_steps if args.max_steps is not None else int(
        os.environ.get("RUNSTATE_DEFAULT_MAX_STEPS", "50")
    )
    max_cost = args.max_cost if args.max_cost is not None else float(
        os.environ.get("RUNSTATE_DEFAULT_MAX_COST_USD", "1.00")
    )
    try:
        budget = rt.Budget(max_steps=max_steps, max_cost_usd=max_cost)
    except ValueError as exc:
        print(f"error: invalid budget: {exc}", file=sys.stderr)
        return 2
    try:
        run = rt.api.create_run(args.objective, budget=budget)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"started run {run.run_id}")
    timeout = float(args.timeout if args.timeout is not None
                    else os.environ.get("RUNSTATE_RUN_TIMEOUT_S", "300"))
    try:
        final = runner.drive_run(run.run_id, provider=args.provider,
                                 timeout=timeout, on_step=_on_step_progress)
    except runner.DriveTimeout as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(f"hint: `runstate status {run.run_id}` to inspect the run", file=sys.stderr)
        return 1
    except rt.providers.ProviderNotConfigured as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("hint: set RUNSTATE_PROVIDER=nebius (needs NEBIUS_API_KEY) or "
              "RUNSTATE_PROVIDER=openai (needs OPENAI_API_KEY)", file=sys.stderr)
        return 2
    print()
    _print_run_human(final)
    return 0 if final.status == "success" else 1


def cmd_status(args):
    run = _get_run_or_exit(args.run_id)
    if args.json:
        print(json.dumps(_run_dict(run), indent=2))
    else:
        _print_run_human(run)
    return 0


def cmd_receipt(args):
    run = _get_run_or_exit(args.run_id)
    if args.json:
        print(json.dumps(_run_dict(run), indent=2))
    else:
        _print_receipt_human(run)
    return 0


def cmd_cancel(args):
    rt = _runtime()
    _get_run_or_exit(args.run_id)  # validates existence first
    run = rt.api.cancel_run(args.run_id)
    print(f"run {run.run_id}: {run.status}")
    return 0


def cmd_list(args):
    rt = _runtime()
    try:
        runs = rt.api.list_runs(status=args.status)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps([_run_dict(r) for r in runs], indent=2))
        return 0
    if not runs:
        print("no runs" + (f" with status {args.status}" if args.status else ""))
        return 0
    print(f"{'run id':<24} {'status':<10} {'cost':>9}  objective")
    print("-" * 80)
    for r in runs:
        print(f"{r.run_id[:24]:<24} {r.status[:10]:<10} "
              f"{_fmt_money(r.usage.cost_usd):>9}  {r.objective[:40]}")
    return 0


def _check(label, fn):
    try:
        detail = fn()
    except Exception as exc:  # noqa: BLE001 - each check reports its own failure
        print(f"[FAIL] {label}: {exc}")
        return False
    print(f"[ok]   {label}" + (f": {detail}" if detail else ""))
    return True


def _warn(label, detail):
    print(f"[warn] {label}: {detail}")


def cmd_doctor(_args):
    import tempfile

    ok = True

    def check_python():
        assert sys.version_info >= (3, 10), f"python {sys.version} < 3.10"
        return f"python {sys.version.split()[0]}"

    ok &= _check("python >= 3.10", check_python)

    home = os.path.expanduser(os.environ.get("RUNSTATE_HOME", "~/.runstate"))

    def check_home():
        os.makedirs(home, exist_ok=True)
        probe = os.path.join(home, ".doctor-probe")
        with open(probe, "w") as fh:
            fh.write("ok")
        os.remove(probe)
        return home

    ok &= _check("RUNSTATE_HOME writable", check_home)

    def check_ledger():
        from runstate.runtime import ledger as _ledger
        path = _ledger.db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, probe = tempfile.mkstemp(dir=str(path.parent), prefix="doctor-")
        os.close(fd)
        os.remove(probe)
        return str(path)

    ok &= _check("ledger database writable", check_ledger)

    # Test runtime importability directly: do NOT use _runtime() here, since it
    # sys.exits instead of reporting a [FAIL].
    try:
        from runstate import runtime as _rt
        missing = [n for n in ("create_run", "get_run", "cancel_run",
                              "list_runs", "get_provider")
                   if not callable(getattr(_rt, n, None))]
        if missing:
            print(f"[FAIL] runtime module importable: missing {missing}")
            ok = False
        else:
            print("[ok]   runtime module importable: runstate.runtime contract ok")
    except ImportError as exc:
        print(f"[FAIL] runtime module importable: {exc}")
        ok = False

    rt = None
    try:
        from runstate import runtime as _rt2
        rt = _rt2
    except ImportError:
        pass
    if rt is not None:
        try:
            provider = rt.providers.get_provider()
            name = getattr(provider, "name", None) or type(provider).__name__
            model = getattr(provider, "model", "?")
            print(f"[ok]   default provider resolves: {name} (model {model})")
        except rt.providers.ProviderNotConfigured as exc:
            _warn("default provider not configured",
                  f"{exc}; set RUNSTATE_PROVIDER=nebius|openai plus its API key "
                  "to run agents")
    else:
        _warn("default provider not checked", "runtime not installed")

    try:
        from runstate.protocol import server  # noqa: F401
        print("[ok]   MCP protocol server module importable")
    except ImportError:
        _warn("MCP protocol server not installed",
              "runstate.protocol.server missing; the MCP interface is unavailable "
              "but the CLI is fully functional")

    print()
    print("doctor: " + ("all checks passed" if ok else "some checks FAILED"))
    return 0 if ok else 1


def build_parser():
    p = argparse.ArgumentParser(
        prog="runstate",
        description="Stateful agent orchestration runtime: budgeted, resumable, "
                    "receipted agent runs.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    pr = sub.add_parser("run", help="start an agent run and drive it to completion")
    pr.add_argument("objective", help="the objective for the agent run, in words")
    pr.add_argument("--max-steps", type=int, default=None,
                    help="max reasoning steps (default: RUNSTATE_DEFAULT_MAX_STEPS or 50)")
    pr.add_argument("--max-cost", type=float, default=None, dest="max_cost",
                    help="max spend in USD (default: RUNSTATE_DEFAULT_MAX_COST_USD or 1.00)")
    pr.add_argument("--provider", default=None,
                    help="provider name: nebius|openai (default: RUNSTATE_PROVIDER)")
    pr.add_argument("--timeout", type=float, default=None,
                    help="seconds to wait for completion (default 300)")
    pr.set_defaults(func=cmd_run)

    ps = sub.add_parser("status", help="show a run's current state")
    ps.add_argument("run_id")
    ps.add_argument("--json", action="store_true", help="machine-readable output")
    ps.set_defaults(func=cmd_status)

    pc = sub.add_parser("receipt", help="show a run's cost/step breakdown")
    pc.add_argument("run_id")
    pc.add_argument("--json", action="store_true", help="machine-readable output")
    pc.set_defaults(func=cmd_receipt)

    px = sub.add_parser("cancel", help="cancel a run")
    px.add_argument("run_id")
    px.set_defaults(func=cmd_cancel)

    pl = sub.add_parser("list", help="list runs")
    pl.add_argument("--status", default=None, help="filter by status")
    pl.add_argument("--json", action="store_true", help="machine-readable output")
    pl.set_defaults(func=cmd_list)

    pd = sub.add_parser("doctor", help="check environment, providers, ledger")
    pd.set_defaults(func=cmd_doctor)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
