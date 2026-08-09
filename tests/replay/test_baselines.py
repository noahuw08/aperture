from datetime import datetime, timedelta, timezone

from mcp_gateway_router.catalog import Catalog, Tool
from mcp_gateway_router.replay.baselines import (
    global_frequency,
    oracle_factory,
    random_selector,
    recency_weighted,
)
from mcp_gateway_router.replay.harness import replay
from mcp_gateway_router.replay.sessions import SessionRecord
from mcp_gateway_router.tokens import StaticTokenCounter

BASE = datetime(2026, 8, 9, 9, 0, tzinfo=timezone.utc)
COUNTER = StaticTokenCounter({}, default=100)


def _catalog(n=8):
    return Catalog(
        [Tool(server_id="s", name=f"t{i}", description="d", input_schema={}) for i in range(n)]
    )


def _session(i, called):
    return SessionRecord(
        session_id=f"s{i}",
        opened_at=BASE + timedelta(hours=i),
        called=tuple(("s", name) for name in called),
    )


def test_global_frequency_ranks_by_all_time_calls():
    # t0 dominates history; the final session calls it too.
    sessions = [_session(i, ["t0"]) for i in range(5)] + [_session(5, ["t0"])]

    result = replay(sessions, global_frequency, _catalog(), 150, COUNTER)

    # Session 0 has no history so cannot cover; the rest should.
    assert result.per_session[-1].coverage == 1.0


def test_recency_beats_global_when_behaviour_changes():
    """Old habit t0 for many sessions, then a switch to t1 — recency should adapt."""
    sessions = [_session(i, ["t0"]) for i in range(10)]
    sessions += [_session(10 + i, ["t1"]) for i in range(4)]

    budget = 100  # exactly one tool
    glob = replay(sessions, global_frequency, _catalog(), budget, COUNTER)
    recent = replay(sessions, recency_weighted(half_life_sessions=2.0), _catalog(), budget, COUNTER)

    assert recent.mean_coverage > glob.mean_coverage


def test_the_null_control_is_poor_on_a_concentrated_workload():
    sessions = [_session(i, ["t0"]) for i in range(12)]

    rand = replay(sessions, random_selector(seed=1), _catalog(), 100, COUNTER)
    glob = replay(sessions, global_frequency, _catalog(), 100, COUNTER)

    assert glob.mean_coverage > rand.mean_coverage


def test_the_oracle_covers_everything_within_budget():
    sessions = [_session(i, ["t0", "t1"]) for i in range(3)]

    result = replay(sessions, oracle_factory(sessions), _catalog(), 10_000, COUNTER)

    assert result.mean_coverage == 1.0


def test_the_oracle_is_still_bounded_by_the_budget():
    sessions = [_session(0, ["t0", "t1", "t2", "t3"])]

    result = replay(sessions, oracle_factory(sessions), _catalog(), 200, COUNTER)

    assert result.mean_coverage == 0.5


def test_baselines_survive_an_empty_history():
    """Session zero has nothing to learn from; it must score, not crash."""
    sessions = [_session(0, ["t0"])]

    for factory in (global_frequency, recency_weighted(), random_selector()):
        result = replay(sessions, factory, _catalog(), 100, COUNTER)
        assert result.failures == 0
