# Pickup — state of play, 2026-08-10

_Written at the end of a long session, for whoever continues it. Read this, then
[`session-handoff.md`](session-handoff.md) for the standing background and the gotcha list._

**Branch** `feat/gateway-data-plane` · **36 commits** · **223 tests passing** · `main` holds
the Phase 0 baseline only. Nothing is merged yet.

---

## 1 · What the project is now asking

Two questions, deliberately co-equal. Conflating them is how the second one gets lost.

| | **Q1 (Gate 0)** | **Q2** |
|---|---|---|
| Question | Does Claude's own tool search already moot ranking-with-a-prompt? | Can we predict a session's tools *at open*, before any prompt? |
| Decision point | C — task in hand | A — no prompt exists |
| Metric | task success at matched measured cost | coverage under temporal replay |
| Cost | model runs (on subscription, not API credit) | **free** |
| Status | harness complete; needs tasks + a margin | harness complete; needs sessions |

**The ordering changed during this session.** Q1 is now load-bearing and should be read
first — see §3.

---

## 2 · What is built

```
src/mcp_gateway_router/
  gateway/     the data plane — proxies 95 tools (github 47 · notion 24 · playwright 24)
               over stdio + HTTP, cuts to a budget in live mode, fails open, logs
               everything to runs/{session_id}.jsonl
  replay/      Q2 — walks the session log in time order, scores any point-A selector.
               Baselines: random (null), d-global, d-recent, per-session oracle.
  bench/       Q1 — Agent SDK harness. Arms, task battery format, deterministic
               scoring, matrix runner with paired stats and an MDE guard.
  harvest.py   control plane — tools/list + count_tokens -> results/catalog.json
notebooks/     gateway_walkthrough (free) · arms_walkthrough (model cells gated)
```

**Two notebooks exist specifically to remove ambiguity** that prose kept reintroducing:
shadow vs live, and our selection vs Claude's tool search. They run the real system.
Use the `mcp-gateway (.venv)` kernel.

---

## 3 · The findings that changed the argument

Recorded in the Notion decision log (newest first) and in `gateway-setup.md`.

**a. Tool search defers MCP schemas, so the token-tax premise is weak at this catalog
size.** The project was founded on schemas being a recurring per-call tax. Deferral means
cutting 95 → 25 saves far less than assumed. ✅ **Re-measured and confirmed — §6.** The
cut is worth **~310 tokens**; loaded-vs-deferred is a **35×** spread.

**b. Capture rate is NEGATIVE.** With a tool deliberately withheld, the agent ran
`ToolSearch`, saw it was unavailable, completed via `Bash`, and said so. **Zero call
records reached the gateway.** `was_exposed: false` cannot be collected here, because
availability is resolved *before* a call is generated. "Measure demand for tools you
aren't serving" — named in the roadmap as core differentiation — has no channel on this
client. The replacement signal is the agent *narrating the workaround*, which lives in
model output: **Q1-observable, Q2-blind.**

**c. Q1 needs no API credit.** The Agent SDK authenticates through the Claude Code CLI's
credential (verified with `ANTHROPIC_API_KEY` unset). The zero account balance gates
`count_tokens`, not the benchmark. Cost is ~$0.07 per 3-turn run, roughly 5× below the
first estimate.

**d. Together, (a) and (b) move two of the three original pillars.** What survives is
**decision point A** (tool search cannot run before a prompt) and **selection quality**
(fewer searches, fewer turns, fewer wrong picks). Both are Q1 questions — which is why
Q1 now comes first.

**e. Real catalog: 39× schema-size spread**, median 1,001 chars. `fill_budget` is a
genuine knapsack. Notion is 25% of tools and **54% of bytes** — cost concentrates by
server, which no earlier doc said.

**f. `fill_budget` was rank-greedy, not density-greedy.** `algorithm.md` specifies
`p·v/c`; the code did not divide by cost. Identical under the old flat-120 placeholder,
divergent at 39×. ✅ **Fixed 2026-08-11 as an opt-in `scores` argument** — it could not
simply be switched, because no caller has a value term to divide by, and density on
uncalibrated scores is not arithmetic. Gap at a 3,000-token budget: **27 tools vs 13**.
See `decisions.md`.

---

## 4 · What is blocked, and on whom

**On the human:**

1. **Collect sessions.** Registration is done — `gateway` is the only MCP server at user
   scope, all three upstreams proxied. Just work across varied projects. **8 sessions so
   far, mostly test traffic.** Q2 replays this; Q1's task battery is authored *from* it.
   Watch the `project` field: one project means `D-context` degenerates to `D-global`.
2. **Three numbers.** The Gate 0 margin, the Q2 margin, and a collection stopping rule.
   None block collection; all block *reading* it. The Gate 0 margin also sizes the task
   battery.

