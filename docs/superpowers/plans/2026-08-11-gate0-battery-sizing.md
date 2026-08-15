# Gate 0 Battery Sizing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a calculator that says how many Gate 0 tasks to author, at how many repetitions each, to resolve a 15-point difference in task success.

**Architecture:** A new pure-math module `bench/power.py` inverts the statistic that `bench/matrix.py` already reports. Per-task variance is split into a between-task component that repetitions cannot reduce and a within-task component that they divide by `r`, so the output is a `(tasks, reps)` frontier rather than a single task count. A third task makes the pilot's persisted format lossless, since the current one drops the numbers the estimate needs.

**Tech Stack:** Python 3.11+, stdlib only (`math`, `statistics`, `dataclasses`, `json`, `argparse`), pytest. No new dependencies.

## Global Constraints

- **Margin is 0.15** (15 percentage points of task success). This is the default everywhere it appears.
- **`Z = 2.8`** — the two-sided ~80% power constant. Must be imported or duplicated from `matrix.minimum_detectable_effect`, never re-derived to a different value.
- **Sample variance throughout** (`statistics.variance`, `statistics.stdev`, both `n−1`). `matrix.py` uses `stdev`; the consistency identity in Task 2 breaks if this module uses population variance.
- **`arms` defaults to 2** — `gateway.armA.json` and `gateway.armC.json` are the two that exist.
- **`cost_per_run` defaults to 0.07** USD.
- **Do not modify `minimum_detectable_effect`.** It answers a different question correctly.
- Run tests with `uv run --extra dev --extra gateway pytest -q`.
- Follow existing style: `from __future__ import annotations`, frozen dataclasses, module docstrings that explain *why* rather than restate the code.

---

### Task 1: Variance decomposition

**Files:**
- Create: `src/mcp_gateway_router/bench/power.py`
- Test: `tests/bench/test_power.py`

**Interfaces:**
- Consumes: `mcp_gateway_router.bench.matrix.ArmSummary`, `.Cell`; `mcp_gateway_router.bench.runner.ArmResult`
- Produces: `Z: float`, `VarianceEstimate` (frozen dataclass with fields `between: float`, `within: float`, `pilot_tasks: int`, `pilot_reps: int`, `saturated_tasks: int`; property `trustworthy: bool`; method `floor_tasks(margin: float) -> int`), and `decompose(left: ArmSummary, right: ArmSummary, reps: int) -> VarianceEstimate`

- [ ] **Step 1: Write the failing test**

Create `tests/bench/test_power.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev --extra gateway pytest tests/bench/test_power.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_gateway_router.bench.power'`

- [ ] **Step 3: Write minimal implementation**

Create `src/mcp_gateway_router/bench/power.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev --extra gateway pytest tests/bench/test_power.py -q`
Expected: PASS, 5 passed

- [ ] **Step 5: Run the full suite for regressions**

Run: `uv run --extra dev --extra gateway pytest -q`
Expected: PASS, 229 passed (224 existing + 5 new)

- [ ] **Step 6: Commit**

```bash
git add src/mcp_gateway_router/bench/power.py tests/bench/test_power.py
git commit -m "feat(bench): variance decomposition for battery sizing

A cell's success rate is a mean over r repetitions, so per-task variance is
b^2 + w/r. Repetitions divide w and never touch b^2, which means the two
have to be estimated separately before task count can be traded against
repetition count. Method of moments, with the negative case clamped and
reported rather than returned."
```

---

### Task 2: The sizing frontier

**Files:**
- Modify: `src/mcp_gateway_router/bench/power.py` (append)
- Test: `tests/bench/test_power.py` (append)

**Interfaces:**
- Consumes: `Z`, `VarianceEstimate` from Task 1
- Produces: `SizingRow` (frozen dataclass with fields `reps: int`, `tasks: int`, `runs_per_arm: int`, `total_runs: int`, `est_cost_usd: float`) and `size_battery(est: VarianceEstimate, margin: float = 0.15, reps: Sequence[int] = (3, 5, 8, 12, 20), arms: int = 2, cost_per_run: float = 0.07) -> list[SizingRow]`

