# Intuitions — a plain-language walk through the flow

_Started 2026-08-08. Companion to the other docs, not a substitute for any of them.
Where this file and a spec disagree, the spec is right and this file is stale._

Every other doc in `docs/` assumes you already know the vocabulary. This one doesn't. It
walks the pipeline in order and explains each term the way you'd explain it out loud, with
a concrete example, and — where there is one — the reason it bites.

Read [`session-handoff.md`](session-handoff.md) for where things stand,
[`roadmap.md`](roadmap.md) for where they're going, and this file when a word in either of
those doesn't land.

---

## The one-paragraph version

An agent talks to MCP servers, and every tool those servers offer has its JSON schema
pasted into the model's prompt on **every single call**. A few hundred tools is a few
hundred thousand tokens of recurring tax. So somebody has to choose a subset. Today that
choice is a human hand-picking a static list. We think it should be a ranking decision made
per request, and we think the interesting part isn't the ranking — it's that a proxy in the
middle is the only place that can see what it exposed, what got called, and what happened
next.

---

## Stage 0 · The catalog

### MCP server, tool, schema

**Plain version.** An MCP server is a process that offers an agent a set of callable
functions. A tool is one of those functions. Its schema is the JSON description of its name,
what it does, and what arguments it takes.

**Concrete.** The `github` server offers ~29 tools. One of them is
`create_pull_request`, whose schema lists `owner`, `repo`, `title`, `head`, `base`, and a
paragraph describing when to use it.

### The prompt prefix, a.k.a. the context tax

**Plain version.** Tool schemas don't get fetched when needed. They sit at the front of the
prompt on every model call in the session, whether or not the model uses any of them.

**Why it bites.** It's recurring, it's paid on the *customer's* bill rather than the
vendor's, and it scales with catalog size rather than with usage. Exposing all 37,292 tools
in our ToolRet catalog costs **4,475,040 tokens**. The oracle — exactly the tools actually
needed — costs **193**. That ratio is the entire business.

> ⚠️ **This is the premise the project was founded on, and on a tool-search client it is
> false.** Read the next entry before using any number above. The first sentence — schemas
> don't get fetched when needed — is exactly what deferral undoes.

### Deferral, a.k.a. tool search

**Plain version.** The client doesn't paste every schema into the prefix any more. It
pastes the tool *names*, plus one `ToolSearch` tool. When the agent needs something, it
searches, and only then does that tool's full schema enter the context.

**Concrete.** Our 95 gateway tools are **135,514 characters** as full definitions and
**3,858 characters** as bare names — a **35× spread**. Measured against the probe, the
whole 95-tool name list costs roughly **420 tokens** of prefix. Cutting the catalog
95 → 25 therefore saves about **310 tokens**, not the ~30,000 the entry above implies.

**Why it bites.** The tax stops scaling with catalog size and starts scaling with *usage* —
you pay for the tools the agent actually fetches. Three consequences, in order of how much
they cost us:

1. **Cutting the catalog to save tokens no longer works.** That was pillar one, and it's
   gone at this catalog size.
2. **A bad cut costs more than no cut.** Withhold the right tool and the agent searches
   again, burning turns and eventually loading the schema anyway. Measured: 4 tools took
   more `ToolSearch` calls and more turns than 95, at the same prefix cost.
3. **What survives is timing and quality.** Tool search can't run before a prompt exists
   (decision point A), and a selector that puts the right tool in front of the agent saves
   searches. Both are measured by task success, not by token count.

**The one number to keep.** 35× is a ratio, so it doesn't depend on a tokenizer and is
exact. Every absolute in this file is an estimate until `count_tokens` is funded — see
`deferral.py`.

### Harvest

**Plain version.** Connect to every MCP server we have, ask each one for its tool list, and
write the result down as a real catalog with real measured token costs.

**Why it bites.** Until this runs, every cost number in the repo assumes a flat 120 tokens
per tool. Real schemas vary by roughly **85×** — median 178 characters, p95 795, max 15,174.
Under a flat cost, "fill the budget" and "take the top K" are the same operation, so the
knapsack below is a top-K wearing a disguise.

---

## Stage 1 · When the decision has to be made

The MCP protocol forces the exposure decision at three moments, and they have wildly
different amounts of information available. Almost every disagreement about this project is
really a disagreement about which one you're talking about.

### Why filtering means intercepting `tools/list`

