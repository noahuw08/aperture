# Decisions

Living architectural decision log, newest first. Entries are immutable — corrections spawn a
new dated entry rather than editing an old one.

Format: `## YYYY-MM-DD — Title`. Decision stated first, rationale and rejected alternatives
after. Background argument lives in [`handoff.md`](handoff.md); the phased build order lives
in [`plan.md`](plan.md).

---

## 2026-08-11 — Density-greedy is opt-in, because the value term does not exist yet

**Decision:** `fill_budget` gains an optional `scores` argument. With it, tools are
ordered by `value / cost` — the `p·v/c` of `algorithm.md` [4]. Without it, rank order,
exactly as before. Supersedes the 2026-08-05 greedy-skip entry on ordering only; the
skip rule and the pinned-core exemption are unchanged.

**Why not simply switch to density.** `fill_budget` takes `list[Tool]` — an order with
no scores attached — and *every* caller passes one. The rankers have scores internally
(cosine, call counts, decayed weights) and drop them at the boundary. So "fix it to
density-greedy" was not implementable as written: there was no value term to divide.
The alternative, synthesising value from rank position, invents a scale that was never
measured, which is worse than the bug.

**And density on uncalibrated scores is not obviously an improvement.** `algorithm.md`
[3] already says this: the `p·v/c` arithmetic is meaningless on monotone-but-uncalibrated
scores, because 0.8 has to *mean* 80% before trading one 2,000-token tool against twenty
100-token ones is arithmetic rather than noise. Empirical call frequency qualifies —
it is a probability by construction. A cosine similarity does not. Making density the
default would have pushed uncalibrated scores through calibrated-only arithmetic and
silently moved every recorded frontier number at the same time.

**The gap, measured** (`mcp_gateway_router.density`, real 95-tool catalog, uniform
value):

| budget | rank | density | gain |
|---|---|---|---|
| 1,000 | 4 | 12 | +200% |
| 3,000 | 13 | 27 | +108% |
| 8,000 | 32 | 48 | +50% |
| 20,000 | 58 | 74 | +28% |

**Read this as a packing floor, not a forecast.** Uniform value makes density-greedy
exactly "cheapest first" and makes the objective a tool count, so the number above is
what the 39× spread is worth structurally with no model at all. A real ranker puts
valuable tools first, which shrinks the gap. The point is that the gap is large enough
at realistic budgets to be worth carrying, and that it was invisible under the old
flat-120 placeholder where the two orders are the same operation.

**Not done:** threading scores from the baselines that have them. That changes recorded
frontier numbers, so it wants a re-run rather than an edit, and the frequency baselines
are the only ones whose scores are legitimately probabilities.

## 2026-08-10 — Deferral costs names, not schemas; the token pillar is retired

**Decision:** stop treating prefix tokens as a Gate 0 outcome. Cutting the catalog is
worth ~310 tokens on this client, which is not a benchmark-able quantity. Gate 0 is read
on selection quality — `ToolSearch` calls, turns, wrong picks — and the token column stays
in the results only as a check that no arm is secretly paying more.

**What was contradictory.** One `/context` showed MCP tools in no token category; another
showed `34.3k deferred`. The handoff treated these as mutually exclusive and blocked on
resolving them, because if deferral really cost 34k the whole token argument came back.

**They measure different things.** 34.3k is the schema mass held back — a counterfactual,
not a charge. Deferral puts only the name list in the prefix: 95 tools is 3,858 chars of
names against 135,514 chars of full definitions, a **35× spread**.

**Settled by data already collected.** The 2026-08-10 probe cut 95 tools to 4 and the
prefix moved **405 tokens**. Loaded schemas would have returned ~30k, and the 95-tool
prefix (31,893) is smaller than the schema mass alone. Accounting is reproducible via
`mcp_gateway_router.deferral`; per-name cost is ~4.5 tokens, about half what chars/token
predicts, because names share the `mcp__gateway__<server>__` run.

**Rejected — re-run `/context` by hand in two sessions.** That was the handoff's proposed
test. It needs an interactive client, isn't scriptable, and the probe's per-run token
accounting already answers it with numbers that are in the repo.

**Standing caveat.** `count_tokens` is still gated by the zero balance, so the *loaded*
absolutes are estimates. The 35× ratio is exact (the chars/token constant cancels) and the
deferred side is anchored on the measured 405, so no conclusion here rests on the estimate.

## 2026-08-07 — Blockers 1, 2 and 4 cleared; first interpretable frontier

