"""Temporal replay: score a selector against sessions it could not have seen.

Walk the real session log in order. At each session open, build the selector from
*only* the sessions that preceded it, hand it that session's ambient context, and score
the set it chose against what the session went on to call.

Chronology is structural here, not a discipline someone has to remember: the selector
is constructed from a history slice, so it cannot peek at the future even by accident.

**Coverage is not task success.** It answers "would we have kept what you used", which
is the right question for a floor and the wrong one for a ceiling — sessions were
collected with the whole catalog available, so they may have used tools they would never
have reached for under a cut set. See the spec's Q2 section.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from ..catalog import Catalog, Tool
from ..selector import DecisionContext, Selector, fill_budget
from ..tokens import TokenCounter
from .sessions import SessionRecord, ToolKey

logger = logging.getLogger(__name__)

# Built fresh per session from the sessions that preceded it. Taking history at
# construction is what makes lookahead impossible rather than merely discouraged.
SelectorFactory = Callable[[Sequence[SessionRecord]], Selector]


@dataclass(frozen=True)
class SessionScore:
    session_id: str
    coverage: float
    n_called: int
    n_covered: int
    n_exposed: int


@dataclass(frozen=True)
class ReplayResult:
    selector_name: str
    budget_tokens: int
    per_session: tuple[SessionScore, ...]
    total_sessions: int
    scored_sessions: int
    failures: int

    @property
    def mean_coverage(self) -> float:
        """Mean over sessions that called at least one tool.

        Sessions with no calls are excluded rather than scored 1.0 — coverage of
        nothing is undefined, and counting it as perfect inflates every selector
        equally while hiding the ones that are actually working.
        """
        if not self.per_session:
            return 0.0
        return sum(s.coverage for s in self.per_session) / len(self.per_session)


def replay(
    sessions: Iterable[SessionRecord],
    factory: SelectorFactory,
    catalog: Catalog,
    budget_tokens: int,
    counter: TokenCounter,
) -> ReplayResult:
    ordered = sorted(sessions, key=lambda s: (s.opened_at, s.session_id))
    scores: list[SessionScore] = []
    failures = 0
    name = "unknown"

    for index, session in enumerate(ordered):
        history = ordered[:index]
        chosen: list[Tool] = []
        try:
            selector = factory(history)
            name = getattr(selector, "name", name)
            chosen = selector.select(
                _context_for(session), catalog, budget_tokens, counter
            )
        except Exception:
            logger.exception("selector failed on session %s", session.session_id)
            failures += 1
            chosen = []

        # Re-apply the budget rather than trusting the selector to have respected it.
        # An engine that over-exposes must not score as though it had obeyed.
        exposed = {t.key for t in fill_budget(list(chosen), budget_tokens, counter)}

        wanted: frozenset[ToolKey] = session.distinct_called
        if not wanted:
            continue

        covered = len(wanted & exposed)
        scores.append(
            SessionScore(
                session_id=session.session_id,
                coverage=covered / len(wanted),
                n_called=len(wanted),
                n_covered=covered,
                n_exposed=len(exposed),
            )
        )

    return ReplayResult(
        selector_name=name,
        budget_tokens=budget_tokens,
        per_session=tuple(scores),
        total_sessions=len(ordered),
        scored_sessions=len(scores),
        failures=failures,
    )


def _context_for(session: SessionRecord) -> DecisionContext:
    """What the gateway would have known at this session's `tools/list`.

    ``task`` stays ``None``. This is decision point A — no prompt exists yet, and a
    replay that leaked one would be measuring a different question entirely.
    """
    return DecisionContext(
        session_id=session.session_id,
        client_name="claude-code",
        environment=dict(session.environment),
    )


def sweep(
    sessions: Sequence[SessionRecord],
    factory: SelectorFactory,
    catalog: Catalog,
    budgets: Sequence[int],
    counter: TokenCounter,
) -> list[ReplayResult]:
    """Coverage across budgets. The result is a curve, never a single number."""
    return [replay(sessions, factory, catalog, b, counter) for b in budgets]
