# Gateway setup

_Written 2026-08-08. Status of the Phase 2 data plane and how to run it._

Design: [`superpowers/specs/2026-08-08-mvp-gateway-design.md`](superpowers/specs/2026-08-08-mvp-gateway-design.md) ·
Plan: [`superpowers/plans/2026-08-08-gateway-data-plane.md`](superpowers/plans/2026-08-08-gateway-data-plane.md)

## What works

Tasks 1–10 of the plan, plus streamable-HTTP transport. **132 tests passing**, up from
the 77-test Phase 0 baseline.

| Component | State |
|---|---|
| `gateway/config.py` — upstreams, modes, `${VAR}` expansion | ✅ built, tested |
| `gateway/log.py` — append-only JSONL, three record types | ✅ built, tested |
| `gateway/naming.py` — server-qualified tool names | ✅ built, tested |
| `gateway/upstream.py` — pool, aggregation, routing | ✅ built, tested |
| `gateway/policy.py` — shadow mode, fail-open | ✅ built, tested |
| `gateway/runner.py` — session lifetime, anyio scope pinning | ✅ built, tested |
| `gateway/stdio_session.py` | ✅ tested against a real MCP subprocess |
| `gateway/http_session.py` | ✅ tested against a real MCP server on a socket |
| `gateway/server.py` — `Gateway`, `build_app`, `serve` | ✅ built, tested |
| `harvest.py` — catalog + `count_tokens` | ✅ built; token counting **never run** |
| `gateway/__main__.py` — stdio entry point | ⬜ Task 12 |
| Wired into Claude Code | ⬜ Task 12 |

## Running the harvester

```bash
export GITHUB_MCP_TOKEN=...            # only if the github upstream is enabled
uv run --extra gateway python -m mcp_gateway_router.harvest \
    --config gateway.json --out results/catalog.json
```

Add `--extra tokens --count-tokens` to measure real per-schema token costs. That needs
`ANTHROPIC_API_KEY`.

## Config format

`gateway.json`. Secrets use `${VAR}` and are read from the environment — a missing
variable raises at load rather than shipping a literal `Bearer ${TOKEN}` header that
fails as a confusing 401 later.

```json
{
  "mode": "shadow",
  "budget_tokens": 3000,
  "log_path": "runs/exposure.jsonl",
  "upstreams": [
    {"server_id": "playwright", "transport": "stdio",
     "command": "npx", "args": ["-y", "@playwright/mcp@latest"]},
    {"server_id": "github", "transport": "http",
     "url": "https://api.githubcopilot.com/mcp/",
     "headers": {"Authorization": "Bearer ${GITHUB_MCP_TOKEN}"}}
  ]
}
```

`mode` is `shadow` (log the would-be selection, advertise everything) or `live`
(actually cut). `server_id` may not contain `__`, which is the namespace separator.

## Routing rule — one path per tool

**A tool must be reachable by exactly one route.** If it is registered directly in the
client *and* proxied by the gateway, calls route unpredictably: the gateway logs what it
advertised but misses whatever went direct, so the dataset is **biased rather than merely
incomplete**. It also makes the capture-rate probe return a false negative, because the
client always has a second route to the tool the probe deliberately withheld.

Verified the hard way: `~/gw-test` inherits the parent project's `github` and `playwright`
registrations, so it was never isolated — a gateway session there saw every tool twice.
`/mcp` shows the truth and should be checked before trusting any collected log.

Current routing: **everything through the gateway.** `github`, `playwright` and `notion`
are all proxied; none is registered directly in the client.

**Notion was briefly excluded and that was a mistake worth recording.** The stated reason
was that the stdio server exposes only the raw REST API (`API-patch-block-children`,
`API-update-a-block`, addressed by block ID) while the hosted OAuth server is
markdown-oriented with a convenient search-and-replace. That was asserted without checking.
`API-update-page-markdown` in fact takes `update_content`, `insert_content`,
`replace_content` and `replace_content_range` — the same operations, one nesting level
deeper:

```json
{"page_id": "...", "type": "update_content",
 "update_content": {"content_updates": [{"old_str": "...", "new_str": "..."}]}}
```

Verified against the live scoping page with a no-op replacement.

**The real difference is auth scope, not capability.** The hosted server uses OAuth and
sees everything the account can. The stdio server uses an internal integration token and
sees only pages explicitly shared with that integration. If a page returns 404 or 403
through the gateway, add the integration under that page's **Connections** — it is a
per-page grant, not a global one.

Excluding notion would have cost 24 of 95 tools and, more importantly, the server that
produced the cost-concentration finding — it is a quarter of the tools and over half the
schema bytes. Completeness of the dataset beat convenience.

