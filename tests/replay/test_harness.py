from datetime import datetime, timedelta, timezone

from mcp_gateway_router.catalog import Catalog, Tool
from mcp_gateway_router.replay.harness import replay
from mcp_gateway_router.replay.sessions import SessionRecord
from mcp_gateway_router.tokens import StaticTokenCounter

BASE = datetime(2026, 8, 9, 9, 0, tzinfo=timezone.utc)


def _catalog(n=6):
    return Catalog(
        [
            Tool(server_id="s", name=f"t{i}", description=f"tool {i}", input_schema={})
            for i in range(n)
        ]
    )


def _session(i, called, environment=None):
    return SessionRecord(
        session_id=f"s{i}",
        opened_at=BASE + timedelta(hours=i),
        environment=environment or {},
        called=tuple(("s", name) for name in called),
    )


def _fixed(names):
    """A factory that always chooses the same tools, ignoring history."""

    def factory(history):
        class Fixed:
            name = "fixed"

            def select(self, context, catalog, budget, counter):
                return [catalog.get("s", n) for n in names if catalog.get("s", n)]

        return Fixed()

    return factory


def test_coverage_is_the_fraction_of_called_tools_that_were_exposed():
    sessions = [_session(0, ["t0", "t1", "t2", "t3"])]

    result = replay(sessions, _fixed(["t0", "t1"]), _catalog(), 10_000, StaticTokenCounter({}))

    assert result.per_session[0].coverage == 0.5


def test_a_session_with_no_calls_is_excluded_from_the_mean():
    """Coverage of nothing is undefined, not 1.0 — averaging it in inflates the score."""
    sessions = [_session(0, []), _session(1, ["t0", "t1"])]

    result = replay(sessions, _fixed(["t0"]), _catalog(), 10_000, StaticTokenCounter({}))

    assert result.scored_sessions == 1
    assert result.total_sessions == 2
    assert result.mean_coverage == 0.5


def test_repeated_calls_do_not_inflate_coverage():
    """Coverage is over distinct tools; ten calls to one tool is still one tool."""
    sessions = [_session(0, ["t0", "t0", "t0", "t1"])]

    result = replay(sessions, _fixed(["t0"]), _catalog(), 10_000, StaticTokenCounter({}))

    assert result.per_session[0].coverage == 0.5


def test_the_selector_only_ever_sees_earlier_sessions():
    """The whole point of replay: a selector that peeks at the future is worthless."""
    seen: list[int] = []

    def factory(history):
        seen.append(len(history))

        class Empty:
            name = "empty"

            def select(self, context, catalog, budget, counter):
                return []

        return Empty()

    sessions = [_session(i, ["t0"]) for i in range(4)]
    replay(sessions, factory, _catalog(), 10_000, StaticTokenCounter({}))

    assert seen == [0, 1, 2, 3]


def test_the_selector_receives_the_session_environment_as_context():
    captured: list[dict] = []

    def factory(history):
        class Capturing:
            name = "capturing"

            def select(self, context, catalog, budget, counter):
                captured.append(dict(context.environment))
                return []

        return Capturing()

    sessions = [_session(0, ["t0"], {"project": "alpha", "hour": "09"})]
    replay(sessions, factory, _catalog(), 10_000, StaticTokenCounter({}))

    assert captured == [{"project": "alpha", "hour": "09"}]


def test_the_context_carries_no_task_because_this_is_decision_point_a():
    captured = []

    def factory(history):
        class Capturing:
            name = "capturing"

            def select(self, context, catalog, budget, counter):
                captured.append(context)
                return []

        return Capturing()

    replay([_session(0, ["t0"])], factory, _catalog(), 10_000, StaticTokenCounter({}))

    assert captured[0].task is None


def test_a_selector_that_raises_scores_zero_rather_than_aborting_the_run():
    def factory(history):
        class Exploding:
            name = "exploding"

            def select(self, context, catalog, budget, counter):
                raise RuntimeError("engine bug")

        return Exploding()

    result = replay([_session(0, ["t0"])], factory, _catalog(), 10_000, StaticTokenCounter({}))

    assert result.mean_coverage == 0.0
    assert result.failures == 1


def test_tools_called_but_absent_from_the_catalog_count_against_coverage():
    """A tool the catalog no longer carries could not have been exposed."""
    sessions = [_session(0, ["t0", "gone"])]

    result = replay(sessions, _fixed(["t0"]), _catalog(), 10_000, StaticTokenCounter({}))

    assert result.per_session[0].coverage == 0.5


def test_the_budget_is_enforced_by_the_harness_not_trusted_from_the_selector():
    """An engine that ignores the budget must not score as if it obeyed it."""
    sessions = [_session(0, ["t0", "t1", "t2", "t3"])]
    counter = StaticTokenCounter({}, default=100)

    result = replay(sessions, _fixed(["t0", "t1", "t2", "t3"]), _catalog(), 250, counter)

    # 250 tokens at 100 each admits two tools, so at most two can be covered.
    assert result.per_session[0].coverage == 0.5
