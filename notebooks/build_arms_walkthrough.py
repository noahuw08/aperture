"""Generate `arms_walkthrough.ipynb`.

Generated, not hand-edited, so it cannot drift from the code it documents.
Re-run after changing the bench, then execute the notebook.
"""

from __future__ import annotations

from pathlib import Path

import nbformat as nbf

REPO = Path(__file__).resolve().parents[1]


def md(text):
    return nbf.v4.new_markdown_cell(text.strip())


def code(src):
    return nbf.v4.new_code_cell(src.strip())


nb = nbf.v4.new_notebook()
nb.cells = [
    code(
        """
# --- environment guard -------------------------------------------------------
# These notebooks import the gateway, which needs the `gateway` extra (and `bench`
# for arm C's bi-encoder). Without them a later cell fails with a bare
# ModuleNotFoundError that reads like a code bug rather than a setup problem.
import importlib.util, sys

missing = [m for m in ("mcp", "sentence_transformers") if importlib.util.find_spec(m) is None]
if missing:
    print("Kernel is missing:", missing)
    print("Kernel python is:", sys.executable)
    print()
    print("Fix once, then select the 'mcp-gateway (.venv)' kernel:")
    print("    uv sync --extra gateway --extra bench --extra dev")
    print("    .venv/bin/python -m ipykernel install --user --name mcp-gateway")
    print()
    print("Or run headless with: uv run --with jupyter --with nbconvert "
          "--extra gateway --extra bench jupyter nbconvert --to notebook "
          "--execute --inplace <notebook>")
    raise SystemExit("wrong kernel")

print("kernel ok:", sys.executable)
"""
    ),
    md(
        """
# Arms, head to head: Claude's tool search vs. ours

**The question this exists to answer.** Gate 0: *does Claude's own tool search already
moot ranking-with-a-prompt?* If a good selector **with the task in hand** cannot beat it,
then predicting **without** the task — strictly less information — will not either, and
the personalization engine is not worth building yet.

**Arm C is deliberately not the engine.** It is the Phase 0 bi-encoder: commodity
retrieval. Using it here means Gate 0 can be read *before* the engine is built, which is
the whole point of falsification-before-construction.

**Where the model runs.** Unlike `gateway_walkthrough.ipynb`, cells here that hit the
model cost real usage. They are gated behind `RERUN` and read cached results otherwise.
"""
    ),
    code(
        """
import json, os, sys, glob, asyncio, logging, collections
from pathlib import Path

REPO = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()
sys.path.insert(0, str(REPO / "src"))
logging.basicConfig(stream=sys.stderr, level=logging.ERROR)

for line in (REPO / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# Model-touching cells are off by default. Flip to re-measure.
RERUN = False
CACHE = REPO / "results/arms_smoke.json"
print("RERUN =", RERUN, "| cache exists:", CACHE.exists())
"""
    ),
    md(
        """
## 1 — An arm is a config, and nothing else

Same binary, same prompt, same model. If anything other than *which tools reach the
client* differs between two arms, the comparison is measuring that difference instead.
"""
    ),
    code(
        """
def arm_config(name):
    return json.loads((REPO / name).read_text())

a, c = arm_config("gateway.armA.json"), arm_config("gateway.armC.json")
keys = sorted(set(a) | set(c))
print(f"{'field':<16}{'arm A':<22}{'arm C':<22}")
print("-" * 60)
for k in keys:
    if k == "upstreams":
        va = f"{len(a[k])} servers"; vc = f"{len(c[k])} servers"
    else:
        va, vc = str(a.get(k)), str(c.get(k))
    mark = "  <-- differs" if va != vc else ""
    print(f"{k:<16}{va[:20]:<22}{vc[:20]:<22}{mark}")
"""
    ),
    md(
        """
## 2 — What each arm actually hands the client

No model involved: this launches real gateways and reads `tools/list`.

Watch the **tool counts change with the task** in arm C. Same 3,000-token budget, but
GitHub's PR schemas are larger than Playwright's browser schemas, so fewer fit. That is
the 39x size spread showing up as behaviour rather than as a table.
"""
    ),
    code(
        """
from mcp_gateway_router.gateway.config import UpstreamSpec
from mcp_gateway_router.gateway.stdio_session import StdioUpstreamSession

SHIM = str(REPO / "bin/mcp-gateway")

async def advertised(config_name, task=None):
    env = {"MCP_GATEWAY_CONFIG": str(REPO / config_name)}
    if task:
        env["MCP_GATEWAY_TASK"] = task
    s = StdioUpstreamSession(UpstreamSpec(server_id="gw", command=SHIM, env=env))
    await s.start()
    try:
        return [t.name for t in await s.list_tools()]
    finally:
        await s.aclose()

TASKS = [
    "which pull requests are open on this repository",
    "take a screenshot of the landing page in a browser",
]

base = await advertised("gateway.armA.json")
print(f"arm A -> {len(base)} tools, task-independent (the client cuts, not us)\\n")

for t in TASKS:
    sel = await advertised("gateway.armC.json", task=t)
    print(f"arm C -> {len(sel):>2} tools for {t!r}")
    print("        ", ", ".join(n.split("__", 1)[1] for n in sel[:5]), "...")
"""
    ),
    md(
        """
## 3 — The tasks, and the guard against inventing them

Tasks must be **drawn from observed sessions**. Inventing them moves the house failure
mode out of the personas and into the task mix, where it is harder to see.

The leakage check makes that an assertion rather than a hope: it measures how much of the
required tools' description vocabulary a prompt reuses. A prompt that reuses the tool's
own words is describing its own answer, and flatters every retrieval arm. The threshold
is the measured ToolRet **query** baseline of 7.4%; the leaky `instruction` field scored
20.3%.

⚠️ The tasks below are **throwaway smoke fixtures**, not evidence.
"""
    ),
    code(
        """
from mcp_gateway_router.bench.tasks import load_tasks, check_leakage
from mcp_gateway_router.harvest import catalog_from_json

tasks = load_tasks(REPO / "bench/smoke_tasks.json")
desc = {t.key: t.description for t in catalog_from_json(json.loads((REPO / "results/catalog.json").read_text()))}

for r in check_leakage(tasks, desc):
    print(f"  {r.task_id:<18} leakage {r.overlap:.3f}   {'FLAG - rewrite' if r.leaks else 'ok'}")

print()
print("Two of the first-draft prompts were flagged, and one had an empty `expect` with")
print("`contains` matching, so it could never fail. Both were fixed before this ran.")
"""
    ),
    md(
        """
## 4 — Run the matrix

Arms x tasks x repetitions, sequential on purpose: concurrent sessions would contend for
rate limits and make the per-run cost and latency figures meaningless, and those figures
are half of what Gate 0 compares.
"""
    ),
    code(
        """
from mcp_gateway_router.bench.arms import Arm
from mcp_gateway_router.bench.matrix import run_matrix, compare

arms = [
    Arm("A-toolsearch", "full catalog; client cuts", REPO / "gateway.armA.json", None),
    Arm("C-semantic", "task-conditioned selection", REPO / "gateway.armC.json", 3000),
]

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

print(f"{'arm':<16}{'success':>9}{'prefix':>10}{'turns':>8}{'searches':>10}{'cost $':>9}")
print("-" * 62)
for name, s in summary.items():
    print(f"{name:<16}{s['success']:>9.2f}{s['prefix']:>10,.0f}{s['turns']:>8.1f}{s['searches']:>10.1f}{s['cost']:>9.4f}")
"""
    ),
    md(
        """
## 5 — Why there is no "winner" column

**Success alone is meaningless** (expose-all wins it) and **cost alone is meaningless**
(expose-nothing wins it). So both are always reported, and arms are compared **paired per
task** — task difficulty varies far more than the effect being measured, and pooling
drowns the signal in that variance.

Every comparison also carries its own **minimum detectable effect**. A benchmark that
cannot resolve its own margin is the quiet sibling of one that cannot lose.
"""
    ),
    code(
        """
import statistics

left, right = summary["C-semantic"], summary["A-toolsearch"]
by_task = {c["task_id"]: c["rate"] for c in right["cells"]}
diffs = [c["rate"] - by_task[c["task_id"]] for c in left["cells"] if c["task_id"] in by_task]

delta = statistics.fmean(diffs) if diffs else 0.0
n = len(diffs)
if n < 2:
    mde = 1.0
else:
    spread = statistics.stdev(diffs)
    mde = 1.0 / n if spread == 0 else 2.8 * spread / (n ** 0.5)

print(f"C - A = {delta:+.3f} over {n} tasks")
print(f"resolution of this run (MDE) = {mde:.3f}")
print()
print("READABLE" if abs(delta) >= mde else "BELOW RESOLUTION - this run cannot support any claim")
"""
    ),
    md(
        """
## 6 — What the arms cannot tell you, and why

**`was_exposed: false` is not collectable on this client.** With a tool deliberately
withheld, the agent ran `ToolSearch`, saw it was unavailable, and completed the task via
`Bash` — volunteering that it had not used the MCP tools. *Zero* call records reached the
gateway, because availability is resolved **before** a call is generated.

So "measure demand for tools you aren't serving" — named in the roadmap as core
differentiation — has no channel here. The replacement signal is the agent *narrating the
workaround*, which lives in model output and is therefore visible to **Q1** and invisible
to **Q2**.

**And cutting the catalog does not save prefix tokens.** Tool search defers schemas, so
arm C's advantage cannot be cost. It has to be task success, turn count, or search count —
all measured above.
"""
    ),
    md(
        """
## What this notebook establishes

| Question | Where to look |
|---|---|
| What differs between arms? | §1 — one config, `mode` and `selector` |
| Does arm C actually adapt to the task? | §2 — 10 PR tools vs 19 browser tools |
| Are the tasks honest? | §3 — leakage below the 7.4% baseline |
| Who wins? | §5 — **and whether the run can even tell** |
| What can arms never show? | §6 — the missing-demand signal has no channel |

**To turn this into a real Gate 0 reading:** replace `bench/smoke_tasks.json` with a
dozen tasks drawn from observed sessions, raise `repetitions`, and set `RERUN = True`.
The MDE line will say whether the result is readable before you interpret it.
"""
    ),
]

out = REPO / "notebooks/arms_walkthrough.ipynb"
nbf.write(nb, str(out))
print("wrote", out)
