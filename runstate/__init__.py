"""runstate: stateful agent orchestration runtime.

The primitive is a budgeted, resumable, receipted agent run.

Public surface:
    runstate.cli / runstate.runner   CLI and agent-loop driver
    runstate.runtime                 runs, budgets, ledger, providers
    runstate.protocol                MCP protocol server + sponsor adapters
"""

from .runtime import (
    Budget,
    BudgetExhausted,
    ChatResult,
    OpenAICompatibleProvider,
    Provider,
    ProviderError,
    ProviderNotConfigured,
    Run,
    RunNotFound,
    Usage,
    append_step,
    cancel_run,
    check_budget,
    cost_for_usage,
    create_run,
    get_provider,
    get_run,
    get_steps,
    list_runs,
    load_run,
    save_run,
)

__all__ = [
    "Run",
    "Budget",
    "Usage",
    "create_run",
    "get_run",
    "cancel_run",
    "list_runs",
    "save_run",
    "load_run",
    "append_step",
    "get_steps",
    "RunNotFound",
    "Provider",
    "OpenAICompatibleProvider",
    "ChatResult",
    "get_provider",
    "ProviderError",
    "ProviderNotConfigured",
    "check_budget",
    "BudgetExhausted",
    "cost_for_usage",
]

__version__ = "0.1.0"