**Blocker 1 — progressive disclosure can now lose.** `find_tools` runs the same retriever
baseline 4 gets, returns `k` schemas, and the agent pays for **all** of them plus a round
trip; recovery succeeds only if the search actually found the tool. Result on real data:
**1.000 → 0.392**, closely matching the 70-point overstatement measured in the notebook.
Three tests now pin this — including one that asserts PD *fails* at small `k`. A baseline
that cannot lose is not a baseline.

**Blocker 2 — bi-encoder run** (`mangopy/ToolRet-trained-e5-base-v2`, 37,292 tools). It
helps most where the frontier is decided: satisfied at 500 tokens went **0.137 → 0.255**;
at 32k, 0.392 → 0.451.

**The reading, with the caveats below:** retrieval does **not** approach the oracle
frontier. `semantic-retrieval` caps at 0.451 while the oracle reaches 1.000 at 193 tokens —
about 125× cheaper. `popularity` reaches 1.000 but needs 15,960 tokens (83× oracle). There
is a great deal of headroom between every real selector and the ceiling.

| Selector | Best satisfied | Tokens there |
|---|---|---|
| oracle | 1.000 | 193 |
| popularity | 1.000 | 15,960 |
| static-set | 0.529 | 3,000 |
| semantic-retrieval | 0.451 | 24,000 |
| progressive-disclosure | 0.392 | 1,656 |
| expose-all | 1.000 | 4,475,040 |

### Calibration caught a real bug: the unused `instruction` field

The published leaderboard band (tab *w/ meta w/ inst*, category *All*) is **nDCG@10
36.04–45.73**, with BM25 at the floor. Our first run scored **25.52 — below BM25**. A
tuned bi-encoder losing to a sparse baseline is a setup problem, not a model problem.

Cause: ToolRet queries carry an `instruction` field that our loader read into a docstring
and then never used. The leaderboard tab we were comparing against prepends it. Adding it:

| | nDCG@10 | recall@10 | hit@1 |
|---|---|---|---|
| query only | 0.2552 | 0.3416 | 0.1881 |
| **+ instruction** | **0.5619** | **0.6073** | **0.5941** |

**+30.7 nDCG points from one field.** Had we not calibrated, baseline 4 would have been
understated by roughly that much and Gate 0 would have been read on a broken retriever —
biased in our favour, in exactly the direction that would have let us claim personalization
was needed when it may not be.

**The instruction stays out of the frontier, deliberately.** It is a benchmark artifact: a
gateway sees the user's prompt, never a hand-written statement of what to retrieve.
Calibration reproduces the published protocol to verify the pipeline; the frontier runs
query-only because that is what production looks like. `Example.retrieval_text` vs
`Example.task` encodes the split.

**We now score *above* the published band (56.19 vs 45.73 ceiling)**, which is explainable
rather than alarming: the band is *base* models and we default to a ToolRet-fine-tuned
checkpoint, we score one category rather than the three-category average, and our corpus is
`web` (37k) not the full 43k. Worth noting the fine-tuned checkpoint saw this benchmark's
training split — that **flatters baseline 4**, which is the conservative direction for
Gate 0 and therefore acceptable.

The band and a `verdict()` check are now in `calibrate.py`, with a regression test
asserting 25.52 would still be flagged.

### Original entry: calibration built and run, comparison incomplete `calibrate.py` scores a
published retriever with standard metrics: **nDCG@10 0.255, recall@10 0.342, hit@1 0.188**.
ToolRet's published tables are images in the paper/leaderboard and are not text-fetchable,
so we have our number but not theirs. What this *does* establish: the value is
non-degenerate (a broken pipeline yields ≈0 or ≈1) and directionally consistent with the
paper's headline that strong IR models perform poorly on tool retrieval.

**A design flaw in calibration-by-absolute-value.** Even with their numbers, our protocol
differs — we score `apibank` queries against the whole 37k `web` corpus, and construct
ideal DCG ourselves. A mismatch could be protocol difference rather than a bug, which makes
absolute nDCG a weak check. **Better: compare the *ordering* across BM25 → general e5 →
ToolRet-tuned.** Relative ordering is robust to protocol differences in a way absolute
scores are not. Not yet run.

**Blocker 3 remains: no Anthropic credentials in this environment** (`ANTHROPIC_API_KEY`
unset, no `ant` CLI), so `--count-tokens` has not run and costs are still flat at 120/tool
against a measured ~85× spread. Until then the knapsack is a top-K cut in disguise, and
every token figure above is proportional to tool count rather than real schema size.

