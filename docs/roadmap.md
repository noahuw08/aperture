# Roadmap — Phase 0 to a sellable product

Diagram: [`roadmap.html`](roadmap.html). Build detail for Phases 0–7: [`plan.md`](plan.md).
Why each choice was made: [`decisions.md`](decisions.md).

This supersedes `plan.md` as the end-to-end view. `plan.md` remains the working detail
for the phases it covers; it stops at P7, which is a component, not a product.

**Every phase has an exit gate, and every gate has a failure branch.** A roadmap without
the failure branches is a wish, not a decision.

---

## Stage 1 · Prove — 3–4 weeks, can reshape or kill the project

| Phase | Deliverable |
|---|---|
| **P0** | Frontier harness: `Selector` interface, 6 baselines, measured token cost, budget sweep |
| **P1** | Multi-seat overlay benchmark: personas over MCP-Bench, per-seat session histories |

**The two gates have different consequences.** Routing both to the same outcome — as an
earlier version of this doc did — treats a weaker finding as if it were the stronger one.

- **Gate 0 — given the task text, does retrieval reach the oracle frontier at realistic
  budgets?**
  If yes → **rescope to decision point C**. The product's centre of gravity moves to the
  `find_tools` meta-tool, and personalization is demoted to tiebreaker and budget
  allocator. **Not a kill, and not a reason to drop personalization** — see below.
- **Gate 1 — does seat history beat the identical selector without it?**
  If the delta is ≈ 0 → **pivot to measurement only**. This is the gate that can actually
  retire the thesis: it measures the residual value of seat history *given* retrieval.

⚠️ **Gate 1 is synthetic.** Phase 1's personas are designed by us — disjoint tool
distributions make personalization work by construction, weak ones make it fail. Two
mitigations are required before the gate is read: derive personas from ToolRet's 35 source
corpora rather than inventing tool sets, and **pre-register the meaningful effect size**
so the result can't be interpreted post-hoc. Even then, Phase 1 proves we can *measure*
the delta, not that it exists in reality. First real evidence is P8.

### Why Gate 0 doesn't settle it

Gate 0 evaluates retrieval **with a prompt in hand** — decision point C. It says nothing
about decision point A, where MCP forces the exposure decision at `tools/list`, before the
user has typed anything. At A, retrieval cannot run at all and tenant + seat is the only
signal there is. So Gate 0's answer only binds if the product commits to operating at C.

Three more things survive strong retrieval:

- **Ambiguity.** *"Show me last week's numbers"* — retrieval can't disambiguate which
  numbers; who you are can.
- **Confusable ties.** `get_user` / `get_user_profile` / `fetch_user_details` score nearly
  identically on the same query. Which is right depends on tenant schema and seat history.
- **Allocation, not ordering.** Retrieval ranks; it doesn't decide how much budget to
  spend or what fills the residual slots — plausibly the tools needed *next* turn, which
  session history predicts and a similarity score does not.

Neither outcome is project death. Measurement is still worth selling; it just isn't the
product we set out to build, and saying so early is the point of this stage.

## Stage 2 · Instrument — the choke point, no model yet

| Phase | Deliverable |
|---|---|
| **P2** | Gateway data plane: MCP proxy, catalog service, `find_tools`, exposure log |
| **P3** | Logging → insight: dead-weight report, ranked miss demand, longitudinal guardrails |
| **P8** | **Design partner** — one vendor, real catalog, real traffic |

- **Gate 2 — does a real vendor keep it running and act on the report?**
  If nobody acts on it → **rescope**: the "sellable alone" claim was wrong, and the
  question is who the buyer actually is.

**P8 sits here deliberately, not at the end.** `plan.md` asserts P3 is sellable on its
own. That claim is untested, and falsifying it with one real vendor is far cheaper than
discovering it after P5 and P6 are built.

⚠️ Phase 4's explore-slot design must be settled **before** the P2 log schema ships.
Propensities are not retrofittable.

## Stage 3 · Learn — nothing here is trainable before P4

| Phase | Deliverable |
|---|---|
| **P4** | Exploration floor: 22 fixed + 3 explore slots, propensity logged at session open |
| **P5** | Ranker: bi-encoder → LightGBM rerank → isotonic calibration → knapsack + constraints |
| **P6** | CB decision layer: per-arm bandit over explore slots, IPS/SNIPS + DR, cold-start chain |

- **Gate 3 — positive OPE lift, and does it hold over weeks?**
  Longitudinal by construction: the popularity baseline only loses over time, so a
  snapshot cannot pass this gate. If lift decays → rescope.

P4 also produces the prompt-cache invalidation number, which retires an open question and
is a prerequisite for choosing any exploration rate.

## Stage 4 · Productize — none of it optional to sell

| Phase | Deliverable |
|---|---|
| **P7** | Out-of-band context: `ContextProvider` → tenant/seat features from the vendor's warehouse |
| **P9** | Admin console: report UI, pin core, never-co-expose rules, override, **kill switch** |
| **P10** | Hardening: **fail-open**, multi-tenancy, SLA, security review, SOC 2 path |

- **Gate 4 — can a gateway outage break a customer's agent?**
  If yes, you are not shipping. Loop back to P10.

## Stage 5 · Sell

| Phase | Deliverable |
|---|---|
| **P11** | Packaging & pricing: OSS self-host vs hosted tiers, per-seat or per-call |
| **P12** | GA — repeatable sale |

- **Gate 5 — do customers 2 and 3 close without founder-led heroics?**
  If not, the product isn't the product yet.

---

## Four requirements this exercise surfaced

None of these were in any previous doc, and the first two are hard blockers.

**Fail-open is a requirement, not a nicety.** The gateway sits in the critical path of
every session. If it is down or slow, the client must still receive a working tool set —
the pinned core, served from cache, with no dependency on the ranker, the feature store,
or the network beyond the upstream servers. A proxy that can take a customer's agent down
is unsellable regardless of how good its ranking is.

**A kill switch belongs in the console.** The vendor must be able to freeze the ranker and
revert to their own static set instantly, without contacting us. Nobody adopts a learned
system in their critical path without that lever, and asking them to is how a pilot dies.

**The design partner comes before the ranker.** See Stage 2.

**We see every tool call and its arguments.** That is customer data flowing through us,
which makes compliance an early enterprise blocker rather than a late-stage checkbox. It
also raises a question we have not answered: what do we retain, for how long, and can a
vendor run the whole thing self-hosted so the answer is "nothing"?

## Still unanswered

- Who owns catalog-level eligibility at a vendor, and is that the same person who would
  buy a ranking layer? Open since the scoping doc; **Gate 2 is where it gets answered**.
- Is the OSS repo a lead generator for the commercial product, or a different project?
  The scoping doc warns that arguing both at once convinces nobody. P11 forces the answer.
