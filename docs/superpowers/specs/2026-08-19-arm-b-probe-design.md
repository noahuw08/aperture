# Arm B — `find_tools`, and whether the client will call what it never saw

_Design spec. Written 2026-08-19._

Related: [`PICKUP.md`](../../PICKUP.md) (§3 the rediscovery, §6.1 the starred next step) ·
[`2026-08-08-mvp-gateway-design.md`](2026-08-08-mvp-gateway-design.md) (§6, where arm B was
specified and then skipped) · [`intuitions.md`](../../intuitions.md) (*meta-tool*, *miss*,
*what the ranker actually learns from a miss*) · [`decisions.md`](../../decisions.md) (:595,
2026-08-05, *"Ship `find_tools`"*)

---

## 1 · What this is

Arm B was designed on 2026-08-05, listed as a P2 deliverable, and never built. This spec
builds the smallest version of it that can answer its one load-bearing question, and pays for
two model runs to answer it.

**The question.** Will an MCP client emit a `tools/call` for a tool that was never in the
`tools/list` array, having learned the tool exists from a `find_tools` result?

Everything else about arm B — ranking quality, `k`, pinned-core membership, the bandit — is
downstream of that answer and worthless without it. If the client refuses, arm B requires
registration via `list_changed`, which costs a prompt-cache invalidation nobody has measured,
and the whole design changes shape.

**This is a decision instrument.** Two runs, ~$0.14. Where artifact quality and readability of
the result conflict, readability wins.

## 2 · Why the answer matters beyond arm B

`was_exposed: false` has never fired: **48 call records, 48 `true`**. That is not a collection
gap. Availability is resolved before a call is generated, so in the ordinary architecture there
is no mechanism by which the signal could fire at all — the model cannot want what it cannot
see, and tool search only ranks what the server declared.

That signal is the only uncensored error signal the gateway has. Cut tools generate no usage,
so the exposure log cannot distinguish a good cut from a bad one; arm C's miss on a
`search_code` task produced **zero call records** and was legible only by differencing against
arm A's log. An error signal requiring a permanent control arm is not shippable.

`find_tools` is the only arrangement where calling an unexposed tool is the *normal path*
rather than an error. See [`intuitions.md`](../../intuitions.md) §*What the ranker actually
learns from a miss* for what the personalization layer does with such records, and for the
catch: a working `find_tools` **drains** the miss channel by converting misses into
disclosures. That catch is why §5 changes the log schema.

## 3 · Scope

**In.** A `find_tools` meta-tool in `gateway/`, config-driven and default-disabled; a
three-state exposure field; two probe runs and a pre-registered reading.

**Out.** Ranking quality — the retriever is deliberately a stub (§4.4). Pinned-core membership
for production, which remains an open design question and is *not* answered by the probe's
fixture core. `list_changed` and its cache cost, which is the branch taken only if this probe
comes back negative. `bench/arms.py::_write_config`'s latent config-path bug (PICKUP §5.2,
§6.3) — same bug class, unrelated feature, tracked separately so this spec stays one thing.

## 4 · Architecture

### 4.1 · `gateway/metatools.py` — new module

```python
# in gateway/config.py, beside NAMESPACE_SEP:
GATEWAY_SERVER_ID = "_gateway"   # reserved; config rejects an upstream with this id

# in gateway/metatools.py:
FIND_TOOLS = "find_tools"

Key = tuple[str, str]

class MetaTools:
    def __init__(self, *, scorer_factory=None, k: int = 5, enabled: bool = False): ...

    def advertise(self) -> list[Tool]:
        """Meta-tools to append to the tools/list array. Empty when disabled."""

    def handles(self, server_id: str) -> bool: ...

    def call(self, name: str, arguments: dict, catalog: Catalog) -> tuple[str, set[Key]]:
        """Returns (result text, keys disclosed). Pure — no I/O, no SDK, no pool."""
```

