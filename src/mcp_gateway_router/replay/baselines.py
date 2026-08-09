"""Point-A baselines: the floors a personalization engine has to clear.

None of these look at a prompt, because at `tools/list` there isn't one. They differ
only in how much of the *past* they use:

``random_selector``    no information at all. The null control — if a real engine cannot
                       separate from this, the harness is broken, not the world.
``global_frequency``   all history, unweighted. The true no-information-about-*this*-
                       session floor.
``recency_weighted``   history with exponential decay. Beating ``global_frequency``
                       means session modes persist — which is *persistence*, one of the
                       two axes personalization needs, and the one measurable at n=1.

A real engine (context-conditioned, learned) is expected to beat all three. That is the
bar, and these exist so the bar is a measurement rather than an assertion.
"""

from __future__ import annotations

import random
from collections import Counter
from typing import Sequence

from ..catalog import Catalog, Tool
from ..selector import DecisionContext, fill_budget
from ..tokens import TokenCounter
from .sessions import SessionRecord, ToolKey


class _Ranked:
    """Exposes tools in a fixed rank order, filling the budget."""

    def __init__(self, name: str, ranking: list[ToolKey]) -> None:
        self.name = name
        self._ranking = ranking

    def select(
        self,
        context: DecisionContext,
        catalog: Catalog,
        budget: int,
        counter: TokenCounter,
    ) -> list[Tool]:
        ranked = [catalog.get(*key) for key in self._ranking]
        return fill_budget([t for t in ranked if t is not None], budget, counter)


def global_frequency(history: Sequence[SessionRecord]):
    """Most-called tools across all prior sessions. The no-information floor."""
    counts: Counter[ToolKey] = Counter()
    for session in history:
        counts.update(session.called)
    return _Ranked("d-global", [key for key, _ in counts.most_common()])


def recency_weighted(half_life_sessions: float = 5.0):
    """Exponentially decayed call counts — recent sessions weigh more.

    Beating ``global_frequency`` is evidence that session modes *persist*. Losing to it
    means behaviour is memoryless at session granularity, which is bad news for point-A
    prediction for reasons that have nothing to do with having one user.
    """

    def factory(history: Sequence[SessionRecord]):
        weights: dict[ToolKey, float] = {}
        total = len(history)
        for index, session in enumerate(history):
            # age 0 is the most recent session.
            age = total - 1 - index
            weight = 0.5 ** (age / half_life_sessions)
            for key in session.called:
                weights[key] = weights.get(key, 0.0) + weight
        ranking = sorted(weights, key=lambda k: weights[k], reverse=True)
        return _Ranked(f"d-recent(hl={half_life_sessions:g})", ranking)

    return factory


def random_selector(seed: int = 0):
    """The null control. Deterministic per session count so runs are reproducible."""

    def factory(history: Sequence[SessionRecord]):
        class Random:
            name = "random"

            def select(self, context, catalog, budget, counter):
                tools = list(catalog)
                random.Random(seed + len(history)).shuffle(tools)
                return fill_budget(tools, budget, counter)

        return Random()

    return factory


def oracle(session: SessionRecord):
    """Upper bound: exactly what this session called. Not achievable — it reads the future.

    Takes the session rather than history, so it cannot be used as a ``SelectorFactory``
    by accident. Use ``oracle_factory`` in a replay.
    """
    return _Ranked("oracle", list(dict.fromkeys(session.called)))


def oracle_factory(sessions: Sequence[SessionRecord]):
    """Per-session oracle, for measuring the heterogeneity ceiling.

    ⚠️ **This reads the future** and exists only to bound what any real selector could
    recover. A *small* gap between this and ``global_frequency`` is decisive — no ranker
    beats the ceiling. A *large* gap is permission to keep going and nothing more; the
    oracle knows what was called, while a real engine sees only what preceded.
    """
    by_index = {index: s for index, s in enumerate(sessions)}

    def factory(history: Sequence[SessionRecord]):
        return oracle(by_index[len(history)])

    return factory
