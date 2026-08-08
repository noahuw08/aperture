"""BM25, the instruction wrapper, and the progressive-disclosure ``k`` sweep.

Each of these exists to stop a benchmark from being unable to lose, so each test asserts
the thing that would make it fail rather than the thing that makes it pass.
"""

import pytest

from mcp_gateway_router.baselines import (
    BM25Scorer,
    InstructionScorer,
    LexicalScorer,
    ProgressiveDisclosure,
)
from mcp_gateway_router.catalog import Catalog, Tool
from mcp_gateway_router.frontier import Example
from mcp_gateway_router.run_frontier import build_selectors
from mcp_gateway_router.tokens import StaticTokenCounter

SCHEMA = {"type": "object", "properties": {"q": {"type": "string"}}}


def tool(name, desc):
    return Tool(server_id="analytics", name=name, description=desc, input_schema=SCHEMA)


CATALOG = Catalog(
    [
        tool("search_events", "search product analytics events by name"),
        tool("create_chart", "create a chart from an event query"),
        tool("get_user", "fetch a user record by id"),
        tool("list_cohorts", "list saved cohorts in the workspace"),
        tool("run_experiment", "start an experiment on a feature flag"),
    ]
)
COUNTER = StaticTokenCounter({}, default=100)


# --- BM25 ---------------------------------------------------------------------


def test_bm25_ranks_the_lexically_matching_tool_first():
    scorer = BM25Scorer(CATALOG)
    ranked = sorted(CATALOG, key=lambda t: -scorer.score("start an experiment", t))
    assert ranked[0].name == "run_experiment"


def test_bm25_scores_zero_when_no_term_overlaps():
    scorer = BM25Scorer(CATALOG)
    assert scorer.score("kubernetes ingress", CATALOG.get("analytics", "get_user")) == 0.0


def test_bm25_rewards_rare_terms_over_common_ones():
    """IDF has to be doing work, or BM25 is just term counting."""
    catalog = Catalog(
        [tool(f"tool_{i}", "search events common common") for i in range(20)]
        + [tool("rare_tool", "search events zygote")]
    )
    scorer = BM25Scorer(catalog)
    rare = scorer.score("zygote", catalog.get("analytics", "rare_tool"))
    common = scorer.score("common", catalog.get("analytics", "tool_0"))
    assert rare > common


def test_bm25_saturates_term_frequency():
    """The k1 saturation is the difference between BM25 and raw TF-IDF."""
    catalog = Catalog(
        [
            tool("once", "alpha beta gamma delta"),
            tool("many", " ".join(["alpha"] * 50) + " beta gamma delta"),
        ]
    )
    scorer = BM25Scorer(catalog)
    once = scorer.score("alpha", catalog.get("analytics", "once"))
    many = scorer.score("alpha", catalog.get("analytics", "many"))
    # 50x the term frequency must not buy anything close to 50x the score.
    assert many < once * 3


# --- InstructionScorer --------------------------------------------------------


class RecordingScorer:
    def __init__(self):
        self.seen = []

    def score(self, task, tool):
        self.seen.append(task)
        return 0.0


def test_instruction_scorer_prepends_the_instruction():
    inner = RecordingScorer()
    wrapped = InstructionScorer(inner, {"chart signups": "Find analytics tools."})
    wrapped.score("chart signups", CATALOG.get("analytics", "create_chart"))
    assert inner.seen == ["Find analytics tools. chart signups"]


def test_instruction_scorer_passes_through_unknown_tasks_unchanged():
    """A task with no instruction must score identically to the unwrapped scorer,
    otherwise the sensitivity run is not comparable to the headline."""
    inner = RecordingScorer()
    wrapped = InstructionScorer(inner, {"other task": "Some instruction."})
    wrapped.score("chart signups", CATALOG.get("analytics", "create_chart"))
    assert inner.seen == ["chart signups"]


def test_instruction_scorer_changes_the_ranking_it_wraps():
    """If wrapping cannot change the result, the sensitivity check measures nothing."""
    plain = LexicalScorer(CATALOG)
    wrapped = InstructionScorer(plain, {"x": "cohorts saved workspace"})
    target = CATALOG.get("analytics", "list_cohorts")
    assert wrapped.score("x", target) > plain.score("x", target)


# --- progressive-disclosure k -------------------------------------------------


def test_pd_k_controls_how_many_schemas_recovery_returns():
    scorer = LexicalScorer(CATALOG)
    from mcp_gateway_router.selector import DecisionContext

    context = DecisionContext(task="search events")
    assert len(ProgressiveDisclosure(scorer, k=2).recover(context, CATALOG)) == 2
    assert len(ProgressiveDisclosure(scorer, k=4).recover(context, CATALOG)) == 4


def test_single_k_keeps_the_plain_selector_name():
    examples = [Example(task="search events", required=(("analytics", "search_events"),))]
    names = [s.name for s in build_selectors(CATALOG, examples, examples, 3, False)]
    assert "progressive-disclosure" in names


def test_sweeping_k_emits_one_distinctly_named_variant_per_value():
    examples = [Example(task="search events", required=(("analytics", "search_events"),))]
    names = [
        s.name
        for s in build_selectors(CATALOG, examples, examples, 3, False, pd_ks=[1, 5, 25])
    ]
    assert "progressive-disclosure(k=1)" in names
    assert "progressive-disclosure(k=5)" in names
    assert "progressive-disclosure(k=25)" in names
    assert len(set(names)) == len(names), "duplicate selector names collapse the sweep"


def test_instructions_reach_the_selectors_built_by_the_runner():
    examples = [
        Example(
            task="search events",
            required=(("analytics", "search_events"),),
            instruction="Find analytics tools.",
        )
    ]
    selectors = build_selectors(
        CATALOG, examples, examples, 3, False, instructions={"search events": "Find analytics tools."}
    )
    retrieval = next(s for s in selectors if s.name == "semantic-retrieval")
    assert isinstance(retrieval._scorer, InstructionScorer)


@pytest.mark.parametrize("k", [1, 10, 100])
def test_recovery_never_returns_more_than_the_catalog_holds(k):
    scorer = LexicalScorer(CATALOG)
    from mcp_gateway_router.selector import DecisionContext

    recovered = ProgressiveDisclosure(scorer, k=k).recover(
        DecisionContext(task="search events"), CATALOG
    )
    assert len(recovered) == min(k, len(CATALOG))