`GATEWAY_SERVER_ID` lives in `config.py` rather than here: it is enforced at config load
(`_gateway` is a rejected upstream id) and read by `naming`-adjacent code, so putting it beside
`NAMESPACE_SEP` keeps the reserved-namespace rules in one file and keeps `config.py` from
importing this module. The constructor takes a **`scorer_factory`**, not a built scorer:
`LexicalScorer` is constructed *from* a catalog, which does not exist until `tools/list` has
run, and building it lazily per catalog is what lets a raising constructor degrade to "search
unavailable" instead of killing startup.

`call` receives the catalog as an **argument** rather than holding a provider, and returns
**text** rather than a `CallToolResult`. Both keep this module free of I/O and of `mcp`
imports, so its tests are string assertions over a hand-built `Catalog` with no transport, no
subprocess and no mocked pool. `Gateway` does the wrapping, as `_augment` already does lazily.

**Why a module rather than a branch in `Gateway.call_tool`.** The search and its result shaping
are the only parts with real logic, and they are pure; the dispatch and bookkeeping are two
lines each. Inlining would mean testing a pure function through a mocked pool. This also
follows how the codebase already separates concerns — `policy.py` describes itself as "a thin
adapter… owns no ranking logic."

**A pseudo-upstream was considered and rejected.** `UpstreamSession` is a three-method
`Protocol` (`upstream.py:24-27`), so an in-process object satisfies it and routing would stay
uniform with no branching. But `aggregate()` would then fold `find_tools` into the `Catalog`,
where the selector could score and cut it, and where it would perturb `n_candidates` and
`catalog_hash`. The meta-tool is not a candidate. It would also need a back-reference to the
`Gateway` that owns it, to record disclosures.

### 4.2 · `Gateway` — three touch points

1. **`list_tools()`** — append `self._meta.advertise()` after `_rewrite`. Cache the aggregated
   catalog on the instance for use at step 3. `_exposed` is unchanged: **catalog keys only**,
   never the meta-tool.
2. **`call_tool()`** — after `parse_advertised`, if `self._meta.handles(server_id)`, dispatch to
   `MetaTools`, record the returned keys into `self._disclosed`, wrap the text as a result, and
   log the call. The pool is not touched.
3. **`_exposure(key)`** — the single place deciding `listed` / `disclosed` / `unexposed`.

`parse_advertised` already yields `("_gateway", "find_tools")` with no change, because server
ids are validated to contain no `__` and it splits from the left.

### 4.3 · Config

```json
"find_tools": { "enabled": false, "k": 5 }
```

**Default-disabled**, so every existing arm and all 28 collected sessions behave identically.
`_gateway` is reserved at config load, beside the existing no-`__` validation on server ids.

### 4.4 · The retriever is a stub, on purpose

