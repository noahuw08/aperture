"""The baseline ladder.

Six selectors, all implementing the same interface, in the order they matter:

1. ``ExposeAll``           — recall ceiling, cost floor. Top of the frontier.
2. ``StaticSet``           — the status quo. Must beat it; it's the easy one.
3. ``PopularityTopK``      — the popularity counter. Strong early, degrades. Beat it *over time*.
4. ``SemanticRetrieval``   — Kong's roadmap, Bedrock AgentCore, the papers. **Existential.**
5. ``ProgressiveDisclosure`` — ``tool_search`` + a minimal core. **Existential.**
6. ``Oracle``              — ground truth. The regret denominator.

Baselines 4 and 5 are the two open blockers from the scoping doc. Making them lines
on a chart is how the argument becomes a measurement.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Iterable, Protocol

from .catalog import Catalog, Tool
from .selector import DecisionContext, fill_budget
from .tokens import TokenCounter

_WORD = re.compile(r"[a-z0-9]+")


def _terms(text: str) -> list[str]:
    return _WORD.findall(text.lower())


class RelevanceScorer(Protocol):
    """Scores a tool's relevance to the current task. Higher is better."""

    def score(self, task: str, tool: Tool) -> float: ...


class LexicalScorer:
    """TF-IDF cosine over tool name + description.

    ⚠️ **Placeholder, not the real baseline 4.** A bi-encoder is what Kong, Bedrock
    AgentCore, and the published results actually use, and lexical matching will
    *understate* it — which biases the kill gate in our favour. Swap in a real
    embedder before reading anything into the Phase 0 result.
    """

    def __init__(self, catalog: Catalog) -> None:
        docs = {t.key: _terms(f"{t.name} {t.description}") for t in catalog}
        n = max(len(docs), 1)
        df = Counter()
        for terms in docs.values():
            df.update(set(terms))
        self._idf = {term: math.log(n / (1 + count)) + 1.0 for term, count in df.items()}
        self._vectors = {key: self._vector(terms) for key, terms in docs.items()}
        self._query_cache: dict[str, dict[str, float]] = {}

    def _vector(self, terms: list[str]) -> dict[str, float]:
        if not terms:
            return {}
        tf = Counter(terms)
        vec = {term: count * self._idf.get(term, 1.0) for term, count in tf.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {term: v / norm for term, v in vec.items()}

    def score(self, task: str, tool: Tool) -> float:
        query = self._query_cache.get(task)
        if query is None:
            query = self._vector(_terms(task))
            self._query_cache[task] = query
        doc = self._vectors.get(tool.key, {})
        return sum(weight * doc.get(term, 0.0) for term, weight in query.items())


class BM25Scorer:
    """Okapi BM25 over tool name + description.

    The published leaderboard's floor is BM25 (nDCG@10 = 36.04 on the *All* tab), which
    makes this the one calibration point needing no model download and no GPU — and the
    most diagnostic one available. Our BM25 landing near the published BM25 validates the
    loader, the gold-label extraction and the metric *independently of any embedding*; a
    neural retriever scoring below it indicts the setup rather than the model.

    Document text matches ``EmbeddingScorer._doc_text`` so both score the same corpus.
    """

    def __init__(self, catalog: Catalog, k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        docs = {t.key: _terms(f"{t.name}\n{t.description}") for t in catalog}
        n = max(len(docs), 1)
        self._freqs = {key: Counter(terms) for key, terms in docs.items()}
        self._lengths = {key: len(terms) for key, terms in docs.items()}
        self._avgdl = (sum(self._lengths.values()) / n) or 1.0
        df: Counter = Counter()
        for terms in docs.values():
            df.update(set(terms))
        self._idf = {
            term: math.log(1.0 + (n - count + 0.5) / (count + 0.5))
            for term, count in df.items()
        }
        self._query_cache: dict[str, list[str]] = {}

    def score(self, task: str, tool: Tool) -> float:
        terms = self._query_cache.get(task)
        if terms is None:
            terms = _terms(task)
            self._query_cache[task] = terms
        freqs = self._freqs.get(tool.key)
        if not freqs:
            return 0.0
        # Repeated query terms are summed rather than de-duplicated, matching the
        # reference `rank_bm25` implementation the published numbers come from.
        norm = self._k1 * (1.0 - self._b + self._b * self._lengths[tool.key] / self._avgdl)
        total = 0.0
        for term in terms:
            tf = freqs.get(term, 0)
            if tf:
                total += self._idf.get(term, 0.0) * (tf * (self._k1 + 1.0)) / (tf + norm)
        return total


class InstructionScorer:
    """Prepends ToolRet's per-query instruction before delegating to ``inner``.

    A **benchmark artifact**, not a production signal — see ``Example.instruction``. The
    frontier deliberately scores query-only, because a gateway sees the user's prompt and
    never a hand-written statement of what to retrieve.

    This wrapper exists to answer the sensitivity question that asymmetry raises: *how
    much of baseline 4's score is the setting rather than the retriever?* Without it the
    query-only number is unattributable — we cannot tell a weak competitor from a
    hobbled one, which is the same failure that put our own retriever below BM25. Report
    it **alongside** the query-only run, never instead of it.
    """

    def __init__(self, inner: RelevanceScorer, instructions: dict[str, str]) -> None:
        self._inner = inner
        self._instructions = instructions

    def score(self, task: str, tool: Tool) -> float:
        instruction = self._instructions.get(task, "")
        query = f"{instruction} {task}".strip() if instruction else task
        return self._inner.score(query, tool)


class ExposeAll:
    name = "expose-all"

    def select(self, context, catalog, budget, counter):
        return list(catalog)


class StaticSet:
    """A hand-picked set, identical for every caller. The status quo."""

    name = "static-set"

    def __init__(self, keys: Iterable[tuple[str, str]]) -> None:
        self._keys = list(keys)

    def select(self, context, catalog, budget, counter):
        ranked = [catalog.get(*k) for k in self._keys]
        return fill_budget([t for t in ranked if t is not None], budget, counter)


class PopularityTopK:
    """Rank by historical call volume.

    The degenerate equilibrium in its purest form: un-exposed tools generate no
    calls, so nothing new ever enters the set. Looks excellent in a snapshot.
    """

    name = "popularity"

    def __init__(self, call_counts: dict[tuple[str, str], int]) -> None:
        self._counts = call_counts
        self._ranked: list[Tool] | None = None

    def select(self, context, catalog, budget, counter):
        # The ranking depends on neither task nor budget — compute it once.
        if self._ranked is None:
            self._ranked = sorted(catalog, key=lambda t: (-self._counts.get(t.key, 0), t.name))
        return fill_budget(self._ranked, budget, counter)


class SemanticRetrieval:
    """Rank by prompt-to-description similarity. No personalization at all."""

    name = "semantic-retrieval"

    def __init__(self, scorer: RelevanceScorer, shortlist: int = 200) -> None:
        self._scorer = scorer
        self._shortlist = shortlist
        self._ranked: dict[str | None, list[Tool]] = {}

    def _rank(self, task: str | None, catalog: Catalog) -> list[Tool]:
        """Rank once per task. The order is budget-independent; the sweep is not."""
        if task in self._ranked:
            return self._ranked[task]
        if not task:
            # No task at session open — degrades to arbitrary-but-stable order.
            ranked = sorted(catalog, key=lambda t: t.name)
        else:
            scored = sorted(catalog, key=lambda t: (-self._scorer.score(task, t), t.name))
            # Nothing below the shortlist can fit a realistic budget anyway.
            ranked = scored[: self._shortlist]
        self._ranked[task] = ranked
        return ranked

    def select(self, context, catalog, budget, counter):
        return fill_budget(self._rank(context.task, catalog), budget, counter)


class ProgressiveDisclosure:
    """A minimal pinned core plus a ``find_tools`` meta-tool.

    A miss is recoverable — but only if ``find_tools`` actually finds the tool, and only
    at the cost of the retrieved schemas plus a round trip. Modelling recovery as free
    and always-successful makes this baseline unfalsifiable: it wins by construction,
    which is exactly what it did in the first run (100% at every budget, measured to be
    overstated by 70 points).

    So ``recover`` runs the *same retriever baseline 4 gets*. Giving progressive
    disclosure an oracle while its competitor gets a real retriever is not a comparison.
    """

    name = "progressive-disclosure"

    META_TOOL = Tool(
        server_id="_gateway",
        name="find_tools",
        description=(
            "Search the full tool catalog by natural-language description of the task. "
            "Returns matching tool definitions that can then be called directly."
        ),
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )

    def __init__(
        self,
        scorer: RelevanceScorer,
        core_keys: Iterable[tuple[str, str]] = (),
        k: int = 10,
        name: str | None = None,
    ) -> None:
        self._scorer = scorer
        self._core_keys = list(core_keys)
        self._k = k
        self._cache: dict[str, list[Tool]] = {}
        # ``k`` is how many schemas find_tools returns, and it is the single knob setting
        # how strong this baseline is: small k is cheap but misses, large k recovers more
        # but pays for every schema returned. Leaving it at an arbitrary default makes
        # this a benchmark that cannot lose in whichever direction the default happens to
        # favour — so the sweep names each variant and reports them separately.
        if name is not None:
            self.name = name

    def select(self, context, catalog, budget, counter):
        core = [catalog.get(*k) for k in self._core_keys]
        pinned = [self.META_TOOL] + [t for t in core if t is not None]
        return fill_budget([], budget, counter, pinned=pinned)

    def recover(self, context, catalog) -> list[Tool]:
        """What a ``find_tools`` call would return for this task.

        The full set lands in context on the next turn — the agent pays for every schema
        the search returns, not just the one it needed. Progressive disclosure defers
        cost; it does not avoid it.
        """
        task = context.task
        if not task:
            return []
        if task not in self._cache:
            ranked = sorted(catalog, key=lambda t: (-self._scorer.score(task, t), t.name))
            self._cache[task] = ranked[: self._k]
        return self._cache[task]


class Oracle:
    """Exposes exactly the tools the task required. Known only offline."""

    name = "oracle"

    def __init__(self, required: dict[str, list[tuple[str, str]]]) -> None:
        self._required = required

    def select(self, context, catalog, budget, counter):
        keys = self._required.get(context.task or "", [])
        tools = [catalog.get(*k) for k in keys]
        return fill_budget([t for t in tools if t is not None], budget, counter)
