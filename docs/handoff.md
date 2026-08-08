# Handoff — MCP Tool Exposure Personalization

_Last updated 2026-08-04. Source of truth for the full argument is the Notion doc;
this file is the pick-up brief._

## Context

Guy raised: SaaS vendors are cutting the number of MCP tools they expose because of
context bloat (~25 is the rule of thumb), which strands a lot of functionality. His
idea: an MCP proxy that personalizes which tools get served per user — "like Kong,
but instead of just auth it's also a personalization spin." Follow-up: "companies
are hesitant to dump all MCP tools into their MCP server."

Separately framed elsewhere as a specialization of an earlier "Continuous API
Rate-Limit Optimization / AI-Driven API Gateway Router" idea — same bandit-as-router
shape, moved from the infrastructure layer to the LLM application layer.

I own NBA v3, the ML ranking system behind Wealthsimple's 4 "For You" slots. The
scoping exercise: do these intersect, and is the MCP idea a real problem.

## Source material

- **Scoping doc (source of truth):** https://app.notion.com/p/3b2d597724dd810982e9d6e29cc40a99
- **NBA v3 workspace hub:** https://app.notion.com/p/3b1d597724dd8008b43ddaf6059ae088
- Most-relevant children: bandit "why" doc (`9dfd597724dd831eb91e8182848942d0`),
  CB/OPE companion (`5a4d597724dd83f6b98601030117a95c` — ~70k chars, exceeds the
  fetch tool's limit, returns as a file to read in character slices), inference data
  logic (`32dd597724dd83bcb5c501c1e057c5e9`), metric & data enhancement
  (`c63d597724dd8260aede015098bdcd6f`), drift/calibration
  (`553d597724dd8305a28a013275bd3dc5`), CIH persona cluster feature
  (`becd597724dd826496050120377ff0cd`).

## Core framing (everything hangs off this)

NBA v3 and "personalized MCP proxy" are the same problem with different nouns: rank
a catalog under a scarce slot budget, eligibility gate in front, partial feedback
behind.

| NBA v3 | MCP proxy |
|---|---|
| 41–43 actions (roadmap ~1000) | hundreds of tools |
| 4 "For You" slots | ~25 exposed tools |
| screen real estate | context window |
| eligibility anti-joins | auth / scopes / provisioned modules |
| 30-day adoption reward | tool called and succeeded |

## Conclusions already settled — don't relitigate

1. **Four things transfer directly from NBA v3:** the frozen-catalog problem
   (`action_encoding.yaml` ↔ tool schema stability); presentation bias /
   popularity collapse (a usage-ranked proxy has a guaranteed degenerate
   equilibrium); propensity logging (blocks our bandit rollout, but is *free* in a
   gateway — single choke point); inverted reward latency (same-session, not a
   30-day label).
2. **What doesn't transfer:** failure asymmetry (a missing tool = task failure, not
   an ignored card → pinned core set + personalized tail, not a fully learned 25);
   exploration costs real money via prompt-cache invalidation; progressive
   disclosure (`tool_search`) is an escape hatch NBA has no analogue for.
3. **The unit of personalization is (user, task), not user.** "For You" is a pull
   surface so you can only rank on who someone is; MCP exposure happens in the
   presence of an explicit request, so the object is `P(tool | user, task)`. A
   static per-user tool set loses to per-request retrieval; the per-user prior
   earns its place breaking ties and ordering.
4. **Context has three grains, not one:** tenant/org (quarterly, ≈auth), seat/human
   (weekly, the only "classic" personalization layer and the thinnest),
   session/task (per request, strongest signal). NBA has exactly one grain,
   `(identity, ds)`. Collapsing these is why the conversation stays vague.
5. **Slots are heterogeneous** — "25 tools" is really a token budget and schemas
   differ wildly in size, so it's a knapsack, not a top-K cut. Needs a *calibrated*
   score, not just a monotone one. Same prerequisite as v3.5's
   `calibrated_P × value − arm_cost`.
6. **Auth vs. personalization = eligibility gate vs. learned ranker**, stages 1 and
   2–3 of the same pipeline. Auth does **not** solve context bloat (it prunes for
   safety, roughly orthogonal to relevance — 400 tools → ~380). Auth is a
   *dependency* of personalization, not an alternative.
7. **A proxy fixes the ranking half of vendor hesitancy and none of the eligibility
   half.** Fixes: bloat, token cost, confusable-pair accuracy, measurement, and
   partly prompt-injection combinations (never-co-expose rules). Doesn't fix: write
   blast radius, support/ownership surface, badly designed tools. So the pitch is
   not "expose everything, we'll rank it" — it's "for tools you've already deemed
   safe, you're under-exposing out of caution with no data to know by how much."
