"""Test setup for the assembled runstate product.

- The single ``runstate`` package is first on sys.path.
- The SQLite ledger is isolated per test via RUNSTATE_DB_PATH; tests never
  touch ~/.runstate.
- The protocol server's in-process meter is reset per test.
"""

import os
import sys

PRODUCT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PRODUCT)

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_product(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNSTATE_DB_PATH", str(tmp_path / "ledger.db"))
    from runstate.protocol import server  # noqa: E402

    server._meter.reset()
    yield
