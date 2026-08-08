"""The frontier runner.

Effectiveness is a curve, not a scalar: token budget on X, task success on Y. Every
single metric here is gameable on its own — expose everything and miss rate is zero;
expose nothing and cost is zero. Only the joint movement is a claim.

Two headline numbers fall out of the curve:

* **tokens-to-parity** — the budget at which a selector matches the static set's
  success rate.
* **lift-at-parity-budget** — success at the static set's budget.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from .catalog import Catalog, Tool
from .selector import DecisionContext, Selector
from .tokens import TokenCounter, total_cost


@dataclass(frozen=True)
class Example:
    """One evaluation item: a task, and the tools it actually needed.

    ``required`` is hand-authored ground truth. It is what makes exact policy value
    computable without an estimator — and it is also why this harness validates the
    machinery, not the value.
    """

    task: str
    required: tuple[tuple[str, str], ...]
    context: DecisionContext | None = None
    instruction: str = ""
    """ToolRet's per-query retrieval instruction.

    A **benchmark artifact, not a production signal** — a gateway sees the user's prompt,
    never a hand-written statement of what to retrieve. It is carried so calibration can
    reproduce the published *w/ inst* protocol; the frontier deliberately ignores it, and
    that asymmetry is the point rather than an oversight.
    """

    @property
    def retrieval_text(self) -> str:
        """Query as the published benchmark scores it: instruction prepended."""
        return f"{self.instruction} {self.task}".strip() if self.instruction else self.task

    def decision_context(self) -> DecisionContext:
        if self.context is not None:
            return self.context
        return DecisionContext(task=self.task)


@dataclass(frozen=True)
class Point:
    selector: str
    budget: int
    satisfied: float
    """Fraction of examples where every required tool was exposed."""
    recovered: float
    """Fraction satisfied only via a recovery round trip (progressive disclosure)."""
    recall: float
    """Mean fraction of required tools exposed."""
    mean_tokens: float
    mean_tools: float
    mean_round_trips: float
    """Extra retrieval round trips per example. Non-zero only for progressive disclosure."""
    exposure_entropy: float
    """Shannon entropy over which tools got exposed. Popularity collapse detector."""

    @property
    def miss_rate(self) -> float:
        return 1.0 - self.satisfied


def evaluate(
    selector: Selector,
    examples: list[Example],
    catalog: Catalog,
    budget: int,
    counter: TokenCounter,
) -> Point:
    satisfied = 0
    recovered = 0
    round_trips = 0
    recall_sum = 0.0
    token_sum = 0
    tool_sum = 0
    exposures: Counter = Counter()
    recover = getattr(selector, "recover", None)

    for example in examples:
        exposed = selector.select(example.decision_context(), catalog, budget, counter)
        exposed_keys = {t.key for t in exposed}
        exposures.update(exposed_keys)

        spent = total_cost(counter, exposed)
        tool_sum += len(exposed)

        required = set(example.required)
        if required:
            hit = len(required & exposed_keys)
            recall_sum += hit / len(required)
            complete = hit == len(required)
        else:
            recall_sum += 1.0
            complete = True

        if complete:
            satisfied += 1
        elif recover is not None:
            # The agent calls find_tools. That costs a round trip and the tokens of
            # every schema the search returns — whether or not the right one is in
            # there. Recovery succeeds only if the retriever actually found the
            # missing tools; a search that misses leaves the task failed, having
            # spent the tokens anyway.
            retrieved = recover(example.decision_context(), catalog)
            spent += total_cost(counter, retrieved)
            round_trips += 1
            if required <= exposed_keys | {t.key for t in retrieved}:
                satisfied += 1
                recovered += 1

        token_sum += spent

    n = max(len(examples), 1)
    return Point(
        selector=selector.name,
        budget=budget,
        satisfied=satisfied / n,
        recovered=recovered / n,
        recall=recall_sum / n,
        mean_tokens=token_sum / n,
        mean_tools=tool_sum / n,
        mean_round_trips=round_trips / n,
        exposure_entropy=_entropy(exposures),
    )


def sweep(
    selectors: list[Selector],
    examples: list[Example],
    catalog: Catalog,
    budgets: list[int],
    counter: TokenCounter,
) -> list[Point]:
    return [
        evaluate(selector, examples, catalog, budget, counter)
        for selector in selectors
        for budget in budgets
    ]


def tokens_to_parity(points: list[Point], selector: str, target: float) -> int | None:
    """Smallest budget at which ``selector`` reaches ``target`` satisfied rate."""
    candidates = [
        p.budget for p in points if p.selector == selector and p.satisfied >= target
    ]
    return min(candidates) if candidates else None


def _entropy(exposures: Counter) -> float:
    total = sum(exposures.values())
    if total == 0:
        return 0.0
    return -sum(
        (count / total) * math.log2(count / total) for count in exposures.values()
    )
