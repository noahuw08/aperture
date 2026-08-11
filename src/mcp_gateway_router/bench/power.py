"""Size the Gate 0 battery: how many tasks, at how many repetitions each.

``matrix.minimum_detectable_effect`` reports what a completed run could see. This
module inverts it — what a run must *be*, to see the margin we care about. Same
statistic, solved for a different unknown.

The refinement over a plain inversion is that sigma is not one thing. A cell's
success rate is a mean over ``r`` repetitions, so the per-task paired difference has
variance ``b^2 + w/r``: between-task variation that repetitions never touch, plus
within-task noise that they divide. Sizing therefore returns a ``(tasks, reps)``
frontier, and ``b^2`` alone fixes a floor no repetition count can beat.

Authoring tasks is the expensive half of this benchmark and model runs are cheap, so
that frontier is worth having rather than a single task count.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .matrix import ArmSummary, Cell
from .runner import ArmResult

#: Two-sided, ~80% power. Same constant as ``matrix.minimum_detectable_effect``; the
#: two must agree or the sizing and the readout are calibrated differently.
Z = 2.8


@dataclass(frozen=True)
class VarianceEstimate:
    """The two noise components, estimated from a pilot run."""

    between: float
    """``b^2`` — task-to-task variation in the effect. Clamped at 0."""

    within: float
    """``w`` — variance of a *single-run* difference, ``p(1-p) + p(1-p)``."""

    pilot_tasks: int
    pilot_reps: int

    saturated_tasks: int
    """Tasks where both arms sat at 0 or 1. These say nothing about ``w``."""

    @property
    def trustworthy(self) -> bool:
        """False when pilot_tasks < 2, or more than half the tasks are saturated."""
        return self.pilot_tasks >= 2 and self.saturated_tasks * 2 <= self.pilot_tasks

    def floor_tasks(self, margin: float) -> int:
        """Tasks needed at infinite repetitions — the asymptote of ``n(r)``.

        The most decision-relevant number here: if this exceeds what can be authored,
        no repetition count rescues the margin and the margin itself has to move.
        """
        if margin <= 0:
            raise ValueError("margin must be positive")
        return math.ceil(Z**2 * self.between / margin**2)


def decompose(left: ArmSummary, right: ArmSummary, reps: int) -> VarianceEstimate:
    """Split the observed per-task spread into between-task and within-task parts.

    Method of moments: the observed variance of per-task differences already contains
    ``w/reps``, so subtracting an estimate of that leaves ``b^2``.
    """
    if reps < 1:
        raise ValueError("reps must be at least 1")

    by_task = {c.task_id: c for c in right.cells}
    pairs = [(c, by_task[c.task_id]) for c in left.cells if c.task_id in by_task]

    per_task_within = [
        l.success_rate * (1.0 - l.success_rate) + r.success_rate * (1.0 - r.success_rate)
        for l, r in pairs
    ]
    saturated = sum(1 for w in per_task_within if w == 0.0)

    if len(pairs) < 2:
        # Mirrors minimum_detectable_effect's n < 2 guard: no spread is estimable, so
        # report the shape of the pilot and let `trustworthy` refuse it.
        return VarianceEstimate(
            between=0.0,
            within=statistics.fmean(per_task_within) if per_task_within else 0.0,
            pilot_tasks=len(pairs),
            pilot_reps=reps,
            saturated_tasks=saturated,
        )

    diffs = [l.success_rate - r.success_rate for l, r in pairs]
    within = statistics.fmean(per_task_within)
    observed = statistics.variance(diffs)

    # Negative means between-task variation is indistinguishable from zero at this
    # pilot size. That is a real answer; a negative variance is not.
    between = max(0.0, observed - within / reps)

    return VarianceEstimate(
        between=between,
        within=within,
        pilot_tasks=len(pairs),
        pilot_reps=reps,
        saturated_tasks=saturated,
    )


@dataclass(frozen=True)
class SizingRow:
    """One point on the (tasks, reps) frontier, all reaching the same margin."""

    reps: int
    tasks: int
    runs_per_arm: int
    total_runs: int
    est_cost_usd: float


def size_battery(
    est: VarianceEstimate,
    margin: float = 0.15,
    reps: Sequence[int] = (3, 5, 8, 12, 20),
    arms: int = 2,
    cost_per_run: float = 0.07,
) -> list[SizingRow]:
    """Tasks required at each repetition count, all reaching ``margin``.

    Every row resolves the same difference. They differ only in how the work is
    split between authoring tasks (expensive, human) and running repetitions
    (cheap, mechanical) — which is the trade this benchmark actually faces.
    """
    if margin <= 0:
        raise ValueError("margin must be positive")

    rows: list[SizingRow] = []
    for r in reps:
        if r < 1:
            raise ValueError("reps must be at least 1")
        needed = Z**2 * (est.between + est.within / r) / margin**2
        # Two is the floor: a paired comparison over one task has no spread to read.
        tasks = max(2, math.ceil(needed))
        runs_per_arm = tasks * r
        total_runs = runs_per_arm * arms
        rows.append(
            SizingRow(
                reps=r,
                tasks=tasks,
                runs_per_arm=runs_per_arm,
                total_runs=total_runs,
                est_cost_usd=total_runs * cost_per_run,
            )
        )
    return rows


def _cell(arm: str, task_id: str, passes: int, reps: int) -> Cell:
    """Rebuild a Cell from a pass count.

    Only ``passed`` is recoverable from a pilot file and only ``passed`` is read by
    ``success_rate``; the rest of ArmResult is filled with zeros rather than invented.
    """
    runs = []
    for i in range(reps):
        result = ArmResult(
            arm=arm, task_id=task_id, ok=True, answer=None, turns=0, cost_usd=0.0,
            input_tokens=0, cache_creation_tokens=0, cache_read_tokens=0,
            output_tokens=0, tool_search_calls=0,
        )
        result.passed = i < passes
        runs.append(result)
    return Cell(arm=arm, task_id=task_id, runs=runs)


def load_pilot(path: Path) -> tuple[ArmSummary, ArmSummary, int]:
    """Read a pilot file written by the arms walkthrough.

    Rejects the pre-2026-08-11 rate-only format outright. Assuming a repetition count
    for it would produce a confident number from data that cannot support one.
    """
    payload = json.loads(Path(path).read_text())
    reps = payload.get("reps")
    if not isinstance(reps, int) or reps < 1:
        raise ValueError(
            f"{path} has no usable 'reps' — it is probably the older rate-only "
            "format, which cannot support sizing. Re-run the pilot."
        )

    arms = payload.get("arms") or {}
    if len(arms) < 2:
        raise ValueError(f"{path} needs at least two arms to compare, found {len(arms)}")

    summaries = [
        ArmSummary(
            arm=name,
            cells=[_cell(name, c["task_id"], int(c["passes"]), reps) for c in body["cells"]],
        )
        for name, body in list(arms.items())[:2]
    ]
    return summaries[0], summaries[1], reps


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="source", type=Path, required=True,
                        help="pilot results JSON")
    parser.add_argument("--margin", type=float, default=0.15)
    parser.add_argument("--arms", type=int, default=2)
    parser.add_argument("--reps", type=int, nargs="+", default=[3, 5, 8, 12, 20])
    args = parser.parse_args()

    try:
        left, right, reps = load_pilot(args.source)
    except ValueError as exc:
        # A stale or malformed pilot file is ordinary operator error, not a bug.
        # A traceback here would bury the one line that says what to do about it.
        raise SystemExit(str(exc))

    est = decompose(left, right, reps)

    print(f"pilot: {est.pilot_tasks} tasks x {est.pilot_reps} reps "
          f"({left.arm} vs {right.arm})")
    print(f"  between-task b^2 = {est.between:.4f}")
    print(f"  within-task  w   = {est.within:.4f}")
    print(f"  saturated tasks  = {est.saturated_tasks}")
    if not est.trustworthy:
        print("\n⚠️  estimate is NOT trustworthy — too few tasks, or most are")
        print("   saturated at 0/1. Add middling-difficulty pilot tasks before")
        print("   sizing anything off these numbers.")

    print(f"\nfloor: {est.floor_tasks(args.margin)} tasks — no repetition count "
          f"reaches {args.margin:.0%} below this\n")
    print(f"{'reps':>6}{'tasks':>8}{'runs/arm':>10}{'total':>8}{'est $':>9}")
    print("-" * 41)
    for row in size_battery(est, margin=args.margin, reps=args.reps, arms=args.arms):
        print(f"{row.reps:>6}{row.tasks:>8}{row.runs_per_arm:>10}"
              f"{row.total_runs:>8}{row.est_cost_usd:>9.2f}")


if __name__ == "__main__":
    main()
