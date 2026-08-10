"""Generate `gateway_walkthrough.ipynb`.

The notebook is generated rather than hand-edited so it stays in step with the code it
documents — a hand-maintained notebook drifts silently and then teaches the wrong thing.
Re-run this script after changing the gateway, then execute the notebook.
"""

from __future__ import annotations

import json
from pathlib import Path

import nbformat as nbf

REPO = Path(__file__).resolve().parents[1]


def md(text: str) -> nbf.NotebookNode:
    return nbf.v4.new_markdown_cell(text.strip())


def code(source: str) -> nbf.NotebookNode:
    return nbf.v4.new_code_cell(source.strip())


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
# The gateway, stage by stage

**What this is for.** Prose kept collapsing two distinctions that matter: *shadow vs live*,
and *our selection vs Claude's tool search*. This notebook runs the real gateway at each
stage and shows what actually moves, so neither can stay ambiguous.

Every cell below runs against the live system — real MCP servers, the real catalog, the
real log format. Nothing here is illustrative.

**The one thing to hold onto:** the gateway sits on the **MCP wire**, between the client
and the upstream servers. It never sees the model call. That single fact explains most of
what follows — including why it can answer one of our two questions and not the other.
"""
    ),
    code(
        """
import json, os, subprocess, sys
from pathlib import Path

REPO = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()
sys.path.insert(0, str(REPO / "src"))