**Still not a gate reading**, for the reason logged 2026-08-06: the catalog is eight merged
corpora with our gold tools at 0.27%, which is the "fleet of servers" setting and not the
single-vendor one. Retrieval looks far worse here than it would against one product's
catalog. **That composition choice determines what the number means and is still open.**

## 2026-08-06 — Gate 1 is synthetic too; two mitigations, and a calibration gap

**Phase 0 cannot evaluate personalization at all.** ToolRet has no user — no tenant, no
seat, no session, no history. Every example is anonymous. It gives us real tool schemas,
real queries with independent vocabulary, real gold labels, and real catalog scale, and
nothing else. Also absent: MCP traffic (these are query→tool pairs, not sessions, so no
`tools/list`, no decision points, no cache invalidation), tenant entitlements (the
provisioned-modules feature the doc calls highest-ROI is unmeasurable here), and any real
outcome signal (`satisfied` means the gold tool was exposed, not that a task completed).

Phase 0 is a **machinery test on a proxy task**. It answers exactly one question — does
retrieval dominate the frontier *given a prompt* — and structurally cannot answer another.

**The flaw that follows: Gate 1 is synthetic too.** Phase 1's personas are designed by us.
Strongly disjoint tool distributions make personalization work by construction; weak ones
make it fail. The gate is only as good as the persona design, and we would be designing
the answer we want. **Same failure class as the progressive-disclosure perfect-retriever
bug: a benchmark that cannot lose.**

Two mitigations, both required before Gate 1 is read:

1. **Derive personas from real structure, not invention.** ToolRet's 35 source corpora are
   natural domains — assign each synthetic seat a skewed mixture over corpora rather than
   hand-picking tool sets. The skew is then a property of the data, not of our choices.
2. **Pre-register the effect size.** Write down the lift that would count as meaningful
   *before* running, so the gate cannot be read post-hoc.

Even with both, the honest label on Phase 1 is: *proves we can measure the delta, not that
the delta exists in reality.* First real evidence is Phase 8 — a further argument for
having moved the design partner ahead of the ranker.

**Separate gap: we have no external calibration.** Our six-baseline ladder is ours, run on
ToolRet with our metrics. ToolRet ships a leaderboard (BM25, e5, BGE, their fine-tuned
retrievers) with nDCG@10 and Completeness@10 on this exact data, and we use none of it.
Defensible — their task is "rank tools for a query," ours is "choose a subset under a token
budget" — but it means a bad `semantic-retrieval` score is uninterpretable: we cannot
distinguish a real finding from a weak implementation. **Add a calibration step to Phase 0:
run one published retriever through the `Selector` interface and check we reproduce roughly
its reported hit rate on the same subset.** That sanity-checks the whole pipeline, not just
baseline 4, and it is the kind of thing that catches a silent bug before it becomes a gate
decision.

## 2026-08-06 — Correction: Gate 0 and Gate 1 are not the same finding

The roadmap entry below routed both Stage 1 gates to a single `PIVOT: measurement only`
outcome, and titled the stage "can kill the project." Both are wrong, and they treat a
weaker finding as if it were the stronger one.

**Gate 0 evaluates retrieval with a prompt in hand — decision point C.** It says nothing
about decision point A, where MCP forces the exposure decision at `tools/list`, before the
user has typed anything. At A retrieval cannot run at all and tenant + seat is the only
signal that exists. **Gate 0's answer only binds if the product commits to operating at
C.** The Phase 0 harness feeds `context.task` to `SemanticRetrieval` on every example —
so it measures at C and was being used to judge the need for personalization at A.

Three more things survive strong retrieval: **ambiguity** (*"show me last week's numbers"*
— retrieval can't disambiguate, identity can), **confusable ties** (`get_user` /
`get_user_profile` / `fetch_user_details` score near-identically; tenant schema and seat
history break the tie), and **allocation vs. ordering** (retrieval ranks but doesn't
decide budget spend or what fills residual slots — plausibly next turn's tools, which
session history predicts and cosine similarity does not).

**Revised:**

| Gate | Fails → |
|---|---|
| **0** — given the task text, does retrieval reach the oracle frontier? | **Rescope to decision point C.** Centre of gravity moves to `find_tools`; personalization demoted to tiebreaker + budget allocator. Not a kill. |
| **1** — does seat history beat the identical selector without it? | **Pivot to measurement only.** This is the gate that can actually retire the thesis. |

Gate 1 was always the decisive one — the multi-seat overlay measures the residual value of
seat history *given* retrieval. Gate 0 was over-weighted.

