# MVP (v0) — working gateway, one user, many sessions

_Design spec. Written 2026-08-08._

Related: [`roadmap.md`](../../roadmap.md) (P2 data plane, Gate 0) ·
[`session-handoff.md`](../../session-handoff.md) (next actions 6 and 7) ·
[`intuitions.md`](../../intuitions.md) (vocabulary) ·
[`decisions.md`](../../decisions.md)

---

## 1 · What this is

A working MCP gateway running in front of a real ~300–500 tool catalog, in the author's own
Claude Code, across many sessions and working contexts — plus two benchmarks it makes possible:
a head-to-head against **Claude's native tool search**, and a cross-session test of whether a
session's tools can be predicted at the moment it opens.

**Scope is one user, many sessions.** That boundary is load-bearing rather than incidental: it
determines which questions are answerable (§2) and rules out the product claim entirely.

**It is a decision instrument, not a product.** Where artifact quality and readability of the
result conflict, readability wins. Nothing here is built for a stranger to install.

## 2 · Two co-equal questions

The v0 asks two things. They are **not** one headline and some supporting evidence — they have
different arms, different metrics, different data, and different failure branches. Conflating
them is how the cross-session half quietly becomes an afterthought.

### Q1 · Does Claude's own tool search already moot ranking-with-a-prompt?

Gate 0, on real tools and real tasks instead of ToolRet. Single-session by nature: every arm
here operates with the task in hand.

> **Does the agent still get the job done when handed 25 tools instead of 400, and does our 25
> beat Claude's own tool search at the same token cost?**

### Q2 · Can we predict a session's tools at session open, from one person's history and context alone?

The cross-session half, and the one that carries *"is the personalization spin necessary."*
Decision point A: no prompt exists, so the only signals are history, recency, and environment.

> **At the moment a session opens — before a word is typed — how much of what that session
> will need can be predicted from everything that came before it?**

### Also produced, regardless of how Q1 and Q2 read

- **The machinery works end to end** — proxy, catalog service, selector, knapsack, exposure
  log, fail-open.
- **Three numbers currently blocking other phases**: real per-schema token costs,
  prompt-cache invalidation cost, and per-client capture rate.

### Cannot answer, and no result here may be read as evidence for it

**Whether different users need different tools.** n=1. There is no cross-seat variation in the
data to measure. This is the product claim and it needs P8. Q2 is its within-user analogue,
not a substitute for it: a strong Q2 shows that *structure exists and is exploitable*, not
that the structure differs between people.

## 3 · Non-goals

No ranker training, no bandit, no OPE, no admin console, no multi-tenancy, no auth-grain
work, no packaging or docs for external users. The eligibility gate ships as a pass-through
stub with the seam in place — single-user means there is nothing to gate.

## 4 · Architecture

One process, `mcp-gateway`, speaking MCP over stdio to the client and MCP as a client to N
upstream servers. The existing `src/mcp_gateway_router/` becomes the selection library; the
gateway adds transport and nothing else.

```
Claude Code / Agent SDK
        │ stdio (MCP)
        ▼
┌──────────────────────────────────────────────────────────┐
│ gateway/server.py     tools/list, tools/call, find_tools │
│ gateway/policy.py     arm → DecisionContext → Selector   │
│ gateway/log.py        append-only JSONL                  │
│ gateway/upstream.py   UpstreamPool: connect, namespace,  │
│                       route, health                      │
└──────────────────────────────────────────────────────────┘
        │ stdio / http (MCP)
        ▼
  github · notion · playwright · filesystem · slack · linear · sentry · postgres · …
```

**Reused unchanged:** `catalog.py`, `selector.py` (`fill_budget`), `baselines.py`,
`embedding.py`, `tokens.py`. `policy.py` is a thin adapter that builds a `DecisionContext` and
calls the existing `Selector` protocol. **If v0 requires changing the selection library's
interface, that is a signal the interface was wrong** — treat it as a finding, not a chore.

**New and load-bearing beyond v0:** `harvest.py` connects to every upstream, dumps the
aggregated catalog as a `Catalog` JSON, and runs `count_tokens` over every schema. This is
handoff next-action 6, it *is* the P2 catalog service, and it retires the flat-120-tokens
placeholder. Its output feeds both the gateway and the offline harness, so the existing
frontier gets re-priced against real schema sizes for free.

