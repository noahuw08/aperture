# Session handoff — start here if you're picking this up cold

_Written 2026-08-07. Supersedes nothing; [`handoff.md`](handoff.md) is the original
inbound brief and is still the best statement of the background argument._

## Read this first, in this order

0. **[`PICKUP.md`](PICKUP.md) — START HERE.** State of play as of 2026-08-10: what is
   built, what the findings changed, what is blocked, and one open contradiction that
   should be resolved before building further.
1. **This file** — standing background, and the gotcha list that will save you a day.
2. [`roadmap.md`](roadmap.md) — the destination, Phase 0 → GA, every gate with a failure
   branch. Read backwards from GA.
3. [`decisions.md`](decisions.md) — newest first. **Read at least the top five entries.**
   Corrections spawn new entries rather than editing old ones, so the top of the file is
   the current position and lower entries may be superseded.
4. [`plan.md`](plan.md) — build detail for Phases 0–7.
5. [`algorithm.md`](algorithm.md) — the ranker itself, stage by stage. Target design for
   P5–P6; read it before touching anything in Stage 3 of the roadmap.
6. The Notion doc — *🔌 Personalizing MCP Tool Exposure* — for the strategy argument.
   Sections `🎯 The problem`, `🗺️ Roadmap`, `🔬 Evaluation`, `🏗️ Architecture & flow`.

---

## What this is

A proxy between an MCP client and a fleet of MCP servers that decides **which subset of
tools to expose per request**, under a token budget. Tool schemas sit in the prompt prefix
on every model call, so a large catalog is a recurring tax — paid on the *customer's* bill,
not the vendor's.

**The differentiation is not retrieval.** Semantic tool retrieval is commodity (Kong's
roadmap, Bedrock AgentCore, several papers). What nobody has is the closed loop: propensity
logging, an exploration floor, a calibrated knapsack, and measurement of demand for tools
you *aren't* serving. If you find yourself pitching "semantic retrieval over tools," you've
lost the thread.

## Where things stand

> ### ⚡ Updated 2026-08-09 — read this box before the Phase 0 material below
>
> **The data plane exists and runs.** 176 tests passing, ~24 commits on
> `feat/gateway-data-plane`. Three things are built that this file's later sections
> predate: an **MCP gateway** proxying 95 real tools (github 47 · notion 24 ·
> playwright 24) over stdio and HTTP; a **Q2 replay harness** that scores any point-A
> selector against collected sessions offline; and a **Q1 Agent SDK harness** whose
> gate (Task 0) passes. See [`gateway-setup.md`](gateway-setup.md) and the Notion
> Decision log for the reasoning.
>
> **Four findings that change the argument, newest first:**
>
> 1. **Tool search defers MCP schemas at ≈ 0 prefix cost.** Measured with `/context`
>    on a real 95-tool session: total 10.9k, and MCP tools appear in *no* category —
>    listed as "loaded on-demand". **The token-tax premise does not hold on this
>    client at this catalog size**; cutting 95 → 25 saves approximately nothing. The
>    engine's remaining value (retrieval precision, fewer round trips, fewer wrong-tool
>    picks) is **invisible to coverage**, which makes Q1 — not Q2 — load-bearing.
> 2. **Q1 needs no API credit.** The Claude Agent SDK authenticates through the Claude
>    Code CLI's own credential (verified with `ANTHROPIC_API_KEY` unset). The blocker
>    on the account balance gates `count_tokens`, not the benchmark.
> 3. **Real catalog measured: 39× schema-size spread**, median 1,001 chars. Enough that
>    `fill_budget` is a genuine knapsack rather than a top-K in disguise. Notion is 25%
>    of tools and 54% of bytes — **cost concentrates by server**, which no earlier doc
>    said.
> 4. **`fill_budget` is rank-greedy, not density-greedy.** `algorithm.md` specifies
>    `p·v/c`; the code does not divide by cost. Identical under the old flat-120
>    placeholder, divergent at 39×.
>
> **The single blocker on everything: collection has not started.** `github` and
> `playwright` are still registered directly in Claude Code, so real calls bypass the
> proxy and the log stays empty. Q2 replays that log; Q1's task battery is authored
> from it. See *Blocked on you* below.

