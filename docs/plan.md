# Implementation plan

Assumes the OSS-repo path. The ordering is deliberately not "build the proxy first" — two
phases up front can cheaply kill the thesis, and they cost ~3–4 weeks against months of
gateway work.

See [`decisions.md`](decisions.md) for why each of these choices was made, and
[`handoff.md`](handoff.md) for the background argument.

## Shape

| Phase | Deliverable | Gate | Rough |
|---|---|---|---|
| 0 | Frontier harness + baseline ladder | **Kill gate** | 1–2 wk |
| 1 | Multi-seat overlay benchmark | **Thesis gate** | 1–2 wk |
| 2 | Gateway data plane, pass-through | works end-to-end | 2–3 wk |
| 3 | Logging → insight report | sellable alone | 1 wk |
| 4 | Exploration floor + propensity log | dataset exists | 1–2 wk |
| 5 | Ranker (retrieval → rerank → knapsack) | beats baselines | 3–4 wk |
| 6 | CB decision layer + OPE | lift holds over time | 3–4 wk |
| 7 | Out-of-band context | vendor-dependent | — |

---

## Phase 0 — Falsification harness

**Goal:** answer the two open blockers empirically before writing gateway code.

**Build**

- `Selector` interface: `select(context, catalog, budget) -> list[Tool]`. Every baseline and
  eventually the real ranker implement this one signature.
- Token measurement — actual schema token cost per tool, not estimates. Model-specific; pick
  one target model and state it.
- Baselines 1–6: expose-all, static hand-picked, popularity top-K, semantic retrieval,
  progressive-disclosure-only, oracle.
- Frontier runner: sweep budget → success vs. tokens curve per selector.

**Stage it cheaply.** Retrieval-only first on HumanMCP / ToolRet — recall@budget, no LLM in
the loop, pennies. Only then run end-to-end on an MCP-Bench subset, which needs a model and
real money.

**Calibrate before trusting anything.** Run one of ToolRet's published retrievers (BM25,
e5, BGE) through the `Selector` interface and check we reproduce roughly its reported hit
rate on the same subset. Without this, a bad `semantic-retrieval` score is uninterpretable
— we can't tell a real finding from a weak implementation.

**Exit:** one frontier chart.

> **Gate:** if retrieval already sits on the oracle frontier at realistic budgets given a
> prompt, **rescope to decision point C** — personalization is demoted to tiebreaker and
> budget allocator. Not a kill; see `roadmap.md`.

## Phase 1 — Multi-seat overlay benchmark

**Goal:** the one question no public benchmark can answer, because they are all identity-free.

**Build:** partition tasks into personas with skewed, partly-disjoint tool distributions.
Replay as multi-session histories per synthetic seat. Compare a selector *with* seat
history against the identical selector without.

**Two mitigations, both required before the gate is read.** The personas are ours, so a
naive design decides the outcome — strongly disjoint distributions make personalization
work by construction, which is the same failure class as the progressive-disclosure
perfect-retriever bug. Derive the skew from **ToolRet's 35 source corpora** rather than
hand-picking tool sets, so it's a property of the data and not of our choices; and
**pre-register the effect size** that counts as meaningful, so the result can't be read
post-hoc. Even then this proves we can *measure* the delta, not that it exists in
reality — first real evidence is P8.

**Exit:** a number for the personalization delta at equal budget.

> **Gate:** if it is ~0, the honest product is retrieval, not personalization — a real
> pivot, not a failure.

Worth noting this artifact is plausibly more valuable than the router itself. Nobody has
published a personalized tool-exposure benchmark.

## Phase 2 — Gateway data plane

**Goal:** the choke point, with no intelligence in it.

**Build**

- MCP proxy on the official Python SDK — server to the client, client to upstreams.
  Intercept `initialize`, `tools/list`, `tools/call`; aggregate multiple upstreams.
- **Catalog service:** discovery, `(server_id, tool_name)` + schema content-hash identity
  (never positional), token cost, description embeddings.
- Static configured tool set — config file, no model.
- `find_tools` meta-tool (decision point C).
- **Exposure log:** decision events with the resolved feature vector inline, plus outcome
  events joined on `decision_id` / `session_id`.
- Miss detection, and **measure capture rate per client** — an open empirical question, not
  an assumption.

**Stack:** SQLite + in-process defaults so `uvx mcp-gateway-router` runs with zero infra;
adapter interfaces for Redis / Postgres / vector DB. Local sentence-transformers default so
the quickstart needs no API key.

**Exit:** Claude Code and Claude Desktop both run through it against 2–3 real MCP servers;
logs populate; `tools/call` passthrough overhead <5ms p99.

> ⚠️ **Cross-phase dependency:** Phase 4's explore-slot design must be settled *before* this
> log schema ships. Propensities are not retrofittable.

## Phase 3 — Logging → insight

**Goal:** the "sellable alone" milestone.

**Build:** dead-weight report (which configured tools are never called), token cost per tool
per session, ranked demand for unexposed tools, and the longitudinal guardrails (exposure
entropy, distinct-tools-called trend).

**Exit:** a generated report from real logs. Also the repo's most legible demo — it shows a
problem the reader did not know they had.

## Phase 4 — Exploration floor + propensity logging

**Build:** N−k deterministic slots + k explore slots (start 22+3) sampled from a ~50-candidate
pool with logged propensities. Randomize at **session** open (decision point A); refresh at B
via `tools/list_changed`.

**Exit:** a propensity-logged dataset, and a real number for prompt-cache invalidation cost —
which retires one of the open questions and is a prerequisite for choosing any γ.

## Phase 5 — Ranker

**Build**

- Bi-encoder retrieval → ~50 candidates.
- LightGBM reranker: retrieval score as a feature + tool-side features + in-band context.
  **Feature-based arms, never `tool_id`.**
- Calibration (isotonic) — non-negotiable, the knapsack needs probabilities not scores.
- Knapsack under token budget with hard constraints: pinned core, never-co-expose pairs,
  dependency bundles.

**Exit:** dominates static and popularity on the Phase 0 frontier, and OPE on Phase 4 logs
shows positive lift.

## Phase 6 — Contextual bandit decision layer

**Build:** per-arm CB over the explore slots only (keeps IPS variance tractable); IPS / SNIPS
+ DR estimators; the cold-start shrinkage chain (seat → tenant × role prior → population
prior).

**Exit:** OPE lift confirmed, and guardrails stable across weeks — the popularity baseline
only loses over time, so this gate is longitudinal by construction.

## Phase 7 — Out-of-band context

`ContextProvider` implementations, tenant / seat feature stores. Only meaningful with a real
vendor's warehouse behind it. This is where the vendor-strategy fork re-enters; fine to leave
stubbed indefinitely for the OSS repo.

---

## Running alongside

- **The progressive-disclosure answer** needs writing once Phase 0 produces baseline 5's
  curve — it is the first comment the repo will get, and by then it is data rather than an
  argument.
- **The UI-usage lift check** — one query joining MCP calls to UI feature usage per seat.
  Independent of everything above; decides whether the seat grain earns its place in Phase 5.