Consistent with the doc's own earlier position, which this contradicted: *"the per-user
prior earns its place breaking ties and ordering, not by being the primary signal."*

**Noted in passing:** the diagram was more correct than the prose accompanying it — it
never made Gate 0 terminal (`PIVOT` always flowed into P2). Only the shared node and the
stage title were wrong.

## 2026-08-06 — End-to-end roadmap; fail-open and the kill switch are requirements

`plan.md` was a build plan, not a roadmap: it stopped at P7 (a component), had no
design-partner milestone, no admin surface, no packaging, and — most importantly — no
failure branches. Added `roadmap.md` + `roadmap.html` covering five stages through GA,
with an exit gate on every phase and an explicit branch for every gate that fails.

**Scope note:** "a product to sell" reopens the vendor-strategy fork the 2026-08-04 entry
parked in favour of the OSS repo. Taken deliberately at the user's direction. P11 is where
the two must be reconciled — lead generator or separate project — and the scoping doc's
warning stands until then: arguing both at once convinces nobody.

**Four requirements surfaced that were in no previous doc.**

1. **Fail-open is a hard requirement.** The gateway sits in the critical path of every
   session. Down or slow, the client must still get a working tool set — pinned core,
   served from cache, no dependency on the ranker, the feature store, or anything beyond
   the upstream servers. A proxy that can take a customer's agent down is unsellable no
   matter how good the ranking is. This is an architectural constraint on P2, not a P10
   afterthought: the data plane has to be built so the ranker is removable at runtime.
2. **A kill switch belongs in the console.** The vendor freezes the ranker and reverts to
   their static set instantly, without contacting us. Nobody puts a learned system in
   their critical path without that lever.
3. **The design partner moves ahead of the ranker** (now P8, in Stage 2). `plan.md`
   asserts P3 is "sellable alone." That is an untested claim, and one real vendor
   falsifies it far more cheaply than discovering it after P5 and P6 are built.
4. **We see every tool call and its arguments.** Customer data flows through us, so
   compliance is an early enterprise blocker, not a late checkbox. Unanswered: what we
   retain, for how long, and whether full self-hosting can make the answer "nothing."

**Gate 2 also finally answers an open question from the scoping doc** — who owns
catalog-level eligibility at a vendor, and whether that is the same person who would buy a
ranking layer. It stops being a thought experiment the moment a design partner exists.

## 2026-08-05 — Notebook walkthrough; the PD overstatement is 70 points

Added `notebooks/frontier_walkthrough.ipynb` — the pipeline stage by stage, executed
end to end. Three things it surfaced that the CLI's summary table hides.

**The progressive-disclosure flaw is now measured, not asserted.** PD needed recovery on
**50 of 51** examples, and a real `find_tools` using the same retriever baseline 4 gets
would have succeeded on **15** — a 30% recovery rate. The harness currently credits it
with 100%, so PD's success is **overstated by 70 percentage points**. That is the entire
reason it appears to dominate at 624 tokens. Fixing this is the gate's blocking item.

**Schema cost spread is ~85×** — median 178 serialised chars, p95 795, max 15,174. The
flat 120/tool placeholder erases exactly the variance that makes this a knapsack instead
of a top-K cut. `--count-tokens` is not a refinement; without it the objective is a
different problem.

**Catalog composition doesn't match the vendor scenario, and that's a choice we should
make deliberately.** ToolRet `web` merges eight unrelated corpora — toolACE (16k) and
toolbench (13.9k) dominate, while our gold `apibank` tools number 101 (0.27%). Retrieval
is therefore searching a haystack that is almost entirely another product's tools. That
matches the repo's stated premise (a proxy in front of a *fleet* of MCP servers) but not
the Amplitude framing (one vendor, ~400 tools, one domain). The two settings answer
different questions and the difficulty is not comparable between them. Decide which one
the gate is about before reading the gate.

Illustrative of that last point, from the one-decision cell: asked "tell me the current
date," semantic retrieval returned `toolbench_tool_12487`, `toolLens_tool_55`, and
`tooleyes_tool_5` — topically plausible matches from three unrelated corpora — and missed
the `apibank` tool that answers it.

## 2026-08-05 — First real run: three methodology flaws, two fixed

Ran the sweep against ToolRet `web` (37,292 tools) with `apibank` queries (101 gold
tools, 101 queries). The pipeline works end-to-end. The numbers do not yet mean
anything, and the run is what surfaced why.