Ranking inside `find_tools` is `LexicalScorer` over the catalog, top-k, k=5. The probe tests
whether the *channel* exists; retrieval quality cannot change that answer. Building a good
retriever here would only add a way to fool ourselves — this project has produced four
benchmarks-that-cannot-lose, and one of them (PD's perfect internal retriever) was this exact
mistake. The scorer is injected, so it is swappable without touching wiring or logging.

## 5 · Log schema — `was_exposed` becomes `exposure`

`was_exposed: bool` → `exposure: "listed" | "disclosed" | "unexposed"`.

| State | Meaning | Signal |
|---|---|---|
| `listed` | was in the `tools/list` array | normal operation |
| `disclosed` | handed over by `find_tools` | arm B working as designed |
| `unexposed` | neither | **the true miss** — the point-A ranker was wrong |

**Why not keep the boolean.** Under a boolean, disclosure logs `false` and the moment arm B
ships, successful disclosures flood the miss channel and the error signal stops meaning
anything — we would have built the signal and destroyed it in one feature. The two records
have different statistics and different consumers: `disclosed` carries the agent's *query* and
feeds decision-point-C retrieval; `unexposed` is rare, unbiased, and feeds the point-A ranker.
Summing them buries the rare one under the common one.

**Why not an additive `disclosed_via` field.** It preserves a field whose `false` value then
means two things, forcing every consumer to remember a compound condition.

**Migration — none needed.** `was_exposed` is written in one place (`log.py:140`), set in one
place (`server.py:147`), and asserted at six test sites. **Nothing reads it**: `load_sessions`
parses only `tool_uid` from a call record (`replay/sessions.py:112-116`). The 28 existing log
files keep the old field and are all `true` (i.e. all `listed`).

An earlier draft of this spec called for a normalizer in `replay/sessions.py` mapping
`was_exposed → exposure` on read. That was dropped: with no consumer it would be dead code
written to satisfy a document. The first reader that actually needs both vocabularies should
add the mapping, with a test, at that point.

`log.py`'s module docstring — which lists `was_exposed` among three non-retrofittable fields —
is rewritten to describe the three states and why they are distinct.

## 6 · The probe

### 6.1 · Two runs

| Condition | Gateway advertises | System prompt |
|---|---|---|
| `findtools` | core (3) + `find_tools` | neutral |
| `trusted` | core (3) only | names the withheld tool's exact advertised name as an operator instruction |

The `trusted` run exists because the system prompt is the one channel never measured shut, and
unlike a tool result it *is* an operator instruction — so PICKUP §2b's refusal does not apply
to it. It is a **positive control**: it establishes whether the client filters at all,
independent of how the agent feels about `find_tools`.

### 6.2 · Fixture

**Task**, reused verbatim from `probe_descriptions` because it is already validated on the two
counts that matter — it forces a tool call, and its control reached the target in 3 turns:

> *"List the tag names of the 3 most recent releases in the modelcontextprotocol/servers
> repository."*

**Withheld target** — `github/list_releases`.
**Core** — `github/get_me`, `notion/API-get-self`, `playwright/browser_close`.

The core keeps GitHub visibly reachable, so the agent does not conclude the server is absent
and stop under the standing "say so if no MCP tool can do it" instruction, while no core tool
can list releases. **This core is a probe fixture, not a proposal.** Production membership is
an open question and nothing here answers it.

**How the core is produced.** No new machinery: `mode: "live"` (so `policy.decide` returns
`chosen` rather than the whole catalog), `selector: "static-set"`, and `pinned` set to the
three tools — `build_selector` already maps `static-set` to `StaticSet(pinned)`
(`selectors.py:96-99`). Both conditions share this; they differ only in `find_tools.enabled`
and the system prompt.

**This is the first live-mode run in the project.** Every session on disk is `mode: shadow`.
Live mode is exercised by tests but has never served a real client, so a failure here could be
a live-mode bug rather than a finding about the client — worth checking that the advertised
count is 4 (or 3, in `trusted`) before reading anything into the result.

Answers are never checked. The probe measures which tool was reached for.

### 6.3 · The measurement is three-layered

The model emitting a call and the gateway receiving one are different events, and the gap
between them is the whole ambiguity:

- **L1 — did the model emit it?** `ArmResult.tool_calls`. The `ToolUseBlock` is in the
  assistant message whether or not the client executes it, so this reads **model intent**.
- **L2 — did the gateway receive it?** A `kind: call` record for `github/list_releases` in
  `runs/probe-armb/{condition}/*.jsonl`. This reads **client forwarding**.
- **L3 — what state did we log?** `disclosed` vs `unexposed`.

**L1-without-L2 is the discriminator.** If the model emits the call and no record reaches us,
the client filtered it in flight — established inside the `findtools` run itself. This is why
the authoritative readout is the gateway's log and not `ArmResult` alone.

### 6.4 · Pre-registered reading

Fixed before the first run. *What result would make this fail?* — the `CLIENT FILTERS` row.

**The refusal gates come first**, in this order. Each states a way the run is *unreadable*
rather than negative, and every one of them describes a setup failure that would otherwise be
reported as a fact about the client:

| # | Condition | Why it is unreadable, not a finding |
|---|---|---|
| 1 | `findtools` wrote **no decision record** | The gateway never answered a `tools/list` — almost always the config failing to load and the process dying at startup, in which case the session saw no MCP tools at all and every field below is empty for that reason. |
| 2 | `findtools` advertised ≠ `len(CORE) + 1` | Live-mode or config problem. The count includes the meta-tool. |
| 3 | `find_tools` was **never called** | The probe did not exercise the thing it exists to test; the agent answered from the core or gave up. Fix framing. |
| 4 | `find_tools` was called but **never returned `TARGET`** | The model was never told the withheld tool exists, so a non-emission cannot be read as refusal. The retriever is a deliberate stub over ~95 tools and a bad or absent `query` discloses nothing — *called* is not *offered*. Fix the retrieval side (the query the agent is steered to write, or `k`), not the client. |
| 5 | `trusted` wrote no decision record, or advertised ≠ `len(CORE)` | Same as 1–2 for the control. Note the asymmetry: `trusted` expects `len(CORE)`, with **no** meta-tool. |

Gate 4 sits **after** the two emit branches, deliberately. A call the model emitted is a valid
measurement however it learned the name — a guessed name that gets forwarded demonstrates the
channel just as conclusively as a disclosed one. The gate guards only the branch that reads a
*non*-emission as a fact about the model, which is the single step requiring that the model was
ever told the tool exists.

**`findtools` — substantive readings:**

| L1 emit | L2 arrive | Reading |
|---|---|---|
| ✅ | ✅ | **CHANNEL OPEN.** Arm B is real — `find_tools` + ranker + bandit, no collection and no cache cost. `exposure: disclosed` fires for the first time in the project. |
| ✅ | ❌ | **CLIENT FILTERS.** Registration is required; arm B becomes `find_tools` + `list_changed`, gated on measuring the cache cost. |
| ❌ | — | Falls through to `trusted`, once gate 4 has confirmed the target was actually offered. |

**`trusted` — the control:**

| Outcome | Reading |
|---|---|
| emit + arrive | **MODEL DECLINES.** The client forwards such a call under operator instruction, so any `findtools` failure is about trust in tool output — a framing problem, and attackable. |
| emit, no arrive | **CLIENT FILTERS**, shown by the control. |
| no emit | **STRUCTURAL.** The model will not call an unadvertised name even when the system prompt names it exactly. Not promptable-around; arm B requires `list_changed`. The strongest negative available, and therefore the one behind the most gates. |

### 6.5 · Traps designed against

- **§5.1 — a probe task must force a tool call.** Inherited by reusing a validated task.
- **§5.2 — configs must live beside their `.env`.** Written to `base.parent`, never an output
  dir. `from_file` resolves `.env`, `log_dir` and `catalog_path` relative to the config's own
  parent and `_expand` *raises* on an unresolvable `${VAR}`; a config written elsewhere kills
  the gateway at startup and the session sees no MCP tools at all — indistinguishable from a
  null result.
- **§5.3 — sentinels get paraphrased.** The `find_tools` result carries one **unbroken** token,
  so "result was read" is distinguishable from "result was ignored" without a substring match
  that paraphrasing defeats.
- **§5.4 — task closedness is an experimental variable.** The task stays closed, which is
  correct here: the agent has positive need for a tool it cannot see, unlike the suggestions
  probe where closedness removed its reason to comply.

### 6.6 · Output and cost

`results/probe_armb.json`. Two runs, ~$0.14. Run exactly as the existing probes
(subscription auth; `ANTHROPIC_API_KEY` must be UNSET) — see PICKUP §7.

## 7 · Invariants pinned by test

The obvious wrong implementation of `_exposed` passes every other test while corrupting the
one field the missing-demand signal depends on. Arm B adds a second route by which tools reach
the model, so these keep that honest:

1. **The meta-tool is not a catalog member.** With `find_tools` enabled, `n_candidates` and
   `catalog_hash` in the decision record are identical to disabled — this is what keeps arm A's
   numbers comparable across the change.
2. **`_exposed` holds pre-rewrite catalog keys only.** `("_gateway", "find_tools")` never
   enters it. Tested with an active `description_overrides` map, pinning the existing invariant
   and the new one together.
3. **Disclosure is not retroactive.** A call to a tool never listed and never disclosed logs
   `unexposed`, even when `find_tools` *would* have returned it for that query. Without this,
   enabling arm B quietly reclassifies every miss as a success.
4. **`listed` beats `disclosed`.** A core tool that `find_tools` also returns still logs
   `listed`.
5. **Disabled changes nothing the client can see.** With `find_tools` disabled the advertised
   list is exactly the selected set, and a call addressed to `_gateway__find_tools` routes to
   the pool and raises `UnknownUpstreamError` as any unknown server id would — the meta-tool is
   not merely hidden, it is not served. It also logs as `unexposed`, not `listed`: it was never
   in the array, so a call naming it is a hallucination like any other, and counting it as a
   hit would corrupt the exposure field in precisely the condition (`trusted`) that runs with
   the feature off.
6. **`_gateway` is reserved.** A config with an upstream of that id raises at load.

## 8 · Failure behaviour

`MetaTools.call` **never raises.** A failed search returns explanatory text as tool *content* —
the normal MCP shape, since a tool error is a result rather than a protocol error — leaving the
agent able to retry with a different query where an exception would kill the turn.

It **never fabricates**: on a raising scorer or an empty catalog it says so rather than
returning arbitrary tools. Returning the first k on a broken search is worse than failing,
because the agent cannot tell the difference and we would have built a way to silently mislead
it.

`Gateway`'s dispatch branch is wrapped: an unexpected exception logs at exception level and
returns an error result rather than propagating. Consistent with `policy.py`'s standing rule —
a proxy that can take the client down is unsellable at any ranking quality.

`_gateway/find_tools` gets its own `kind: call` record with `exposure: listed` **when the
feature is enabled**, since it genuinely was in the array. That is how we know it was called and
what it cost. With the feature disabled it was not in the array, so the same name logs
`unexposed` (§7.5).

**That record also carries `query` and the uids it disclosed — on the failure path too**, where
they are the query as given and an empty list. A failed meta call that omitted both would write
a record shape-identical to an ordinary tool call, making the one record that most needs to be
identifiable indistinguishable from every other. Without the pair, §5's argument
for a three-state field is unsupported, because nothing would in fact hold the query. The pair
is what makes the record training data rather than an audit trail: the agent states what it
wanted, and the subsequent call says which candidate it then chose. That is the task-grain
signal [decision point A](../../intuitions.md) structurally cannot obtain, and it is the reason
`disclosed` and `unexposed` must not share a field.

Logging a user-authored query string is a privacy surface the exposure log did not previously
have. It is in scope to record it for this probe, whose queries are our own; it is **not**
decided here whether a production gateway logs query text, hashes it, or drops it.

## 9 · Tests

| File | Covers |
|---|---|
| `tests/gateway/test_metatools.py` | pure, no I/O: advertise shape, `handles`, top-k with schemas, disclosed-key set, malformed arguments, empty catalog, raising scorer |
| `tests/gateway/test_exposure_states.py` | all three states through a `Gateway` with a fake pool, following `test_server.py`'s pattern |
| `tests/gateway/test_config.py` | reserved id, `find_tools` defaults |
| existing six `was_exposed` sites + `test_log.py` | updated to the enum |

### What the tests deliberately cannot cover

**Whether the client forwards a call for an unadvertised tool.** That is unknowable offline —
it is the entire reason the probe costs money. A green suite means the gateway would *serve*
such a call correctly and *log* it correctly. It says nothing about whether one will ever
arrive. Passing tests are not a working arm B.

## 10 · What this spec does not decide

- **Pinned-core membership in production.** There are no gold labels in a live gateway. Open.
- **`k`.** Swept offline across 0.176 → 0.549; a default chosen from our own chart is
  circular. The probe uses 5 because the probe does not test ranking.
- **Whether arm B is the product.** PICKUP §6 asks whether personalization or the control plane
  is the product. This probe informs that question and does not answer it.
