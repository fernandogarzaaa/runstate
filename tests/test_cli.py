"""CLI unit tests: argument parsing, output shaping, error paths."""

import io
import json
from contextlib import redirect_stdout, redirect_stderr

import pytest

from runstate import cli


def parse(*argv):
    return cli.build_parser().parse_args(list(argv))


def test_parser_run_defaults():
    args = parse("run", "do stuff")
    assert args.command == "run"
    assert args.objective == "do stuff"
    assert args.max_steps is None
    assert args.max_cost is None
    assert args.provider is None


def test_parser_run_flags():
    args = parse("run", "x", "--max-steps", "5", "--max-cost", "0.25",
                 "--provider", "nebius", "--timeout", "60")
    assert args.max_steps == 5
    assert args.max_cost == 0.25
    assert args.provider == "nebius"
    assert args.timeout == 60


def test_parser_subcommands():
    assert parse("status", "run-abc").run_id == "run-abc"
    assert parse("status", "run-abc", "--json").json is True
    assert parse("receipt", "run-abc", "--json").json is True
    assert parse("cancel", "run-abc").run_id == "run-abc"
    assert parse("list", "--status", "success").status == "success"
    assert parse("doctor").command == "doctor"


def test_no_command_exits_with_usage():
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2


def test_fmt_money_handles_garbage():
    assert cli._fmt_money(None) == "?"
    assert cli._fmt_money("abc") == "?"
    assert cli._fmt_money(0.0043) == "$0.0043"


def test_list_invalid_status_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNSTATE_DB_PATH", str(tmp_path / "ledger.db"))
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(["list", "--status", "bogus"])
    assert code == 2
    assert "error" in err.getvalue()
