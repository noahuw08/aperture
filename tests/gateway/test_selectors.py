import pytest

from mcp_gateway_router.catalog import Catalog, Tool
from mcp_gateway_router.gateway.selectors import (
    SemanticSelector,
    StableOrder,
    build_selector,
)
from mcp_gateway_router.selector import DecisionContext
from mcp_gateway_router.tokens import StaticTokenCounter

COUNTER = StaticTokenCounter({}, default=100)


def _catalog():
    return Catalog(
        [
            Tool(server_id="github", name="search_code", description="search code", input_schema={}),
            Tool(server_id="github", name="list_prs", description="list pull requests", input_schema={}),
            Tool(server_id="notion", name="search", description="search pages", input_schema={}),
        ]
    )


def test_build_selector_rejects_unknown_names():
    """A run that silently used a different selector than its config claims is worse
    than a failed run."""
    with pytest.raises(ValueError, match="carrier-pigeon"):
        build_selector("carrier-pigeon", ())


def test_build_selector_returns_each_known_kind():
    assert build_selector("stable-order", ()).name == "stable-order"
    assert build_selector("semantic", ()).name == "semantic"
    assert build_selector("static-set", ()).name == "static-set"


def test_stable_order_respects_the_budget():
    chosen = StableOrder().select(DecisionContext(), _catalog(), 250, COUNTER)

    assert len(chosen) == 2


def test_the_semantic_selector_degrades_rather_than_raising(monkeypatch):
    """A missing extra or a failed model download must not take down tools/list."""
    selector = SemanticSelector()

    import mcp_gateway_router.embedding as embedding

    def explode(*a, **k):
        raise RuntimeError("no sentence-transformers here")

    monkeypatch.setattr(embedding, "EmbeddingScorer", explode)

    chosen = selector.select(DecisionContext(task="find prs"), _catalog(), 250, COUNTER)

    assert len(chosen) == 2  # stable order, budget respected


def test_a_broken_semantic_selector_is_only_diagnosed_once(monkeypatch):
    """Retrying a failed model load on every tools/list would be a latency trap."""
    import mcp_gateway_router.embedding as embedding

    calls = []

    def explode(*a, **k):
        calls.append(1)
        raise RuntimeError("nope")

    monkeypatch.setattr(embedding, "EmbeddingScorer", explode)

    selector = SemanticSelector()
    for _ in range(3):
        selector.select(DecisionContext(task="x"), _catalog(), 250, COUNTER)

    assert len(calls) == 1


def test_the_semantic_selector_ranks_by_task_similarity():
    """With a stub scorer, the task should drive the order — that is arm C's whole claim."""
    selector = SemanticSelector()

    class Stub:
        def score(self, task, tool):
            return 1.0 if "pull" in tool.description else 0.0

    from mcp_gateway_router.baselines import SemanticRetrieval

    selector._inner = SemanticRetrieval(Stub())

    chosen = selector.select(DecisionContext(task="which PRs are open"), _catalog(), 100, COUNTER)

    assert [t.name for t in chosen] == ["list_prs"]