**Phase 0 (falsification harness). Three of four gate blockers cleared. 61 tests passing.**
_(Historical — superseded by the box above. The frontier numbers below are still the
best offline reading, but they assume a flat 120 tokens per tool.)_

The harness runs on real data — ToolRet, 37,292 tools, 101 `apibank` queries (50 fit / 51
eval). It produces a frontier chart, a stage-by-stage notebook, and a calibration report.

| Selector | Best satisfied | Tokens |
|---|---|---|
| oracle | 1.000 | **193** |
| popularity | 1.000 | 15,960 |
| static-set | 0.529 | 3,000 |
| semantic-retrieval | 0.451 | 15,960 |
| progressive-disclosure (k=10) | 0.392 | 1,656 |
| expose-all | 1.000 | 4,475,040 |

> ⚠️ **Read that table with the two caveats below, or don't read it.** Both existential
> baselines move further on configuration choices nobody had justified than they do
> against each other.
>
> _Corrected 2026-08-07: `semantic-retrieval` was previously listed at 24,000 tokens. The
> measured value in `frontier_bienc.json` is 15,960._

### Setting 1 — progressive disclosure's `k`

How many schemas `find_tools` returns. Swept 2026-08-07, `results/frontier_pdk.json`:

| k | satisfied | tokens |
|---|---|---|
| 1 | 0.176 | 598 |
| 3 | 0.294 | 833 |
| 10 *(the old default)* | 0.392 | 1,656 |
| 25 | 0.529 | 3,421 |
| 50 | 0.549 | 6,362 |

**At k ≥ 25 progressive disclosure dominates `semantic-retrieval`** — 0.549 for 6,362
tokens against 0.451 for 15,960 — while using the *same retriever*. PD pays only for its k
retrieved schemas plus core; `semantic-retrieval` spends the whole budget pre-filling with
a ranking that is mostly wrong. **Exposing the retriever's top-k on demand beats
pre-loading its ranking** — a sharper form of the existential critique than any doc
currently states. The knee is k=25, which also happens to tie `static-set` at comparable
cost (3,421 vs 3,000).

Two mechanical facts. `round_trips` is 0.980 at *every* k, because `select()` only ever
exposes the meta-tool plus 3 core tools, so PD recovers on 98% of examples regardless. And
PD is **budget-invariant** — a single point on the (tokens, satisfied) plane, not a curve —
so comparing it to other selectors "at a budget" is a category error.

### Setting 2 — query vs. query + ToolRet `instruction`

`results/frontier_inst.json`, run via `--retrieval-instruction`:

| Selector | query only | w/ instruction |
|---|---|---|
| `semantic-retrieval` | 0.451 | 0.549 |
| `progressive-disclosure` (k=10) | 0.392 | **0.667** |

**The frontier is right to ignore the instruction, and there is now evidence rather than an
assertion.** Measured over the 172 (query, gold tool) pairs: the instruction contains
**20.3%** of the gold tool's description vocabulary against the query's **7.4%** — 2.7× the
leakage. ToolRet's instructions are written to describe the tools to be retrieved
(*"retrieve tools that access historical databases…"*), so w/-inst is much closer to *the
query paraphrases the answer* than to *a user typed a prompt*.

Keep the variant, but label it correctly: an **upper bound on retrieval under vocabulary
leakage**, never a fair-competitor setting. Note PD gains ~3× what baseline 4 does from it —
**PD's strength is far more sensitive to retrieval quality than baseline 4's is**, which is
the thing to know before assuming how good retrieval will be.

**This is not yet a gate reading.** Two things stand in the way, and both need a human —
see *Blocked on you* below.

## Settled — don't relitigate

- **NBA v3 and MCP tool exposure are the same problem shape.** Rank a catalog under a
  scarce slot budget, eligibility gate in front, partial feedback behind.
- **The unit of personalization is `(user, task)`, not user.**
- **Three decision points, forced by the protocol.** A: session open (`tools/list`, *no
  prompt exists yet*). B: `list_changed` refresh (costs a cache invalidation). C:
  `find_tools` call (the only task-grain channel). This is why Gate 0 is a rescope, not a
  kill — retrieval can't run at A.
- **Arms must be feature-based, never `tool_id`.** Catalogs churn; a categorical id can't
  score an unseen tool.
- **Explore at session boundaries, not per request.** Changing the tool list invalidates
  `tools` + `system` + the whole message history at full price.
