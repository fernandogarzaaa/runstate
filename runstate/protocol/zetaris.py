"""Zetaris adapter: federated data layer for runstate agent runs.

Real client
------------
:class:`ZetarisSource` talks to the Zetaris REST API documented in the
Zetaris knowledge base ("Accessing Zetaris through Rest API") and in the
``rh-ai-quickstart/ai-taxi-anomaly-detector`` integration notes:

- ``POST {endpoint}/api/v1.0/query/sql/start`` with JSON body
  ``{"select": "<SQL>", "pageLimit": N}`` (N in 1..100). Returns the first
  page plus a ``queryToken``.
- ``GET {endpoint}/api/v1.0/query/sql/page`` with ``queryToken`` and
  ``pageNumber`` for subsequent pages.
- ``DELETE {endpoint}/api/v1.0/query/sql/close/{queryToken}`` to release
  server-side resources.
- Headers ``X-Request-ID`` (UUID) and ``X-Org-ID`` on every request.

Documented assumptions (no live credentials exist yet to verify against):
  - The API key travels in the ``X-API-Key`` header (overridable with
    ``ZETARIS_API_KEY_HEADER``). The 401-handling in Zetaris's own notebook
    examples keys off ``ZETARIS_API_KEY``/``ZETARIS_API_URL`` env names,
    which this module adopts.
  - The ``/start`` response is parsed defensively: rows are read from the
    first of ``rows`` / ``data`` / ``results`` / ``records`` present, and the
    token from ``queryToken``. Columnar payloads (``{"columns": [...],
    "rows": [[...]]}``) are zipped into dicts.

Mock
----
:class:`MockZetarisSource` implements the identical ``query()`` interface
against a deterministic in-memory fixture dataset with a simple
case-insensitive keyword filter, so tests and demos run with no network.
Selected with ``ZETARIS_MODE=mock`` (the default) via :func:`get_zetaris_source`.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List, Optional

import httpx


class ZetarisError(Exception):
    """Raised for transport-level or API-level Zetaris failures."""


def _first_present(payload: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return None


class ZetarisSource:
    """Query layer over a Zetaris deployment's SQL REST API."""

    def __init__(
        self,
        endpoint: Optional[str] = None,
        api_key: Optional[str] = None,
        org_id: Optional[str] = None,
        api_key_header: Optional[str] = None,
        page_limit: int = 50,
        timeout: float = 30.0,
    ) -> None:
        self.endpoint = (endpoint or os.environ.get("ZETARIS_ENDPOINT", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("ZETARIS_API_KEY", "")
        self.org_id = org_id or os.environ.get("ZETARIS_ORG_ID", "")
        self.api_key_header = (
            api_key_header or os.environ.get("ZETARIS_API_KEY_HEADER", "X-API-Key")
        )
        if not self.endpoint:
            raise ZetarisError("Zetaris endpoint not configured (ZETARIS_ENDPOINT)")
        if not self.api_key:
            raise ZetarisError("Zetaris API key not configured (ZETARIS_API_KEY)")
        self.page_limit = max(1, min(page_limit, 100))
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        headers = {
            "X-Request-ID": str(uuid.uuid4()),
            "Content-Type": "application/json",
            self.api_key_header: self.api_key,
        }
        if self.org_id:
            headers["X-Org-ID"] = self.org_id
        return headers

    def _extract_rows(self, payload: Any) -> List[Dict[str, Any]]:
        """Normalize the many shapes a Zetaris page can take into rows."""
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = _first_present(payload, "rows", "data", "results", "records") or []
            columns = payload.get("columns")
            if columns and rows and isinstance(rows[0], (list, tuple)):
                return [dict(zip(columns, r)) for r in rows]
        else:
            rows = []
        return [dict(r) if isinstance(r, dict) else {"value": r} for r in rows]

    def query(self, query: str) -> List[Dict[str, Any]]:
        """Run a SQL query against Zetaris; return all result rows as dicts."""
        if not query or not query.strip():
            raise ZetarisError("query must be a non-empty SQL string")
        rows: List[Dict[str, Any]] = []
        token: Optional[str] = None
        try:
            with httpx.Client(timeout=self.timeout) as client:
                start = client.post(
                    f"{self.endpoint}/api/v1.0/query/sql/start",
                    headers=self._headers(),
                    json={"select": query, "pageLimit": self.page_limit},
                )
                if start.status_code == 401:
                    raise ZetarisError("Zetaris rejected the API key (HTTP 401)")
                start.raise_for_status()
                body = start.json()
                token = _first_present(body, "queryToken", "query_token")
                rows.extend(self._extract_rows(body))

                page = 1
                # Paginate only while a full page keeps coming back.
                while token and len(rows) and len(rows) % self.page_limit == 0:
                    page += 1
                    resp = client.get(
                        f"{self.endpoint}/api/v1.0/query/sql/page",
                        headers=self._headers(),
                        params={"queryToken": token, "pageNumber": page},
                    )
                    resp.raise_for_status()
                    page_rows = self._extract_rows(resp.json())
                    if not page_rows:
                        break
                    rows.extend(page_rows)
                    if len(page_rows) < self.page_limit:
                        break
        except httpx.HTTPError as exc:
            raise ZetarisError(f"Zetaris request failed: {exc}") from exc
        finally:
            if token:
                try:
                    with httpx.Client(timeout=self.timeout) as client:
                        client.delete(
                            f"{self.endpoint}/api/v1.0/query/sql/close/{token}",
                            headers=self._headers(),
                        )
                except httpx.HTTPError:
                    pass  # best-effort cleanup; query already succeeded/failed
        return rows


# ------------------------------------------------------------------ mock

_FIXTURES: List[Dict[str, Any]] = [
    {"provider": "nebius", "model": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
     "input_usd_per_1m": 0.06, "output_usd_per_1m": 0.24, "region": "eu-north1"},
    {"provider": "nebius", "model": "meta-llama/Llama-3.3-70B-Instruct",
     "input_usd_per_1m": 0.35, "output_usd_per_1m": 1.05, "region": "eu-north1"},
    {"provider": "openrouter", "model": "anthropic/claude-sonnet-4",
     "input_usd_per_1m": 3.0, "output_usd_per_1m": 15.0, "region": "global"},
    {"dataset": "nyc_taxi", "table": "trips", "rows": 1_742_088,
     "source": "zetaris-fabric", "freshness": "2026-09-30"},
    {"dataset": "sec_filings", "table": "filings_10k", "rows": 48_211,
     "source": "zetaris-fabric", "freshness": "2026-10-01"},
    {"run_id": "run-demo-1", "status": "success", "steps": 6,
     "prompt_tokens": 1240, "completion_tokens": 388},
]


class MockZetarisSource:
    """Deterministic in-memory stand-in with the same ``query()`` interface.

    Applies a case-insensitive keyword filter over fixture rows: every
    whitespace-separated token in the query must appear somewhere in the
    row's stringified values. A query matching nothing returns [].
    """

    def __init__(self, fixtures: Optional[List[Dict[str, Any]]] = None) -> None:
        self._fixtures = fixtures if fixtures is not None else list(_FIXTURES)

    def query(self, query: str) -> List[Dict[str, Any]]:
        if not query or not query.strip():
            return []
        tokens = query.lower().split()
        out = []
        for row in self._fixtures:
            haystack = " ".join(str(v) for v in row.values()).lower()
            if all(tok in haystack for tok in tokens):
                out.append(dict(row))
        return out


def get_zetaris_source() -> Any:
    """Return the configured Zetaris source.

    ``ZETARIS_MODE=mock`` (default) -> :class:`MockZetarisSource`.
    ``ZETARIS_MODE=live`` -> :class:`ZetarisSource` (needs ZETARIS_ENDPOINT
    and ZETARIS_API_KEY).
    """
    mode = os.environ.get("ZETARIS_MODE", "mock").lower()
    if mode == "live":
        return ZetarisSource()
    if mode != "mock":
        raise ZetarisError(f"unknown ZETARIS_MODE: {mode!r} (want mock|live)")
    return MockZetarisSource()