8. **Why hesitancy is damaging:** under-exposure fails *nearly* silently; produces
   quiet wrong answers rather than clean refusals (models improvise with the tools
   they have); commoditizes the vendor into a data source when writes are withheld;
   fits nobody when static; and is self-sealing (no exposure → no evidence → no
   basis to expand). It's the exploit-only strategy from the 3-restaurants example
   in our bandit doc: locked onto a noisy early sample, stuck on a second-best arm,
   regret accruing at a constant rate forever.
   - **Partial exception, and the most useful signal available:** when a model tries
     to call a tool that isn't in the exposed list, the gateway sees the attempted
     name. That's a direct — if sparse and noisy — observation of a *missing* tool.
     So a proxy can measure demand for tools it isn't serving. Cheapest path from
     judgment to evidence; belongs in v1.

## Context features (what to build the ranking on)

- **Tenant:** plan/SKU, industry, and above all **which product modules are
  provisioned and actually used** — likely kills 30–40% of the catalog per tenant at
  zero ML cost. Workspace shape (event types, whether cohorts/dashboards exist).
- **Seat:** role inferred from in-product behaviour; **UI feature-usage history**
  (MCP tools mostly mirror UI actions, so UI usage predicts tool relevance — the
  direct analogue of the FM v0.5 embedding, and a product-analytics vendor's version
  of that embedding *is* their core product).
- **Session/task:** current prompt semantically matched against tool descriptions
  (highest signal available); tools already called this session (home for the
  2-step-sequence lift work); connected client; recent errors/retries; **editor and
  workspace state** for dev-tool clients — open file extension (`.py`/`.sql`/
  `.json`/`.md`), repo, language, branch.
- **Tool-side:** global call/success/error rate, read vs. write, dependency
  structure, **schema token cost**.
- **Cold start hierarchy** (cleaner than NBA's, since seats nest into tenants):
  new tenant → population prior on industry × plan × provisioned modules; new seat
  in known tenant → borrow the tenant's tool distribution weighted by role prior;
  known seat → personal history conditioned on the task.

## Reward vocabulary

| Signal | Sign | Note |
|---|---|---|
| Exposed tool called, returned cleanly | + | base reward |
| Called but errored / retried | − | separates "wrong tool" from "tool broken" |
| User manually overrides the tool set | − − | explicit dissatisfaction |
| Agent attempts a tool that isn't exposed | − − | direct evidence of a missing tool |

**Warning:** a naive `+1 if an exposed tool is called` reward walks straight into the
degenerate equilibrium — un-exposed tools generate no evidence, so the set freezes on
day one. Exploration floor + propensity logging from the start, or it's a popularity
counter wearing a bandit's clothes.

## Unresolved fork — decide before writing more

Two different projects are being blended:

| | Vendor strategy | OSS gateway repo |
|---|---|---|
| Deliverable | should we build it, what breaks | a repo + demo |
| Success | evidence a vendor should expand exposure | stars, timing, adoption |
| What matters | hesitancy analysis, eligibility vs. ranking split | wave timing, easy setup, legible demo |

The hesitancy analysis is load-bearing for the first and nearly irrelevant to the
second; wave-timing is the reverse. Arguing both at once convinces nobody.

**If the repo path is taken, two blockers up front:**
1. "Easy simulation" is doing a lot of work — scoring whether a tool subset *would
   have* satisfied a prompt needs hand-authored ground truth (prompt → required
   tools). Same full-information-holdout trick as our decision-layer stage, so
   there's a template, but the same caveat holds verbatim: **it validates the
   machinery, not the value.**
2. Progressive disclosure — "the protocol already solves this with `tool_search`" is
   a caveat for a vendor and an existential critique for a repo. Answer before code.

## Other open questions

- What does exploration actually cost given prompt-cache invalidation? Need a real
  number before choosing any γ.
- Does "tool was called" over-credit tools the agent tried and abandoned? (The
  dwell-ambiguity problem from our metric doc, in miniature.)
- Who owns catalog-level eligibility at a vendor, and is that the same person who'd
  buy a ranking layer?

## Suggested next actions

1. **Ship logging, not ranking** — instrument the vendor's existing hand-picked set
   so they can see which of the 25 are dead weight, plus attempted-but-unexposed
   calls. Sellable alone.
2. **Expand with an exploration floor**, or the set freezes on day one.
3. **Then personalize:** tenant → seat → session, in order of data availability.

Guardrails carried from the bandit doc: a pinned core set the ranker can't override,
and risky tools as hard eligibility constraints — never reward terms.