**Fixed — recovery was free, so progressive disclosure could not lose.** It scored
`satisfied = 1.000` at every budget because a miss cost nothing. That is a modelling
error, not a finding: progressive disclosure does **not** avoid the token cost of a tool
the agent ends up using, it defers it — the retrieved schema enters context on the next
turn either way. What it avoids is paying for tools you *don't* use. `evaluate` now
charges the missing tools' schema cost on recovery and counts `mean_round_trips` as a
separate metric. Effect: 480 → 624 mean tokens.

**Fixed — `static-set` and `popularity` were fitted on the eval set's gold tools.**
With 101 tools and 101 queries in `apibank`, gold counts essentially enumerate the
answer key, so both baselines were memorising. Both are *history-dependent* and need
past usage to pick a set, so the fix is a disjoint fit/eval split rather than removing
the fitting. Effect: `static-set` peak 0.752 → 0.529; `popularity` reaches 1.0 at 16k
tokens rather than 8k.

**Outstanding — progressive disclosure has a perfect retriever inside it.** Its recovery
currently always finds the right tool. In reality `find_tools` runs the same retriever as
baseline 4, which scores 0.392 on this data. So PD's apparent dominance (full success at
624 tokens, against oracle's 193 and popularity's 15,960) is partly an artifact of giving
it an oracle where baseline 4 gets a real retriever. **This must be fixed before the gate
is read** — PD looking unbeatable *is* the existential critique, and it has to lose the
ability to win by construction before that means anything.

Sanity check worth keeping: the full 37k-tool catalog costs **4.4M tokens** at a flat
120/tool — about 35× a 1M context window. The premise is not exaggerated.

## 2026-08-05 — ToolRet replaces HumanMCP as the Phase 0 retrieval dataset

**Decision:** wire `mangopy/ToolRet-Tools` + `mangopy/ToolRet-Queries` instead of HumanMCP.
MCP-Bench stays deferred behind the kill gate.

HumanMCP was the first choice — persona-varied queries across 2,800 MCP tools, which is
the closest published match to our setting. **It has no public release:** no GitHub repo,
no HuggingFace dataset, and the paper names no distribution channel. Not buildable
against.

ToolRet is the nearest released equivalent and is better distributed (HF datasets, ACL
2025 paper, public leaderboard, 43k tools / 7.6k tasks). It keeps the property that
actually matters for the gate: **its queries come from 35 pre-existing tool-use datasets,
so query vocabulary and tool-description vocabulary are independent.** Ground truth we
authored ourselves would put our own phrasing on both sides of the match and score
retrieval near-perfectly for the wrong reason — the one failure mode that would make a
green light actively misleading.

**What we lose:** ToolRet is general tool retrieval, not MCP-native, and has no persona
variation. MCP-native scale can come later from MCP-Zero's `MCP-tools` corpus (308
servers / 2,797 tools, Google Drive distribution).

**MCP-Bench deferred deliberately.** The kill gate is a retrieval question — does semantic
retrieval already sit on the oracle frontier — and retrieval-only evaluation answers it
with no model in the loop and no spend. MCP-Bench earns its place only if the gate passes:
the frontier's Y-axis is *task success* rather than hit-rate, and Phase 1's multi-seat
overlay needs a task set to partition into personas. If the gate fails, it was spend on a
cancelled project.

## 2026-08-05 — Baseline 4 uses a tool-retrieval-trained bi-encoder

**Decision:** `EmbeddingScorer` defaults to `mangopy/ToolRet-trained-e5-base-v2`.

Partially retires the `LexicalScorer` caveat logged below. A general-purpose embedder
would still be a weaker opponent than a vendor would actually deploy — the same bias one
step milder — so the honest baseline is a retriever fine-tuned for tool retrieval, which
ToolRet publishes alongside the dataset.

e5-family prefixes (`query:` / `passage:`) are applied; omitting them measurably degrades
retrieval, which would understate the baseline again through the back door.

## 2026-08-05 — Schema token cost comes from `count_tokens`, cached by schema hash

**Decision:** measure per-tool schema cost with Anthropic's `messages.count_tokens`,
passing the tool in the `tools` array and subtracting an empty-`tools` baseline so the
number is the tool's *marginal* contribution to the prefix. Cache on disk keyed by
`(model, tool.uid)`.

`tiktoken` is OpenAI's tokenizer — it undercounts Claude tokens by ~15–20% on prose and
more on JSON schemas, and the whole point of the knapsack is that the budget is in
tokens. An estimator that is wrong per-tool is wrong in a way that correlates with
schema shape, which is exactly the axis the ranker is optimising over.

