"""The one interface every baseline and every future ranker implements."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .catalog import Catalog, Tool
from .tokens import TokenCounter


@dataclass
class DecisionContext:
    """What the gateway knows at decision time.

    Split by *where it comes from*, because that is the integration cost. In-band
    fields the proxy sees for free; out-of-band fields require a ``ContextProvider``
    wired to the vendor's warehouse (Phase 7) and are ``None`` in the harness.

    ``task`` is only populated at decision points B and C — at session open
    (``tools/list``) the protocol gives us no prompt. See ``docs/decisions.md``.
    """

    # In-band — identity and session
    tenant_id: str | None = None
    seat_id: str | None = None
    session_id: str | None = None
    client_name: str | None = None
    scopes: tuple[str, ...] = ()

    # In-band — session state
    tools_called: tuple[tuple[str, str], ...] = ()
    errors_seen: int = 0
    turn_index: int = 0

    # Task grain — decision points B and C only
    task: str | None = None

    # Out-of-band — stubbed until a ContextProvider exists
    tenant_features: dict = field(default_factory=dict)
    seat_features: dict = field(default_factory=dict)


class Selector(Protocol):
    """Choose which tools to expose, subject to a token budget."""

    name: str

    def select(
        self,
        context: DecisionContext,
        catalog: Catalog,
        budget: int,
        counter: TokenCounter,
    ) -> list[Tool]:
        ...


def fill_budget(
    ranked: list[Tool],
    budget: int,
    counter: TokenCounter,
    pinned: list[Tool] | None = None,
) -> list[Tool]:
    """Greedily take tools in rank order until the token budget is exhausted.

    Skips over a tool that doesn't fit rather than stopping, so one oversized schema
    doesn't strand the remaining budget. This is the greedy relaxation of the knapsack
    — a real solver arrives in Phase 5 once scores are calibrated and the value term
    means something.

    ``pinned`` is the always-on core set. It is taken first and is *not* subject to
    the budget: a missing core tool is a task failure, and the ranker never gets to
    override it.
    """
    selected: list[Tool] = []
    seen: set[tuple[str, str]] = set()
    spent = 0

    for tool in pinned or []:
        if tool.key in seen:
            continue
        selected.append(tool)
        seen.add(tool.key)
        spent += counter.cost(tool)

    for tool in ranked:
        if tool.key in seen:
            continue
        cost = counter.cost(tool)
        if spent + cost > budget:
            continue
        selected.append(tool)
        seen.add(tool.key)
        spent += cost

    return selected
