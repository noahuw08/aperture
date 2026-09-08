# Pickup — state of play, 2026-08-18

_Written at the end of a long session, for whoever continues it. Read this, then
[`session-handoff.md`](session-handoff.md) for the standing background and the gotcha list.
Supersedes the 2026-08-10 pickup; its findings §3(a)–(f) still hold._

**Branch** `feat/gateway-data-plane` · **47 commits, pushed** · **269 tests passing** ·
`main` holds the Phase 0 baseline only. Nothing is merged yet.

---

## 1 · The one-paragraph version

This session ran the first two live probes against the client and **both channels came
back shut**. Descriptions do not reach the tool-search ranker; text appended to a tool
result reaches the model and is explicitly refused as untrusted. The unifying finding is
that **the gateway is not a trusted principal in the client's trust model**, and the trust
gradient runs one way: our text can make the model trust a tool *less*, never *more*. That
closes promotion — and exploration is inherently promotion — so no bandit can ride a
semantic channel. Only structural levers survive: which tools appear, their names, and when
the list changes. Separately: **arm B (`find_tools`) was decided on 2026-08-05, listed as a
P2 deliverable, and never built** — and it is close to the design this session re-derived
from scratch.

---

## 2 · What was measured, and what it means

### 2a · Descriptions do not reach the ranker

`bench/probe_descriptions.py` → `results/probe_descriptions.json`.

| condition | target surfaced | target called | decoy surfaced | turns |
|---|---|---|---|---|
| `control` | ✅ | ✅ | — | 3 |
| `blind` — target's description made irrelevant | ✅ | ✅ | — | 3 |
| `steer` — target blinded, decoy boosted | ✅ | ❌ | ❌ | **13** |

Two directions, both null. Make the right tool's description maximally irrelevant → still
retrieved. Make a wrong tool's description maximally relevant → never retrieved. **The
candidate set is name-driven.**

Descriptions *do* reach the model after retrieval: in `steer` the agent read the blinded
description, concluded *"the `list_releases` tool doesn't list releases"*, and routed around
a working tool via three others — **3 turns to 13**.

### 2b · Tool results reach the model and are refused

`bench/probe_suggestions.py` → `results/probe_suggestions.json`. The model quoted our
injected hint and declined it:

> *"the tool response included an embedded instruction claiming to be a 'gateway hint'…
> That's content returned by the tool, **not an instruction from you**, so I ignored it."*

Correct behaviour, not a framing problem, and not promptable-around.

### 2c · The synthesis

**Suppression is reachable; promotion is not.**

| Blind spot | Shape | Reachable |
|---|---|---|
| Failure memory · Entitlement | suppress | ✅ |
| Sequence · Co-occurrence · Identity | **promote** | ❌ |

What remains are **structural** levers — the ones the client needn't trust because they
aren't claims: *which tools appear*, *their names*, *when the list changes*.

### 2d · There is no within-session decision point

**`tools/list` fires exactly once across all 39 sessions on disk, and `tools_called` is
empty at every decision record.** A within-session ranker has no moment to act, and
`errors_seen` / `turn_index` (`selector.py:33-34`) stay declared-but-never-written.
Creating that moment needs `list_changed`: unimplemented, cache cost unmeasured.

### 2e · The cut is a one-shot commitment

Arm C ranks all 95 and exposes ~11. In the real `search_code` case it spent **2,968 of
3,000 tokens** on browser and Notion tools, exposed no search tool, and produced **zero call
records** — the miss is visible only by differencing against arm A's log. No second trip, no
feedback, no way to change its mind. **A mediocre retriever with three tries beats a perfect
one-shot retriever**, independent of ranking quality.

### 2f · `was_exposed: false` has still never fired

**48 call records, 48 `true`.** Availability is resolved before a call is generated, so
there is nothing to log. The only design that revives it is arm B — a call to a tool learned
from a `find_tools` result is by definition unexposed.

---