Counts are model-specific; the harness pins one target model and states it. Because
`tool.uid` carries the schema content hash, a changed schema invalidates its own cache
entry and nothing else — the identity decision above pays for itself here.

**Consequence:** `anthropic` is an optional extra, not a core dependency. The quickstart
runs on `StaticTokenCounter` with no API key; accurate measurement is opt-in.

## 2026-08-05 — Budget fill is greedy-skip, and the pinned core is exempt

**Decision:** `fill_budget` walks the ranked list and *skips* a tool that doesn't fit
rather than stopping. The pinned core set is taken first and is not subject to the
budget at all.

Stopping at the first oversized schema strands the remaining budget — a 2,000-token tool
at rank 3 would block eight 100-token tools behind it. Skipping is the standard greedy
relaxation of the knapsack; a real solver arrives in Phase 5, once the score is
calibrated and the value term means something. Greedy is not optimal, and the gap is
worth measuring rather than assuming.

Exempting the pinned core follows from failure asymmetry: a missing core tool is a task
failure, and the guardrail is that the ranker can never override it. A core set that
silently drops under budget pressure is not a guarantee.

## 2026-08-05 — `LexicalScorer` is a placeholder and biases the kill gate toward us

**Decision:** ship TF-IDF cosine as the default `RelevanceScorer` so the harness runs
dependency-free, and mark it loudly as **not** baseline 4.

Baseline 4 — pure semantic retrieval — is the existential one: Kong's roadmap, Bedrock
AgentCore, and the published results all use a bi-encoder. Lexical matching will
*understate* that baseline, which biases the Phase 0 kill gate in our favour. A green
light measured against a lexical stand-in is not a green light.

**Blocks the Phase 0 gate:** plug in a real bi-encoder before reading anything into the
frontier. Recorded here so the caveat can't quietly evaporate between now and the
result.

## 2026-08-05 — Falsification before construction (phase order)

**Decision:** Phases 0 and 1 (eval harness, multi-seat benchmark) ship before any gateway
code. Both carry explicit kill gates.

The two open blockers — does progressive disclosure moot this, does personalization beat
generic retrieval — are answerable for ~3–4 weeks of work and are unanswerable by argument.
Building the proxy first means discovering the answer after months of sunk cost. If pure
semantic retrieval already sits on the oracle frontier at realistic budgets, the correct
outcome is to stop, and that is worth knowing cheaply.

Consequence: the repo's first artifact is a benchmark, not a proxy.

## 2026-08-05 — Effectiveness is a frontier, not a scalar

**Decision:** the unit of evaluation is a curve — schema token budget on X, task success on
Y. Headline numbers are *tokens-to-parity* and *lift-at-parity-budget*.

Every single metric is gameable: expose everything → miss rate 0; expose nothing → cost 0;
optimize "tool was called" → you have rebuilt the popularity counter. Only the joint movement
of miss rate down and token cost down is a claim.

**Baseline ladder** (all six implement the same `Selector` interface): expose-all · static
hand-picked · popularity top-K · **pure semantic retrieval, no personalization** ·
**progressive-disclosure-only** · oracle. The two bolded are the existential critiques from
the scoping doc; making them lines on a chart converts arguments that cannot be won
rhetorically into measurements.

**Evaluation must be longitudinal.** The popularity baseline looks excellent in a snapshot and
degrades over weeks — a one-shot eval hands the win to the exact strategy we argue is wrong.
Track exposure entropy and distinct-tools-called-per-week, not just accuracy today.

## 2026-08-05 — Explore-slot randomization, not full-set randomization

**Decision:** N−k deterministic slots + k randomized explore slots drawn from a ~50-candidate
pool. Start at 22+3. Propensities logged per explore slot.

The action here is a *set*, so naive IPS over `P(this exact 25-subset | context)` has
vanishing propensities and unusable variance. Restricting randomization to a few slots keeps
the propensity space small and factorizable, makes IPS tractable, and is the same exploration
floor the bandit needs anyway.

**Why not** slate / pseudo-inverse estimators: they assume reward decomposes across slots,
which is roughly true for tools *except* the confusable-pair interaction we specifically care
about. Keep them as a fallback if the explore-slot budget proves too small.

⚠️ Not retrofittable. This must land in the Phase 2 log schema even though the bandit is
Phase 6.

## 2026-08-05 — Build a multi-seat overlay benchmark

**Decision:** construct personas over MCP-Bench / MCP-Atlas tasks with skewed, partly-disjoint
tool distributions; replay as multi-session histories per synthetic seat.

