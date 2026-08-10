"""Selectors the gateway can be configured to run.

The gateway builds its selector before it has a catalog — `tools/list` is what produces
the catalog — so anything needing the catalog at construction has to be built lazily, on
the first `select()` call, where the catalog is finally a parameter.

Failure here is never fatal. A selector that cannot be constructed (missing extra, model
download, no disk) degrades to a stable ordering rather than taking down `tools/list`;
the run is then visibly a different arm rather than an outage.
"""

from __future__ import annotations

import logging

from ..catalog import Catalog, Tool
from ..selector import DecisionContext, fill_budget
from ..tokens import TokenCounter

logger = logging.getLogger(__name__)


class StableOrder:
    """Alphabetical, budget-filled. The degradation target, and a usable control."""

    name = "stable-order"

    def select(
        self,
        context: DecisionContext,
        catalog: Catalog,
        budget: int,
        counter: TokenCounter,
    ) -> list[Tool]:
        return fill_budget(sorted(catalog, key=lambda t: (t.server_id, t.name)), budget, counter)


class SemanticSelector:
    """Arm C — rank by similarity between the task text and each tool's description.

    **This is commodity retrieval, not the personalization engine.** That is the point:
    Gate 0 asks whether *any* pre-selection beats the client's own tool search when the
    task is known. If off-the-shelf retrieval cannot, then predicting without the task —
    strictly less information — will not either, and the engine is not worth building yet.

    ``task`` arrives via the harness (see ``gateway/server.py``), because at ``tools/list``
    the protocol supplies no prompt. With no task this degrades to a stable order, which
    is the honest behaviour at decision point A rather than a silent failure.
    """

    name = "semantic"

    def __init__(self, model_name: str | None = None) -> None:
        self._model_name = model_name
        self._scorer = None
        self._inner = None
        self._broken = False

    def _ensure(self, catalog: Catalog):
        """Build on first use — the catalog does not exist until tools/list has run."""
        if self._inner is not None or self._broken:
            return
        try:
            from ..baselines import SemanticRetrieval
            from ..embedding import DEFAULT_MODEL, EmbeddingScorer

            self._scorer = EmbeddingScorer(catalog, model_name=self._model_name or DEFAULT_MODEL)
            self._inner = SemanticRetrieval(self._scorer)
        except Exception:
            logger.exception("semantic selector unavailable; degrading to stable order")
            self._broken = True

    def select(
        self,
        context: DecisionContext,
        catalog: Catalog,
        budget: int,
        counter: TokenCounter,
    ) -> list[Tool]:
        self._ensure(catalog)
        if self._inner is None:
            return StableOrder().select(context, catalog, budget, counter)
        return self._inner.select(context, catalog, budget, counter)


def build_selector(name: str, pinned: tuple[tuple[str, str], ...]):
    """Map a config string to a selector.

    Unknown names raise rather than silently falling back — an arm that quietly ran a
    different selector than its config claims is worse than a failed run.
    """
    if name == "semantic":
        return SemanticSelector()
    if name == "stable-order":
        return StableOrder()
    if name == "static-set":
        from ..baselines import StaticSet

        return StaticSet(pinned)
    raise ValueError(f"unknown selector {name!r}")