- **Kong is substrate, not competitor.** They ship the eligibility gate; they've *announced*
  semantic selection but not shipped it.
- **Fail-open is a Phase 2 architectural constraint, not a Phase 10 task.** The ranker must
  be removable at runtime.

## ⚠️ The recurring failure mode — the most important thing in this file

**This project has produced four "benchmarks that cannot lose."** Each time, an evaluation
was constructed so the answer we wanted was guaranteed:

1. **Progressive disclosure had a perfect retriever inside it** — recovery always succeeded
   and cost nothing. Scored 1.000 at every budget. Fixed; now 0.392 at k=10.
2. **Gate 1's personas are designed by us** — disjoint tool distributions make
   personalization work by construction. Mitigations specified, not yet implemented.
3. **Calibration was scoring a different protocol than the leaderboard** — ToolRet's
   `instruction` was loaded and ignored, putting our tuned bi-encoder 30.7 nDCG points low
   and below BM25. Fixed in `calibrate.py`.
   ⚠️ **Amended 2026-08-07:** this was originally written up as "the retriever was crippled,
   understating the competitor," implying the frontier should use the instruction too. It
   should not — the instruction leaks 2.7× more gold-description vocabulary than the query
   does (see *Setting 2* above). The bug was real **for calibration**, where reproducing the
   published protocol is the whole point; it was never a bug in the frontier.
4. **The existential baseline's strength was an unexamined default** — PD's `k` sat at 10
   on a range spanning 0.176 → 0.549. Whichever way the default happened to fall, the gate
   inherited it. Swept 2026-08-07; the ladder should now report the curve, not a point.

Items 1, 2 and 4 were caught by introspection. **Item 3 was only caught because an external
reference existed** — and its amendment was only caught because a *second* reference (BM25,
which cannot be "tuned") disagreed with the first. That is the argument for calibration as a
habit rather than a task, and for more than one calibration point.

> **Standing check: ask of every new evaluation — what result would make this fail?**
> If there isn't one, it isn't an evaluation.

## Blocked on you (the human) — 2026-08-09

**1. Start collection. Nothing downstream moves until this happens.** Three registrations
have to go so calls stop bypassing the proxy, and one has to replace them:

```sh
claude mcp remove -s user notion
cd /Users/nguyenvietkhoi && claude mcp remove github && claude mcp remove playwright
claude mcp add -s user gateway -- /Users/nguyenvietkhoi/mcp-gateway-router/bin/mcp-gateway
claude mcp list      # expect exactly: gateway
```

Then work normally. Shadow mode passes all 95 tools through unchanged; each session writes
`runs/{session_id}.jsonl` with project / repo / branch / hour and every call.
⚠️ **`claude mcp list` is the check that matters** — a directory that inherits a parent
project's registrations is not isolated, and that already spoiled one round of sessions.

**2. Pick three numbers.** The Gate 0 margin, the Q2 margin, and the collection stopping
rule. None block collection; all block *reading* it. Without a stopping rule, collection
runs until the number looks good — a movable margin wearing a different hat.

**3. The capture-rate probe** (`gateway.probe.json`, cuts to 4 tools). Does Claude Code
forward a call to an *unexposed* tool, or filter it client-side? The gateway side is
verified; what is untested is whether the client ever emits one. If it filters, the
missing-demand signal has no channel here. **Only valid once the direct registrations are
gone** — otherwise the client just uses the other route and you read a false negative.

**4. Anthropic account credit** — still zero, still gates `count_tokens`. It does *not*
gate Q1 (see the box at the top).

---

## Blocked on you — earlier (historical)

1. **Anthropic credentials.** `ANTHROPIC_API_KEY` is unset and the `ant` CLI isn't
   installed, so `--count-tokens` has never run. Costs are flat at 120/tool against a
   measured **~85× spread** (median 178 chars, p95 795, max 15,174). Until this runs, every
   token number is proportional to tool *count*, not schema size — **the knapsack is a
   top-K cut in disguise.**
