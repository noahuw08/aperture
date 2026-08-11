# Gate 0 battery sizing — how many tasks, at how many repetitions

_Design spec. Written 2026-08-11._

Related: [`decisions.md`](../../decisions.md) ·
[`PICKUP.md`](../../PICKUP.md) (§4.2, "three numbers") ·
[`2026-08-08-mvp-gateway-design.md`](2026-08-08-mvp-gateway-design.md) (Q1) ·
`src/mcp_gateway_router/bench/matrix.py` (the statistic this extends)

---

## 1 · What this is

A calculator that answers **"how many tasks must the Gate 0 battery contain, and at how many
repetitions each, to resolve a 15-point difference in task success?"**

It exists because the number was previously a guess. `matrix.py` already reports a
*minimum detectable effect* after a run — what the run could see. This inverts that: what a
run must be, to see what we need. Same statistic, solved for a different unknown.

**This sizes Gate 0 only.** The Q2 margin and the collection stopping rule — the other two of
PICKUP's three numbers — stay open. They are different questions with different data.

## 2 · The decision it serves

Gate 0 asks whether Claude's own tool search already moots ranking-with-a-prompt. The battery
is how that gets read, and a battery too small to resolve a real difference produces a null
that cannot be distinguished from an absence.

**The margin is 15 percentage points, decided 2026-08-11.** It reads: *selection beats
tool-search by at least 15 points of task success at matched cost, or we stop.* Chosen over
20 pp (which would render a genuine 12-point advantage invisible and identical to a null) and
over 10 pp (which needs ~71 hand-authored tasks — beyond what this project can author from
its current traffic).

**Authoring is the binding constraint, not dollars.** At ~$0.07 per 3-turn run, a 31-task
battery at three repetitions costs about **$13** across the two arms that exist today, and
about $20 if a third arm is added. Writing 31 deterministic, session-derived tasks is the
expensive half, and every design choice below trades money for authoring effort deliberately.

## 3 · The math

`minimum_detectable_effect` computes `MDE = 2.8 · σ / √n` over `n` paired tasks. Setting
`MDE = m` and solving:

```
n = (2.8 · σ / m)²
```

The refinement is that **σ is not one thing.** A cell's success rate is a mean over `r`
repetitions, so the per-task paired difference has variance:

```
Var(d_t) = b² + w / r
```

- **`b²` — between-task variance.** Genuine task-to-task variation in the size of the effect.
  Repetitions never reduce it.
- **`w` — within-task variance of a single-run difference**, `p_L(1−p_L) + p_R(1−p_R)`.
  Repetitions divide it by `r`.

Which gives the sizing law:

```
n(r) = 2.8² · (b² + w/r) / m²        with m = 0.15  →  n(r) = 348.4 · (b² + w/r)
```

**`n(r)` is decreasing in `r` and asymptotes at `348.4 · b²`.** That asymptote — the *floor* —
is the most decision-relevant output of the whole exercise. It is the number of tasks below
which no amount of repetition can reach 15 pp. If the floor lands above what can be authored,
the margin has to move, and it is better to learn that before writing tasks than after.

### Estimating the components

Method of moments, from pilot cells run at `r_pilot`:

```
w_hat  = mean over tasks of [ p_L(1−p_L) + p_R(1−p_R) ]
b2_hat = max(0, Var(observed per-task diffs) − w_hat / r_pilot)
```

The `max(0, ·)` clamp is load-bearing. With few pilot tasks the subtraction routinely goes
negative; that means *"between-task variation is indistinguishable from zero at this pilot
size,"* not a negative variance. Clamping and saying so is honest. Returning a negative, or
crashing, is not.

### Worked example (illustrative — real values come from the pilot)

With `b = 0.18` (so `b² = 0.032`) and within-run sd `0.40` (so `w = 0.16`):

| reps | tasks needed | runs per arm | authoring load |
|---|---|---|---|
| 3 | 30 | 90 | 30 tasks |
| 8 | 19 | 152 | 19 tasks |
| 20 | 15 | 300 | 15 tasks |
| ∞ | **12 (floor)** | — | 12 tasks |

Same 15-pp resolution across every row. Trading repetitions for tasks halves the authoring
load for a modest increase in model spend — which is the correct direction given §2.

## 4 · Components

**`src/mcp_gateway_router/bench/power.py`** — pure functions, no model calls, no I/O:

```python
@dataclass(frozen=True)
class VarianceEstimate:
    between: float          # b², clamped at 0
    within: float           # w, single-run variance
    pilot_tasks: int
    pilot_reps: int
    saturated_tasks: int    # cells at p=0 or p=1 — these inform w not at all
    floor_tasks: int        # 2.8²·b²/m², the asymptote

    @property
    def trustworthy(self) -> bool:
        """False when pilot_tasks < 2, or more than half the tasks are saturated."""
        return self.pilot_tasks >= 2 and self.saturated_tasks * 2 <= self.pilot_tasks


@dataclass(frozen=True)
class SizingRow:
    reps: int
    tasks: int
    runs_per_arm: int
    total_runs: int
    est_cost_usd: float


def decompose(left: ArmSummary, right: ArmSummary, reps: int) -> VarianceEstimate
def size_battery(est: VarianceEstimate, margin: float = 0.15,
                 reps: Sequence[int] = (3, 5, 8, 12, 20),
                 arms: int = 2, cost_per_run: float = 0.07) -> list[SizingRow]
```

