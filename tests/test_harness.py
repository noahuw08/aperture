from mcp_gateway_router.baselines import (
    ExposeAll,
    LexicalScorer,
    Oracle,
    PopularityTopK,
    ProgressiveDisclosure,
    SemanticRetrieval,
    StaticSet,
)
from mcp_gateway_router.catalog import Catalog, Tool, schema_hash
from mcp_gateway_router.frontier import Example, evaluate, sweep, tokens_to_parity
from mcp_gateway_router.selector import DecisionContext, fill_budget
from mcp_gateway_router.tokens import StaticTokenCounter

SCHEMA = {"type": "object", "properties": {"q": {"type": "string"}}}


def tool(name, desc="", server="analytics", schema=None):
    return Tool(server_id=server, name=name, description=desc or name, input_schema=schema or SCHEMA)


CATALOG = Catalog(
    [
        tool("search_events", "search product analytics events by name"),
        tool("create_chart", "create a chart from an event query"),
        tool("get_user", "fetch a user record by id"),
        tool("get_user_profile", "fetch extended user profile attributes"),
        tool("run_experiment", "start an experiment on a feature flag"),
        tool("list_cohorts", "list saved cohorts in the workspace"),
    ]
)

COUNTER = StaticTokenCounter(
    {
        "search_events": 100,
        "create_chart": 200,
        "get_user": 50,
        "get_user_profile": 50,
        "run_experiment": 400,
        "list_cohorts": 100,
    }
)

EXAMPLES = [
    Example("search events for signup", (("analytics", "search_events"),)),
    Example(
        "chart the signup events",
        (("analytics", "search_events"), ("analytics", "create_chart")),
    ),
    Example("fetch the user profile", (("analytics", "get_user_profile"),)),
]


# --- catalog identity -------------------------------------------------------


def test_schema_hash_is_order_independent():
    a = {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "int"}}}
    b = {"properties": {"b": {"type": "int"}, "a": {"type": "string"}}, "type": "object"}
    assert schema_hash(a) == schema_hash(b)


def test_uid_changes_when_schema_changes():
    before = tool("x", schema={"type": "object", "properties": {}})
    after = tool("x", schema={"type": "object", "properties": {"new": {"type": "string"}}})
    assert before.key == after.key
    assert before.uid != after.uid


def test_catalog_rejects_duplicate_keys():
    try:
        Catalog([tool("dupe"), tool("dupe")])
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_same_name_different_servers_coexist():
    catalog = Catalog([tool("search", server="a"), tool("search", server="b")])
    assert len(catalog) == 2
    assert len(catalog.by_name("search")) == 2


# --- budget filling ---------------------------------------------------------


def test_fill_budget_skips_oversized_rather_than_stopping():
    ranked = [
        CATALOG.get("analytics", "run_experiment"),  # 400 — will not fit
        CATALOG.get("analytics", "get_user"),  # 50
    ]
    selected = fill_budget(ranked, budget=100, counter=COUNTER)
    assert [t.name for t in selected] == ["get_user"]


def test_pinned_core_is_exempt_from_budget():
    pinned = [CATALOG.get("analytics", "run_experiment")]  # 400 > budget
    selected = fill_budget([], budget=10, counter=COUNTER, pinned=pinned)
    assert [t.name for t in selected] == ["run_experiment"]


# --- baselines --------------------------------------------------------------


def test_expose_all_ignores_budget():
    exposed = ExposeAll().select(DecisionContext(), CATALOG, budget=1, counter=COUNTER)
    assert len(exposed) == len(CATALOG)


def test_popularity_ranks_by_call_volume():
    counts = {("analytics", "get_user"): 10, ("analytics", "search_events"): 5}
    exposed = PopularityTopK(counts).select(DecisionContext(), CATALOG, 150, COUNTER)
    assert [t.name for t in exposed] == ["get_user", "search_events"]


def test_semantic_retrieval_ranks_by_task_match():
    selector = SemanticRetrieval(LexicalScorer(CATALOG))
    ctx = DecisionContext(task="create a chart")
    exposed = selector.select(ctx, CATALOG, budget=200, counter=COUNTER)
    assert exposed[0].name == "create_chart"


