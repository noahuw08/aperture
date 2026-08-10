"""Run arms × tasks × repetitions and aggregate into a Gate 0 reading.

Three things this deliberately does not do:

**No single "winner".** Success without cost is meaningless (expose-all wins) and cost
without success is meaningless (expose-nothing wins). Every arm reports both, and the
comparison is paired per task.

**No pooled comparison.** Arms are compared **paired over tasks** — the same task under
two arms — because task difficulty varies far more than the effect being measured, and
pooling drowns the signal in that variance.

**No claim below the resolution.** ``minimum_detectable_effect`` reports what the run
could see, and comparisons carry it. A benchmark that cannot resolve its own margin is
the quieter sibling of a benchmark that cannot lose.
"""

from __future__ import annotations

import asyncio
import statistics
from dataclasses import dataclass, field
from typing import Sequence

from .arms import Arm
from .runner import ArmResult, run_arm
from .tasks import Task


@dataclass
class Cell:
    """One arm × one task, over all repetitions."""

    arm: str
    task_id: str
    runs: list[ArmResult] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        """Continuous in [0,1] rather than binary — repetitions are what buy resolution."""
        if not self.runs:
            return 0.0
        return sum(1 for r in self.runs if r.passed) / len(self.runs)

    @property
    def mean_prefix_tokens(self) -> float:
        return statistics.fmean(r.prefix_tokens for r in self.runs) if self.runs else 0.0

    @property
    def mean_cost_usd(self) -> float:
        return statistics.fmean(r.cost_usd for r in self.runs) if self.runs else 0.0

    @property
    def mean_turns(self) -> float:
        return statistics.fmean(r.turns for r in self.runs) if self.runs else 0.0

    @property
    def mean_tool_searches(self) -> float:
        return statistics.fmean(r.tool_search_calls for r in self.runs) if self.runs else 0.0


@dataclass
class ArmSummary:
    arm: str
    cells: list[Cell]

    @property
    def success(self) -> float:
        return statistics.fmean(c.success_rate for c in self.cells) if self.cells else 0.0

    @property
    def prefix_tokens(self) -> float:
        return statistics.fmean(c.mean_prefix_tokens for c in self.cells) if self.cells else 0.0

    @property
    def cost_usd(self) -> float:
        return sum(c.mean_cost_usd for c in self.cells)

    @property
    def turns(self) -> float:
        return statistics.fmean(c.mean_turns for c in self.cells) if self.cells else 0.0

    @property
    def tool_searches(self) -> float:
        return statistics.fmean(c.mean_tool_searches for c in self.cells) if self.cells else 0.0


@dataclass
class Comparison:
    """One arm against another, paired over the tasks they both ran."""

    left: str
    right: str
    delta: float
    n_tasks: int
    mde: float

    @property
    def readable(self) -> bool:
        """Is the difference larger than what this run could resolve?"""
        return abs(self.delta) >= self.mde

    def describe(self) -> str:
        verdict = "readable" if self.readable else f"BELOW resolution (mde {self.mde:.3f})"
        return f"{self.left} - {self.right} = {self.delta:+.3f} over {self.n_tasks} tasks · {verdict}"


def minimum_detectable_effect(cells_left: list[Cell], cells_right: list[Cell]) -> float:
    """Roughly the smallest paired difference this run could distinguish from noise.

    Two-sided, ~80% power, from the observed spread of per-task differences. Reported
    rather than assumed: with a handful of tasks the resolution is coarse, and a
    comparison quoted without it invites reading noise as a result.
    """
    by_task = {c.task_id: c for c in cells_right}
    diffs = [
        c.success_rate - by_task[c.task_id].success_rate
        for c in cells_left
        if c.task_id in by_task
    ]
    n = len(diffs)
    if n < 2:
        return 1.0  # unresolvable: any claim would be noise
    spread = statistics.stdev(diffs)
    if spread == 0.0:
        # Perfectly consistent difference; resolution is limited by sample size alone.
        return 1.0 / n
    return 2.8 * spread / (n ** 0.5)


def compare(left: ArmSummary, right: ArmSummary) -> Comparison:
    by_task = {c.task_id: c for c in right.cells}
    shared = [c for c in left.cells if c.task_id in by_task]
    delta = statistics.fmean(
        c.success_rate - by_task[c.task_id].success_rate for c in shared
    ) if shared else 0.0
    return Comparison(
        left=left.arm,
        right=right.arm,
        delta=delta,
        n_tasks=len(shared),
        mde=minimum_detectable_effect(left.cells, right.cells),
    )


async def run_matrix(
    *,
    arms: Sequence[Arm],
    tasks: Sequence[Task],
    repetitions: int = 3,
    model: str = "claude-opus-5",
    max_budget_usd: float = 1.0,
    progress: bool = True,
) -> dict[str, ArmSummary]:
    """Run every arm on every task, ``repetitions`` times each.

    Sequential on purpose. Concurrent sessions would contend for subscription rate
    limits and make the per-run cost and latency figures meaningless — and those
    figures are half of what Gate 0 compares.
    """
    summaries: dict[str, ArmSummary] = {}

    for arm in arms:
        cells: list[Cell] = []
        for task in tasks:
            cell = Cell(arm=arm.name, task_id=task.task_id)
            for rep in range(repetitions):
                result = await run_arm(
                    arm=arm.name,
                    task_id=task.task_id,
                    prompt=task.prompt,
                    gateway_config=str(arm.config_path),
                    model=model,
                    max_budget_usd=max_budget_usd,
                )
                result.passed = task.check(result.answer)
                cell.runs.append(result)
                if progress:
                    mark = "." if result.passed else "x"
                    print(f"  {arm.name} {task.task_id} rep{rep} {mark}", flush=True)
            cells.append(cell)
        summaries[arm.name] = ArmSummary(arm=arm.name, cells=cells)

    return summaries


def run_matrix_sync(**kwargs) -> dict[str, ArmSummary]:
    return asyncio.run(run_matrix(**kwargs))