**Three things are config, never code:** upstream set, mode (`shadow` | `live`), arm (selector
+ budget). Any difference between arms other than selection is a confound.

**Fail-open is in v0**, not deferred to P10. If the selector raises or the scorer is cold,
serve the pinned core. Ten lines now, an architectural retrofit later.

**Task text reaches the gateway only when the harness injects it**, per session, before
`tools/list`. Interactive Claude Code cannot do this because no prompt exists at that point.
This is not a workaround — it is how the design encodes decision point A versus point C, and
it is what makes the arm comparisons below mean anything.

## 5 · Catalog

Install real public MCP servers until the aggregated catalog reaches ~300–500 tools. Currently
installed: `notion`, `github`, `playwright` (~85). Add filesystem, slack, linear, sentry,
postgres, aws or similar. Read-only or mock credentials are acceptable — the benchmark scores
exposure and task completion, and tasks are chosen to work within available credentials.

**This resolves open blocker 2** (fleet-of-servers vs. one-vendor) with a real setting rather
than a choice: real schemas, real token costs, real cross-server name collisions.

## 6 · Arms

Seven arms plus one subsample control. One binary; they differ only in gateway config. Budget
is expressed in tokens from measured `count_tokens` values, never in slots.

**Pinned core** throughout means a small fixed set always exposed regardless of scoring, on
the grounds that a miss is a task failure rather than an ignored suggestion. Its membership is
chosen once from harvested call frequency and held constant across all arms — if it varied by
arm it would be a confound.

| Arm | Gateway serves | Point | What it is |
|---|---|---|---|
| **A · native ToolSearch** | full catalog | client-side | The incumbent. Claude's own retrieval makes the cut. |
| **B · gateway find_tools** | pinned core + `find_tools` | C | Our progressive disclosure, `k` swept not defaulted. |
| **C · task-conditioned** | knapsack over bi-encoder scores, task text injected | C | Ceiling for ranking-with-a-prompt. Not a product. |
| **D-global · session-blind** | knapsack over all-time call frequency | A | The true no-information floor. |
| **D-recent · session-blind** | knapsack over recency-weighted history | A | Exploits session-to-session momentum. |
| **D-context · session-blind** | knapsack over a model of `P(tool \| repo, branch, cwd, hour, last-session tools)` | A | **The Q2 arm.** The closest thing to personalization that exists at n=1. |
| **R · random** | random draw at budget | — | Null control. |
| **O · oracle** | gold tools only | — | Upper bound. |

**E · expose-all eager** runs on a 10-task subsample only, to price the tax without paying it
300 times.

`D-context` is the arm the previous draft of this spec was missing, and its absence was a real
hole: §10 logs `cwd`, `repo`, `branch`, `client` and `recent_tools`, and without this arm
nothing ever conditions on them. It is also what makes the C-vs-D gap interpretable — absent
it, a win for C can't distinguish "the prompt is irreplaceable" from "we never tried the cheap
substitutes for it."

### What each comparison reads

**Q1 comparisons — single-session, task in hand:**

- **A vs C — Gate 0.** If native ToolSearch matches or beats task-conditioned selection at
  equal-or-lower measured token cost, ranking-given-a-prompt is mooted; the product rescopes
  to point A or to measurement.
- **B vs C — on-demand vs. pre-loading, retriever held fixed.** The offline sweep found
  on-demand wins at k≥25 (0.549 / 6,362 tokens vs. 0.451 / 15,960). If that reproduces on real
  tools it is the strongest result in the benchmark, and the sharpest form of the critique the
  project must answer.

**Q2 comparisons — cross-session, no prompt:**

- **D-context vs D-global — the Q2 headline.** How much environment and recency buy over a
  flat all-time prior. This is the measurement that says whether point-A prediction has any
  signal at all.
- **D-recent vs D-global — persistence.** If recency wins, session modes recur. If it does
  not, session behaviour is memoryless and the point-A story is in trouble for reasons
  unrelated to n=1.
- **C vs best-D — the heterogeneity ceiling.** The value of knowing the task, perfectly
  exploited, over the best we can do without it. Upper bound only; see §8.2.

**Spanning both:**

- **A vs D-context — the harshest pair, and the most likely to end the thesis.** Real Claude
  Code gets tool search for free. A point-A selector has to beat *that*, not beat expose-all.