Every public benchmark — MCP-Atlas, MCP-Bench, MCPAgentBench, HumanMCP, ToolRet, BFCL, τ-bench
— is single-user and identity-free. They can benchmark the retrieval stage but *structurally
cannot* benchmark personalization, because there is nobody to personalize to. Without an
overlay there is no way to measure the project's actual thesis.

Secondary: no personalized tool-exposure benchmark has been published. The artifact may
outlive the router.

## 2026-08-05 — Log the resolved feature vector inline at decision time

**Decision:** decision events carry the full resolved feature vector, not just keys to
reconstruct it later.

Eliminates point-in-time backfill joins entirely. NBA v3 cannot do this because the slate is
assembled downstream; a gateway is the choke point, so take it. Storage cost is trivial next
to the correctness it buys.

## 2026-08-05 — Tool identity is `(server_id, tool_name)` + schema content hash

**Decision:** never a positional index, never a re-derived `sorted()` over the observed
population.

Direct application of the `action_encoding.yaml` lesson. MCP catalogs churn continuously —
tools are added, renamed, deprecated — and across multiple upstream servers the catalog is not
ours to freeze. A re-derived catalog silently rebinds every id.

Corollary, recorded separately below: model arms must be feature-based for the same reason.

## 2026-08-05 — Three decision points, forced by the protocol

**Decision:** name and instrument A / B / C separately rather than assuming a single
"per-request" decision.

- **A — session open (`tools/list`).** Context = tenant + seat + client. Free, always present.
- **B — mid-session refresh** via `notifications/tools/list_changed`. Costs a prompt-cache
  invalidation; this is the exploration boundary.
- **C — `find_tools` meta-tool call.** The only channel carrying task text.

MCP fetches the tool array once at session open, *before the user has typed anything*. This
inverts the scoping doc's ranking of the three grains: session/task is described there as the
strongest signal, but it is **not available at the moment the protocol forces the decision**.
The thin seat grain turns out to be the only thing actionable at A.

## 2026-08-05 — Ship `find_tools`; do not argue against progressive disclosure

**Decision:** expose a retrieval meta-tool as a first-class part of the gateway.

Progressive disclosure is framed in the scoping doc as the existential critique. From an
infrastructure standpoint it is also the *transport* for task-grain context — the only
legitimate channel that delivers the prompt. Reframed: the protocol hands us a task-context
channel, and the ranker is what makes its results personalized rather than generic cosine
similarity. Competing with it is a losing position; carrying it is not.

Also yields an unambiguous miss signal (see below).

## 2026-08-05 — Model shape: bi-encoder retrieval → LightGBM rerank → knapsack + CB

**Decision:** port the NBA v3 two-stage shape (learned scorer → decision layer) with three
changes.

1. **Feature-based arms, never `tool_id`.** A categorical id cannot score a tool it has never
   seen, and new/renamed tools are the normal case here. Arms are represented by content:
   description embedding, token cost, read/write, arg count, server, category.
2. **LightGBM is the reranker, not the whole scorer.** The highest-signal feature is
   prompt ↔ description semantic match, which a GBDT cannot consume raw. Bi-encoder retrieval
   → ~50 candidates → LightGBM rerank with the similarity score as a feature.
3. **Set interactions go in the decision layer as hard constraints**, not into the bandit.
   Never-co-expose pairs, dependency bundles, pinned core. Slate/combinatorial bandits are a
   research project; a per-arm CB plus constraints is not.

Objective is `calibrated_P(call | user, task) × value − λ · schema_tokens` — a knapsack, not a
top-K cut. Requires calibration (isotonic), not merely a monotone score.

**Deferred to later phases:** the CB itself (Phase 6). Phase 5 ships the scorer and knapsack
with a fixed exploration floor.

## 2026-08-05 — Zero-infrastructure defaults

**Decision:** SQLite + in-process stores + local sentence-transformers by default, with
adapter interfaces for Redis / Postgres / vector DB.

"Easy setup" is a stated success criterion for the OSS path. A Redis dependency or a required
API key in the quickstart kills adoption. `uvx mcp-gateway-router` must work with nothing else
installed.

## 2026-08-05 — Miss signal capture rate is an open measurement, not an assumption

**Decision:** treat attempted-but-unexposed tool calls as *client-dependent* and measure
capture rate per client in Phase 2.

The scoping doc treats this as the cheapest path from judgment to evidence. It is — where it
works. Many clients hard-filter generation to the advertised list, so the attempt never leaves
the client and never reaches the gateway. Reliable substitutes: `find_tools` calls (explicit,
unambiguous) and manual tool-set overrides.

