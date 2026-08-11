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

    # In-band — ambient signals available at session open, before any prompt exists.
    # This is the *entire* feature vector at decision point A: project, repo, branch,
    # hour, weekday. Populated by the gateway; empty in the offline harness.
    environment: dict[str, str] = field(default_factory=dict)

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


def _by_density(
    ranked: list[Tool],
    counter: TokenCounter,
    scores: dict[tuple[str, str], float],
) -> list[Tool]:
    """Order by value per token, keeping the incoming rank as the tie-break.

    Rankers shortlist, so a tool can arrive with no score; it is worth 0 rather than a
    ``KeyError``. A free tool has infinite density rather than an undefined one. Ties
    hold the incoming order — which is what makes density reduce exactly to rank order
    under a flat cost, so this is a strict generalisation of the old behaviour.
    """

    def density(tool: Tool) -> float:
        cost = counter.cost(tool)
        if cost <= 0:
            return float("inf")
        return scores.get(tool.key, 0.0) / cost

    return sorted(ranked, key=density, reverse=True)


def fill_budget(
    ranked: list[Tool],
    budget: int,
    counter: TokenCounter,
    pinned: list[Tool] | None = None,
    scores: dict[tuple[str, str], float] | None = None,
) -> list[Tool]:
    """Greedily fill the token budget, skipping tools that don't fit.

    Skips over a tool that doesn't fit rather than stopping, so one oversized schema
    doesn't strand the remaining budget. This is the greedy relaxation of the knapsack
    — a real solver arrives in Phase 5.

    Two orders, and which one you get depends on whether you pass ``scores``:

    * **rank-greedy** (``scores=None``) — take ``ranked`` in the order given. Correct
      only when cost is flat, since with no scores there is no value term to divide.
    * **density-greedy** (``scores`` given) — reorder by ``value / cost``, the `p·v/c`
      of ``algorithm.md`` [4]. On the real catalog schema sizes span 39×, so the two
      orders diverge and the difference is worth real coverage.

    Rank-greedy stays the default because most callers genuinely have no value term:
    a ranker that sorts by similarity or by call count is throwing its scale away at
    the boundary, and inventing one from rank position would be a fabricated value.
    ``scores`` is keyed by ``tool.key`` and any tool missing from it is worth 0.

    ⚠️ Density is only *meaningful* on calibrated scores — see ``algorithm.md`` [3].
    Empirical call frequency is a probability and qualifies; a cosine similarity is
    monotone but not calibrated, and dividing it by tokens mixes units. Passing
    ``scores`` from an uncalibrated ranker will change the answer without improving it.

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

    if scores is not None:
        ranked = _by_density(ranked, counter, scores)

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