## Measured catalog — 2026-08-08

**95 tools across three servers, all live.**

| Server | Transport | Tools | Median chars | Max chars | Total chars |
|---|---|---|---|---|---|
| github | http | 47 | 958 | 2,746 | 47,100 |
| notion | stdio | 24 | 2,909 | 5,743 | 73,554 |
| playwright | stdio | 24 | 707 | 1,438 | 14,860 |
| **all** | | **95** | **1,001** | **5,743** | **135,514** |

Size is `name + description + compact input_schema`, in characters.

### Finding 1 — schema size is decisively non-uniform, on a real catalog

**39× spread**, 146 characters (`playwright/browser_close`) to 5,743
(`notion/API-update-page-markdown`), median 1,001, p95 3,205.

Lower than the 85× measured over ToolRet's 37,292 tools, which is expected — a 95-tool
catalog has less room in the tails. It is more than large enough for the distinction to
bite: **`fill_budget` on this catalog is a genuine knapsack, not a top-K in disguise.**
That was the open question the harvest existed to settle, and it is settled even before
token counts land.

### Finding 2 — cost concentrates by server, and no doc says so

**Notion is 25% of the tools and 54% of the bytes.** Its median schema is 2,909
characters against playwright's 707 — 4×.

This is new. Every doc treats the exposure decision as per-tool ranking under a budget.
At real catalog composition, *which servers you connect* is itself a budget decision
sitting upstream of any ranker, and connecting one verbose server can cost more than
several terse ones combined. Worth carrying into the arm design: a selector that is
server-blind will systematically overspend on whichever upstream happens to be wordiest.

### Finding 3 — the whole catalog is a real recurring tax at this size

135,514 characters of schema sit in the prompt prefix on every model call. Converting to
tokens honestly needs `count_tokens`; a rough order of magnitude for dense JSON is
**35–45k tokens per call**, and that estimate should be replaced with the measurement
rather than cited.

### Token costs: still not measured

`ANTHROPIC_API_KEY` is valid, but the account has **zero credit balance** and that gates
the entire API:

```
400 invalid_request_error — Your credit balance is too low to access the Anthropic API.
```

**Correction to an earlier note in this repo:** `count_tokens` is free *per call*, but it
is not free to *reach* — the account still needs a non-zero balance. Adding the minimum
credit unblocks it; the measurement itself consumes none of it.

## Resolved blockers

**github — the PAT was expired.** Verified with a raw `curl` POST of `initialize`
(`401 AuthenticateToken authentication failed` on a well-formed 40-char token), which
ruled out our transport. A fresh token in `GITHUB_MCP_TOKEN` fixed it. 47 tools.

**notion — OAuth avoided entirely.** `https://mcp.notion.com/mcp` needs an interactive
OAuth flow that the gateway would have had to implement: discovery, authorization code
flow, token storage, refresh. Notion's official **stdio** server
(`@notionhq/notion-mcp-server`) takes a static internal-integration token instead, so the
upstream moved from `http` to `stdio` and the OAuth work disappeared. 24 tools.

⚠️ **The Notion integration must be granted access to pages explicitly** — Connections →
add integration, per page or teamspace. Without it the server authenticates cleanly and
returns an empty workspace, which looks like a working setup with no content.

## Still short of the spec's catalog target

95 tools against a 300–500 target. The budget bites at 95 — 39× spread and 135k
characters make that so — but the regime is thinner than the spec assumed. Adding public
stdio servers would close the count gap while making the traffic *less* representative,
since they would not appear in real sessions. That trade is a decision, not an oversight.

## Verified behaviour worth knowing

**Fail-open works in practice, not just in tests.** The harvest run above had two of
three upstreams reject the connection and still returned a usable 24-tool catalog with
no error to the caller. That is the designed behaviour.

**mcp 2.x, not 1.x.** The SDK dropped the `@server.list_tools()` decorators; `MCPServer`
is the high-level replacement and its handlers dispatch to `self.list_tools()` /
`self.call_tool()`, so a subclass overriding both is how a runtime-varying tool list is
served. `inputSchema` is now `input_schema`.

**Sessions must be owned by one task.** `stdio_client`, `streamable_http_client` and
`ClientSession` each open an anyio task group, and anyio requires a cancel scope to be
exited by the entering task. An `AsyncExitStack` entered in `start()` and unwound in
`aclose()` raises *"Attempted to exit cancel scope in a different task"*. `runner.py`
solves it by parking the contexts in a dedicated task for their whole lifetime; both
transports share it.
