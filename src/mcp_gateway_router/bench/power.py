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

import math
import statistics
from dataclasses import dataclass

from .matrix import ArmSummary

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