Do not build the expansion story on a signal whose availability is unverified.

## 2026-08-05 — UI feature usage is a family-level prior that may boost but never suppress

**Decision:** encode UI usage aggregated to module/tool-family level, shrinking toward real
MCP call history as it accumulates. It may raise a tool's score; it may not be used to
suppress.

The scoping doc's claim — MCP tools mirror UI actions, so tool relevance is predictable from
UI usage — holds via *stable jobs-to-be-done*, not mechanical schema correspondence. It has a
systematic bias: people reach for an agent precisely to do what they avoid in the UI, so for
the highest-value tools UI usage *anti*-correlates with demand. Letting the prior suppress
would bake that selection effect in permanently — the popularity failure arriving through a
more respectable door.

Also silent on tools with no UI analogue (`get_by_id`, `list_event_types`, pagination);
those are predicted by dependency structure instead.

**Validation before it ships:** join MCP calls to UI feature usage per seat, measure AUC on
"will this seat call tool family F this month" against a tenant-level prior. One query.

## 2026-08-05 — Kong is substrate, not competitor; the differentiation is the closed loop

**Decision:** position on the feedback loop, not on retrieval.

Kong ships the eligibility gate today — MCP Tool ACLs (AI Gateway 3.13), OAuth2 scope-based
tool filtering (3.14), MCP server generation (3.12), MCP Registry (Feb 2026, tech preview).
Their own engineering blog states the filtering is static identity-based with no dynamic
ranking, semantic matching, or learned relevance. Their observability is adoption dashboards
(call counts, latency percentiles by consumer), and their docs show no capture of denied or
attempted-but-unexposed calls.

But they have **announced the lane**: the Enterprise MCP Gateway post says Kong "will be able
to leverage … semantic intelligence to automate the selection and injection of tools based on
specific prompts," and they presented "Solving Context Bloat: Semantic Tool Routing" at QCon AI
Boston in June 2026. They already own the primitives (embeddings since 3.8, vector stores,
semantic routing in AI Proxy Advanced).

Semantic retrieval over tools is now commodity — Kong roadmap, Bedrock AgentCore ships it,
OSS registries implement it, papers report 99.6% tool-token reduction at 97.1% hit@3. What
nobody has: propensity logging built for policy training, an exploration floor, a calibrated
token knapsack, and the missing-tool demand signal.

**Consequence:** a repo whose pitch is "semantic tool retrieval" has no wedge. The pitch is
that everyone is building retrieval and nobody is building the loop that measures whether it
works or what it fails to serve.

## 2026-08-05 — Correction: "auth prunes ~400 → 380" is conditioned on grain

**Decision:** the scoping doc's claim that auth is roughly orthogonal to relevance holds only
for broadly-permissioned human seats. It is withdrawn for narrow service agents.

Kong's ACL material is titled *"Security Meets Context Efficiency"* and its worked example is
40 tools → 2. For a purpose-built agent, scope filtering *is* most of the selection problem.
The original claim stands for the Amplitude-PM case that motivated it; stated unconditionally
it is wrong, and a Kong-literate reader will say so.

---

## 2026-08-04 — Core framing: NBA v3 and MCP tool exposure are one problem

**Decision:** build the scoping argument on the mapping rather than treating MCP as new
territory.

Rank a catalog under a scarce slot budget, eligibility gate in front, partial feedback behind.
Actions → tools; 4 *For You* slots → ~25 exposed tools; screen real estate → context window;
eligibility anti-joins → auth/scopes/provisioned modules; 30-day adoption → tool called and
succeeded.

Transfers directly: frozen-catalog id binding, presentation bias, propensity logging (*free*
at a gateway, unlike NBA where the slate is assembled downstream), inverted reward latency.
Does not transfer: failure asymmetry (a miss is task failure, not an ignored card → pinned
core + personalized tail), exploration costing real money via prompt-cache invalidation, and
progressive disclosure having no NBA analogue.

## 2026-08-04 — OSS repo over vendor-strategy deliverable

**Decision:** take the repo path. Scoping doc records both framings; the repo resolves the
fork.

The two are different projects with near-disjoint requirements — hesitancy analysis is
load-bearing for the vendor argument and nearly irrelevant to a repo; wave timing is the
reverse. Arguing both at once convinces nobody.

Consequence: hesitancy analysis stays in the scoping doc as background and does not drive the
build. The vendor fork re-enters only at Phase 7 (out-of-band context), which is fine to leave
stubbed.