def test_semantic_retrieval_without_task_is_stable():
    """At tools/list there is no prompt — the protocol decides when we decide."""
    selector = SemanticRetrieval(LexicalScorer(CATALOG))
    first = selector.select(DecisionContext(), CATALOG, 300, COUNTER)
    second = selector.select(DecisionContext(), CATALOG, 300, COUNTER)
    assert [t.name for t in first] == [t.name for t in second]


def test_progressive_disclosure_always_exposes_meta_tool():
    exposed = ProgressiveDisclosure(LexicalScorer(CATALOG)).select(DecisionContext(), CATALOG, 0, COUNTER)
    assert [t.name for t in exposed] == ["find_tools"]


def test_oracle_exposes_exactly_what_was_required():
    required = {e.task: list(e.required) for e in EXAMPLES}
    selector = Oracle(required)
    exposed = selector.select(EXAMPLES[1].decision_context(), CATALOG, 1000, COUNTER)
    assert {t.name for t in exposed} == {"search_events", "create_chart"}


# --- frontier ---------------------------------------------------------------


def test_oracle_is_fully_satisfied_at_sufficient_budget():
    required = {e.task: list(e.required) for e in EXAMPLES}
    point = evaluate(Oracle(required), EXAMPLES, CATALOG, 1000, COUNTER)
    assert point.satisfied == 1.0
    assert point.miss_rate == 0.0


def test_progressive_disclosure_recovers_when_search_finds_the_tool():
    # k=10 exceeds the toy catalog, so find_tools always returns everything.
    pd = ProgressiveDisclosure(LexicalScorer(CATALOG), k=10)
    point = evaluate(pd, EXAMPLES, CATALOG, 0, COUNTER)
    assert point.satisfied == 1.0
    assert point.recovered == 1.0
    assert point.mean_round_trips == 1.0


def test_progressive_disclosure_fails_when_search_misses():
    """The point of the fix: recovery is no longer guaranteed.

    At k=1 find_tools returns a single tool, so the two-tool example cannot be
    satisfied however good the retriever is. Before this, PD scored 1.0 at every
    budget and could not lose.
    """
    pd = ProgressiveDisclosure(LexicalScorer(CATALOG), k=1)
    point = evaluate(pd, EXAMPLES, CATALOG, 0, COUNTER)
    assert point.satisfied < 1.0
    assert point.mean_round_trips == 1.0  # the search still ran, and still cost tokens


def test_progressive_disclosure_pays_for_every_retrieved_schema():
    """You pay for what the search returns, not just what you needed."""
    one = evaluate(ProgressiveDisclosure(LexicalScorer(CATALOG), k=1), EXAMPLES, CATALOG, 0, COUNTER)
    three = evaluate(ProgressiveDisclosure(LexicalScorer(CATALOG), k=3), EXAMPLES, CATALOG, 0, COUNTER)
    assert three.mean_tokens > one.mean_tokens


def test_static_set_misses_what_it_did_not_pick():
    selector = StaticSet([("analytics", "get_user")])
    point = evaluate(selector, EXAMPLES, CATALOG, 1000, COUNTER)
    assert point.satisfied == 0.0
    assert point.recall == 0.0


def test_expose_all_costs_the_most_tokens():
    points = {
        p.selector: p
        for p in sweep(
            [ExposeAll(), StaticSet([("analytics", "get_user")])],
            EXAMPLES,
            CATALOG,
            [1000],
            COUNTER,
        )
    }
    assert points["expose-all"].mean_tokens > points["static-set"].mean_tokens


def test_entropy_is_zero_when_one_tool_is_always_exposed():
    point = evaluate(StaticSet([("analytics", "get_user")]), EXAMPLES, CATALOG, 1000, COUNTER)
    assert point.exposure_entropy == 0.0


def test_tokens_to_parity_finds_smallest_passing_budget():
    # The hardest example needs search_events (100) + create_chart (200) = 300,
    # so 200 is not enough and 400 is the first budget that satisfies every example.
    required = {e.task: list(e.required) for e in EXAMPLES}
    points = sweep([Oracle(required)], EXAMPLES, CATALOG, [50, 100, 200, 400], COUNTER)
    assert tokens_to_parity(points, "oracle", target=1.0) == 400


def test_tokens_to_parity_returns_none_when_never_reached():
    points = sweep([StaticSet([])], EXAMPLES, CATALOG, [100, 200], COUNTER)
    assert tokens_to_parity(points, "static-set", target=1.0) is None