- **R** — if any arm fails to separate from random, the harness is broken and nothing is
  readable.

### Two accounting rules

**Token cost is measured, never computed.** Read actual input tokens off per-turn API usage,
per arm. Native tool search is not free — tool *names* remain in context even when schemas do
not, plus the search call and its result. Computing our cost while estimating theirs
manufactures benchmark-that-cannot-lose number five, in our favour, and it is the first thing
a reader will attack.

**Report cached and uncached input tokens separately.** The arms have different cache
behaviour. This is also where the prompt-cache invalidation number comes from — open since the
start, a stated prerequisite for choosing any exploration rate, currently blocking P4.

## 7 · Metrics

**Two questions, two headline metrics, two datasets.** Neither is a supporting exhibit for the
other.

### 7.1 · Q1 headline — task success at matched measured token cost

Success alone is meaningless (expose-all wins); cost alone is meaningless (expose-nothing
wins). Arms A and B are budget-invariant single points, so "compare at a budget" is a category
error. Instead: measure A's actual prefix cost, sweep C's and D's budget until measured cost
lands there, compare success. One scalar per comparison — *at the incumbent's own cost, what
does ours achieve?*

Gate 0 statistic: `Δ = success(C) − success(A)` at A's measured cost, paired over tasks.

**Dataset:** the authored task battery (§9.1). Each task runs in a fresh, independent session,
which is correct here — Q1 is about what happens when the task is already known.

### 7.2 · Q2 headline — next-session coverage under temporal replay

Walk the real session log in chronological order. At each session open, the selector sees only
what preceded it and produces an exposed set. Score that set against the tools the session
actually went on to call.

**Metric:** fraction of a session's called tools that were exposed at its open, averaged over
sessions, at matched token cost. Report by budget, as a curve.

Q2 statistic: `Δ = coverage(D-context) − coverage(D-global)`, paired over sessions.

**Dataset:** the session replay log (§9.2) — every session collected, not 40 authored ones.
Costs no model calls, so it runs over everything and this is where the statistical power is.

**Why replay rather than a task battery.** A battery of independent tasks cannot express the
thing Q2 is about: that session *n* is predictable from sessions 1..*n*−1. Replay is
cross-session by construction, respects chronology for free, and makes the time-split
mandatory rather than a discipline someone has to remember.

### 7.3 · Two tiers within Q1

**Screen — `satisfied`.** Did the exposed set contain the gold tools. Deterministic, no model
call, free. Run over every arm × budget × task. Directly comparable to the existing frontier.

**Confirm — task success.** Deterministic assertion on a verifiable answer. Expensive, low-n,
and the only metric that observes whether the agent actually did the thing.

`satisfied` cannot be the Q1 headline — it produced two of the four known bad benchmarks. As a
screen it is ideal, and **where the tiers disagree, that gap is a finding**: right tools
exposed but task still failed means the agent was confused by 400 tool names, or the round
trip cost it, or the schemas were ambiguous. None of that is visible to `satisfied`, and all
of it matters for something sitting in the critical path.

Q2 has no confirm tier in v0. Replaying a session under a counterfactual tool set would need
the model back in the loop with no deterministic assertion available, which is a judge by
another name. Q2's coverage metric is therefore a proxy for success, and the spec says so
rather than pretending otherwise.

### 7.4 · Secondary

Turns to completion · unexposed-tool call attempts · wall-clock · cached vs. uncached input
tokens.

### Correctness bar — pass/fail, gates shipping, not comparative

Fail-open survives a chaos test (kill the scorer mid-session, client still receives a working
set) · passthrough p99 under 5 ms · zero dropped or misrouted calls over a week of real use.

## 8 · Pre-registration

Written down before the first run. Not adjustable afterward.

1. **The Gate 0 margin, as a number.** If native ToolSearch is within it of task-conditioned
   selection at equal-or-lower measured cost, Gate 0 fails and the product rescopes.
2. **The ceiling asymmetry.** A per-session oracle reads the future; a real ranker sees only
   pre-session signal. Therefore: a **small** ceiling is decisive and kills the ranker; a
   **large** ceiling is permission to continue and nothing more. It must not be read as
   evidence the value is recoverable.