2. **Catalog composition — and now query subset too.** ToolRet `web` merges eight unrelated
   corpora; our gold tools are 0.27% of it. That's the "fleet of servers" setting, not "one
   vendor, 400 tools, one domain." Retrieval looks materially worse in the former. **This
   choice determines what the gate result means.** Nobody has decided.
   **Raised then largely retired, 2026-08-07.** The worry was that `apibank` might be
   anomalously easy — our BM25 scored 52.67 against a published 36.04 — which would flatter
   every retrieval baseline and make the gate unreadable. Two findings retire most of it:
   the 36.04 was the wrong tab (Web API BM25 is 28.74), and more decisively, **`e5-base`
   reproduces its published Web API number within 1.6 points on this exact subset.** If
   `apibank` were unusually easy, a general dense retriever would overshoot too. It does
   not. So the subset looks representative, and the anomaly is specific to our BM25 — see
   *Calibration* below. The **catalog** question (fleet-of-servers vs one-vendor) is
   untouched by this and remains open.
3. **Notion page order.** Everything added recently appended to the page bottom. Reordering
   needs a full-page rewrite that would destroy a live comment thread on the *"current
   prompt, semantically matched"* bullet. Recommendation has been: drag manually.

## Gotchas that will waste your time — added 2026-08-09

- **`mcp` is 2.x, not 1.x.** The `@server.list_tools()` decorators are gone; `MCPServer`
  is the high-level replacement and its handlers dispatch to `self.list_tools()` /
  `self.call_tool()`, so subclassing is how you serve a runtime-varying tool list.
  `inputSchema` is now `input_schema`.
- **An MCP session must be owned by one task.** `stdio_client`, `streamable_http_client`
  and `ClientSession` each open an anyio task group, and anyio requires a cancel scope to
  be exited by the entering task. An `AsyncExitStack` entered in `start()` and unwound in
  `aclose()` raises *"Attempted to exit cancel scope in a different task"*. `runner.py`
  parks the contexts in a dedicated task for their whole lifetime.
- **`claude-agent-sdk` cannot be a pyproject extra.** It pins an older `mcp` than the
  gateway needs. They coexist at runtime but uv's universal resolver rejects the pair and
  breaks `uv run pytest` entirely. Pass `--with claude-agent-sdk` per invocation.
- **Tool search is a client `ToolUseBlock` named `ToolSearch`** — *not* a
  `ServerToolUseBlock`. The SDK's server-tool enum lists `tool_search_tool_regex`/`_bm25`,
  a different API-side mechanism. Instrumenting the enum counts zero searches.
- **The client's working directory is not `os.getcwd()`.** The launcher runs
  `uv --directory`, so cwd is the gateway's own repo; and `$PWD` fails too because the
  launcher is bash and **bash overwrites `PWD` with its inherited cwd at startup**. The
  shim exports `MCP_GATEWAY_CLIENT_CWD` before anything can change it.
- **`git rev-parse --abbrev-ref HEAD` fails on a repo with no commits.** Use
  `git branch --show-current`.
- **Second-resolution session ids collide.** Two Claude Code windows opened in the same
  second shared an id — and a log file. Ids carry a uuid suffix now.
- **Notion API — `update_content` injection depth.** Leading tabs on the *first* line of
  `new_str` make the whole edit a **silent no-op** (success response, nothing changes).
  Without them, injected blocks land at `anchor_depth − 1 + tabs`, so a depth-2 anchor
  cannot produce a depth-1 sibling — which is why every new snapshot lands inside the
  intro callout and needs one manual drag. For an *existing* block, tabs set absolute
  depth, so re-matching its text with a `\t` prefix repairs nesting. Raw `<table>` markup
  in injected content silently kills the entire edit. `insert_content` with
  `position: end` nests correctly and is the safe path for new top-level sections.
- **A Claude Pro subscription does not fund the Anthropic API.** Separate products,
  separate billing. A zero balance gates *every* endpoint including the free
  `count_tokens`.

## Gotchas that will waste your time — earlier

- **Notion API is append-only.** `insert_content` takes start/end only. Headings can't be
  matched by `update_content` — **but heading *text* can** (without the `#` and emoji).
  Replacing heading text preserves the block type, so you can't inject multi-block content
  that way.
- **A batch `update_content` silently skips non-matching replacements and still returns
  success.** A single non-match errors loudly; in a batch it partially applies. **Always
  probe afterward.**
- **Notebook iframes:** a relative `src` never resolves — JupyterLab resolves it against the
  notebook's URL, VS Code's sandbox blocks it outright, and `IFrame` never validates, so you
  get an empty box with no error. Use `srcdoc`. IPython's warning telling you to use
  `IFrame` is exactly wrong here.
