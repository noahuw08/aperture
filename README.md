# mcp-gateway-router

A proxy that sits between an MCP client (the agent) and a fleet of MCP servers, and
decides **which subset of tools to expose on each request** — instead of dumping the
whole catalog into the context window.

Ideally you expose ~25 tools. Most SaaS catalogs are far larger than that, so vendors
hand-pick a static set and strand the rest. This is a ranking problem under a scarce
slot budget, not a config problem.

## The core idea

```
agent ──▶ gateway ──▶ [ eligibility gate ] ──▶ [ scorer ] ──▶ [ budget cut ] ──▶ N tools
                            (auth, scopes)      (learned)      (token knapsack)
   ▲                                                                              │
   └──────────────── log (context, exposed, called, outcome) ◀────────────────────┘
```

- **Eligibility** is hard rules — auth, scopes, provisioned modules, write-safety.
  Not learned, ever.
- **Scoring** is learned, per request, on `P(tool | user, task)`.
- **Budget cut** is a knapsack over token cost, not a top-K — tool schemas differ
  wildly in size.
- **The loop is the point.** A gateway is a single choke point that sees what it
  exposed, what got called, and what came back. That telemetry doesn't exist
  anywhere else.

## Non-obvious things that shape the design

- **A naive `+1 when an exposed tool is called` reward is a popularity counter, not
  a bandit.** Un-exposed tools generate no evidence, so the set freezes on day one.
  Exploration floor + propensity logging from the start.
- **When the model calls a tool that isn't exposed, that's signal** — direct evidence
  of a missing tool, and the only way to measure demand for what you aren't serving.
- **Exploration isn't free.** Tool schemas live in the prompt prefix; reshuffling
  them invalidates prompt caching. Cost still unquantified — needs a number before
  any exploration rate is chosen.
- **A miss is a task failure, not an ignored suggestion.** Argues for a pinned core
  set + a personalized tail, not a fully learned 25.

## Open blockers (answer before building far)

1. **Does progressive disclosure moot this?** If the agent can call `tool_search` and
   pull in what it needs, the ranking problem is largely solved at the cost of one
   extra call. This is the first critique the repo will get.
2. **What does prompt-cache invalidation actually cost?**
3. **Simulation ground truth** — scoring whether a tool subset *would have* satisfied
   a prompt requires a hand-authored prompt → required-tools mapping. Doable, but it
   validates the machinery, not the value.

## Status

Scaffold only.

- **[`docs/session-handoff.md`](docs/session-handoff.md) — start here if you're picking
  this up cold.** Current state, what's blocked, and the gotchas that waste time.
- [`docs/intuitions.md`](docs/intuitions.md) — plain-language walk through the flow, stage by
  stage. Read this if any term in the other docs doesn't land.
- [`docs/roadmap.md`](docs/roadmap.md) — end-to-end, Phase 0 to GA, every gate with a
  failure branch. Diagram: [`docs/roadmap.html`](docs/roadmap.html).
- [`docs/plan.md`](docs/plan.md) — build detail for Phases 0–7.
- [`docs/decisions.md`](docs/decisions.md) — dated architectural decision log, newest first.
- [`docs/handoff.md`](docs/handoff.md) — background, the intersection with the NBA v3 ranking
  system, and the context/feature design.
