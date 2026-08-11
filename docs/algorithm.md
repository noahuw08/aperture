# The personalization stack

What the ranker actually is, stage by stage. Build order and gates: [`plan.md`](plan.md)
and [`roadmap.md`](roadmap.md). Why each choice was made: [`decisions.md`](decisions.md).
Where the data comes from and how any of it gets validated: the Notion doc's
*🧫 Getting the data* section.

This is the target design for Phases 5–6. Nothing above stage 0 is trainable before
Phase 4 produces propensity-logged exposure — see *Staging* at the end.

---

## The pipeline

```
[0] eligibility gate      hard rules, never learned          → candidate set
[1] candidate generation  A: priors · C: retrieval           → ~50 candidates
[2] scorer                LightGBM over features             → raw score
[3] calibration           isotonic                           → P(call | context)
[4] decision layer        knapsack + hard constraints        → N−k slots
[5] exploration           k slots, propensity logged         → final exposed set
[6] learning              OPE over explore slots             → next model
```

Stages 1–5 must be removable at runtime. See *Fail-open* below.

---

## [0] Eligibility — the only stage that is never learned

Auth, scopes, provisioned modules. An anti-join, not a score. Everything downstream
operates on what survives.

Risky tools are excluded **here**, never penalised in the reward. A ranker that can be
talked into exposing an unauthorised tool is a security bug, not a mediocre model — and a
reward term is a soft constraint, which is the wrong instrument for a hard rule.

## [1] Candidate generation — A and C are genuinely different problems

**At C** (`find_tools`) the task text exists: bi-encoder over tool descriptions, top ~50.
This is commodity — Kong's roadmap, Bedrock AgentCore, the published results.

**At A** (`tools/list`) there is no task text, so there is no query to embed. Candidate
generation becomes a union of priors:

- the pinned core
- the seat's historical top-N, recency-weighted
- the tenant's top-M not already covered
- the k-slot explore pool

The unification: at A the gateway **predicts the task** from environment — repo, branch,
cwd, client, time of day, tools called in the seat's recent sessions. Mechanically that is
a *pseudo-query*: assemble a synthetic context string from those signals, embed it in the
same space as tool descriptions, and retrieve on it. One code path for A and C, with A's
query predicted rather than stated, degrading to the plain prior union when environment
signals are absent.

⚠️ A pseudo-query is a modelling claim, not a free lunch — it asserts that environment
predicts task. That is testable on Phase 4 logs before any of it ships, and should be.

## [2] Scorer — where personalization actually lives

LightGBM reranker over the candidate set. Binary `P(called this session)` is the simpler
target; LambdaMART if pairwise ranking proves better. Feature blocks:

| Block | Examples |
|---|---|
| **Tool content** | description embedding, retrieval similarity, measured token cost, read/write, arg count, server, dependency degree |
| **Tool behaviour** | global call rate, success rate, error rate — *shrunk*, see below |
| **Seat × tool** | has this seat called it, recency, frequency, calls in last N sessions |
| **Tenant × tool** | tenant call rate, provisioned-module match |
| **Context** | client, hour/day, session index, repo/branch/language, errors so far |

The seat×tool block is the entire personalization claim, and note what it is: **features
computed at scoring time, not learned per-`(seat, tool)` parameters.** "Has this seat
called this tool" has a defined value for a tool the model has never seen — `false`. A
seat×tool embedding does not.

## [3] Calibration — non-negotiable, and it is about stage 4

Isotonic regression on held-out data. The knapsack compares `p·v/c` across tools, and that
arithmetic is meaningless on monotone-but-uncalibrated scores: 0.8 has to *mean* 80%, or
trading one 2,000-token tool against twenty 100-token ones is nonsense.

Refit on every retrain. Check calibration **per segment**, not just in aggregate — a model
calibrated overall is routinely miscalibrated on exactly the tail tools the exploration
floor exists to surface.

## [4] Decision layer — all set interactions go here

```
maximize   Σ pᵢ · vᵢ · xᵢ
subject to Σ cᵢ · xᵢ ≤ B          cᵢ = measured schema tokens
           pinned core ⊆ x, exempt from B
           never-co-expose pairs   (get_user / get_user_profile)
           dependency bundles      (get_by_id needs an id producer)
```

Greedy by density `p·v/c` is the relaxation `fill_budget` implements when you hand it
`scores`; a real solver arrives once the value term means something. Without `scores` it
falls back to rank order, which is what every caller still passes — see `decisions.md`,
2026-08-11. Note the dependency on [3] above: density is only arithmetic on calibrated
scores, so the fallback is not merely a convenience.

The gap between the two orders is measured rather than assumed —
`python -m mcp_gateway_router.density`. On the real catalog at a 3,000-token budget it is
27 tools against 13, under uniform value.

**Interactions are hard constraints here, not terms in the bandit.** Slate and
combinatorial bandits are a research project; a per-arm CB plus constraints is not.

## [5] Exploration

N−k deterministic + k randomized slots (start 22+3) drawn from the ~50-candidate pool,
propensities logged per explore slot, randomized at **session open**.

Session-boundary randomization is forced by economics, not taste: changing the tool list
invalidates `tools` + `system` + the entire message history at full price, so reshuffling
per request re-pays the whole conversation every turn.

Explore slots only, because naive IPS over `P(this exact 25-subset | context)` has
vanishing propensities and unusable variance.

## [6] Learning loop

**Reward vocabulary:**

| Signal | Sign |
|---|---|
| Exposed tool called, returned cleanly | + |
| Called, errored or retried | − |
| User manually overrides the tool set | − − |
| Agent attempts a tool that isn't exposed | − − *and the only positive evidence for the un-exposed tail* |