- **`~/.claude/mermaid-diagram/SKILL.md` does not exist** despite being referenced in the
  global CLAUDE.md. Write mermaid directly; validate with
  `npx @mermaid-js/mermaid-cli -i x.mmd -o x.svg` and grep the SVG for `Syntax error`
  (note `.error-icon` appears in mermaid's boilerplate CSS — that's a false positive).
- **Encoding 37k tools with the bi-encoder takes ~5–8 minutes.** Run it in the background.
- **`uv run --extra dev --with datasets --with sentence-transformers`** is the working
  invocation. Token counting additionally needs `--with anthropic`.
- **`timeout` is not available** on this macOS shell.

## Calibration — reference corrected, pipeline validated, BM25 outstanding

**The leaderboard publishes exactly two settings** (from its own `config.yaml`):
`w/ meta w/ inst` and `w/ meta w/o inst`, across categories `Avg → All`, `API → Web API`,
`Code`, `Customized`. **`meta` is held constant** — tool documentation is in the document
in every published number and there is no `w/o meta` tab. The only axis is `inst`.

**Corrected 2026-08-07.** `PUBLISHED` held the *All* / with-instruction tab, wrong on both
axes: `apibank` is a Web API subset, and the frontier scores query-only. Every query-only
run was judged against with-instruction numbers and reported "BELOW the band" — a false
alarm pointing at a non-bug. `calibrate.py` now carries the full 20-model Web API tables
for both settings and selects by `--no-instruction`.

**`verdict()` also changed shape.** A min/max band over 20 models spans 12.7–41.3, which
would accept almost anything — a check that cannot fail is not a check. It now compares
**like for like against the same model's published row**, with a ±5 tolerance loose enough
to absorb our corpus and subset differences. Tuned checkpoints have no published row, so
they are checked against **the base model they were fine-tuned from** — that is what keeps
the signal loud for our default model.

Re-judged results:

| Run | nDCG@10 | Verdict |
|---|---|---|
| `e5-base`, w/ inst | 26.22 | **REPRODUCES** — published 24.59, +1.63 ✅ |
| `toolret-e5-base`, w/ inst | 56.19 | beats base by +31.60, ranks 1 of 21 ✅ |
| `toolret-e5-base`, query-only | 25.52 | **SUSPECT** — only +0.93 over its base |
| `bm25`, w/ inst | 52.67 | **DOES NOT REPRODUCE** — published 28.74, +23.93 ⚠️ |
| `bm25`, query-only | 15.00 | **DOES NOT REPRODUCE** — published 20.20, −5.20 ⚠️ |

**The pipeline is validated.** A general published model reproduces its published number
within 1.6 points, which exercises the loader, catalog identity, gold-label extraction, the
metric and the embedding wiring. That was the entire purpose of the exercise and it passed.
The 25.52 row also confirms the redesign kept catching the original missing-instruction bug.

**Outstanding: our BM25 misses in both directions.** Leading hypothesis — and it is a
hypothesis, not a finding: **dense scoring is corpus-independent, sparse scoring is not.**
An embedding does not care what else is in the catalog; BM25's IDF and average document
length do. We index `web` (37,292 tools); the leaderboard scores its own Web API corpus. The
same mismatch that leaves `e5-base` untouched would move BM25 substantially, and could
plausibly flip sign between long (with-instruction) and short (query-only) queries. If that
holds, BM25 may simply not be calibratable here without reproducing their corpus exactly —
in which case **`e5-base` is the calibration anchor and BM25 is not**. Test before citing
52.67 as evidence about anything, `apibank` difficulty included.

Still blocking a fuller ordering check:

- **`EmbeddingScorer` prefixes are not model-aware.** It applies e5's `query: ` / `passage: `
  unconditionally. That is wrong for BGE, so **`bge-large` was deliberately not run** —
  running it would have manufactured a fifth benchmark-that-cannot-lose, in the
  unfavourable direction. Fix before any BGE calibration.

## Next actions

1. **Decide whether the ladder ships a default `k` or the swept curve.** If a single value
   is wanted, k=25 is defensible as the knee *and* as the parity point with `static-set` —
   but justify it against what real `find_tools` implementations return, not against our own
   chart, or it is the same circularity in a new place.