# Secrets come from .env beside gateway.json, exactly as the gateway itself loads them.
for line in (REPO / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

print("repo:", REPO)
"""
    ),
    md(
        """
## Stage 1 — What the gateway knows before you type anything

This is the **entire** input at decision point A. There is no prompt at `tools/list`, so
whatever is captured here *is* the feature vector any point-A ranker gets.

Note where it comes from: the launcher shim exports the client's working directory
**before** `uv --directory` changes it. `os.getcwd()` would give the gateway's own repo,
and `$PWD` fails too because bash overwrites it at startup.
"""
    ),
    code(
        """
from mcp_gateway_router.gateway.environment import capture_environment

os.environ["MCP_GATEWAY_CLIENT_CWD"] = str(REPO)
env = capture_environment()
for k, v in sorted(env.items()):
    print(f"  {k:12s} {v}")
"""
    ),
    md(
        """
## Stage 2 — The catalog, and why cost is not uniform

`harvest.py` connects to every upstream, aggregates their tools, and writes the artifact
the gateway reads at request time. Measurement is **control plane**: the data plane never
calls out to size a schema.

The spread below is why `fill_budget` is a knapsack rather than a top-K. Under a flat
cost-per-tool the two are the same operation.
"""
    ),
    code(
        """
from mcp_gateway_router.harvest import catalog_from_json
import statistics, collections

payload = json.loads((REPO / "results/catalog.json").read_text())
catalog = catalog_from_json(payload)

def size(t):
    return len(t.name) + len(t.description) + len(json.dumps(t.input_schema, separators=(",", ":")))

sizes = sorted(size(t) for t in catalog)
by_server = collections.Counter(t.server_id for t in catalog)

print(f"{len(catalog)} tools:", dict(by_server))
print(f"  min {sizes[0]:,}  median {int(statistics.median(sizes)):,}  max {sizes[-1]:,} chars")
print(f"  spread {sizes[-1]/sizes[0]:.0f}x")
print()
print("  bytes by server (cost concentrates, and not where tool COUNT suggests):")
tot = collections.Counter()
for t in catalog:
    tot[t.server_id] += size(t)
grand = sum(tot.values())
for s, n in tot.most_common():
    print(f"    {s:11s} {by_server[s]:>3} tools  {n:>7,} chars  {100*n/grand:>5.1f}% of bytes")
"""
    ),
    md(
        """
## Stage 3 — Shadow vs live, run for real

Same binary, same upstreams, same prompt. **One config field differs.**

- **shadow** — advertise the whole catalog, and *log the selection we would have made*.
  Behaviour is identical to having no gateway, so the client's own tool search still does
  the cut.
- **live** — advertise the cut.

The helper below launches an actual gateway subprocess and asks it `tools/list`.

⚠️ The `env` argument is load-bearing. `env=None` on `StdioServerParameters` is **not**
"inherit" — the MCP SDK substitutes a sanitised default carrying only
`HOME/LOGNAME/PATH/SHELL/TERM/USER`. Omit it and the gateway silently serves its default
config while looking perfectly healthy.
"""
    ),
    code(
        """
import asyncio, logging
logging.basicConfig(stream=sys.stderr, level=logging.ERROR)

from mcp_gateway_router.gateway.config import UpstreamSpec
from mcp_gateway_router.gateway.stdio_session import StdioUpstreamSession

SHIM = str(REPO / "bin/mcp-gateway")

async def ask_gateway(config_name, task=None):
    env = {"MCP_GATEWAY_CONFIG": str(REPO / config_name)}
    if task:
        env["MCP_GATEWAY_TASK"] = task
    s = StdioUpstreamSession(UpstreamSpec(server_id="gw", command=SHIM, env=env))
    await s.start()
    try:
        return [t.name for t in await s.list_tools()]
    finally:
        await s.aclose()

# Top-level await: the Jupyter kernel already runs an event loop, so
# asyncio.run() would raise here.
shadow = await ask_gateway("gateway.json")
print(f"shadow advertises {len(shadow)} tools -> the client sees everything")
print("  first few (catalog order):", ", ".join(n.split("__", 1)[1] for n in shadow[:4]))
"""
    ),
    code(
        """
TASK = "which pull requests are open on this repository"
live = await ask_gateway("gateway.armC.json", task=TASK)

print(f"live advertises {len(live)} tools for: {TASK!r}")
for n in live:
    print("   ", n)
"""
    ),
    md(
        """
## Stage 4 — The decision record, read side by side

`exposed: []` used to be ambiguous — it read identically for *"shadow, the client received
everything"* and *"live, the selector chose nothing"*, which are opposite situations. A
reader hit exactly that and concluded the selector was broken.

The record now carries `mode` and `n_advertised`, so the two are distinguishable.
"""
    ),
    code(
        """
import glob

def latest_record(log_dir):
    files = sorted(glob.glob(str(REPO / log_dir / "*.jsonl")))
    if not files:
        return None
    for line in reversed(Path(files[-1]).read_text().splitlines()):
        r = json.loads(line)
        if r["kind"] == "decision":
            return r
    return None

rows = [("field", "shadow", "live (arm C)")]
a, b = latest_record("runs"), latest_record("runs/bench/C")
for f in ("mode", "n_candidates", "n_advertised", "selector_version", "decision_point"):
    rows.append((f, str(a and a.get(f)), str(b and b.get(f))))
rows.append(("exposed (selector chose)", str(a and len(a["exposed"])), str(b and len(b["exposed"]))))

w = [max(len(r[i]) for r in rows) for i in range(3)]
for i, r in enumerate(rows):
    print("  " + "  ".join(c.ljust(w[j]) for j, c in enumerate(r)))
    if i == 0:
        print("  " + "  ".join("-" * x for x in w))
"""
    ),
    md(
        """
### Reading that table

- **`decision_point: A`** in shadow — no prompt exists at `tools/list`, so `task` is `None`.
  Arm C shows **C** only because the harness injected the task via `MCP_GATEWAY_TASK`.
  That injection is what makes A-vs-C a comparison rather than a category error.
- **`n_advertised`** is what the client actually received. **`exposed`** is what the
  selector chose. In shadow they diverge on purpose: the selection is a *counterfactual*,
  recorded but never applied.
"""
    ),
    md(
        """
## Stage 5 — Where Claude's tool search sits, and why the log can't see it

```
  ┌─ Claude Code ──────────────────────────────────────┐
  │  1. tools/list ───────────────────────────────────────▶ gateway
  │  2. holds all 95 definitions locally                │
  │  3. builds the API request with schemas DEFERRED    │
  │  4. you prompt                                      │
  │  5. model emits ToolSearch                          │
  │  6. Claude Code answers it from its local copy      │
  │  7. model emits the real call ───────────────────────▶ gateway
  └─────────────────────────────────────────────────────┘
```

**Steps 2–6 never cross the MCP wire.** The cell below shows the consequence directly:
sessions log the resulting tool calls and never a search.
"""
    ),
    code(
        """
records = []
for f in glob.glob(str(REPO / "runs/*.jsonl")):
    records += [json.loads(l) for l in Path(f).read_text().splitlines() if l.strip()]

calls = [r for r in records if r["kind"] == "call"]
print(f"{len({r['session_id'] for r in records})} sessions, {len(calls)} tool calls logged")
print("any record mentioning a search:",
      any("search" in json.dumps(r).lower() and r["kind"] != "call" for r in records))
print()
print("what the gateway saw:")
for uid, n in collections.Counter(r["tool_uid"] for r in calls).most_common(6):
    print(f"   {uid:45s} x{n}")
print()
print("ToolSearch calls visible here: 0 — they are resolved inside the client.")
"""
    ),
    md(
        """
## Stage 6 — What the log becomes for the engine

The replay harness reconstructs sessions and hands a selector **only what preceded** the
session being predicted. History is passed at *construction*, so lookahead is impossible
rather than merely discouraged.
"""
    ),
    code(
        """
from mcp_gateway_router.replay.sessions import load_sessions

sessions = load_sessions(REPO / "runs")
print(f"{len(sessions)} sessions reconstructed\\n")
for s in sessions[-3:]:
    print(f"  {s.session_id}")
    print(f"    opened   {s.opened_at.isoformat()}")
    print(f"    env      {json.dumps({k: v for k, v in s.environment.items() if k != 'repo_path'})}")
    print(f"    called   {[f'{a}/{b}' for a, b in s.called] or '(none)'}")
"""
    ),
    md(
        """
## What this notebook establishes

| Question | Answer |
|---|---|
| What does the gateway intake at point A? | project · repo · branch · hour · weekday. No prompt. |
| What differs between shadow and live? | One config field. Shadow advertises everything and logs a counterfactual; live advertises the cut. |
| Can the gateway see Claude's tool search? | **No.** It resolves inside the client, never crossing the MCP wire. |
| What can this data answer? | **Q2** — can we predict a session's tools at open. |
| What can it *not* answer? | **Q1** — prefix cost and task success, which need the client. See `arms_walkthrough.ipynb`. |
"""
    ),
]

out = REPO / "notebooks/gateway_walkthrough.ipynb"
nbf.write(nb, str(out))
print("wrote", out)