- [ ] **Step 1: Write the failing test**

Append to `tests/bench/test_power.py`:

```python
from mcp_gateway_router.bench.matrix import minimum_detectable_effect
from mcp_gateway_router.bench.power import size_battery


def test_more_repetitions_never_need_more_tasks():
    est = VarianceEstimate(
        between=0.0324, within=0.16, pilot_tasks=6, pilot_reps=5, saturated_tasks=0
    )

    rows = size_battery(est, margin=0.15, reps=(3, 5, 8, 12, 20))

    counts = [r.tasks for r in rows]
    assert counts == sorted(counts, reverse=True)
    assert all(r.tasks >= est.floor_tasks(0.15) for r in rows)


def test_the_worked_example_from_the_spec():
    est = VarianceEstimate(
        between=0.0324, within=0.16, pilot_tasks=6, pilot_reps=5, saturated_tasks=0
    )

    rows = {r.reps: r for r in size_battery(est, margin=0.15, reps=(3, 8, 20))}

    assert rows[3].tasks == 30
    assert rows[8].tasks == 19
    assert rows[20].tasks == 15
    assert rows[3].runs_per_arm == 90
    assert rows[3].total_runs == 180          # two arms
    assert rows[3].est_cost_usd == pytest.approx(12.60)


def test_with_no_within_noise_repetitions_buy_nothing():
    est = VarianceEstimate(
        between=0.0324, within=0.0, pilot_tasks=6, pilot_reps=5, saturated_tasks=0
    )

    rows = size_battery(est, margin=0.15, reps=(3, 20))

    assert rows[0].tasks == rows[1].tasks == est.floor_tasks(0.15)


def test_sizing_agrees_with_the_statistic_it_inverts():
    """At the pilot's own reps, n must match inverting minimum_detectable_effect.

    b^2 + w/reps reconstructs the observed variance exactly when the clamp is
    inactive, so this is an identity, not an approximation. It is what stops this
    module drifting into a second, differently calibrated statistic.
    """
    reps = 10
    left = _summary("L", {"t1": (10, reps), "t2": (0, reps), "t3": (0, reps)})
    right = _summary("R", {"t1": (0, reps), "t2": (0, reps), "t3": (10, reps)})

    est = decompose(left, right, reps)
    sized = {r.reps: r.tasks for r in size_battery(est, margin=0.15, reps=(reps,))}

    mde = minimum_detectable_effect(left.cells, right.cells)
    # mde = Z * stdev / sqrt(3); invert for the n that would have made mde == 0.15
    expected = math.ceil((mde * math.sqrt(3) / 0.15) ** 2)

    assert sized[reps] == expected


def test_a_battery_is_never_smaller_than_two_tasks():
    est = VarianceEstimate(
        between=0.0, within=0.0, pilot_tasks=6, pilot_reps=5, saturated_tasks=0
    )

    rows = size_battery(est, margin=0.15, reps=(3,))

    assert rows[0].tasks == 2  # a paired comparison needs two to have any spread


def test_a_nonpositive_margin_is_rejected():
    est = VarianceEstimate(
        between=0.01, within=0.1, pilot_tasks=6, pilot_reps=5, saturated_tasks=0
    )

    with pytest.raises(ValueError):
        size_battery(est, margin=0.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev --extra gateway pytest tests/bench/test_power.py -q`
