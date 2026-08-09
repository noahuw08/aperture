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

## Measured catalog — 2026-08-08

**24 tools, playwright only.** Well short of the spec's 300–500 target.

| Server | Transport | Tools | Status |
|---|---|---|---|
| playwright | stdio | 24 | ✅ |
| github | http | — | ⛔ `401 AuthenticateToken authentication failed` |
| notion | http | — | ⛔ `401 invalid_token` |

Token costs: **not measured.** `ANTHROPIC_API_KEY` is unset, so the flat-120-per-tool
placeholder still stands and the knapsack is still a top-K in disguise.

## The two auth blockers

Both are the same shape: the gateway can reach the endpoints, and both reject it.

**github — expired credential.** The PAT in `~/.claude.json` is well-formed (40 chars)
but the endpoint returns `401 AuthenticateToken authentication failed`. Verified with a
raw `curl` POST of `initialize`, so it is the credential and not our transport. Fix is a
fresh token exported as `GITHUB_MCP_TOKEN`.

**notion — OAuth, which the gateway does not implement.** `https://mcp.notion.com/mcp`
returns `401 invalid_token`; there is no static token to supply. Claude Code performs an
interactive OAuth flow and caches the result. Proxying notion needs an OAuth client in
the gateway — discovery, authorization code flow, token storage and refresh. That is
materially more work than the HTTP transport was and is not in the current plan.

**Why this matters beyond the tool count.** Q2 replays *real sessions*. If the gateway
cannot proxy github and notion, the shadow-mode logs will not reflect the work actually
done, and the cross-session dataset the whole Q2 half depends on is unrepresentative.
This is a prerequisite, not a shortfall.

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
