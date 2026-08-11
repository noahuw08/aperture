"""Battery sizing math, tested without touching the model."""

import math

import pytest

from mcp_gateway_router.bench.matrix import ArmSummary, Cell
from mcp_gateway_router.bench.power import VarianceEstimate, decompose
from mcp_gateway_router.bench.runner import ArmResult


def _cell(task_id: str, passes: int, reps: int) -> Cell:
    """A cell whose success rate is exactly passes/reps."""
    runs = []
    for i in range(reps):
        result = ArmResult(
            arm="a", task_id=task_id, ok=True, answer="x", turns=1, cost_usd=0.0,
            input_tokens=0, cache_creation_tokens=0, cache_read_tokens=0,
            output_tokens=0, tool_search_calls=0,
        )
        result.passed = i < passes
        runs.append(result)
    return Cell(arm="a", task_id=task_id, runs=runs)


def _summary(name: str, rates: dict[str, tuple[int, int]]) -> ArmSummary:
    return ArmSummary(
        arm=name,
        cells=[_cell(tid, passes, reps) for tid, (passes, reps) in rates.items()],
    )


def test_decompose_splits_between_and_within():
    reps = 4
    # Both arms at 2/4 on every task: no between-task spread, maximal within-task noise.
    left = _summary("L", {"t1": (2, reps), "t2": (2, reps), "t3": (2, reps)})
    right = _summary("R", {"t1": (2, reps), "t2": (2, reps), "t3": (2, reps)})

    est = decompose(left, right, reps)

    # p=0.5 on both sides -> w = .5*.5 + .5*.5 = 0.5
    assert est.within == pytest.approx(0.5)
    # Observed diffs are all zero, so between clamps to 0.
    assert est.between == 0.0
    assert est.pilot_tasks == 3
    assert est.pilot_reps == reps
    assert est.saturated_tasks == 0
    assert est.trustworthy is True


def test_between_survives_when_tasks_genuinely_differ():
    reps = 10
    # Task-level effects differ hugely: +1.0, 0.0, -1.0. Cells are saturated, so
    # within is zero and the whole observed spread must land in `between`.
    left = _summary("L", {"t1": (10, reps), "t2": (0, reps), "t3": (0, reps)})
    right = _summary("R", {"t1": (0, reps), "t2": (0, reps), "t3": (10, reps)})

    est = decompose(left, right, reps)

    assert est.within == 0.0
    assert est.between == pytest.approx(1.0)  # sample variance of [1, 0, -1]
    assert est.saturated_tasks == 3
    assert est.trustworthy is False  # every task saturated


def test_negative_variance_is_clamped_not_returned():
    reps = 2
    # Identical arms at p=0.5: observed variance 0, within/reps = 0.25 -> negative.
    left = _summary("L", {"t1": (1, reps), "t2": (1, reps)})
    right = _summary("R", {"t1": (1, reps), "t2": (1, reps)})

    est = decompose(left, right, reps)

    assert est.between == 0.0


def test_fewer_than_two_shared_tasks_refuses_to_estimate():
    left = _summary("L", {"t1": (1, 2)})
    right = _summary("R", {"t1": (1, 2)})

    est = decompose(left, right, 2)

    assert est.pilot_tasks == 1
    assert est.trustworthy is False


def test_floor_tasks_is_the_asymptote():
    est = VarianceEstimate(
        between=0.0324, within=0.16, pilot_tasks=6, pilot_reps=5, saturated_tasks=0
    )

    # 2.8^2 * 0.0324 / 0.15^2 = 11.29 -> 12
    assert est.floor_tasks(0.15) == 12
