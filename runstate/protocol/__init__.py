"""runstate.protocol: MCP protocol server plus sponsor integrations.

Exposes stateful, budgeted agent runs over the Model Context Protocol and
integrates the two required sponsor technologies:

- Zetaris (federated data layer): :mod:`runstate.protocol.zetaris`
- Meterless (local-first context layer): :mod:`runstate.protocol.meterless`

Depends on the runtime contract in :mod:`runstate.runtime`.
"""

_LAZY = {
    "create_server": ".server",
    "main": ".server",
    "ZetarisSource": ".zetaris",
    "MockZetarisSource": ".zetaris",
    "get_zetaris_source": ".zetaris",
    "ZetarisError": ".zetaris",
    "Meter": ".meterless",
    "MockMeter": ".meterless",
    "MeteredProvider": ".meterless",
    "MeterlessMemory": ".meterless",
    "MockMeterlessMemory": ".meterless",
    "UsageEvent": ".meterless",
    "get_meter": ".meterless",
    "get_memory": ".meterless",
}


def __getattr__(name: str):  # PEP 562: lazy so `python -m` has no double import
    if name in _LAZY:
        import importlib

        module = importlib.import_module(_LAZY[name], __name__)
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "create_server",
    "main",
    "ZetarisSource",
    "MockZetarisSource",
    "get_zetaris_source",
    "ZetarisError",
    "Meter",
    "MockMeter",
    "MeteredProvider",
    "MeterlessMemory",
    "MockMeterlessMemory",
    "UsageEvent",
    "get_meter",
    "get_memory",
]
