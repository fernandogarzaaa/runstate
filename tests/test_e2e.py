"""End-to-end tests: CLI -> runner loop -> real runtime -> fake provider ->
SQLite ledger -> receipt assertions. These validate the assembled product
surface against the real Builder 1 runtime modules."""

import io
import json
from contextlib import redirect_stderr, redirect_stdout

import pytest

from runstate import cli, runner
from runstate.runtime import api as rt_api
from runstate.runtime import get_steps as rt_get_steps
from runstate.runtime import providers as rt_providers
from runstate.runtime.models import Budget


@pytest.fixture()
def isolated_env(tmp_path, monkeypatch):
    """Isolated ledger DB + home dir; tests never touch ~/.runstate."""
    monkeypatch.setenv("RUNSTATE_DB_PATH", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("RUNSTATE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("RUNSTATE_PROVIDER", raising=False)
    yield tmp_path


class ScriptedProvider:
    """Deterministic fake provider: returns canned ChatResults in order."""

    def __init__(self, texts, model="mock/mock-model", tokens=(50, 20)):
        self._texts = list(texts)
        self.model = model
        self.name = "mock"
        self._tokens = tokens
        self.calls = 0

    def complete(self, messages):
        assert messages, "provider requires messages"
        text = self._texts[min(self.calls, len(self._texts) - 1)]
        self.calls += 1
        pt, ct = self._tokens
        return rt_providers.ChatResult(
            text=text, prompt_tokens=pt, completion_tokens=ct, model=self.model
        )


@pytest.fixture()
def fake_provider(monkeypatch):
    provider = ScriptedProvider([
        "Step 1: analyzing the objective.",
        "FINAL: all done, objective achieved.",
    ])
    monkeypatch.setattr(rt_providers, "get_provider", lambda name=None: provider)
    return provider


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
    except SystemExit as exc:
        code = exc.code
    return code, out.getvalue(), err.getvalue()


def test_run_completes_end_to_end(isolated_env, fake_provider):
    code, out, err = run_cli("run", "summarize the quarterly report",
                             "--max-steps", "10", "--max-cost", "1.00")
    assert code == 0, err + out
    assert "started run run-" in out
    assert "step 1 [assistant]" in out
    assert "step 2 [assistant]" in out
    assert "Status    success" in out
    assert fake_provider.calls == 2


def test_run_records_steps_in_ledger(isolated_env, fake_provider):
    code, out, _ = run_cli("run", "check inbox zero")
    assert code == 0
    run_id = next(l for l in out.splitlines() if l.startswith("started run ")).split()[-1]

    run = rt_api.get_run(run_id)
    assert run.status == "success"
    assert run.usage.steps == 2
    assert run.usage.prompt_tokens == 100
    assert run.usage.completion_tokens == 40
    assert run.usage.cost_usd > 0

    steps = rt_get_steps(run_id)
    assert [s["seq"] for s in steps] == [1, 2, 3]
    assert [s["kind"] for s in steps] == ["assistant", "assistant", "outcome"]
    assert steps[-1]["content"] == "all done, objective achieved."
    assert all(s["cost"] > 0 for s in steps[:2])


def test_status_and_receipt_roundtrip(isolated_env, fake_provider):
    code, out, _ = run_cli("run", "roundtrip check")
    run_id = next(l for l in out.splitlines() if l.startswith("started run ")).split()[-1]

    code, out, _ = run_cli("status", run_id)
    assert code == 0
    assert run_id in out and "success" in out

    code, out, _ = run_cli("status", run_id, "--json")
    assert code == 0
    data = json.loads(out)
    assert data["run_id"] == run_id
    assert data["status"] == "success"
    assert len(data["steps"]) == 3
    assert data["outcome"] == "all done, objective achieved."
    assert data["budget"]["max_steps"] == 50

    code, out, _ = run_cli("receipt", run_id)
    assert code == 0
    assert f"Receipt for run {run_id}" in out
    assert "Total cost:" in out

    code, out, _ = run_cli("receipt", run_id, "--json")
    assert code == 0
    data = json.loads(out)
    assert data["usage"]["steps"] == 2
    assert data["usage"]["cost_usd"] > 0


def test_run_unknown_id_errors(isolated_env):
    code, _, err = run_cli("status", "run-deadbeefdeadbeef")
    assert code == 1
    assert "no such run" in err


def test_run_empty_objective_rejected(isolated_env):
    code, _, err = run_cli("run", "   ")
    assert code == 2
    assert "error" in err


def test_run_without_provider_fails_clearly(isolated_env, monkeypatch):
    monkeypatch.setattr(
        rt_providers, "get_provider",
        lambda name=None: (_ for _ in ()).throw(
            rt_providers.ProviderNotConfigured("no provider configured")),
    )
    code, _, err = run_cli("run", "needs a model")
    assert code == 2
    assert "RUNSTATE_PROVIDER" in err


def test_step_budget_exhaustion(isolated_env, monkeypatch):
    never_final = ScriptedProvider(["still thinking..."] * 10)
    monkeypatch.setattr(rt_providers, "get_provider", lambda name=None: never_final)
    code, out, _ = run_cli("run", "loop forever", "--max-steps", "3")
    assert code == 1
    assert "exhausted" in out
    run_id = next(l for l in out.splitlines() if l.startswith("started run ")).split()[-1]
    assert rt_api.get_run(run_id).status == "exhausted"


def test_list_and_cancel(isolated_env, fake_provider):
    code, out, _ = run_cli("run", "first objective")
    run_id = next(l for l in out.splitlines() if l.startswith("started run ")).split()[-1]
    run_cli("run", "second objective")

    code, out, _ = run_cli("list")
    assert code == 0
    assert "first objective" in out and "second objective" in out

    code, out, _ = run_cli("list", "--status", "success", "--json")
    assert code == 0
    assert len(json.loads(out)) == 2

    code, out, _ = run_cli("cancel", run_id)
    assert code == 0
    assert run_id in out  # terminal runs: cancel is a no-op reporting status


def test_doctor_passes_against_full_stack(isolated_env):
    code, out, _ = run_cli("doctor")
    assert code == 0, out
    assert out.count("[ok]") >= 4
    assert "[FAIL]" not in out
    for label in ("python >= 3.10", "RUNSTATE_HOME writable",
                  "ledger database writable", "runtime module importable"):
        assert label in out


def test_doctor_warns_without_provider(isolated_env):
    code, out, _ = run_cli("doctor")
    assert code == 0
    assert "provider not configured" in out
    assert "RUNSTATE_PROVIDER" in out


def test_runner_final_detection():
    assert runner.is_final("FINAL: done")
    assert runner.is_final("  final: done  ")
    assert not runner.is_final("not done yet")
    assert runner.final_text("FINAL: the answer\n") == "the answer"
    assert runner.final_text("thinking...\nFINAL: ok") == "thinking...\nok"


def test_protocol_server_module_importable():
    from runstate.protocol import server
    assert callable(getattr(server, "main", None))
    assert callable(getattr(server, "create_server", None))


def test_budget_dataclass_guards():
    with pytest.raises(ValueError):
        Budget(max_steps=0)
    with pytest.raises(ValueError):
        rt_api.create_run("   ")