3. **Ceiling effects checked first.** If the global arm already scores near the metric's
   maximum, the gap is bounded by arithmetic rather than by absent heterogeneity. Read the
   global arm's absolute level before reading any gap.
4. **The ceiling is a curve over budget, not a number.** Quoting one gap at one budget is the
   same category error as quoting progressive disclosure at one `k`.
5. **Minimum detectable effect, computed before the suite is authored.** Paired binary
   outcomes at n=40 resolve roughly 20–25 points; with 5 repetitions per task turning each
   into a continuous 0–1 score, roughly 13. Five-point resolution needs on the order of 250
   tasks. **If the margin in (1) is below the MDE, the suite is too small and that must be
   fixed before running, not discovered after.** The house failure mode has a sibling: a
   benchmark that cannot *resolve*.
6. **Null arm must not separate.** If random is indistinguishable from a real arm, or
   separates in the wrong direction, the harness is broken and no result is readable.
7. **The Q2 margin and stopping rule, as numbers.** What `coverage(D-context) −
   coverage(D-global)` has to reach to count, and how many sessions get collected before the
   result is read. Both fixed in advance — otherwise collection continues until the number
   looks good, which is the same failure as a movable margin wearing a different hat.
8. **Q2's proxy status is declared, not discovered.** Coverage is not task success. A
   `D-context` win means the selector would have kept the tools the session used; it does not
   establish the session would have succeeded on a cut set. No Q2 result may be reported as a
   success-rate claim.

## 9 · The two datasets

### 9.1 · Authored task battery — for Q1

~40 tasks minimum, sized upward by (8.5). **Drawn from shadow-mode logs, not authored at a
desk** — inventing the task distribution relocates the house failure mode from the personas to
the task mix.

Each task carries: prompt text as the user would actually type it, a verifiable answer,
environmental context (`cwd`, `repo`, `branch`), and hand-labelled required tools so
`satisfied` stays comparable to the existing frontier. All read-only, all deterministically
assertable by exact string, number, or set match. No LLM judge, no write credentials.

**Leakage guard, reusing the existing measurement.** Compute gold-tool-description vocabulary
overlap for every task prompt, the same way ToolRet queries measured 7.4% and instructions
20.3%. Any task above the query-level baseline is describing its own answer; rewrite it. This
makes "did we write tasks that flatter retrieval" an assertion rather than a hope.

### 9.2 · Session replay log — for Q2

Every session captured in shadow mode, in chronological order, each carrying its opening
context (`cwd`, `repo`, `branch`, `client`, `ts`) and the ordered list of tools it went on to
call. No authoring, no gold labels, no hand-curation — the session's own calls *are* the
labels.

**Chronology is enforced by the harness, not by discipline.** A replay that only ever shows
the selector sessions 1..*n*−1 when predicting session *n* cannot leak the future, which is
the failure random splitting invites. This is why replay replaced the earlier draft's
"time-split the task suite" instruction: the split is now structural.

**Sizing.** A week of ordinary use is a few dozen sessions, which is thin. Coverage is a
continuous per-session fraction rather than a binary, so it carries more information per unit
than task success does — but if `D-context` vs `D-global` lands inside the noise, the answer
is more weeks of collection, not a stronger claim. Decide the stopping rule before reading
(§8.7).

**Known bias, stated up front.** Sessions were collected in shadow mode, so every tool was
available. What a session *called* under full availability is not necessarily what it would
have called under a cut set — the agent may have used a tool precisely because it was there.
Coverage therefore measures "would we have kept what you used," which is the right question
for a floor and the wrong one for a ceiling. It cannot be fixed without live-mode collection,
and it should be re-checked once the live flag has run.

## 10 · Exposure log

JSONL, append-only, local. Three record types.

**Decision** — `session_id`, `ts`, `arm`, `decision_point` (A|B|C), `catalog_hash`,
`selector_version`, `budget_tokens`, context features (`cwd`, `repo`, `branch`, `client`,
`recent_tools`), `n_candidates`, `exposed: [{tool_uid, score, propensity, token_cost}]`.

**Call** — `session_id`, `ts`, `tool_uid`, `was_exposed`, `status` (ok|error), `latency_ms`.

**Turn** — `session_id`, `ts`, `input_tokens`, `cached_input_tokens`, `output_tokens`.

Three fields exist for later phases and would be unrecoverable if omitted:

- **`propensity`** — 1.0 for every deterministic v0 arm. Recorded anyway: it is the field's
  *absence* that is non-retrofittable, not its value.
- **`was_exposed`** — the miss signal, and what capture rate is measured from.
- **`cached_input_tokens`** — the prompt-cache invalidation number, currently blocking P4.

## 11 · Sequence

**Task 0 — gates everything else.** Verify native tool search can be enabled and observed under
the Agent SDK, with per-turn token usage readable. A "no" reshapes the benchmark: arm A
degrades to expose-all-eager, a much weaker and more expensive competitor, and the head-to-head
silently becomes one we cannot lose. Nothing is built on top of this until it is confirmed.

**Task 0b — capture-rate probe.** Deliberately expose a set excluding a tool the task needs;
check whether the attempted call reaches the gateway or is filtered client-side. If Claude Code
filters silently, `was_exposed` is always true, the miss signal is empty, and the loop that is
supposed to be the differentiator does not exist on this client. Prerequisite, not a result.

Then:

1. Install upstream servers to ~300–500 tools.
2. `harvest.py` → catalog JSON + `count_tokens`. **Re-price the existing offline frontier;
   expect selector ordering to change once schema size stops being uniform.**
3. Gateway: `upstream` → `server` → `policy` → `log`. Shadow mode. Fail-open test from day one.
4. Run it in daily Claude Code, collecting, until the §8.7 stopping rule is met.
5. **Q2 first** — replay harness, `D-*` arms, coverage curves. Needs no model calls, no
   authored tasks, and no Agent SDK, so it delivers a reading as soon as step 4's data exists.
6. **Q1 second** — author the battery from the captured sessions, run the leakage check,
   implement A/B/C, run the screen tier across budgets and the confirm tier at matched costs.
7. Read Q1 and Q2 separately against §8.

**Q2 before Q1 is deliberate.** Q2 runs on data step 4 already produces and costs nothing per
additional arm, so it turns the collection week into a period with a result at the end rather
than a wait. Q1 is the expensive half — authored tasks, model runs, repetitions — and it is
also the half gated on task 0.

**Two costs stated plainly.** Step 4 is wall-clock time before any Q1 number exists; that is
the price of not inventing the task distribution. And step 2 may reorder the existing frontier
results, which is a finding to publish rather than an inconvenience.

## 12 · Risks

| Risk | Consequence | Mitigation |
|---|---|---|
| Native tool search not controllable under the Agent SDK | Q1's headline arm invalid | Task 0, gates Q1 — Q2 unaffected |
| Client filters unexposed calls | Miss signal empty; the loop thesis has no evidence channel on this client | Task 0b |
| Suite underpowered | Q1 chart reads as noise | MDE computed before authoring (8.5) |
| Too few sessions collected | Q2 lands inside the noise | Stopping rule fixed in advance (8.7); collect longer, don't weaken the claim |
| Shadow-mode availability bias | Q2 coverage measures a floor, not a ceiling | Declared in §9.2; re-check after the live flag runs |
| Q2 read as a success-rate claim | Overstates a proxy metric | Declared non-negotiable in (8.8) |
| Task suite flatters retrieval | Benchmark-that-cannot-lose number five | Leakage check (§9.1) |
| Upstream servers slow to boot (playwright et al.) | Per-task boot cost dominates wall-clock | Warm pool in `UpstreamPool` |
| Credentials unavailable for some servers | Catalog smaller than planned | Tools count toward the catalog even if uncallable; tasks chosen within available creds |

## 13 · Open questions

- **The Gate 0 margin (8.1) and the Q2 margin plus stopping rule (8.7)** — all three must be
  numbers before their respective runs. They are circular with sizing: the margin has to sit at
  or above what the data can resolve, and how much data there is follows from the margin. One
  decision each: *what size difference would change what you build?*
- What `D-context`'s model actually is. A count-based conditional over a handful of discrete
  buckets (repo × hour-bucket) is the honest starting point at a few dozen sessions; anything
  learned will overfit at this n. Worth pinning before implementation so the arm doesn't
  quietly become a tuned model competing against untuned baselines.
- Whether shadow-mode collection can begin before the full catalog is installed. It would
  shorten the critical path considerably, at the cost of early sessions having a smaller
  catalog than later ones — which biases Q2's replay across the boundary.