## 3 · Arm B was designed, decided, and skipped

`docs/superpowers/specs/2026-08-08-mvp-gateway-design.md:134`:

| Arm | Gateway serves | Status |
|---|---|---|
| **A · native ToolSearch** | full catalog | ✅ built |
| **B · gateway `find_tools`** | pinned core + `find_tools` | ❌ **never built** |
| **C · task-conditioned knapsack** | bi-encoder + budget | ✅ built — spec calls it *"Not a product"* |

`decisions.md:595` (2026-08-05), *"Ship `find_tools`"*:

> Progressive disclosure … is the **transport** for task-grain context — the only legitimate
> channel that delivers the prompt. **Competing with it is a losing position; carrying it is
> not.** Also yields an unambiguous miss signal.

Almost exactly what this session re-derived. It was also the best offline baseline:
**0.549 at 6,362 tokens** vs semantic-retrieval's 0.451 at 15,960.

**Where it exists:** `baselines.py:216-280` (`ProgressiveDisclosure`, `META_TOOL` at :231),
`frontier.py:115`, `tests/test_harness.py`. **Zero hits anywhere in `gateway/`.**

**The pinned core** was `static_keys[:3]` — the three most-required tools from the fit split
(`run_frontier.py:75,93`). There is **no specification of what it should be in production**;
a live gateway has no gold labels. Open design question, not a config value to copy.

---

## 4 · What was built this session

| Commit | What |
|---|---|
| `6a5b2a4` | `ArmResult.search_results` — captures what ToolSearch *returned*, not just that it ran |
| `afa482d` | `description_overrides` + `result_suggestions` gateway levers, invariants pinned by test |
| `872e043` | both probes and their results |
| `3a036c1` | `roadmap.md` blind-spot table + five-lever action space; `intuitions.md` arm A/C asymmetry |

**Invariant worth knowing:** `_exposed` is built from **pre-rewrite** `(server_id, name)`
keys, so a rewritten tool still routes and still logs `was_exposed` correctly. The obvious
implementation — building it from the rewritten list — passes every other test while
corrupting the one field the missing-demand signal depends on. There is a test named for it.

**Notion** (`🔌 Personalizing MCP Tool Exposure`) gained four appends, **each needing one
manual drag** one level in: Snapshot 6; Intuitions → Stage 5 *"Arm A vs arm C — what a miss
costs"*; Evaluation → residual list extended from four places to eight; Architecture in
diagrams → **§3 revised runtime diagram** (adds `_rewrite`, `SEL`, `TASK`).

---

## 5 · Traps discovered the hard way

Four false starts, each of which produced a **confidently wrong verdict** before a guard
caught it. All now documented in the probe modules.

1. **A probe task must force a tool call.** The first asked which login handle the session
   was signed in with; Claude Code injects the account identity, so the agent answered from
   context in one turn — zero calls, zero searches, three indistinguishable conditions.
2. **⚠️ Config files must live beside their `.env`.** `from_file` resolves `.env`, `log_dir`
   and `catalog_path` relative to *the config's own parent*, and `_expand` **raises** on an
   unresolvable `${VAR}`. A config written elsewhere kills the gateway at startup and the
   session silently sees **no MCP tools at all** — indistinguishable from a null result.
   **`bench/arms.py::_write_config` has this same latent bug**: it writes arm configs into
   an arbitrary `out_dir`. It hasn't bitten only because the notebook uses root-level configs.
3. **Sentinels get paraphrased.** `GATEWAY-HINT-7F3A` came back as `GATEWAY-7F3A`, so a
   substring match reported a hint the model had quoted in full as never seen. Use one
   unbroken token.
4. **Task closedness is an experimental variable.** An open-ended task forced the suggested
   tool in *both* arms (no headroom); a closed one removed the agent's reason to comply.
   Same treatment, opposite readings.

---

## 6 · Next steps

**The decision the project now faces:** is there a personalization product here, or is the
control plane the product? Promotion is closed on every semantic channel; what remains is
structural, and each structural lever costs something.