**Plain version.** The model never talks to an MCP server. The *client* — Claude Code,
Cursor — calls `tools/list`, takes the array that comes back, and renders it into the
prompt prefix. So the `tools/list` response **is** the exposure. A tool that isn't in that
array does not exist as far as the model is concerned; a tool that is in it exists
completely. There is no third state.

**Why it has to be the response path.** The protocol has no per-tool visibility flag — no
`hidden`, no `defer_loading`, no "include but don't show." The only way to make a tool not
appear is to not send it. And the only party that can do that is upstream of the client,
because once the client has the array it has already built the prefix and we own no hook
after that point. Hence: intercept the response, cut, forward.

**Why not filter at `tools/call` instead.** You can, and a gateway should also do it — but
it buys something different. Denying at *call* time means the model already read the
schema, already paid the prefix for it, already planned around it, and now has to recover
from an error. Denying at *list* time means it never knew. Same policy, two enforcement
points, and only one of them affects context. (Kong runs both; a 2026 changelog entry
records the bug where the two disagreed — `tools/list` leaked tools that the call-time ACL
would have denied.)

**Why it can't be undone later.** Once a schema is in the prefix it is cached and billed
there. Removing it isn't an edit — it's `list_changed` plus a re-fetch, which invalidates
the cache behind it. See decision point B.

**The consequence, and it's the important one.** Because the response array is the entire
surface, the gateway's action space is exactly five levers: **which entries appear, their
names, their descriptions, their order, and when the list changes.** Nothing else reaches
the client. That is why a design premised on "pre-load a personalized core and defer the
rest" is unbuildable — deferral is not one of the five.

**And subtraction is the only lever with a guarantee behind it.** Not sending a tool is
authoritative: the client cannot act on what it never received. Everything else in that
list is *text we are asking the client to believe* — and the probes in `872e043` showed it
mostly doesn't. Descriptions never reached the client's own retriever, and text appended to
a tool result was read and explicitly refused on the grounds that tool output is not an
operator instruction. Suppression is enforceable; promotion is a request.

### Decision point A — `tools/list`, at session open

**Plain version.** The client connects and asks "what tools do you have?" You must answer
immediately. **The user has not typed anything yet.**

**Why it bites.** No task text exists, so retrieval — of any kind, ours or Anthropic's —
cannot run. The only signals are who the user is, what they've done before, and what time it
is. This is the point where personalization is the *only* available answer, and it's why a
strong retrieval result doesn't settle the project.

### Decision point B — `list_changed`, mid-session

**Plain version.** The protocol lets you tell the client "my tool list changed, re-fetch
it." So you *can* revise the decision once the task is known.

**Why it bites.** It isn't free. Tool schemas live at the front of the prompt, so changing
them invalidates the prompt cache for `tools`, the system prompt, and the entire message
history behind it — all re-billed at full price. Nobody has measured what that actually
costs, which is why no exploration rate has been chosen.

### Meta-tool

**Plain version.** A tool whose subject is the catalog itself. It does nothing in the world —
it tells the model which tools exist. `find_tools("something about releases")` returns
schemas.

