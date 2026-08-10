"""Aggregation and comparison logic, tested without touching the model."""

from mcp_gateway_router.bench.matrix import (
    ArmSummary,
    Cell,
    compare,
    minimum_detectable_effect,
)
from mcp_gateway_router.bench.runner import ArmResult


def _run(passed, *, prefix=1000, cost=0.05, turns=3, searches=1):
    return ArmResult(
        arm="a", task_id="t", ok=True, answer="x", turns=turns, cost_usd=cost,
        input_tokens=0, cache_creation_tokens=prefix, cache_read_tokens=0,
        output_tokens=0, tool_search_calls=searches, passed=passed,
    )


def _cell(arm, task_id, results):
    return Cell(arm=arm, task_id=task_id, runs=[_run(p) for p in results])


def test_repetitions_make_success_continuous_not_binary():
    """Two of three passing is 0.667, not 'passed' — that is what buys resolution."""
    cell = _cell("A", "t1", [True, True, False])

    assert cell.success_rate == 2 / 3


def test_a_cell_with_no_runs_scores_zero_rather_than_dividing_by_zero():
    assert Cell(arm="A", task_id="t1").success_rate == 0.0


def test_arm_success_averages_over_tasks_not_over_runs():
    """Otherwise a task that happened to run more often would weigh more."""
    arm = ArmSummary("A", [_cell("A", "t1", [True]), _cell("A", "t2", [False, False, False, False])])

    assert arm.success == 0.5


def test_comparison_is_paired_over_shared_tasks():
    left = ArmSummary("C", [_cell("C", "t1", [True]), _cell("C", "t2", [True])])
    right = ArmSummary("A", [_cell("A", "t1", [False]), _cell("A", "t2", [True])])

    result = compare(left, right)

    assert result.n_tasks == 2
    assert result.delta == 0.5


def test_tasks_only_one_arm_ran_are_excluded_from_the_pairing():
    left = ArmSummary("C", [_cell("C", "t1", [True]), _cell("C", "only-c", [True])])
    right = ArmSummary("A", [_cell("A", "t1", [False])])

    result = compare(left, right)

    assert result.n_tasks == 1


def test_a_difference_below_the_resolution_is_flagged_unreadable():
    """Noisy per-task differences mean a small delta cannot be distinguished."""
    left = ArmSummary("C", [
        _cell("C", "t1", [True, True, True]),
        _cell("C", "t2", [False, False, False]),
        _cell("C", "t3", [True, False, False]),
    ])
    right = ArmSummary("A", [
        _cell("A", "t1", [False, False, False]),
        _cell("A", "t2", [True, True, True]),
        _cell("A", "t3", [True, False, True]),
    ])

    result = compare(left, right)

    assert not result.readable
    assert "BELOW resolution" in result.describe()


def test_a_consistent_difference_is_readable():
    left = ArmSummary("C", [_cell("C", f"t{i}", [True, True, True]) for i in range(6)])
    right = ArmSummary("A", [_cell("A", f"t{i}", [False, False, False]) for i in range(6)])

    result = compare(left, right)

    assert result.readable
    assert result.delta == 1.0


def test_one_task_cannot_resolve_anything():
    """A single paired observation has no spread, so no claim is supportable."""
    assert minimum_detectable_effect([_cell("C", "t1", [True])], [_cell("A", "t1", [False])]) == 1.0


def test_cost_and_prefix_are_reported_alongside_success():
    """Success alone is meaningless — expose-all wins it."""
    cell = Cell(arm="A", task_id="t1", runs=[_run(True, prefix=1000, cost=0.1, turns=4, searches=2)])
    arm = ArmSummary("A", [cell])

    assert arm.prefix_tokens == 1000
    assert arm.cost_usd == 0.1
    assert arm.turns == 4
    assert arm.tool_searches == 2