2. **Resolve the BM25 discrepancy** — test the corpus-dependence hypothesis above (score
   BM25 over a catalog slice and watch the number move; a bi-encoder's should not). Then
   make `EmbeddingScorer` prefixes model-aware and add BGE and the large tuned checkpoints.
3. **Once credentials exist:** `--count-tokens`, then re-read the frontier. Expect the
   ranking of selectors to change, because schema size stops being uniform.
4. **Decide catalog *and query* composition**, then re-run and treat that as the Gate 0
   reading.
5. **Phase 1** — but implement both mitigations first (personas derived from ToolRet's 35
   source corpora, and a pre-registered effect size), or Gate 1 joins the list above.

New since the Notion *🧫 Getting the data* section (2026-08-07), not yet started:

6. **Harvest a real MCP catalog** — `tools/list` over installed servers → `count_tokens`.
   Retires the flat-cost and merged-corpora gaps together, and the harvester *is* the P2
   catalog service. `Tool.as_api_tool()` already emits the shape `count_tokens` wants.
7. **Dogfood a shadow-mode proxy** in front of our own Claude Code, to measure per-client
   capture rate before the P2 log schema freezes.
8. **Send the auth-grain question** — does a real vendor's MCP token resolve to a seat or a
   shared org key? Decides whether Gate 1 measures something that exists.

## File map

| Path | What |
|---|---|
| `src/mcp_gateway_router/catalog.py` | `Tool`, `Catalog`, identity = `(server_id, name)` + schema hash |
| `selector.py` | `DecisionContext`, `Selector` protocol, `fill_budget` |
| `baselines.py` | The six baselines + `LexicalScorer`, `BM25Scorer`, `InstructionScorer` |
| `embedding.py` | Bi-encoder scorer, `CALIBRATION_MODELS` |
| `tokens.py` | `AnthropicTokenCounter` (uses `count_tokens`, **never** tiktoken) |
| `frontier.py` | `Example`, `Point`, `evaluate`, `sweep` |
| `calibrate.py` | Published band, `verdict()`, IR metrics |
| `run_frontier.py` | CLI entry point |
| `report.py` | Standalone HTML frontier chart |
| `tests/test_retrieval_variants.py` | BM25, the instruction wrapper, the `k` sweep |
| `notebooks/frontier_walkthrough.ipynb` | 8 stages, executed, outputs embedded |
| `results/` | `frontier*.json/html`, `calibration*.json` |

Runs added 2026-08-07: `frontier_pdk.json` (k sweep), `frontier_inst.json` (leakage upper
bound), `calibration_bm25.json`, `calibration_bm25_noinst.json`, `calibration_e5base.json`.

**Committed.** ~24 commits on `feat/gateway-data-plane`; `main` holds the Phase 0 baseline.

## File map — added 2026-08-09

| Path | What |
|---|---|
| `gateway/config.py` | upstreams, modes, `${VAR}` + `.env` expansion, `log_dir` |
| `gateway/runner.py` | session lifetime; the anyio task-pinning fix both transports share |
| `gateway/stdio_session.py` · `http_session.py` | the two transports |
| `gateway/upstream.py` | pool, aggregation, routing; one dead server degrades the catalog |
| `gateway/policy.py` | shadow mode, fail-open, `mode` + `n_advertised` on every record |
| `gateway/server.py` | `Gateway`, `build_app` (mcp 2.x), `serve`, `build_counter` |
| `gateway/environment.py` | point-A context capture (project/repo/branch/hour) |
| `gateway/log.py` | append-only JSONL, one file per session |
| `harvest.py` | catalog service + `count_tokens`; writes `results/catalog.json` |
| `replay/sessions.py` | log → `SessionRecord`; reads a directory or a file |
| `replay/harness.py` | temporal replay; history passed at selector construction |
| `replay/baselines.py` | `random` (null), `d-global`, `d-recent`, per-session oracle |
| `replay/run.py` | CLI; `--engine module:factory` plugs in your own |
| `bench/runner.py` | one arm × one task through the Agent SDK |
| `bench/task0.py` | the Q1 gate — run it before building anything in `bench/` |
| `bin/mcp-gateway` | launcher shim; captures the client cwd before anything changes it |