Expected: FAIL — `ImportError: cannot import name 'SizingRow'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/mcp_gateway_router/bench/power.py`. Add `from typing import Sequence` to the imports:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev --extra gateway pytest tests/bench/test_power.py -q`
Expected: PASS, 11 passed

- [ ] **Step 5: Run the full suite for regressions**

Run: `uv run --extra dev --extra gateway pytest -q`
Expected: PASS, 235 passed

- [ ] **Step 6: Commit**

```bash
git add src/mcp_gateway_router/bench/power.py tests/bench/test_power.py
git commit -m "feat(bench): (tasks, reps) sizing frontier at a fixed margin

Every row resolves the same 15-point margin; they differ in how the work
splits between authoring tasks and running repetitions. Authoring is the
binding constraint, so the frontier is the useful object rather than a
single task count.

Pinned to matrix.minimum_detectable_effect by an identity test: at the
pilot's own reps and an inactive clamp, b^2 + w/r reconstructs the observed
variance exactly, so the two agree by construction rather than by luck."
```

---

### Task 3: Lossless pilot data, loader, and CLI

**Files:**
- Modify: `notebooks/build_arms_walkthrough.py:210-218` (the `CACHE.write_text` block)
- Modify: `src/mcp_gateway_router/bench/power.py` (append loader and CLI)
- Test: `tests/bench/test_power.py` (append)

**Interfaces:**
- Consumes: `VarianceEstimate`, `decompose`, `size_battery` from Tasks 1–2; `mcp_gateway_router.bench.matrix.Cell`, `.ArmSummary`; `mcp_gateway_router.bench.runner.ArmResult`
- Produces: `load_pilot(path: Path) -> tuple[ArmSummary, ArmSummary, int]` and a `main()` entry point runnable as `python -m mcp_gateway_router.bench.power`

**Why the format changes:** `results/arms_smoke.json` stores `{"task_id": ..., "rate": 0.0}` and nothing else. Estimating `w` needs `p` *and* `r`, and neither the repetition count nor the pass count survives. A file without them is rejected rather than assumed to be `reps=3`.

- [ ] **Step 1: Write the failing test**

Append to `tests/bench/test_power.py`:

```python
import json
from pathlib import Path

from mcp_gateway_router.bench.power import load_pilot


def _write_pilot(tmp_path: Path, reps: int) -> Path:
    path = tmp_path / "pilot.json"
    path.write_text(
        json.dumps(
            {
                "reps": reps,
                "arms": {
                    "A-toolsearch": {
                        "cells": [
                            {"task_id": "t1", "passes": 3},
                            {"task_id": "t2", "passes": 1},
                        ]
                    },
                    "C-semantic": {
                        "cells": [
                            {"task_id": "t1", "passes": 1},
                            {"task_id": "t2", "passes": 4},
                        ]
                    },
                },
            }
        )
    )
    return path


def test_load_pilot_reconstructs_rates_and_reps(tmp_path):
    path = _write_pilot(tmp_path, reps=5)

    left, right, reps = load_pilot(path)

    assert reps == 5
    assert {c.task_id: c.success_rate for c in left.cells} == {"t1": 0.6, "t2": 0.2}
    assert {c.task_id: c.success_rate for c in right.cells} == {"t1": 0.2, "t2": 0.8}