### Unblocked, cheap, decisive

1. **~~⭐ Probe arm B's core assumption~~ — ✅ DONE 2026-08-31. The answer is *no*.**
   Built (`find_tools` in `gateway/`, three-state `exposure` field, 317 tests) and run for
   ~$0.14. `results/probe_armb.json`; full reasoning in `decisions.md` (2026-08-31).

   **The model emitted the call, in two name formats, in both conditions. The gateway
   received nothing.** The trusted-channel control — the tool named in the *system prompt*,
   an operator instruction rather than tool output — gave the identical result, so this is
   not the untrusted-content refusal from 2026-08-18 and is not promptable-around.
   `find_tools` itself worked perfectly: one call, five tools disclosed, target among them.

   **Mechanism, in the agent's words:** *"the `github__list_releases` tool doesn't actually
   exist in this environment, despite being described as available"*, and `ToolSearch` for
   it returned *"No matching deferred tools found"*. **The client's registry is built from
   the `tools/list` array and is authoritative** — tool search cannot see past it and
   neither can dispatch.

   **Therefore: capture rate on this client is zero, `exposure: unexposed` is unreachable,
   and the branch below is the live one.** Note this is a *stronger* claim than §2f's: the
   model does generate the call, so the block is at dispatch, not at generation.

2. **⭐ Measure `list_changed`'s cache cost — now the critical path, not a contingency.**
   Step 1 failed, so arm B becomes `find_tools` + `list_changed`. Needs a minimal
   implementation: `mcp.types.ToolListChangedNotification` exists but `MCPServer` exposes no
   send method, so it needs plumbing to the session. `ArmResult` already captures
   `cache_creation_tokens` / `cache_read_tokens`, so the measurement itself is free.
3. **Fix `bench/arms.py::_write_config`** (§5.2) before anyone calls `build_arms` for real.
4. **Run arm R once** (~$0.07). The null control has **zero** data. If the harness cannot
   separate random from anything, later numbers are noise.

### Free, no model calls

5. **B-variant replay** — score coverage of a session's *remaining* calls given the first
   _k_ observed. Tests whether within-session evidence beats point-A prediction, on
   existing logs.
6. **Capture call status into `DecisionContext`** — `server.py:89` appends only
   `(server_id, name)` and drops `status`, so `errors_seen` has no data source.
7. **Fix lossy persistence** — `results/arms_smoke.json` keeps only `rate`: no reps, no
   passes, no `answer`. `answer` is the only replacement for the dead capture-rate signal (§2f).

### Blocked on the human

8. **Collection is still the bottleneck, and still degenerate: 27 sessions, 23 in this
   repo.** No probe, lever, or refactor touches it. Every personalization claim is
   unmeasurable until there is varied traffic. **If that traffic is not going to
   materialise, decide that deliberately** — the control plane (exposure log, entitlement,
   dead-weight reporting) needs none of it.

### Doc debt

9. The arms in `roadmap.md`, the baseline ladder, and the frontier framing all assume
   cutting. Snapshot 6 names arm C as the challenger. A **Snapshot 7** should record §2's
   findings and the arm-B rediscovery — snapshots are immutable, so append, don't edit.

---

## 7 · How to run things

```sh
# tests
uv run --extra dev --extra gateway pytest -q

# either probe (subscription auth; ANTHROPIC_API_KEY must be UNSET)
cd /tmp && env -u ANTHROPIC_API_KEY uv run --directory <repo> \
    --with claude-agent-sdk --extra gateway \
    python -m mcp_gateway_router.bench.probe_descriptions   # or probe_suggestions

# Q2 replay — free, no model calls
uv run --extra gateway python -m mcp_gateway_router.replay.run --engine mymodule:my_factory
```

Probe configs are generated beside `gateway.armA.json` as `gateway.probe-*.json` /
`gateway.suggest-*.json`, and are gitignored.