`arms` defaults to 2 because two arm configs exist today (`gateway.armA.json`,
`gateway.armC.json`); it is a parameter so a third arm does not silently invalidate a
cost estimate.

**Why a separate module rather than more of `matrix.py`.** Sizing is a *planning* activity
that runs before an experiment; `matrix.py` is *execution*. `matrix.py` already carries the
run loop, the aggregation and the statistics, and making it the experiment planner too would
give it a fourth purpose. The split also means the sizing math is testable with synthetic
numbers and never needs a model call — which is most of its value.

**CLI:** `python -m mcp_gateway_router.bench.power --from results/pilot.json` prints the
frontier table; `--pilot` runs the pilot first, then prints it.

## 5 · Two changes outside the new module

Both are prerequisites, discovered by trying to use the pilot data that already exists.

**a. The persisted cell format is lossy.** `results/arms_smoke.json` keeps only `rate` per
cell:

```json
{ "task_id": "gh-search-count", "rate": 0.0 }
```

`w_hat` needs both `p` and `r`, and neither the repetition count nor the pass count survives
serialization. Whatever writes this file must persist `reps` and passes alongside the rate.
Existing files remain readable; the added fields are optional on load, and a file lacking
them is rejected for sizing rather than silently assumed to be `reps=3`.

**b. The pilot needs middling-difficulty tasks.** Both current smoke tasks are saturated —
`gh-search-count` at 0.0 and `gh-whoami` at 1.0, identically across both arms. At `p = 0` and
`p = 1`, `p(1−p) = 0`, so `w_hat = 0` and `Var(diffs) = 0`: the decomposition would report
"no noise anywhere," which is a property of that battery, not a measurement of the system.
(`gh-search-count` also fails every run — its expected count has drifted, exactly as its own
note warned it would.)

The pilot therefore needs **4–6 tasks that land between always-pass and always-fail**.

## 6 · The pilot

**Pilot tasks are authored to full battery standard and reused as part of the final battery.**
Nothing is written twice. This is an internal-pilot design: the same tasks estimate σ and then
appear in the final run.

**The caveat, stated rather than hidden.** Estimating σ on tasks that also appear in the final
comparison introduces mild optimism — the sizing is tuned to those tasks' spread. At roughly
5 of 31 tasks the effect is negligible, and the alternative (discarding calibrated,
session-derived tasks) costs more than the bias is worth.

**Sequence:**

1. Author 4–6 middling-difficulty tasks from observed sessions.
2. Run them across arms at `r_pilot = 5`. Five rather than three: `w/r` is estimated from the
   pilot too, and three repetitions resolve a per-cell rate only to thirds.
3. `decompose` → `size_battery` → read the frontier.
4. Pick a `(tasks, reps)` row. Author the remainder up to that count.

If step 3 reports `trustworthy == False`, the answer is more pilot tasks, not a bigger battery
sized off a number nobody believes.

## 7 · Testing

The math is testable without a single model call.

| Test | Asserts |
|---|---|
| Recovery on synthetic data | Cells generated with known `b²` and `w` are recovered within tolerance |
| Monotonicity | `n(r)` is non-increasing in `r` and converges to `floor_tasks` |
| The floor is real | With `w = 0`, additional repetitions change `n` not at all |
| Negative-variance clamp | `Var(diffs) < w/r` yields `between == 0`, no crash, no negative |
| Saturation guard | All cells at 0/1 → `saturated_tasks == pilot_tasks` and `trustworthy is False` |
| Too few tasks | `pilot_tasks < 2` refuses to estimate, mirroring `minimum_detectable_effect`'s `n < 2 → 1.0` |
| Consistency with `matrix.py` | At `r = r_pilot` and an inactive clamp, `n` from `decompose` equals `(2.8 · stdev(diffs) / m)²` exactly |

The last row is the important one. `b2_hat + w_hat/r_pilot` reconstructs `Var(diffs)` by
construction, so the agreement is an identity rather than an approximation — which pins the
new module to the existing statistic instead of introducing a second, differently calibrated
one alongside it.

## 8 · Out of scope

- **`minimum_detectable_effect` is not modified.** It answers a different question correctly.
- **The arms, the runner and the task format are unchanged**, apart from §5(a)'s added fields.
- **Q2.** Its margin and the collection stopping rule are separate decisions on separate data.
- **Authoring the battery itself.** This spec sizes it; writing the tasks is the work that
  follows, and it depends on session collection, which is ongoing.

## 9 · What this does not fix

Sizing tells you how many tasks to write. It does not tell you the tasks are any good. A
battery of 31 tasks drawn from a single project's traffic will size correctly and still fail
to represent the workload — the same degeneracy that currently limits Q2, in a different
place. The task mix remains a judgment call that no statistic resolves.
