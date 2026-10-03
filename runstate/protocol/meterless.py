"""Meterless integration and runstate usage metering.

IMPORTANT — what Meterless actually is
---------------------------------------
Meterless (github.com/meterless/meterless, Apache-2.0) is a **local-first
context layer for AI agents**: hierarchical memory (H-MEM), world modeling,
reasoning compression (Markovian), intent detection. It is NOT a
usage-metering SaaS and it exposes **no usage-events ingest API** — this was
verified against their public repo (Oct 2026), which ships engine specs with
runnable references and conformance suites, not a hosted endpoint.

So this module does two honest things:

1. :class:`Meter` — runstate's own usage ledger. ``record(run_id, usage)``
   appends a :class:`UsageEvent` to an in-memory log; totals per run feed
   budgets and receipts. :class:`MockMeter` is the identical-behavior
   test double kept for interface compatibility. Selected with
   ``METERLESS_MODE`` (``mock`` default, ``live`` behaves the same — there
   is deliberately no fake external call).
2. :class:`MeterlessMemory` — a genuine Meterless integration: a Python
   implementation of their H-MEM three-tier memory pattern (short-term /
   working / long-term), following the engine spec in their repo. Agent runs
   use it as durable memory across steps. :class:`MockMeterlessMemory`
   seeds deterministic fixtures for tests.

:class:`MeteredProvider` is the middleware: it wraps a provider's
``complete()`` so every inference is metered into the run's ledger.
"""

from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

from runstate.runtime.budgets import cost_for_usage


# ------------------------------------------------------------ usage meter

@dataclass
class UsageEvent:
    run_id: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float = 0.0
    provider: str = ""
    model: str = ""
    ts: float = field(default_factory=time.time)


def _as_usage_dict(usage: Any) -> Dict[str, float]:
    if isinstance(usage, dict):
        src = usage
    else:
        src = {k: getattr(usage, k, 0) for k in
               ("prompt_tokens", "completion_tokens", "cost_usd")}
    return {
        "prompt_tokens": int(src.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(src.get("completion_tokens", 0) or 0),
        "cost_usd": float(src.get("cost_usd", 0.0) or 0.0),
    }


class Meter:
    """In-memory usage ledger for agent runs.

    ``record()`` never touches the network: Meterless exposes no
    usage-ingest API, so metering is runstate-internal by design.
    """

    def __init__(self) -> None:
        self._events: List[UsageEvent] = []

    def record(self, run_id: str, usage: Any,
               provider: str = "", model: str = "") -> UsageEvent:
        """Append one usage event for ``run_id``; return the event."""
        if not run_id:
            raise ValueError("run_id is required")
        u = _as_usage_dict(usage)
        event = UsageEvent(run_id=run_id, provider=provider, model=model, **u)
        self._events.append(event)
        return event

    def events_for(self, run_id: str) -> List[UsageEvent]:
        return [e for e in self._events if e.run_id == run_id]

    def totals(self, run_id: str) -> Dict[str, float]:
        events = self.events_for(run_id)
        return {
            "events": len(events),
            "prompt_tokens": sum(e.prompt_tokens for e in events),
            "completion_tokens": sum(e.completion_tokens for e in events),
            "cost_usd": sum(e.cost_usd for e in events),
        }

    def all_events(self) -> List[UsageEvent]:
        return list(self._events)

    def reset(self) -> None:
        self._events.clear()


class MockMeter(Meter):
    """Test double with the identical interface and behavior."""


def get_meter() -> Meter:
    """Return the configured meter.

    ``METERLESS_MODE=mock`` (default) -> :class:`MockMeter`.
    ``METERLESS_MODE=live`` -> :class:`Meter`. Both are in-memory by
    design (see module docstring); the switch exists so configuration,
    not code, changes when a real sink is ever added.
    """
    mode = os.environ.get("METERLESS_MODE", "mock").lower()
    if mode == "mock":
        return MockMeter()
    if mode == "live":
        return Meter()
    raise ValueError(f"unknown METERLESS_MODE: {mode!r} (want mock|live)")


class MeteredProvider:
    """Middleware: wraps a provider so every inference is metered.

    Usage::

        metered = MeteredProvider(provider, meter, provider_name="nebius")
        response = metered.complete(messages, run_id=run.id)
    """

    def __init__(self, provider: Any, meter: Meter,
                 provider_name: str = "", model: str = "") -> None:
        self._provider = provider
        self._meter = meter
        self._provider_name = provider_name or getattr(provider, "name", "")
        self._model = model

    def complete(self, messages: List[Dict[str, str]],
                 run_id: Optional[str] = None) -> Any:
        """Run one inference, meter it (real USD pricing), return the response."""
        response = self._provider.complete(messages)
        if run_id:
            prompt = int(getattr(response, "prompt_tokens", 0) or 0)
            completion = int(getattr(response, "completion_tokens", 0) or 0)
            model = str(getattr(response, "model", "") or "") or self._model
            try:
                cost = cost_for_usage(model, prompt, completion)
            except ValueError:
                cost = 0.0
            self._meter.record(
                run_id,
                {"prompt_tokens": prompt, "completion_tokens": completion,
                 "cost_usd": cost},
                provider=self._provider_name, model=model,
            )
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)