**What it trades.** One schema in the [prompt prefix](#the-prompt-prefix-aka-the-context-tax)
instead of ninety-five, at the price of an extra round trip and k schemas in a tool result
whenever the agent needs one. A context-size problem converted into a latency problem.

**Why the shape matters.** Three consequences, and they drive everything at decision point C:

- The schemas arrive **in a tool result, not the prefix** — so they are neither cached nor
  billed per turn, but they are delivered through the channel measured as untrusted.
- The tool it names **was never advertised**, so calling it requires the client to emit a
  `tools/call` for a name absent from the `tools/list` array. Whether any client does that is
  an assumption, not a fact — see [miss](#miss-and-missing-tool-demand).
- It is **the only channel carrying the task**. The agent writes the query, handing us the
  prompt that [decision point A](#decision-point-a--toolslist-at-session-open) cannot see.

### Decision point C — a `find_tools` call

**Plain version.** Expose one meta-tool that searches the catalog. The agent calls it when
it needs something, and gets schemas back in the tool result.

**Why it bites.** This is the only channel with the task in hand, and it's also the
existential threat — see *progressive disclosure* below.

---

## Interlude · Data plane and control plane

Borrowed from networking, and used without definition in the roadmap, the spec and the
plans — so it belongs here.

**Control plane.** Decides the rules. Config, which servers are connected, what the
pinned core is, training the ranker, the admin console. Runs rarely, sits off the
critical path, and if it's down for an hour nobody's request fails.

**Data plane.** Actually moves the traffic. Every `tools/list` and every `tools/call`
between the client and the upstream servers passes through it. Runs constantly, in-line.

In this project the **gateway is the data plane**. `harvest.py`, the arm configuration,
and later the ranker training and admin console are control plane — they shape what the
data plane does without sitting in the path.

**Why it earns its place rather than being jargon.** The asymmetry is the reason
fail-open is a v0 requirement instead of a P10 task: a control-plane outage degrades
future decisions, while a data-plane outage takes the customer's agent down mid-session.
Same reason there's a sub-5ms p99 latency bar at all — nobody budgets latency for a
config screen.

---

## Stage 2 · Eligibility

**Plain version.** Hard rules about what this user is *allowed* to see. Auth, scopes,
provisioned modules, whether a write tool is safe to expose.

**The rule.** Eligibility is never learned. A ranker that can be talked into exposing a tool
someone lacks permission for is a security bug, not a mediocre model. It runs first, as a
filter, and the scorer only ever sees what survives.

---

## Stage 3 · Scoring

**Plain version.** For each surviving tool, estimate how likely it is to be needed. Not a
relevance score in the abstract — `P(tool | user, task)`.

### The unit is `(user, task)`, not user

**Plain version.** "Quang's tools" isn't a meaningful set. "Quang, debugging a failing CI
run" is. The same person wants completely different tools an hour later.

### Task-conditioned vs. session-blind

**Plain version.** Task-conditioned means the selector gets the text of what you're about to
ask, and scores against it. Session-blind means it doesn't, and has to pick tools that are
good on average for you.

**Concrete.** Task: *"find the open PRs on repo X that touch the auth module."*
Task-conditioned embeds that string and pulls `github__list_pull_requests`,
`github__get_pull_request_files`, `github__search_code`. Session-blind has no idea you're
about to say that and serves your most-used tools instead.

**Why it bites.** It's `P(tool | task)` against `P(tool)`. Task-conditioned is strictly
better informed — and **it is not available at decision point A**, which is the whole
problem. When it appears as a benchmark arm it's a ceiling, not a product.

Session-blind splits in two, and the split is the point:

- **Global** — all-time call frequency. No session information at all. The true
  no-information floor.
- **Recent** — recency-weighted, or just the last N sessions. Still no prompt, still legal
  at point A, but it exploits the fact that people tend to keep doing what they were doing.

### What "personalization" actually means at decision point A

**Plain version.** No prompt exists at point A, so the gateway is predicting the task from
whatever else is observable. **Identity is one predictor of task, not the only one.** The
others are environment (cwd, repo, branch, client), recency (what the last session did),
time of day, and all-time history.

**Why it bites.** The project's name says personalization, but the mechanism is
*context-conditioned prediction of the task before the task is stated*. Personalization is
one feature block inside that. A ranker with a `who` block and a `where / when / recently`
block is a single model — so a deployment with one user doesn't test a different system, it
tests the same system with one feature block zeroed.

**What that means for a single-seat dogfood.** These are testable:

| Claim | n=1? |
|---|---|
| Structure exists in one user's history, exploitable before the prompt | **Yes** — recent vs. global |
| The most that structure could be worth | **Yes** — the heterogeneity ceiling, upper bound only |
| The machinery works end to end | **Yes** — that's the whole point of dogfooding |
| **Different users need different tools** | **No.** Needs real seats. |

The last row is the product claim. Nothing measurable at n=1 touches it, and a good n=1
result must not be read as evidence for it.

### Bi-encoder, BM25, lexical

**Plain version.** Three ways to score text similarity, cheapest first. Lexical is word
overlap. BM25 is word overlap with sensible weighting for rare words and document length.
A bi-encoder embeds the query and every tool description into vectors and compares them, so
it can match *"find PRs"* to a tool described as *"list pull requests"* — which word overlap
cannot.

**Why it bites.** BM25's scores depend on what else is in the catalog (its IDF and average
document length are corpus statistics); a bi-encoder's don't. That asymmetry is the leading
hypothesis for why our BM25 doesn't reproduce its published number and `e5-base` does.

### Arms are features, never tool IDs

**Plain version.** The model must score a tool it has never seen, using properties of the
tool — its server, its description, its cost, its category. If the model instead learns "tool
#4471 is good," a catalog update breaks it and every new tool starts from zero forever.

---

## Stage 4 · The budget cut

### Knapsack, not top-K

**Plain version.** You have a token budget, not a slot count. Tools have different sizes.
Picking the best set is "maximize total value subject to total weight" — the knapsack
problem — not "take the top 25."

**Concrete.** A 15,174-character schema can eat the budget of eighty small ones. If it's
ranked third, top-K takes it and top-K is wrong.

**Why it bites.** This distinction only exists if costs actually vary, which is why Stage 0's
harvest is a prerequisite and not a chore.

### Rank-greedy vs. density-greedy

**Plain version.** Two ways to fill the budget. **Rank-greedy** walks the ranked list in
order and takes whatever fits. **Density-greedy** reorders by *value per token* first —
score divided by cost — and then takes whatever fits. The second is the `p·v/c` in
`algorithm.md`.

**Concrete.** One tool scores 1.0 and costs 400 tokens; four others score 0.6 and cost 50.
Budget 440. Rank-greedy takes the favourite and strands 40 tokens: total value 1.0.
Density-greedy takes all four cheap ones: total value 2.4. On the real catalog at a
3,000-token budget the difference is **27 tools against 13**.

**Why it bites — twice.** First, the two are *the same operation* when every tool costs the
same, which is why nobody noticed under the old flat-120 placeholder. Real schemas span
39×, and only then does the choice exist.

Second, and less obvious: **density needs a number, and rank order isn't one.** "Third
best" doesn't divide by tokens. You need an actual value — and it has to be *calibrated*,
meaning 0.8 really is 80%, or the division is comparing units that don't compare. A cosine
similarity ranks fine and calibrates badly. Empirical call frequency is a genuine
probability and works. This is why the fix is an opt-in argument rather than a
switch-flip: most rankers have nothing legitimate to pass.

### Pinned core + personalized tail

**Plain version.** Don't learn all 25 slots. Fix a core set that's always present, and let
the ranker fight over what's left.

**Why it bites.** A miss here isn't a bad recommendation the user scrolls past — the task
fails outright. That asymmetry argues for a floor of guaranteed-present tools rather than
trusting a model with the whole set.

---

## Stage 5 · What we're competing against

### Expose-all

Everything, always. Perfect availability, absurd cost. The thing every alternative is trying
to beat, and a useful ceiling on success rate.

### Static set

A human hand-picks ~25 tools once. This is what vendors actually ship today. Scored **0.529**
in our harness. It is a much better baseline than it sounds and beating it is the minimum bar.

### Semantic retrieval (pre-loading a ranking)

**Plain version.** Run the retriever at session open and spend the whole budget on its
top-ranked tools.

**Why it bites.** When the ranking is mostly wrong, you've paid full price for the mistake.
Scored **0.451 for 15,960 tokens**.

### Progressive disclosure / ToolSearch

**Plain version.** Don't pre-load anything. Expose a `find_tools` meta-tool; the agent calls
it when it needs something and pays only for what comes back, plus one round trip. Claude's
own tool search is exactly this.

**Why it bites — and this is the sharpest finding in the repo.** Using the *identical
retriever*, on-demand beats pre-loading: **0.549 for 6,362 tokens at k=50**, against
semantic retrieval's 0.451 for 15,960. Exposing a retriever's top-k on demand beats
pre-loading its ranking. If this holds on real tools, it's the strongest form of the
critique the project has to answer.

Two mechanical facts people get wrong about it:

- **`k` is not a detail.** It swings the result from 0.176 to 0.549. Quoting progressive
  disclosure at a single `k` is quoting whichever number the default happened to produce.
- **It's budget-invariant.** It's one point on the (tokens, satisfied) plane, not a curve.
  Asking "how does PD do at a 500-token budget" is a category error.

### Oracle

Exactly the tools the task needed, nothing else. Not achievable; it's the ceiling. **1.000 at
193 tokens.**

---

## Stage 6 · The loop — the part that's actually the product

Everything above is ranking, which is commodity. This stage is not.

### Exposure log

**Plain version.** Every session, write down: what the context was, which tools we exposed,
which got called, and what happened. A gateway is the single choke point that can see all
four at once.

### Miss, and missing-tool demand

**Plain version.** A miss is the model trying to call a tool we didn't expose.

**Why it bites.** It's the only direct evidence of demand for something you aren't serving —
you can't learn it from usage data, because a tool that isn't exposed generates no usage.
It's also entirely dependent on an unverified assumption: that the client actually forwards
such an attempt instead of silently filtering it. That's what *capture rate* measures, and
it's the first thing the dogfood deployment is for.

**Why we care at all — it is the only uncensored error signal.** The gateway's job is to not
send some tools, but every observation coming back is about the tools it *did* send. Cut
tools generate no usage by construction, so the log cannot distinguish a good cut from a bad
one. A session where we withheld the one tool the task needed looks identical to a session
that went fine — the agent just quietly does worse. Measured: arm C exposed no search tool
for a `search_code` task and produced **zero call records**; the miss was visible only by
differencing against arm A's log. An error signal that requires running a control arm forever
is not one you can ship.

`was_exposed: false` replaces that with the agent telling us directly. Per-request,
unambiguous, no control arm, no gold labels. The same asymmetry governs *learning*: a ranker
trained on this log sees "exposed and called" against "exposed and not called," and the cut
tools — where the losses are — contribute nothing. Censored feedback makes a ranker confident
exactly where it is already winning. The [exploration floor](#exploration-floor) exists to buy
that feedback back, and it costs a cache invalidation every time. A miss is the same evidence
for free.

**Why it has never fired, and why that is structural.** 48 call records, 48 `true`. The model
cannot want what it cannot see: availability is resolved before a call is ever generated, so
an unexposed tool is not refused — it was never a candidate. Tool search does not rescue this
either, since it only ranks what the server declared. In the ordinary architecture there is
**no mechanism by which this signal could fire at all.** That is the argument for
[decision point C](#decision-point-c--a-find_tools-call): `find_tools` is the only arrangement
where calling an unexposed tool is the *normal path* rather than an error, so missing demand
becomes a side effect of the product working.

**The honest limit.** If you never cut, there is no missing demand to observe — and on this
client at this catalog size, cutting saves approximately nothing (see
[deferral](#deferral-aka-tool-search)). The signal is worth whatever cutting is worth. Where
it survives that objection is the control plane: *entitlement* denials produce the same
record with no ranker involved, and "your team keeps reaching for tools you have not
licensed" is sellable before any model exists.

### What the ranker actually learns from a miss

**Plain version.** A miss is a training label sitting in the one place the log has none.

The exposure log gives you context → exposed set → called subset, which trains *"given I
exposed these, which get called."* The ranker's job is *"given the whole catalog, which should
I expose."* The gap between those two questions is exactly the tools never exposed, and a
`was_exposed: false` record is a labeled positive inside it.

Four uses, in rough order of how badly each is currently unmet:

1. **Gradient where there is none.** A tool that is never exposed appears in no loss term, so
   its score never moves and today's cut becomes permanent. Missing demand is the only thing
   that lifts a tool back from below the line — the same job as the
   [exploration floor](#exploration-floor), minus the cache invalidation per unit of
   information.
2. **The knapsack's value term.** `fill_budget` maximises `p·v/c` and no caller has a `v`.
   Value is roughly `P(session needs T)` × the cost of not having it. The log measures
   `P(called | exposed)`, which is conditioned on the decision under evaluation and therefore
   biased by construction. A miss measures the numerator directly, per context.
3. **Off-policy evaluation.** [IPS/SNIPS/DR](#ope-ips-snips-dr) reweight by `1/propensity`, so
   tools the logging policy exposed with probability ≈ 0 blow up the variance or get dropped.
   Without misses you can only evaluate policies resembling the one already running — never a
   better one.
4. **Grain.** Records arrive tagged with `(repo, branch, hour)`, which is the
   [`(user, task)` grain](#the-unit-is-user-task-not-user) the personalization claim rests on.

**The catch — a working `find_tools` drains this channel.** If the agent's route to a cut tool
*is* the meta-tool, arm B converts misses into successful disclosures. Good for the product,
and it empties the signal: residual misses are only the cases where the agent guessed a raw
tool name, which are rare and weak.

What replaces it is better, but it is *not the same record*. A `find_tools` call carries **the
query text** — the task-grain prompt [decision point A](#decision-point-a--toolslist-at-session-open)
structurally cannot see. So two distinct signals, feeding different stages:

| Record | Signal | Volume | Feeds |
|---|---|---|---|
| `disclosed` + query | what was wanted, and what was then called | high, self-labelling | decision point C retrieval |
| `unexposed` | the point-A ranker was wrong | rare, unbiased | the point-A ranker |

They have different statistics and different consumers, which is why the exposure field needs
three states rather than a boolean. Summing them into one `false` buries the rare unbiased
signal under the common one.

### Dead weight

Tools that were exposed for weeks and never called. The cheap half of the insight report,
and sellable before any model exists.

### Propensity

**Plain version.** The probability that *this* tool got exposed in *this* session, recorded
at the moment of the decision.

**Why it bites.** Without it you cannot tell "the ranker was right" from "the ranker got
lucky because it exposes that tool to everyone." It is **not retrofittable** — you cannot
reconstruct it later from logs. Every off-policy method downstream requires it, which is why
it has to be in the log schema before the schema freezes.

### Exploration floor

**Plain version.** Deliberately reserve a few slots for tools the ranker *didn't* pick, so
the system keeps learning about options it currently rates poorly.

**Why it bites.** Without it, a naive "+1 when an exposed tool gets called" rule is a
popularity counter, not a learner. Un-exposed tools generate no evidence, so the set freezes
on day one and never recovers. Explore at session boundaries, not per request — mid-session
reshuffles cost a full cache invalidation.

### Binding — the thing exploration is actually over

**Plain version.** An engineering task has a fixed shape: locate the symbol → read it → edit
→ run tests → open the PR. That shape does *not* say which tool satisfies a step. The choice
of tool for a step is the **binding**, and it's the only thing the exploration is ranging
over. Never the plan.

**Concrete.** "Locate the symbol" binds to `grep`, or `search_code`, or an LSP
`find_references`, or an Explore subagent. Same step, same terminal state, one round trip
against six.

**Why it bites — the objection it answers.** The obvious critique of a personalization layer
here is *"engineering tasks are structured, so the path is already determined, so exploring
can only make it worse."* The structure is in the step sequence; the binding is unconstrained
by it. Two further consequences:

- **Structure is the precondition for the moat, not a threat to it.** Bandits converge when
  arms recur in a small context space. Recurring step types are what make the per-cell sample
  counts arrive at all — the [`(user, task)` grain](#the-unit-is-user-task-not-user) claim
  depends on that recurrence. Unstructured one-off tasks would make personalization hopeless,
  not safer.
- **It is where the knapsack's missing `v` lives.** [`fill_budget`](#knapsack-not-top-k)
  maximises `p·v/c` and nothing supplies `v`. Value is *round trips saved on a step this
  context will hit*, not `P(called | exposed)`.

### Sequence collapse

**Plain version.** The wins that matter aren't one tool swapped for a better one. They're one
tool that absorbs a whole run of calls.

**Concrete.** A recurring span — `list_files → get_file_contents ×4 → search_code` — that
`pull_request_read` subsumes in a single call.

**Why it bites.** It only becomes minable *because* the steps are structured: you can diff a
span against a candidate binding only when spans recur with recognizable boundaries. And the
payoff is arithmetic rather than a judgement call — tokens of the collapsed span, minus the
schema you had to disclose to get it.

### Span reward, not call reward

**Plain version.** Score the cost of getting a *step* done, not the outcome of a single call.

**Why it bites.** The [reward vocabulary](algorithm.md) in `algorithm.md` [6] is per-call:
*exposed tool called, returned cleanly → +*. That systematically prefers cheap tools that
need many round trips — a six-call `grep` loop books `+6` against `search_code`'s `+1`, which
is backwards from the thing the product claims to sell. The unit that matches the claim is
tokens in + tokens out + turns, from step-open to step-satisfied.

⚠️ Same standing as [propensity](#propensity): cheap to put in the log schema now, expensive
after it freezes.

### Where the explore flavor still pays

Three cases the fixed shape of a task does nothing to help, so exposure is the only teacher:

- **Cold-start arms.** A newly installed server has no evidence.
  [Features](#arms-are-features-never-tool-ids) let you *score* a new tool; only a call
  confirms it.
- **Drift.** Vendors ship batch endpoints, deprecate, get slow. Structured tasks make drift
  *detectable* — same step, cost moved — which most bandit settings don't get.
- **Subpopulation discovery.** The best binding for "locate symbol" varies with repo size,
  language, whether an LSP is running. A global winner is wrong for a segment, and only
  exploration reveals the segment exists. That is the personalization claim itself.

### Who pays for exploration

**Plain version.** The fleet explores; the individual seat exploits. Pool a fleet-level prior
and let each seat's posterior override it only where that seat has the evidence to.

**Why it bites.** It's the answer to *"how does a developer get their task done faster while
also exploring?"* — mostly they don't explore. Per-seat exploration rate can approach zero
while the system as a whole still learns quickly. Note this is a **multi-seat** mechanism:
like the last row of [the n=1 table](#what-personalization-actually-means-at-decision-point-a),
nothing in a single-seat dogfood tests it.

### Prompt-cache invalidation

**Plain version.** Change the tool list and the model re-reads and re-bills the whole prefix
instead of reusing a cached copy.

**Why it bites.** It's the price of every exploration decision, and it is currently an
unmeasured number blocking the choice of any exploration rate.

**Why [decision point C](#decision-point-c--a-find_tools-call) may dissolve it.** Exploring at
point A re-bills the prefix, which is why the unmeasured number blocks. If `find_tools`
discloses through the tool *result* — appended to the message history, not a mutation of the
`tools` array — then an explore slot in the result set costs only the marginal schema tokens.
Bounded, known before it's spent, no prefix invalidation. Capped downside against uncapped
upside, which is a rate you can actually choose.

⚠️ Conditional on which channel arm B lands. The `list_changed` route pays full price and
this paragraph does not apply to it.

### OPE, IPS, SNIPS, DR

**Plain version.** Off-policy evaluation: estimating how a *new* ranker would have performed,
using logs collected under the *old* one, without deploying it. IPS reweights each logged
decision by how likely the new policy was to make it. SNIPS is IPS with the weights
normalized so a few huge weights can't dominate. DR ("doubly robust") combines IPS with a
learned reward model and stays correct if either one is right.

**Why it bites.** All of them consume propensities. No propensities, no OPE, no lift claim.

---

## Stage 7 · Not breaking anything

### Fail-open

**Plain version.** If the ranker is down, slow, or throwing, the client still gets a working
tool set — pinned core from cache, no dependency on the model or the network.

**Why it bites.** The gateway is in the critical path of every session. A proxy that can take
a customer's agent down is unsellable at any ranking quality. It has to be an architectural
property from the start; it cannot be added at the end.

### Shadow mode

**Plain version.** The gateway computes and logs what it *would* have exposed, then passes
the existing set through unchanged. Behaviour identical, risk zero.

**Why it bites.** It's not a timid launch — it's the only honest way to collect the
propensity-logged data every later phase needs, and it ships the insight report with no
behaviour change.

### Kill switch

One control that freezes the ranker and reverts to the vendor's own static list, without
contacting us. Nobody puts a learned system in their critical path without one.

---

## Evaluation vocabulary

### Satisfied

Did the exposed set contain the tools the task actually needed. The headline metric of the
offline harness. Note what it does *not* measure: whether the agent then succeeded.

### Round trips

How many extra request/response cycles a selector costs. Progressive disclosure's recovery
path costs one; pre-loading costs zero.

### The frontier

Success plotted against token cost, with every selector on the same axes. "Better" means up
and to the left. A selector that wins at one budget and loses at another has no single
verdict, which is why the sweep exists.

### Temporal replay

**Plain version.** Walk a real session log in chronological order. At each session open, show
the selector only what happened *before* it, let it pick a tool set, then score that set
against what the session actually went on to call.

**Why it bites.** It's the only evaluation shape that can express "session *n* is predictable
from sessions 1..*n*−1." A battery of independent tasks structurally cannot. It also makes
the time-split mandatory rather than a discipline someone has to remember — replay can't leak
the future because it never sees it.

### Coverage

**Plain version.** Of the tools a session actually called, what fraction were exposed when it
opened.

**Why it bites.** It is *not* task success, and must never be reported as if it were. It also
carries a bias worth naming: logs collected in shadow mode had every tool available, so what a
session called isn't necessarily what it would have called under a cut set. Coverage answers
*"would we have kept what you used"* — the right question for a floor, the wrong one for a
ceiling.

### Calibration

Reproducing a published benchmark number with our own pipeline, to prove the pipeline works
before trusting anything novel it says. `e5-base` reproducing its published nDCG@10 within
1.6 points is what validates our loader, catalog identity, gold labels, metric, and
embedding wiring all at once.

### nDCG@10

Standard retrieval metric. Roughly: of the ten results you returned, how good are they and
how near the top are the good ones. Higher is better, 100 is perfect.

### Gates

Named decision points on the roadmap, each with a **failure branch** written down in advance.
Gate 0 asks whether retrieval already reaches the frontier. Gate 1 asks whether seat history
adds anything on top. A gate without a stated failure branch is a wish.

### Why the semantic arm is built before the engine

**Plain version.** Arm C runs off-the-shelf retrieval — a downloaded bi-encoder, no
personalization, no history — and it exists before the engine does because it is the
*control half of the engine's own experiment*. Gate 1 asks whether seat history beats **the
identical selector without it**, and that selector is arm C. You cannot measure what
personalization adds until you have measured what it adds to.

**Why it bites.** Build the engine first and three things go wrong. A loss cannot be
attributed — "the idea is wrong" and "this implementation is bad" look identical, and only
one of them is fixable. There is no history to train on. And the engine's commodity
substitute is what a vendor would ship instead, so beating it is the minimum bar for the
engine existing at all. C also happens to be the only arm that exercises the live path;
arm A runs in shadow mode, where no cut happens.

**What it does not settle.** C is task-conditioned and the engine is not — that is
*different* signal, not less of it (see [Task-conditioned vs. session-blind](#task-conditioned-vs-session-blind)).
So a weak C rescopes Gate 0 rather than killing the engine, which is why its failure branch
reads "centre of gravity moves to `find_tools`" and not "stop."

### Null arm

A deliberately meaningless variant — random selection, or seats drawn from an identical
distribution. If it shows an effect, the harness is broken. Cheapest falsification control
available.

### Pre-registered effect size

Writing down what result would count as success *before* running the experiment, so the
threshold can't be moved afterward to match whatever came out.

### Heterogeneity and persistence

Two properties of a population of users. Heterogeneity: how different their tool needs are
from each other. Persistence: whether those differences hold from week to week. Personalization
requires both. Report lift as a surface over these two axes, not as a single number — the
number is only true for the personas we invented.

**Persistence is measurable at n=1; heterogeneity is not.** One person's sessions still
cluster into recurring modes, and whether those modes hold from session to session is
exactly what a recency-weighted selector beating an all-time one would show.

### The heterogeneity ceiling

**Plain version.** Compare a per-session *oracle* against a global top-25. The gap is the
total value of session information, perfectly exploited. Any real ranker captures some
fraction of that gap and never more, so the gap is an upper bound on what any amount of
ranking work can buy.

**Why it bites — the two readings are not mirror images.**

- **Small gap → strong conclusion.** If the ceiling is three points, the ranker, the feature
  store, the bandit and the OPE machinery are all competing for three points. Decide
  immediately; the honest product is measurement. Upper bounds are reliable exactly when
  they are small.
- **Large gap → weak conclusion.** It means the value exists *in principle*, nothing more.
  **The oracle reads the future** — it is defined by the calls that actually happened. A real
  ranker sees only what is observable before the session opens. Nothing guarantees the second
  predicts the first.

So the measurement can cheaply *kill* the ranker but cannot *justify* it. Pre-register that
asymmetry, or a large number gets read as a green light later.

Three ways to corrupt it:

- **It bounds only the signal it conditioned on.** A per-session oracle bounds
  session-conditioned rankers. It says nothing about cross-seat personalization — at n=1
  there is no cross-seat variation in the data to bound.
- **Ceiling effects.** If the global arm already scores 0.95, the gap cannot exceed 0.05 by
  arithmetic. That is the metric running out of room, not heterogeneity being absent. Read
  the global arm's absolute level first.
- **It is a curve over budget, not a number.** At a generous budget the global set covers
  everything and the gap closes. Quoting one gap at one budget is the same category error as
  quoting progressive disclosure at one `k`.

### Benchmark-that-cannot-lose

The house failure mode. An evaluation built so the answer we wanted was guaranteed. Four so
far, documented in [`session-handoff.md`](session-handoff.md). Three were caught by
introspection; one was caught only because an external reference existed, and its correction
only because a *second* reference disagreed with the first.

> **The standing check: ask of every new evaluation — what result would make this fail?**
> If there isn't one, it isn't an evaluation.