**On code (unblocked, no dependencies):**

3. ~~Fix `fill_budget` to density-greedy~~ — done, §3(f). **What's left of it:** thread
   scores from the baselines that have them. Only the frequency-based ones (`d-global`,
   `d-recent`, `popularity`) have scores that are legitimately probabilities; cosine
   similarity needs calibration first. This moves recorded frontier numbers, so it wants
   a re-run, not an edit.
4. ~~Re-measure the deferred-tool prefix cost~~ — done, §6.

---

## 5 · How to run things

```sh
# tests
uv run --extra dev --extra gateway pytest -q

# harvest the catalog (add --extra tokens --count-tokens once the account is funded)
set -a; . ./.env; set +a
uv run --extra gateway python -m mcp_gateway_router.harvest --config gateway.json --out results/catalog.json

# Q2 — free, no model calls
uv run --extra gateway python -m mcp_gateway_router.replay.run --engine mymodule:my_factory

# Q1 gate check (subscription auth; ANTHROPIC_API_KEY must be UNSET)
cd /tmp && env -u ANTHROPIC_API_KEY uv run --directory <repo> \
  --with claude-agent-sdk --extra gateway python -m mcp_gateway_router.bench.task0
```

**Writing an engine** — a factory taking prior sessions, returning the existing `Selector`
protocol:

```python
def my_factory(history):            # Sequence[SessionRecord]
    class Engine:
        name = "my-engine"
        def select(self, context, catalog, budget, counter):
            # context.environment -> project/repo/branch/hour/weekday
            # context.task is None at point A
            return [...]            # list[Tool]
    return Engine()
```

---

## 6 · ✅ The contradiction is resolved — finding (a) holds

_Closed 2026-08-10. Full writeup in `gateway-setup.md` § *Probe results* 4._

The two `/context` readings were measuring different things. **34.3k is the schema mass
held back, not a charge against the prefix.** What deferral actually costs is the name
list: 95 tools = 3,858 chars ≈ **420 tokens**, against 135,514 chars ≈ 34-39k if loaded.
A **35× spread**.

The probe already contained the independent check: **dropping 91 of 95 tools moved the
prefix by 405 tokens**, and the full 95-tool prefix (31,893) is smaller than the schema
mass alone. The schemas were never in the prefix.

So cutting 95 → 25 saves **~310 tokens**. There is no token argument for cutting at this
catalog size, Gate 0 stays framed on selection quality, and §3(a) needs no revision.

```sh
uv run python -m mcp_gateway_router.deferral --catalog results/catalog.json
```

⚠️ `count_tokens` is still blocked (zero balance), so the *loaded* absolutes are
chars/token estimates. The 35× ratio is exact and the deferred side is probe-anchored, so
the conclusion doesn't move — but re-run once funded.

---

## 7 · Traps that already cost time

Full list in `session-handoff.md` § *Gotchas*. The five most expensive:

- **`claude mcp list` is the only trustworthy check.** Config files lie — scopes merge
  (user / local / `.mcp.json`), and `~/.mcp.json` applies to *every* subdirectory. This is
  why an early "isolated" test directory was not isolated, and it invalidated a round of
  data.
- **`env=None` on `StdioServerParameters` is not "inherit".** The MCP SDK substitutes a
  sanitised default (`HOME/LOGNAME/PATH/SHELL/TERM/USER`). Launching the gateway through
  it without `spec.env` **silently serves the default config**.
- **An MCP session must be owned by one task** — anyio cancel scopes. `runner.py` parks
  the contexts in a dedicated task; don't "simplify" it to an `AsyncExitStack`.
- **`ToolSearch` is a client `ToolUseBlock`**, not a `ServerToolUseBlock`. Instrumenting
  the SDK's server-tool enum counts zero searches.
- **Notion `update_content`:** leading tabs on the first line of `new_str` make the whole
  edit a **silent no-op**; raw `<table>` markup kills it entirely; new blocks land at
  `anchor_depth − 1 + tabs`, so every new snapshot needs one manual drag.

---

## 8 · Where the writing lives

- **Notion** *🔌 Personalizing MCP Tool Exposure* — decision log (newest first),
  Architecture snapshots 3–5, an intuitions glossary, and two mermaid diagrams under
  *🧭 How it actually fits together*. Snapshots 4 and 5 each need one manual drag out of
  the intro callout.
- **Repo** — `gateway-setup.md` (setup, measured catalog, probe results),
  `intuitions.md` (plain-language glossary), `algorithm.md` (target ranker),
  `superpowers/specs/2026-08-08-mvp-gateway-design.md` (the v0 spec),
  `superpowers/plans/2026-08-08-gateway-data-plane.md` (plan 1, complete).