**The training-data subtlety that matters most: un-exposed tools are unobserved, not
negative.** Training on `exposed & called = 1, exposed & not called = 0` learns nothing
about the tail and feeds the degenerate equilibrium straight back in — the popularity
counter arriving through a more respectable door. Either restrict training to exposed
tools with IPS weights, or model exposure explicitly.

**OPE:** IPS / SNIPS over the explore slots; DR once there is a reward model worth
trusting. Guardrails are longitudinal by construction — exposure entropy and
distinct-tools-called-per-week — because the popularity baseline looks excellent in a
snapshot and only loses over weeks.

---

## Why arms are features and never tool IDs

Four independent reasons, each sufficient on its own.

**1. An unseen id produces an arbitrary number, not an uncertain one.** A categorical
`tool_id` learns one parameter per id from rows where that id appeared. One-hot: the column
does not exist for a new tool. LightGBM categorical: it falls to a default branch whose
leaf value is a training artifact. The output is crisp and confident, so nothing downstream
distrusts it — whereas wide uncertainty would at least have been explorable.

**2. "Never seen" is the normal case here, not an edge case.** NBA v3 gets away with
id-based arms because its catalog is 41–43 actions pinned by `action_encoding.yaml`. Three
churn sources break that here: upstream vendors add, rename and deprecate on their own
schedule; a new upstream connection injects a block of unseen tools at once; and **our own
identity scheme guarantees it** — `Tool.uid` is `server_id/name@schema_hash`, so one added
optional parameter mints a fresh zero-history arm. Keying on `key` instead only defers the
problem to renames.

**3. It rebuilds the degenerate equilibrium inside the ranker.** An unseen tool scores at
the unknown-bucket default → is not exposed → generates no calls → stays unseen. That is
the self-sealing loop the project exists to break, reconstructed one layer down. Feature-
based arms let a brand-new tool be scored on content — embedding, token cost, read/write,
arg count, server — so it can win a slot on merit before it has any history.

**4. Sample efficiency and cross-install transfer.** Id arms need exposures *per tool*:
400 tools is 400 estimation problems. Features share parameters, so every observation about
every tool informs all of them — complexity drops from O(|catalog|) to O(features). And
every OSS install has a *different* catalog: feature parameters transfer, id parameters
transfer nothing. A model that can only score tools it has personally observed is worthless
on first install, which is every install.

**What this gives up, and how to buy it back.** Content features cannot memorise
tool-specific idiosyncrasy — a flaky tool, or one whose description oversells it. The fix
is already in the feature table: global call rate, success rate, error rate. Those memorise,
but as *numeric features* rather than as arm identity — and a numeric feature has a defined
value for an unseen tool (missing → shrink to the population prior) where an id has none.
The design is not "give up memorisation"; it is **move memorisation out of the arm's
identity and into a feature that degrades gracefully.**

> **Two lessons, not one.** `decisions.md` files this as a corollary of the
> `action_encoding.yaml` identity decision. They solve different failures.
> **Identity stability** — id 17 must mean the same tool at train and inference time;
> prevents silent rebinding. **Identity coverage** — what to do when the id did not exist
> at training time at all. Stability does not help when the id is new: a perfectly stable
> scheme still yields an arm with no parameter.

---

## Cold start is shrinkage on features, not branching in the model

```
seat_rate_shrunk = (n_seat·r_seat + α·r_tenant×role + β·r_population) / (n_seat + α + β)
```

The model never learns "which regime am I in" — it reads a feature that is already the
right blend. New seat in an existing tenant → the estimate *is* the tenant prior weighted
by role. New tenant and new seat → the population prior on industry × plan × provisioned
modules. As `n_seat` grows the estimate slides to personal history with no code path
switching and no discontinuity in the score.

This is also why the hierarchy is cleaner here than on *For You*: seats nest inside
tenants, so each level is a natural shrinkage target for the next.

## Fail-open

Stages 1–5 must be removable at runtime. Scorer times out, feature store unreachable, model
throws → serve the pinned core plus the last-known-good set from cache, with no dependency
on the ranker or anything beyond the upstream servers.

This is an architectural constraint on **Phase 2**, not a Phase 10 task: retrofitting it
means rewriting the data plane. A proxy that can take a customer's agent down is unsellable
regardless of ranking quality.

---

## Staging

| Phase | Ships | Buys |
|---|---|---|
| **P2–P3** | [0] + static configured set + exposure logging | The choke point. No model. |
| **P4** | + [5] exploration floor | **The dataset.** Nothing above is trainable before this. |
| **P5** | + [1] [2] [3] [4] | First real ranker, trained offline on P4 logs |
| **P6** | + [6] CB over explore slots | Lift that holds over weeks |

**One ordering is not negotiable: [5] precedes [2].** Without propensity-logged exposure
there is no unbiased training data, and a scorer fit on unlogged historical exposure
inherits every bias of the static set it was trained under — it learns to reproduce the
hand-picked 25, then reports high accuracy for doing so.

## Rejected alternatives

| Considered | Why not |
|---|---|
| Per-arm Thompson / UCB over tool ids | Cannot score an unseen tool; see *Why arms are features* |
| Slate or combinatorial bandits | Correct model of the set action, but a research project. Constraints in the decision layer get most of the benefit. |
| Two-tower with a learned seat embedding | Needs far more per-seat data than a gateway has early, and the seat grain is the thinnest of the three. Revisit if Gate 1 comes back strong. |
| Pure semantic retrieval | The commodity baseline, and the thing to beat — not a design. It cannot run at decision point A at all. |