# ------------------------------------------------- Meterless H-MEM memory

@dataclass
class MemoryItem:
    key: str
    text: str
    tier: str
    ts: float = field(default_factory=time.time)
    score: float = 0.0


class MeterlessMemory:
    """Three-tier agent memory following the Meterless H-MEM engine pattern.

    Tiers: ``short_term`` (bounded recency buffer), ``working`` (active
    facts for the current run), ``long_term`` (consolidated knowledge).
    Implements the capture -> retrieve -> consolidate loop described in
    the Meterless H-MEM spec (github.com/meterless/meterless,
    engines/hmem, Apache-2.0). Retrieval is keyword-scored; production
    deployments can swap in the reference engine behind this interface.
    """

    TIERS = ("short_term", "working", "long_term")

    def __init__(self, short_term_size: int = 20) -> None:
        self._short: Deque[MemoryItem] = deque(maxlen=short_term_size)
        self._working: Dict[str, MemoryItem] = {}
        self._long: Dict[str, MemoryItem] = {}
        self._seq = 0

    def _next_key(self, tier: str) -> str:
        self._seq += 1
        return f"{tier}:{self._seq}"

    def remember(self, text: str, tier: str = "short_term",
                 key: Optional[str] = None) -> str:
        """Store ``text`` in ``tier``; return its key."""
        if tier not in self.TIERS:
            raise ValueError(f"unknown tier {tier!r}; want one of {self.TIERS}")
        if not text or not text.strip():
            raise ValueError("cannot remember empty text")
        item = MemoryItem(key=key or self._next_key(tier),
                          text=text.strip(), tier=tier)
        if tier == "short_term":
            self._short.append(item)
        elif tier == "working":
            self._working[item.key] = item
        else:
            self._long[item.key] = item
        return item.key

    def recall(self, query: str, limit: int = 5) -> List[MemoryItem]:
        """Keyword-scored retrieval across all tiers, best first."""
        tokens = [t for t in query.lower().split() if t]
        if not tokens:
            return []
        scored: List[MemoryItem] = []
        for item in list(self._short) + list(self._working.values()) + \
                list(self._long.values()):
            hay = item.text.lower()
            hits = sum(hay.count(t) for t in tokens)
            if hits:
                scored.append(MemoryItem(key=item.key, text=item.text,
                                         tier=item.tier, ts=item.ts,
                                         score=hits))
        scored.sort(key=lambda i: (-i.score, -i.ts))
        return scored[:max(0, limit)]

    def consolidate(self) -> int:
        """Promote working-tier items into long-term; return count moved."""
        moved = 0
        for key, item in list(self._working.items()):
            item.tier = "long_term"
            self._long[key] = item
            del self._working[key]
            moved += 1
        return moved

    def forget(self, key: str) -> bool:
        """Remove one item by key; return True if it existed."""
        for store in (self._working, self._long):
            if key in store:
                del store[key]
                return True
        for item in list(self._short):
            if item.key == key:
                self._short.remove(item)
                return True
        return False

    def snapshot(self) -> Dict[str, List[Dict[str, Any]]]:
        """Inspectable view of all tiers (for receipts/debugging)."""
        def dump(items: List[MemoryItem]) -> List[Dict[str, Any]]:
            return [{"key": i.key, "text": i.text, "ts": i.ts} for i in items]
        return {
            "short_term": dump(list(self._short)),
            "working": dump(list(self._working.values())),
            "long_term": dump(list(self._long.values())),
        }


class MockMeterlessMemory(MeterlessMemory):
    """Deterministic seeded memory for tests and demos."""

    def __init__(self) -> None:
        super().__init__()
        self.remember("Runstate bills per agent run, not per token.",
                      tier="long_term", key="long_term:seed-pricing")
        self.remember("The current run's objective is stored on the Run object.",
                      tier="working", key="working:seed-objective")


def get_memory() -> MeterlessMemory:
    """Return the configured memory.

    ``METERLESS_MODE=mock`` (default) -> seeded :class:`MockMeterlessMemory`.
    ``METERLESS_MODE=live`` -> empty :class:`MeterlessMemory`.
    """
    mode = os.environ.get("METERLESS_MODE", "mock").lower()
    if mode == "mock":
        return MockMeterlessMemory()
    if mode == "live":
        return MeterlessMemory()
    raise ValueError(f"unknown METERLESS_MODE: {mode!r} (want mock|live)")