def test_a_legacy_rate_only_file_is_rejected(tmp_path):
    """The old format cannot support sizing, and must say so rather than guess."""
    path = tmp_path / "old.json"
    path.write_text(
        json.dumps(
            {
                "A-toolsearch": {"cells": [{"task_id": "t1", "rate": 0.0}]},
                "C-semantic": {"cells": [{"task_id": "t1", "rate": 1.0}]},
            }
        )
    )

    with pytest.raises(ValueError, match="reps"):
        load_pilot(path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev --extra gateway pytest tests/bench/test_power.py -q`
Expected: FAIL — `ImportError: cannot import name 'load_pilot'`

- [ ] **Step 3: Write the loader and CLI**

Append to `src/mcp_gateway_router/bench/power.py`. Add `import argparse`, `import json`, and `from pathlib import Path` to the imports, plus `from .matrix import ArmSummary, Cell` and `from .runner import ArmResult`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev --extra gateway pytest tests/bench/test_power.py -q`
Expected: PASS, 13 passed

- [ ] **Step 5: Make the pilot serialization lossless**

In `notebooks/build_arms_walkthrough.py`, replace the `CACHE.write_text` block (currently lines 210–218) so pass counts and reps survive. Change `REPETITIONS` to a named variable so it can be written out:

```python
if RERUN:
    REPETITIONS = 5
    res = await run_matrix(arms=arms, tasks=tasks, repetitions=REPETITIONS, max_budget_usd=0.60)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({
        "reps": REPETITIONS,
        "arms": {
            name: {
                "success": s.success, "prefix": s.prefix_tokens, "turns": s.turns,
                "searches": s.tool_searches, "cost": s.cost_usd,
                # `passes` alongside `rate`: sizing needs p AND r, and rate alone
                # loses r. See docs/superpowers/specs/2026-08-11-gate0-battery-sizing-design.md
                "cells": [
                    {
                        "task_id": c.task_id,
                        "rate": c.success_rate,
                        "passes": sum(1 for r in c.runs if r.passed),
                    }
                    for c in s.cells
                ],
            } for name, s in res.items()
        },
    }, indent=2))
    summary = json.loads(CACHE.read_text())["arms"]
else:
    summary = json.loads(CACHE.read_text())
    if "arms" in summary:
        summary = summary["arms"]
    print("(cached results — set RERUN = True to re-measure)\\n")
```

The `if "arms" in summary` branch keeps the existing `results/arms_smoke.json` readable by the walkthrough, which only needs `rate`. Sizing still rejects it — the notebook can display old data it cannot size from.

- [ ] **Step 6: Verify the notebook builder still runs**

Run: `uv run --with nbformat python notebooks/build_arms_walkthrough.py`
Expected: exits 0, rewrites `notebooks/arms_walkthrough.ipynb`

Then confirm the existing cached file still displays:

Run: `uv run --extra gateway python -c "
import json
d = json.loads(open('results/arms_smoke.json').read())
d = d.get('arms', d)
print(sorted(d))
"`
Expected: `['A-toolsearch', 'C-semantic']`

- [ ] **Step 7: Confirm the CLI rejects the stale file with a useful message**

Run: `uv run --extra gateway python -m mcp_gateway_router.bench.power --from results/arms_smoke.json`
Expected: exits non-zero with a single clean line naming `reps` and telling you to re-run the pilot — no traceback. This is the correct behaviour: that file predates the format and cannot support sizing.

- [ ] **Step 8: Run the full suite**

Run: `uv run --extra dev --extra gateway pytest -q`
Expected: PASS, 237 passed

- [ ] **Step 9: Commit**

```bash
git add src/mcp_gateway_router/bench/power.py tests/bench/test_power.py notebooks/build_arms_walkthrough.py
git commit -m "feat(bench): pilot loader and sizing CLI, with a lossless pilot format

The pilot file stored only a per-cell rate, which loses the repetition
count; estimating within-task variance needs p and r both. Pass counts and
reps are now written, and the old rate-only format is rejected for sizing
rather than assumed to be reps=3. The walkthrough still reads old files,
since displaying a rate needs nothing more."
```

---

## After the plan

Two prerequisites remain, both human work outside this plan and both recorded in the spec:

1. **Author 4–6 middling-difficulty pilot tasks** drawn from observed sessions. The current smoke tasks are saturated at 0.0 and 1.0, which makes `w = 0` a property of the battery rather than a measurement. Written to full battery standard, they are reused in the final battery.
2. **Run the pilot at `reps = 5`**, then `python -m mcp_gateway_router.bench.power --from results/arms_smoke.json` to read the frontier and pick a `(tasks, reps)` row.

If the CLI reports `trustworthy == False`, the answer is more pilot tasks — not a larger battery sized off a number nobody believes.
