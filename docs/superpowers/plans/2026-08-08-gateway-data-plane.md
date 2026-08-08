# Gateway Data Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A real MCP gateway that aggregates N upstream servers, selects which tools to expose under a token budget, logs every decision, and runs in the author's own Claude Code in shadow mode.

**Architecture:** One stdio MCP server process. `UpstreamPool` holds MCP client sessions to each upstream and aggregates their catalogs under namespaced tool names. `Policy` wraps the existing `Selector` protocol and fails open to a pinned core. `ExposureLog` writes append-only JSONL. `server.py` binds it all to the MCP protocol. Everything in `src/mcp_gateway_router/` is reused unchanged as the selection library.

**Tech Stack:** Python ≥3.11, official `mcp` Python SDK (stdio transport, `ClientSession`, low-level `Server`), `anthropic` for `count_tokens`, `pytest` + `pytest-asyncio`.

## Global Constraints

- **Python ≥3.11.** Package layout is `src/mcp_gateway_router/`, built with hatchling.
- **Use `uv run python`, never bare `python`.** Working invocation for dev:
  `uv run --extra dev --extra tokens python ...`
- **The selection library is reused, not modified.** `catalog.py`, `selector.py`,
  `baselines.py`, `embedding.py`, `tokens.py` are consumed as-is. If a task appears to
  require changing one of their interfaces, **stop and report it** — the spec says that is
  a finding about the interface, not a chore.
- **Tool identity is `(server_id, name)`** plus a schema content hash. Never a positional
  index, never a re-derived `sorted()` over the observed population.
- **Token costs come from `AnthropicTokenCounter`** (`messages.count_tokens`). Never
  `tiktoken`.
- **Arms differ by config only.** Upstream set, `mode`, and `arm` are configuration. Any
  behavioural difference between arms other than tool selection is a confound.
- **Fail-open is a hard requirement in this plan, not a later phase.** Any exception in
  scoring or selection results in the pinned core being served, never an error to the
  client.
- **Three log fields are non-retrofittable and must be present from the first commit:**
  `propensity` (1.0 for deterministic arms), `was_exposed`, `cached_input_tokens`.
- **Commit after every task.** The repo is currently entirely untracked; Task 1 makes the
  first commit.

**Out of scope for this plan** (separate plans): the Q2 temporal-replay harness, the Q1
Agent SDK benchmark, arm implementations beyond the pass-through and pinned-core defaults.

---

### Task 1: Repository baseline and dependencies

**Files:**
- Modify: `pyproject.toml`
- Create: `.gitignore` entries (verify existing)
- Create: `src/mcp_gateway_router/gateway/__init__.py`

**Interfaces:**
- Consumes: nothing
- Produces: a `gateway` extra installing `mcp>=1.2`, and an importable
  `mcp_gateway_router.gateway` package

- [ ] **Step 1: Confirm the current test suite passes before touching anything**

Run: `uv run --extra dev --with datasets --with sentence-transformers pytest -q`
Expected: all tests pass. Record the count — later tasks must not reduce it.

- [ ] **Step 2: Add the gateway extra to `pyproject.toml`**

In `[project.optional-dependencies]`, add:

```toml
# Phase 2 data plane: the MCP protocol itself, client and server side.
gateway = ["mcp>=1.2", "anyio>=4.0"]
```

And extend the `dev` extra:

```toml
dev = ["pytest>=8.0", "pytest-asyncio>=0.24"]
```

- [ ] **Step 3: Add asyncio config so async tests run without per-test markers**

Append to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 4: Create the gateway package**

Create `src/mcp_gateway_router/gateway/__init__.py`:

```python
"""The Phase 2 data plane: an MCP proxy that selects which tools to expose.

Transport only. All ranking and budget logic lives in the selection library one
package up (``catalog``, ``selector``, ``baselines``, ``tokens``) and is consumed
here unchanged.
"""
```

- [ ] **Step 5: Verify the package imports and deps resolve**

Run: `uv run --extra dev --extra gateway python -c "import mcp; from mcp_gateway_router import gateway; print('ok', mcp.__name__)"`
Expected: prints `ok mcp`

- [ ] **Step 6: Make the first commit of the repository**

```bash
git add -A
git commit -m "chore: initial commit — Phase 0 harness, docs, gateway package skeleton"
```

---

### Task 2: Gateway configuration

**Files:**
- Create: `src/mcp_gateway_router/gateway/config.py`
- Test: `tests/gateway/test_config.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `UpstreamSpec(server_id: str, command: str, args: tuple[str, ...], env: dict[str, str])`
  - `GatewayConfig(upstreams: tuple[UpstreamSpec, ...], mode: str, arm: str, budget_tokens: int, pinned: tuple[tuple[str, str], ...], log_path: Path, model: str)`
  - `GatewayConfig.from_file(path: Path) -> GatewayConfig`
  - `MODES = ("shadow", "live")`

- [ ] **Step 1: Write the failing test**

Create `tests/gateway/__init__.py` (empty) and `tests/gateway/test_config.py`:

```python
import json
import pytest

from mcp_gateway_router.gateway.config import GatewayConfig, UpstreamSpec


def _payload(**overrides):
    base = {
        "mode": "shadow",
        "arm": "passthrough",
        "budget_tokens": 3000,
        "log_path": "runs/exposure.jsonl",
        "pinned": [["github", "search_code"]],
        "upstreams": [
            {"server_id": "github", "command": "npx", "args": ["-y", "gh-mcp"]},
        ],
    }
    base.update(overrides)
    return base


def test_from_file_parses_upstreams_and_pinned(tmp_path):
    path = tmp_path / "gateway.json"
    path.write_text(json.dumps(_payload()))

    config = GatewayConfig.from_file(path)

    assert config.mode == "shadow"
    assert config.budget_tokens == 3000
    assert config.pinned == (("github", "search_code"),)
    assert config.upstreams == (
        UpstreamSpec(server_id="github", command="npx", args=("-y", "gh-mcp"), env={}),
    )


def test_log_path_is_resolved_relative_to_the_config_file(tmp_path):
    path = tmp_path / "gateway.json"
    path.write_text(json.dumps(_payload()))

    config = GatewayConfig.from_file(path)

    assert config.log_path == tmp_path / "runs" / "exposure.jsonl"


def test_unknown_mode_is_rejected(tmp_path):
    path = tmp_path / "gateway.json"
    path.write_text(json.dumps(_payload(mode="turbo")))

    with pytest.raises(ValueError, match="turbo"):
        GatewayConfig.from_file(path)


def test_server_id_containing_the_namespace_separator_is_rejected(tmp_path):
    path = tmp_path / "gateway.json"
    path.write_text(
        json.dumps(
            _payload(
                upstreams=[{"server_id": "git__hub", "command": "npx", "args": []}]
            )
        )
    )

    with pytest.raises(ValueError, match="__"):
        GatewayConfig.from_file(path)


def test_duplicate_server_ids_are_rejected(tmp_path):
    path = tmp_path / "gateway.json"
    path.write_text(
        json.dumps(
            _payload(
                upstreams=[
                    {"server_id": "github", "command": "a", "args": []},
                    {"server_id": "github", "command": "b", "args": []},
                ]
            )
        )
    )

    with pytest.raises(ValueError, match="duplicate"):
        GatewayConfig.from_file(path)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --extra dev --extra gateway pytest tests/gateway/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_gateway_router.gateway.config'`

- [ ] **Step 3: Write the implementation**

Create `src/mcp_gateway_router/gateway/config.py`:

```python
"""Gateway configuration.

Upstream set, mode and arm are *configuration*, never code. Every benchmark arm runs
the same binary; anything that differs between arms other than tool selection is a
confound.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

MODES = ("shadow", "live")

# Separates server id from tool name in the name advertised to the client. Server ids
# may not contain it, so a single split from the left always recovers the pair even
# when the tool's own name contains a double underscore.
NAMESPACE_SEP = "__"


@dataclass(frozen=True)
class UpstreamSpec:
    """How to start and talk to one upstream MCP server."""

    server_id: str
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict, hash=False, compare=True)


@dataclass(frozen=True)
class GatewayConfig:
    upstreams: tuple[UpstreamSpec, ...]
    mode: str
    arm: str
    budget_tokens: int
    pinned: tuple[tuple[str, str], ...]
    log_path: Path
    model: str = "claude-opus-5"

    @classmethod
    def from_file(cls, path: Path) -> "GatewayConfig":
        path = Path(path)
        payload = json.loads(path.read_text())

        mode = payload.get("mode", "shadow")
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")

        upstreams: list[UpstreamSpec] = []
        seen: set[str] = set()
        for raw in payload.get("upstreams", []):
            server_id = raw["server_id"]
            if NAMESPACE_SEP in server_id:
                raise ValueError(
                    f"server_id {server_id!r} may not contain {NAMESPACE_SEP!r}"
                )
            if server_id in seen:
                raise ValueError(f"duplicate server_id: {server_id!r}")
            seen.add(server_id)
            upstreams.append(
                UpstreamSpec(
                    server_id=server_id,
                    command=raw["command"],
                    args=tuple(raw.get("args", ())),
                    env=dict(raw.get("env", {})),
                )
            )

        log_path = Path(payload.get("log_path", "runs/exposure.jsonl"))
        if not log_path.is_absolute():
            log_path = path.parent / log_path

        return cls(
            upstreams=tuple(upstreams),
            mode=mode,
            arm=payload.get("arm", "passthrough"),
            budget_tokens=int(payload.get("budget_tokens", 3000)),
            pinned=tuple(tuple(p) for p in payload.get("pinned", [])),
            log_path=log_path,
            model=payload.get("model", "claude-opus-5"),
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --extra dev --extra gateway pytest tests/gateway/test_config.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/mcp_gateway_router/gateway/config.py tests/gateway/
git commit -m "feat(gateway): configuration with upstream and mode validation"
```

---

### Task 3: Exposure log

**Files:**
- Create: `src/mcp_gateway_router/gateway/log.py`
- Test: `tests/gateway/test_log.py`

**Interfaces:**
- Consumes: `Tool` from `mcp_gateway_router.catalog`
- Produces:
  - `ExposureLog(path: Path)`
  - `.decision(session_id, arm, decision_point, catalog_hash, selector_version, budget_tokens, context, n_candidates, exposed) -> None` where `exposed: list[ExposedTool]`
  - `.call(session_id, tool_uid, was_exposed, status, latency_ms) -> None`
  - `.turn(session_id, input_tokens, cached_input_tokens, output_tokens) -> None`
  - `ExposedTool(tool_uid: str, score: float, propensity: float, token_cost: int)`
  - `.close() -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/gateway/test_log.py`:

```python
import json

from mcp_gateway_router.gateway.log import ExposedTool, ExposureLog


def _records(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_decision_record_carries_propensity_and_token_cost(tmp_path):
    path = tmp_path / "exposure.jsonl"
    log = ExposureLog(path)

    log.decision(
        session_id="s1",
        arm="passthrough",
        decision_point="A",
        catalog_hash="abc123",
        selector_version="passthrough/1",
        budget_tokens=3000,
        context={"repo": "mcp-gateway-router", "branch": "main"},
        n_candidates=400,
        exposed=[ExposedTool("github/search_code@aa", 1.0, 1.0, 180)],
    )
    log.close()

    (record,) = _records(path)
    assert record["kind"] == "decision"
    assert record["decision_point"] == "A"
    assert record["context"]["repo"] == "mcp-gateway-router"
    assert record["exposed"][0]["propensity"] == 1.0
    assert record["exposed"][0]["token_cost"] == 180


def test_call_record_carries_was_exposed(tmp_path):
    path = tmp_path / "exposure.jsonl"
    log = ExposureLog(path)

    log.call(
        session_id="s1",
        tool_uid="notion/search@bb",
        was_exposed=False,
        status="error",
        latency_ms=12,
    )
    log.close()

    (record,) = _records(path)
    assert record["kind"] == "call"
    assert record["was_exposed"] is False
    assert record["status"] == "error"


def test_turn_record_separates_cached_input_tokens(tmp_path):
    path = tmp_path / "exposure.jsonl"
    log = ExposureLog(path)

    log.turn(
        session_id="s1",
        input_tokens=1200,
        cached_input_tokens=900,
        output_tokens=64,
    )
    log.close()

    (record,) = _records(path)
    assert record["kind"] == "turn"
    assert record["cached_input_tokens"] == 900


def test_every_record_is_timestamped_and_appends(tmp_path):
    path = tmp_path / "exposure.jsonl"

    log = ExposureLog(path)
    log.turn(session_id="s1", input_tokens=1, cached_input_tokens=0, output_tokens=1)
    log.close()

    reopened = ExposureLog(path)
    reopened.turn(session_id="s2", input_tokens=2, cached_input_tokens=0, output_tokens=1)
    reopened.close()

    records = _records(path)
    assert [r["session_id"] for r in records] == ["s1", "s2"]
    assert all(r["ts"] for r in records)


def test_parent_directory_is_created(tmp_path):
    path = tmp_path / "nested" / "deeper" / "exposure.jsonl"
    log = ExposureLog(path)
    log.turn(session_id="s1", input_tokens=1, cached_input_tokens=0, output_tokens=1)
    log.close()

    assert path.exists()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --extra dev --extra gateway pytest tests/gateway/test_log.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_gateway_router.gateway.log'`

- [ ] **Step 3: Write the implementation**

Create `src/mcp_gateway_router/gateway/log.py`:

```python
"""Append-only exposure log.

A gateway is the single choke point that sees what it exposed, what got called, and
what came back. This is that record.

Three fields exist for phases that have not been built yet and are **not
retrofittable** — their absence cannot be reconstructed from later data:

``propensity``            the probability this tool was exposed on this decision. 1.0
                          for every deterministic arm; recorded anyway, because it is
                          the field's absence that cannot be undone, not its value.
``was_exposed``           whether a called tool was in the advertised set. The only
                          direct evidence of demand for tools we are not serving.
``cached_input_tokens``   prompt-cache behaviour, and therefore the invalidation cost
                          of changing the tool list.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ExposedTool:
    tool_uid: str
    score: float
    propensity: float
    token_cost: int


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExposureLog:
    """One JSONL file, opened in append mode, flushed per record.

    Flushing every record costs throughput and buys the thing that matters here: a
    crashed or killed session still leaves a readable log.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._path.open("a", encoding="utf-8")

    def _write(self, record: dict[str, Any]) -> None:
        self._handle.write(json.dumps(record, sort_keys=True) + "\n")
        self._handle.flush()

    def decision(
        self,
        *,
        session_id: str,
        arm: str,
        decision_point: str,
        catalog_hash: str,
        selector_version: str,
        budget_tokens: int,
        context: dict[str, Any],
        n_candidates: int,
        exposed: list[ExposedTool],
    ) -> None:
        self._write(
            {
                "kind": "decision",
                "ts": _now(),
                "session_id": session_id,
                "arm": arm,
                "decision_point": decision_point,
                "catalog_hash": catalog_hash,
                "selector_version": selector_version,
                "budget_tokens": budget_tokens,
                "context": context,
                "n_candidates": n_candidates,
                "exposed": [asdict(e) for e in exposed],
            }
        )

    def call(
        self,
        *,
        session_id: str,
        tool_uid: str,
        was_exposed: bool,
        status: str,
        latency_ms: int,
    ) -> None:
        self._write(
            {
                "kind": "call",
                "ts": _now(),
                "session_id": session_id,
                "tool_uid": tool_uid,
                "was_exposed": was_exposed,
                "status": status,
                "latency_ms": latency_ms,
            }
        )

    def turn(
        self,
        *,
        session_id: str,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
    ) -> None:
        self._write(
            {
                "kind": "turn",
                "ts": _now(),
                "session_id": session_id,
                "input_tokens": input_tokens,
                "cached_input_tokens": cached_input_tokens,
                "output_tokens": output_tokens,
            }
        )

    def close(self) -> None:
        self._handle.close()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --extra dev --extra gateway pytest tests/gateway/test_log.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/mcp_gateway_router/gateway/log.py tests/gateway/test_log.py
git commit -m "feat(gateway): append-only exposure log with non-retrofittable fields"
```

---

### Task 4: Tool namespacing

**Files:**
- Create: `src/mcp_gateway_router/gateway/naming.py`
- Test: `tests/gateway/test_naming.py`

**Interfaces:**
- Consumes: `NAMESPACE_SEP` from `gateway.config`, `Tool` from `catalog`
- Produces:
  - `advertised_name(tool: Tool) -> str`
  - `parse_advertised(name: str) -> tuple[str, str]` returning `(server_id, tool_name)`
  - `AdvertisedNameError(ValueError)`

- [ ] **Step 1: Write the failing test**

Create `tests/gateway/test_naming.py`:

```python
import pytest

from mcp_gateway_router.catalog import Tool
from mcp_gateway_router.gateway.naming import (
    AdvertisedNameError,
    advertised_name,
    parse_advertised,
)


def _tool(server_id="github", name="search_code"):
    return Tool(server_id=server_id, name=name, description="d", input_schema={})


def test_advertised_name_joins_server_and_tool():
    assert advertised_name(_tool()) == "github__search_code"


def test_round_trip():
    tool = _tool()
    assert parse_advertised(advertised_name(tool)) == ("github", "search_code")


def test_tool_name_may_itself_contain_the_separator():
    tool = _tool(name="get__user__profile")
    assert parse_advertised(advertised_name(tool)) == ("github", "get__user__profile")


def test_unqualified_name_is_rejected():
    with pytest.raises(AdvertisedNameError, match="search_code"):
        parse_advertised("search_code")


def test_empty_server_id_is_rejected():
    with pytest.raises(AdvertisedNameError):
        parse_advertised("__search_code")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --extra dev --extra gateway pytest tests/gateway/test_naming.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_gateway_router.gateway.naming'`

- [ ] **Step 3: Write the implementation**

Create `src/mcp_gateway_router/gateway/naming.py`:

```python
"""Names advertised to the client.

Tool names collide across servers — ``search`` exists on several. The client sees one
flat namespace, so the gateway qualifies every name with its server id. Server ids are
validated at config load to contain no ``__``, which makes a single split from the left
unambiguous even when the tool's own name contains one.
"""

from __future__ import annotations

from ..catalog import Tool
from .config import NAMESPACE_SEP


class AdvertisedNameError(ValueError):
    """An advertised tool name that does not resolve to (server_id, tool_name)."""


def advertised_name(tool: Tool) -> str:
    return f"{tool.server_id}{NAMESPACE_SEP}{tool.name}"


def parse_advertised(name: str) -> tuple[str, str]:
    server_id, separator, tool_name = name.partition(NAMESPACE_SEP)
    if not separator or not server_id or not tool_name:
        raise AdvertisedNameError(
            f"{name!r} is not a qualified tool name "
            f"(expected 'server_id{NAMESPACE_SEP}tool_name')"
        )
    return server_id, tool_name
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --extra dev --extra gateway pytest tests/gateway/test_naming.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/mcp_gateway_router/gateway/naming.py tests/gateway/test_naming.py
git commit -m "feat(gateway): server-qualified tool names"
```

---

### Task 5: Upstream pool

**Files:**
- Create: `src/mcp_gateway_router/gateway/upstream.py`
- Test: `tests/gateway/test_upstream.py`

**Interfaces:**
- Consumes: `UpstreamSpec` (Task 2), `Tool`/`Catalog` from `catalog`
- Produces:
  - `UpstreamPool(specs, session_factory=None)`
  - `async .start() -> None`
  - `async .aggregate() -> Catalog`
  - `async .call(server_id: str, tool_name: str, arguments: dict) -> Any`
  - `async .aclose() -> None`
  - `.catalog_hash() -> str`
  - `UnknownUpstreamError(KeyError)`

`session_factory` is an async callable `(UpstreamSpec) -> UpstreamSession`, where
`UpstreamSession` has `async list_tools()` and `async call_tool(name, arguments)`.
Injecting it is what makes this testable without spawning real servers.

- [ ] **Step 1: Write the failing test**

Create `tests/gateway/test_upstream.py`:

```python
import pytest

from mcp_gateway_router.gateway.config import UpstreamSpec
from mcp_gateway_router.gateway.upstream import UnknownUpstreamError, UpstreamPool


class FakeTool:
    def __init__(self, name, description, schema):
        self.name = name
        self.description = description
        self.inputSchema = schema


class FakeSession:
    def __init__(self, tools, fail_on=None):
        self._tools = tools
        self._fail_on = fail_on
        self.calls = []
        self.closed = False

    async def list_tools(self):
        return self._tools

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == self._fail_on:
            raise RuntimeError("upstream exploded")
        return {"ok": name}

    async def aclose(self):
        self.closed = True


def _factory(mapping):
    async def factory(spec):
        return mapping[spec.server_id]

    return factory


def _spec(server_id):
    return UpstreamSpec(server_id=server_id, command="noop", args=())


async def test_aggregate_namespaces_by_server():
    sessions = {
        "github": FakeSession([FakeTool("search", "find code", {"type": "object"})]),
        "notion": FakeSession([FakeTool("search", "find pages", {"type": "object"})]),
    }
    pool = UpstreamPool(
        [_spec("github"), _spec("notion")], session_factory=_factory(sessions)
    )
    await pool.start()

    catalog = await pool.aggregate()

    assert len(catalog) == 2
    assert catalog.get("github", "search").description == "find code"
    assert catalog.get("notion", "search").description == "find pages"
    await pool.aclose()


async def test_call_routes_to_the_right_upstream_with_the_bare_name():
    sessions = {
        "github": FakeSession([FakeTool("search", "d", {})]),
        "notion": FakeSession([FakeTool("search", "d", {})]),
    }
    pool = UpstreamPool(
        [_spec("github"), _spec("notion")], session_factory=_factory(sessions)
    )
    await pool.start()

    result = await pool.call("notion", "search", {"q": "x"})

    assert result == {"ok": "search"}
    assert sessions["notion"].calls == [("search", {"q": "x"})]
    assert sessions["github"].calls == []
    await pool.aclose()


async def test_unknown_upstream_raises():
    pool = UpstreamPool([_spec("github")], session_factory=_factory({"github": FakeSession([])}))
    await pool.start()

    with pytest.raises(UnknownUpstreamError, match="slack"):
        await pool.call("slack", "search", {})
    await pool.aclose()


async def test_a_failing_upstream_does_not_break_aggregation():
    class Exploding(FakeSession):
        async def list_tools(self):
            raise RuntimeError("no")

    sessions = {
        "github": FakeSession([FakeTool("search", "d", {})]),
        "broken": Exploding([]),
    }
    pool = UpstreamPool(
        [_spec("github"), _spec("broken")], session_factory=_factory(sessions)
    )
    await pool.start()

    catalog = await pool.aggregate()

    assert len(catalog) == 1
    assert catalog.get("github", "search") is not None
    await pool.aclose()


async def test_catalog_hash_changes_when_a_schema_changes():
    first = {"github": FakeSession([FakeTool("search", "d", {"type": "object"})])}
    pool = UpstreamPool([_spec("github")], session_factory=_factory(first))
    await pool.start()
    await pool.aggregate()
    before = pool.catalog_hash()
    await pool.aclose()

    second = {
        "github": FakeSession(
            [FakeTool("search", "d", {"type": "object", "properties": {"q": {}}})]
        )
    }
    pool = UpstreamPool([_spec("github")], session_factory=_factory(second))
    await pool.start()
    await pool.aggregate()
    after = pool.catalog_hash()
    await pool.aclose()

    assert before != after


async def test_aclose_closes_every_session():
    sessions = {"github": FakeSession([]), "notion": FakeSession([])}
    pool = UpstreamPool(
        [_spec("github"), _spec("notion")], session_factory=_factory(sessions)
    )
    await pool.start()
    await pool.aclose()

    assert all(s.closed for s in sessions.values())
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --extra dev --extra gateway pytest tests/gateway/test_upstream.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_gateway_router.gateway.upstream'`

- [ ] **Step 3: Write the implementation**

Create `src/mcp_gateway_router/gateway/upstream.py`:

```python
"""Connections to the upstream MCP servers, and the aggregated catalog.

An upstream that fails to answer ``tools/list`` is skipped rather than fatal. The
gateway is in the critical path of every session: one broken server must degrade the
catalog, never take the client down.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Awaitable, Callable, Iterable, Protocol

from ..catalog import Catalog, Tool
from .config import UpstreamSpec

logger = logging.getLogger(__name__)


class UnknownUpstreamError(KeyError):
    """A call addressed to a server that is not in the pool."""


class UpstreamSession(Protocol):
    async def list_tools(self) -> list[Any]: ...
    async def call_tool(self, name: str, arguments: dict) -> Any: ...
    async def aclose(self) -> None: ...


SessionFactory = Callable[[UpstreamSpec], Awaitable[UpstreamSession]]


async def _stdio_session_factory(spec: UpstreamSpec) -> UpstreamSession:
    """Real transport. Imported lazily so tests never need the mcp package."""
    from .stdio_session import StdioUpstreamSession

    session = StdioUpstreamSession(spec)
    await session.start()
    return session


class UpstreamPool:
    def __init__(
        self,
        specs: Iterable[UpstreamSpec],
        session_factory: SessionFactory | None = None,
    ) -> None:
        self._specs = list(specs)
        self._factory = session_factory or _stdio_session_factory
        self._sessions: dict[str, UpstreamSession] = {}
        self._catalog: Catalog | None = None
        self._catalog_hash: str = ""

    async def start(self) -> None:
        for spec in self._specs:
            try:
                self._sessions[spec.server_id] = await self._factory(spec)
            except Exception:
                logger.exception("upstream %s failed to start", spec.server_id)

    async def aggregate(self) -> Catalog:
        tools: list[Tool] = []
        for server_id, session in self._sessions.items():
            try:
                advertised = await session.list_tools()
            except Exception:
                logger.exception("upstream %s failed tools/list", server_id)
                continue
            for raw in advertised:
                tools.append(
                    Tool(
                        server_id=server_id,
                        name=raw.name,
                        description=raw.description or "",
                        input_schema=dict(getattr(raw, "inputSchema", None) or {}),
                    )
                )

        self._catalog = Catalog(tools)
        self._catalog_hash = hashlib.sha256(
            "\n".join(sorted(t.uid for t in self._catalog)).encode()
        ).hexdigest()[:16]
        return self._catalog

    def catalog_hash(self) -> str:
        return self._catalog_hash

    async def call(self, server_id: str, tool_name: str, arguments: dict) -> Any:
        session = self._sessions.get(server_id)
        if session is None:
            raise UnknownUpstreamError(f"no upstream named {server_id!r}")
        return await session.call_tool(tool_name, arguments)

    async def aclose(self) -> None:
        for server_id, session in self._sessions.items():
            try:
                await session.aclose()
            except Exception:
                logger.exception("upstream %s failed to close", server_id)
        self._sessions.clear()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --extra dev --extra gateway pytest tests/gateway/test_upstream.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/mcp_gateway_router/gateway/upstream.py tests/gateway/test_upstream.py
git commit -m "feat(gateway): upstream pool with per-server isolation on failure"
```

---

### Task 6: Policy — selection, shadow mode, and fail-open

**Files:**
- Create: `src/mcp_gateway_router/gateway/policy.py`
- Test: `tests/gateway/test_policy.py`

**Interfaces:**
- Consumes: `DecisionContext`, `Selector` from `selector`; `TokenCounter` from `tokens`;
  `ExposureLog`, `ExposedTool` from Task 3; `GatewayConfig` from Task 2
- Produces:
  - `Policy(config, selector, counter, log)`
  - `.decide(catalog: Catalog, catalog_hash: str, context: DecisionContext) -> list[Tool]`
  - `.pinned_tools(catalog: Catalog) -> list[Tool]`

`decide` returns what the client should be advertised. In `shadow` mode it returns the
**whole catalog** while logging the selection it would have made; in `live` mode it
returns the selection.

- [ ] **Step 1: Write the failing test**

Create `tests/gateway/test_policy.py`:

```python
import json

from mcp_gateway_router.catalog import Catalog, Tool
from mcp_gateway_router.gateway.config import GatewayConfig
from mcp_gateway_router.gateway.log import ExposureLog
from mcp_gateway_router.gateway.policy import Policy
from mcp_gateway_router.selector import DecisionContext
from mcp_gateway_router.tokens import StaticTokenCounter


def _catalog():
    return Catalog(
        [
            Tool(server_id="github", name=f"t{i}", description=f"tool {i}", input_schema={})
            for i in range(10)
        ]
    )


def _config(tmp_path, mode="live", pinned=(("github", "t0"),), budget=250):
    return GatewayConfig(
        upstreams=(),
        mode=mode,
        arm="test-arm",
        budget_tokens=budget,
        pinned=tuple(pinned),
        log_path=tmp_path / "exposure.jsonl",
    )


class HeadSelector:
    """Ranks tools in catalog order. Deterministic, so propensity is 1.0."""

    name = "head"

    def select(self, context, catalog, budget, counter):
        from mcp_gateway_router.selector import fill_budget

        return fill_budget(list(catalog), budget, counter)


class ExplodingSelector:
    name = "exploding"

    def select(self, context, catalog, budget, counter):
        raise RuntimeError("scorer is cold")


def _records(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_live_mode_returns_the_selection(tmp_path):
    config = _config(tmp_path, mode="live")
    log = ExposureLog(config.log_path)
    policy = Policy(config, HeadSelector(), StaticTokenCounter({}, default=100), log)

    exposed = policy.decide(_catalog(), "hash1", DecisionContext(session_id="s1"))
    log.close()

    # Pinned t0 is taken first and is exempt from the budget, spending 100 of 250.
    # t1 fits at 200; t2 would reach 300 and is skipped, as is everything after it.
    assert [t.name for t in exposed] == ["t0", "t1"]


def test_shadow_mode_returns_everything_but_logs_the_selection(tmp_path):
    config = _config(tmp_path, mode="shadow")
    log = ExposureLog(config.log_path)
    policy = Policy(config, HeadSelector(), StaticTokenCounter({}, default=100), log)

    exposed = policy.decide(_catalog(), "hash1", DecisionContext(session_id="s1"))
    log.close()

    assert len(exposed) == 10
    (record,) = _records(config.log_path)
    assert len(record["exposed"]) == 2


def test_a_raising_selector_fails_open_to_the_pinned_core(tmp_path):
    config = _config(tmp_path, mode="live")
    log = ExposureLog(config.log_path)
    policy = Policy(config, ExplodingSelector(), StaticTokenCounter({}, default=100), log)

    exposed = policy.decide(_catalog(), "hash1", DecisionContext(session_id="s1"))
    log.close()

    assert [t.name for t in exposed] == ["t0"]
    (record,) = _records(config.log_path)
    assert record["selector_version"] == "fail-open"


def test_decision_is_logged_with_propensity_and_context(tmp_path):
    config = _config(tmp_path, mode="live")
    log = ExposureLog(config.log_path)
    policy = Policy(config, HeadSelector(), StaticTokenCounter({}, default=100), log)

    context = DecisionContext(session_id="s1", client_name="claude-code")
    policy.decide(_catalog(), "hash1", context)
    log.close()

    (record,) = _records(config.log_path)
    assert record["arm"] == "test-arm"
    assert record["decision_point"] == "A"
    assert record["catalog_hash"] == "hash1"
    assert record["n_candidates"] == 10
    assert record["context"]["client_name"] == "claude-code"
    assert all(e["propensity"] == 1.0 for e in record["exposed"])


def test_decision_point_is_c_when_a_task_is_present(tmp_path):
    config = _config(tmp_path, mode="live")
    log = ExposureLog(config.log_path)
    policy = Policy(config, HeadSelector(), StaticTokenCounter({}, default=100), log)

    policy.decide(_catalog(), "h", DecisionContext(session_id="s1", task="find the PRs"))
    log.close()

    (record,) = _records(config.log_path)
    assert record["decision_point"] == "C"


def test_a_pinned_tool_missing_from_the_catalog_is_skipped_not_fatal(tmp_path):
    config = _config(tmp_path, mode="live", pinned=(("github", "t0"), ("slack", "gone")))
    log = ExposureLog(config.log_path)
    policy = Policy(config, HeadSelector(), StaticTokenCounter({}, default=100), log)

    exposed = policy.decide(_catalog(), "h", DecisionContext(session_id="s1"))
    log.close()

    assert "t0" in [t.name for t in exposed]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --extra dev --extra gateway pytest tests/gateway/test_policy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_gateway_router.gateway.policy'`

- [ ] **Step 3: Write the implementation**

Create `src/mcp_gateway_router/gateway/policy.py`:

```python
"""Selection, shadow mode, and fail-open.

A thin adapter. It owns no ranking logic — it builds a ``DecisionContext``, hands it to
the existing ``Selector`` protocol, and records what happened.

**Fail-open is the point of this module.** The gateway sits in the critical path of
every session; if the scorer raises, is cold, or hangs, the client still receives a
working tool set. A proxy that can take a client down is unsellable at any ranking
quality, and this is cheaper to build now than to retrofit.
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from ..catalog import Catalog, Tool
from ..selector import DecisionContext, Selector
from ..tokens import TokenCounter
from .config import GatewayConfig
from .log import ExposedTool, ExposureLog

logger = logging.getLogger(__name__)


def _context_payload(context: DecisionContext) -> dict:
    payload = asdict(context)
    # tools_called is a tuple of tuples; JSON-friendly and cheap to read back.
    payload["tools_called"] = [list(k) for k in context.tools_called]
    payload["scopes"] = list(context.scopes)
    return payload


class Policy:
    def __init__(
        self,
        config: GatewayConfig,
        selector: Selector,
        counter: TokenCounter,
        log: ExposureLog,
    ) -> None:
        self._config = config
        self._selector = selector
        self._counter = counter
        self._log = log

    def pinned_tools(self, catalog: Catalog) -> list[Tool]:
        """Pinned entries that are actually present. A stale pin is not fatal."""
        found = []
        for server_id, name in self._config.pinned:
            tool = catalog.get(server_id, name)
            if tool is None:
                logger.warning("pinned tool %s/%s is not in the catalog", server_id, name)
                continue
            found.append(tool)
        return found

    def decide(
        self,
        catalog: Catalog,
        catalog_hash: str,
        context: DecisionContext,
    ) -> list[Tool]:
        pinned = self.pinned_tools(catalog)
        version = self._selector.name

        try:
            chosen = self._selector.select(
                context, catalog, self._config.budget_tokens, self._counter
            )
        except Exception:
            logger.exception("selector %s failed; failing open to pinned core", version)
            chosen = pinned
            version = "fail-open"

        self._log.decision(
            session_id=context.session_id or "unknown",
            arm=self._config.arm,
            decision_point="C" if context.task else "A",
            catalog_hash=catalog_hash,
            selector_version=version,
            budget_tokens=self._config.budget_tokens,
            context=_context_payload(context),
            n_candidates=len(catalog),
            exposed=[
                ExposedTool(
                    tool_uid=tool.uid,
                    score=0.0,
                    propensity=1.0,
                    token_cost=self._counter.cost(tool),
                )
                for tool in chosen
            ],
        )

        if self._config.mode == "shadow":
            return list(catalog)
        return chosen
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --extra dev --extra gateway pytest tests/gateway/test_policy.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/mcp_gateway_router/gateway/policy.py tests/gateway/test_policy.py
git commit -m "feat(gateway): policy with shadow mode and fail-open to pinned core"
```

---

### Task 7: Real stdio transport for upstreams

**Files:**
- Create: `src/mcp_gateway_router/gateway/stdio_session.py`
- Test: `tests/gateway/test_stdio_session.py`

**Interfaces:**
- Consumes: `UpstreamSpec` (Task 2)
- Produces: `StdioUpstreamSession(spec)` with `async start()`, `async list_tools()`,
  `async call_tool(name, arguments)`, `async aclose()` — satisfying the
  `UpstreamSession` protocol from Task 5

This is the one module that touches the real `mcp` package. It is tested against a
real, trivial MCP server started as a subprocess, because mocking the SDK's transport
would test the mock.

- [ ] **Step 1: Write the failing test with a real echo server**

Create `tests/gateway/echo_server.py`:

```python
"""A minimal real MCP server, used as a subprocess in transport tests."""

import anyio
import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

app = Server("echo")


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="echo",
            description="Echo the message back.",
            inputSchema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        )
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=arguments["message"])]


async def main() -> None:
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


if __name__ == "__main__":
    anyio.run(main)
```

Create `tests/gateway/test_stdio_session.py`:

```python
import sys
from pathlib import Path

from mcp_gateway_router.gateway.config import UpstreamSpec
from mcp_gateway_router.gateway.stdio_session import StdioUpstreamSession

ECHO = str(Path(__file__).parent / "echo_server.py")


def _spec():
    return UpstreamSpec(server_id="echo", command=sys.executable, args=(ECHO,))


async def test_list_tools_reaches_a_real_server():
    session = StdioUpstreamSession(_spec())
    await session.start()
    try:
        tools = await session.list_tools()
    finally:
        await session.aclose()

    assert [t.name for t in tools] == ["echo"]
    assert tools[0].inputSchema["properties"]["message"]["type"] == "string"


async def test_call_tool_round_trips():
    session = StdioUpstreamSession(_spec())
    await session.start()
    try:
        result = await session.call_tool("echo", {"message": "hello"})
    finally:
        await session.aclose()

    assert result.content[0].text == "hello"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --extra dev --extra gateway pytest tests/gateway/test_stdio_session.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_gateway_router.gateway.stdio_session'`

- [ ] **Step 3: Write the implementation**

Create `src/mcp_gateway_router/gateway/stdio_session.py`:

```python
"""One upstream MCP server, spoken to over stdio.

``stdio_client`` and ``ClientSession`` are async context managers, but the pool's
lifetime is not lexically scoped — sessions are opened in ``start()`` and closed in
``aclose()``. ``AsyncExitStack`` is what bridges the two: enter the contexts onto the
stack, and unwind the whole stack on close.
"""

from __future__ import annotations

import os
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import UpstreamSpec


class StdioUpstreamSession:
    def __init__(self, spec: UpstreamSpec) -> None:
        self._spec = spec
        self._stack = AsyncExitStack()
        self._session: ClientSession | None = None

    async def start(self) -> None:
        params = StdioServerParameters(
            command=self._spec.command,
            args=list(self._spec.args),
            env={**os.environ, **self._spec.env} if self._spec.env else None,
        )
        read, write = await self._stack.enter_async_context(stdio_client(params))
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._session = session

    def _require(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError(f"upstream {self._spec.server_id!r} is not started")
        return self._session

    async def list_tools(self) -> list[Any]:
        return (await self._require().list_tools()).tools

    async def call_tool(self, name: str, arguments: dict) -> Any:
        return await self._require().call_tool(name, arguments)

    async def aclose(self) -> None:
        self._session = None
        await self._stack.aclose()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --extra dev --extra gateway pytest tests/gateway/test_stdio_session.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/mcp_gateway_router/gateway/stdio_session.py tests/gateway/test_stdio_session.py tests/gateway/echo_server.py
git commit -m "feat(gateway): real stdio transport, tested against a live MCP server"
```

---

### Task 8: The MCP server facing the client

**Files:**
- Create: `src/mcp_gateway_router/gateway/server.py`
- Test: `tests/gateway/test_server.py`

**Interfaces:**
- Consumes: everything from Tasks 2–7
- Produces:
  - `Gateway(config, pool, policy, log)` with `async list_tools() -> list[Tool]` and
    `async call_tool(advertised: str, arguments: dict) -> Any`
  - `build_app(gateway) -> mcp.server.Server`
  - `async serve(config_path: Path) -> None`

`Gateway` holds the protocol-independent logic so it can be tested without a transport;
`build_app` is the thin binding to the MCP SDK.

- [ ] **Step 1: Write the failing test**

Create `tests/gateway/test_server.py`:

```python
import json

import pytest

from mcp_gateway_router.catalog import Catalog, Tool
from mcp_gateway_router.gateway.config import GatewayConfig, UpstreamSpec
from mcp_gateway_router.gateway.log import ExposureLog
from mcp_gateway_router.gateway.policy import Policy
from mcp_gateway_router.gateway.server import Gateway
from mcp_gateway_router.gateway.upstream import UpstreamPool
from mcp_gateway_router.tokens import StaticTokenCounter


class FakeTool:
    def __init__(self, name):
        self.name = name
        self.description = f"does {name}"
        self.inputSchema = {"type": "object"}


class FakeSession:
    def __init__(self, names):
        self._tools = [FakeTool(n) for n in names]
        self.calls = []
        self.closed = False

    async def list_tools(self):
        return self._tools

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return {"ok": name}

    async def aclose(self):
        self.closed = True


class HeadSelector:
    name = "head"

    def select(self, context, catalog, budget, counter):
        from mcp_gateway_router.selector import fill_budget

        return fill_budget(list(catalog), budget, counter)


def _records(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


async def _gateway(tmp_path, mode="live", budget=250):
    sessions = {"github": FakeSession(["a", "b", "c", "d"])}

    async def factory(spec):
        return sessions[spec.server_id]

    config = GatewayConfig(
        upstreams=(UpstreamSpec(server_id="github", command="noop"),),
        mode=mode,
        arm="test-arm",
        budget_tokens=budget,
        pinned=(),
        log_path=tmp_path / "exposure.jsonl",
    )
    pool = UpstreamPool(config.upstreams, session_factory=factory)
    await pool.start()
    log = ExposureLog(config.log_path)
    policy = Policy(config, HeadSelector(), StaticTokenCounter({}, default=100), log)
    return Gateway(config, pool, policy, log), sessions, log, config


async def test_list_tools_advertises_namespaced_names(tmp_path):
    gateway, _, log, _ = await _gateway(tmp_path)
    tools = await gateway.list_tools()
    log.close()

    assert [t.name for t in tools] == ["github__a", "github__b"]


async def test_shadow_mode_advertises_the_whole_catalog(tmp_path):
    gateway, _, log, config = await _gateway(tmp_path, mode="shadow")
    tools = await gateway.list_tools()
    log.close()

    assert len(tools) == 4
    (record,) = _records(config.log_path)
    assert len(record["exposed"]) == 2


async def test_call_tool_routes_and_strips_the_namespace(tmp_path):
    gateway, sessions, log, _ = await _gateway(tmp_path)
    await gateway.list_tools()

    result = await gateway.call_tool("github__a", {"x": 1})
    log.close()

    assert result == {"ok": "a"}
    assert sessions["github"].calls == [("a", {"x": 1})]


async def test_calling_an_unexposed_tool_is_logged_as_a_miss(tmp_path):
    gateway, _, log, config = await _gateway(tmp_path)
    await gateway.list_tools()

    await gateway.call_tool("github__d", {})
    log.close()

    calls = [r for r in _records(config.log_path) if r["kind"] == "call"]
    assert calls[0]["was_exposed"] is False


async def test_calling_an_exposed_tool_is_logged_as_a_hit(tmp_path):
    gateway, _, log, config = await _gateway(tmp_path)
    await gateway.list_tools()

    await gateway.call_tool("github__a", {})
    log.close()

    calls = [r for r in _records(config.log_path) if r["kind"] == "call"]
    assert calls[0]["was_exposed"] is True
    assert calls[0]["status"] == "ok"


async def test_an_unqualified_tool_name_is_an_error_not_a_crash(tmp_path):
    gateway, _, log, _ = await _gateway(tmp_path)
    await gateway.list_tools()

    with pytest.raises(ValueError):
        await gateway.call_tool("a", {})
    log.close()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --extra dev --extra gateway pytest tests/gateway/test_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_gateway_router.gateway.server'`

- [ ] **Step 3: Write the implementation**

Create `src/mcp_gateway_router/gateway/server.py`:

```python
"""The MCP server the client talks to.

``Gateway`` is protocol-independent and holds the logic; ``build_app`` binds it to the
MCP SDK. Keeping them apart is what lets the behaviour be tested without a transport.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from ..catalog import Tool
from ..selector import DecisionContext
from .config import GatewayConfig
from .naming import advertised_name, parse_advertised
from .policy import Policy
from .upstream import UpstreamPool

logger = logging.getLogger(__name__)


class Gateway:
    def __init__(
        self,
        config: GatewayConfig,
        pool: UpstreamPool,
        policy: Policy,
        log: ExposureLog,
    ) -> None:
        self._config = config
        self._pool = pool
        self._policy = policy
        self._log = log
        self._session_id = f"s-{int(time.time())}"
        self._exposed: set[tuple[str, str]] = set()
        self._called: list[tuple[str, str]] = []

    def _context(self) -> DecisionContext:
        """What the gateway knows at ``tools/list``.

        There is no prompt here — the client has not sent one and the protocol offers
        no channel for it. ``task`` stays ``None``, which is what makes this decision
        point A.
        """
        return DecisionContext(
            session_id=self._session_id,
            client_name="claude-code",
            tools_called=tuple(self._called),
        )

    async def list_tools(self) -> list[Tool]:
        catalog = await self._pool.aggregate()
        exposed = self._policy.decide(catalog, self._pool.catalog_hash(), self._context())
        self._exposed = {t.key for t in exposed}
        return exposed

    async def call_tool(self, advertised: str, arguments: dict) -> Any:
        server_id, tool_name = parse_advertised(advertised)
        key = (server_id, tool_name)
        started = time.monotonic()
        status = "ok"
        try:
            return await self._pool.call(server_id, tool_name, arguments)
        except Exception:
            status = "error"
            raise
        finally:
            self._called.append(key)
            self._log.call(
                session_id=self._session_id,
                tool_uid=f"{server_id}/{tool_name}",
                was_exposed=key in self._exposed,
                status=status,
                latency_ms=int((time.monotonic() - started) * 1000),
            )


def build_app(gateway: Gateway):
    """Bind a ``Gateway`` to the MCP SDK's low-level server."""
    import mcp.types as types
    from mcp.server import Server

    app = Server("mcp-gateway")

    @app.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=advertised_name(tool),
                description=tool.description,
                inputSchema=tool.input_schema or {"type": "object", "properties": {}},
            )
            for tool in await gateway.list_tools()
        ]

    @app.call_tool()
    async def _call_tool(name: str, arguments: dict) -> Any:
        result = await gateway.call_tool(name, arguments)
        return getattr(result, "content", result)

    return app


async def serve(config_path: Path) -> None:
    from mcp.server.stdio import stdio_server

    from ..tokens import AnthropicTokenCounter
    from .log import ExposureLog

    config = GatewayConfig.from_file(config_path)
    pool = UpstreamPool(config.upstreams)
    await pool.start()

    log = ExposureLog(config.log_path)
    counter = AnthropicTokenCounter(
        model=config.model,
        cache_path=config.log_path.parent / "token_costs.json",
    )

    from ..baselines import StaticSet

    selector = StaticSet([list(p) for p in config.pinned])
    policy = Policy(config, selector, counter, log)
    gateway = Gateway(config, pool, policy, log)
    app = build_app(gateway)

    try:
        async with stdio_server() as (read, write):
            await app.run(read, write, app.create_initialization_options())
    finally:
        await pool.aclose()
        log.close()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --extra dev --extra gateway pytest tests/gateway/test_server.py -v`
Expected: 6 passed

- [ ] **Step 5: Check `StaticSet`'s real constructor signature before trusting `serve`**

Run: `uv run python -c "import inspect; from mcp_gateway_router.baselines import StaticSet; print(inspect.signature(StaticSet.__init__))"`

If the signature differs from `StaticSet(keys)`, fix the call in `serve` to match and
re-run the full suite. Do **not** change `baselines.py`.

- [ ] **Step 6: Commit**

```bash
git add src/mcp_gateway_router/gateway/server.py tests/gateway/test_server.py
git commit -m "feat(gateway): MCP server with namespaced exposure and miss logging"
```

---

### Task 9: Chaos test for fail-open

**Files:**
- Test: `tests/gateway/test_fail_open.py`

**Interfaces:**
- Consumes: `Gateway`, `Policy`, `UpstreamPool` from Tasks 5–8
- Produces: nothing — this task exists to make a stated architectural requirement
  falsifiable

The spec makes fail-open a hard v0 requirement and lists a chaos test on the pass/fail
correctness bar. Task 6 covers a selector that raises. This covers the rest.

- [ ] **Step 1: Write the failing test**

Create `tests/gateway/test_fail_open.py`:

```python
import json

from mcp_gateway_router.catalog import Catalog, Tool
from mcp_gateway_router.gateway.config import GatewayConfig, UpstreamSpec
from mcp_gateway_router.gateway.log import ExposureLog
from mcp_gateway_router.gateway.policy import Policy
from mcp_gateway_router.gateway.server import Gateway
from mcp_gateway_router.gateway.upstream import UpstreamPool
from mcp_gateway_router.tokens import StaticTokenCounter


class FakeTool:
    def __init__(self, name):
        self.name = name
        self.description = f"does {name}"
        self.inputSchema = {"type": "object"}


class FakeSession:
    def __init__(self, names):
        self._tools = [FakeTool(n) for n in names]

    async def list_tools(self):
        return self._tools

    async def call_tool(self, name, arguments):
        return {"ok": name}

    async def aclose(self):
        pass


class MidSessionFailure:
    """Works once, then breaks — the ranker dying mid-session."""

    name = "flaky"

    def __init__(self):
        self.calls = 0

    def select(self, context, catalog, budget, counter):
        self.calls += 1
        if self.calls > 1:
            raise RuntimeError("ranker died")
        from mcp_gateway_router.selector import fill_budget

        return fill_budget(list(catalog), budget, counter)


async def _build(tmp_path, selector, counter):
    async def factory(spec):
        return FakeSession(["a", "b", "c"])

    config = GatewayConfig(
        upstreams=(UpstreamSpec(server_id="github", command="noop"),),
        mode="live",
        arm="chaos",
        budget_tokens=250,
        pinned=(("github", "a"),),
        log_path=tmp_path / "exposure.jsonl",
    )
    pool = UpstreamPool(config.upstreams, session_factory=factory)
    await pool.start()
    log = ExposureLog(config.log_path)
    policy = Policy(config, selector, counter, log)
    return Gateway(config, pool, policy, log), log, config


async def test_client_still_gets_a_working_set_when_the_ranker_dies_mid_session(tmp_path):
    selector = MidSessionFailure()
    gateway, log, _ = await _build(tmp_path, selector, StaticTokenCounter({}, default=100))

    first = await gateway.list_tools()
    second = await gateway.list_tools()
    log.close()

    assert len(first) > 0
    assert [t.name for t in second] == ["a"]


async def test_the_gateway_never_raises_out_of_list_tools_when_scoring_is_broken(tmp_path):
    class Exploding:
        name = "exploding"

        def select(self, context, catalog, budget, counter):
            raise RuntimeError("cold")

    gateway, log, _ = await _build(tmp_path, Exploding(), StaticTokenCounter({}, default=100))

    exposed = await gateway.list_tools()
    log.close()

    assert [t.name for t in exposed] == ["a"]


async def test_every_upstream_down_yields_an_empty_set_not_an_exception(tmp_path):
    class Dead:
        async def list_tools(self):
            raise RuntimeError("down")

        async def call_tool(self, name, arguments):
            raise RuntimeError("down")

        async def aclose(self):
            pass

    async def factory(spec):
        return Dead()

    config = GatewayConfig(
        upstreams=(UpstreamSpec(server_id="github", command="noop"),),
        mode="live",
        arm="chaos",
        budget_tokens=250,
        pinned=(("github", "a"),),
        log_path=tmp_path / "exposure.jsonl",
    )
    pool = UpstreamPool(config.upstreams, session_factory=factory)
    await pool.start()
    log = ExposureLog(config.log_path)

    class HeadSelector:
        name = "head"

        def select(self, context, catalog, budget, counter):
            from mcp_gateway_router.selector import fill_budget

            return fill_budget(list(catalog), budget, counter)

    policy = Policy(config, HeadSelector(), StaticTokenCounter({}, default=100), log)
    gateway = Gateway(config, pool, policy, log)

    exposed = await gateway.list_tools()
    log.close()

    assert exposed == []
```

- [ ] **Step 2: Run the tests**

Run: `uv run --extra dev --extra gateway pytest tests/gateway/test_fail_open.py -v`
Expected: the first two pass if Task 6 is correct. If any fail, **fix `policy.py` or
`server.py`, not the test** — the requirement is the spec's, not the test's.

- [ ] **Step 3: Commit**

```bash
git add tests/gateway/test_fail_open.py
git commit -m "test(gateway): chaos tests pinning the fail-open requirement"
```

---

### Task 10: Catalog harvester and real token costs

**Files:**
- Create: `src/mcp_gateway_router/harvest.py`
- Test: `tests/test_harvest.py`

**Interfaces:**
- Consumes: `UpstreamPool` (Task 5), `GatewayConfig` (Task 2), `Catalog`/`Tool`,
  `AnthropicTokenCounter`
- Produces:
  - `catalog_to_json(catalog: Catalog, costs: dict[str, int]) -> dict`
  - `catalog_from_json(payload: dict) -> Catalog`
  - `async harvest(config: GatewayConfig, count_tokens: bool) -> tuple[Catalog, dict[str, int]]`
  - CLI: `python -m mcp_gateway_router.harvest --config gateway.json --out results/catalog.json [--count-tokens]`

This is handoff next-action 6. It retires the flat 120-tokens-per-tool placeholder and
produces the artifact both the gateway and the offline harness read.

- [ ] **Step 1: Write the failing test**

Create `tests/test_harvest.py`:

```python
import json

from mcp_gateway_router.catalog import Catalog, Tool
from mcp_gateway_router.gateway.config import GatewayConfig, UpstreamSpec
from mcp_gateway_router.harvest import catalog_from_json, catalog_to_json, harvest


class FakeTool:
    def __init__(self, name, schema):
        self.name = name
        self.description = f"does {name}"
        self.inputSchema = schema


class FakeSession:
    def __init__(self, tools):
        self._tools = tools

    async def list_tools(self):
        return self._tools

    async def call_tool(self, name, arguments):
        return None

    async def aclose(self):
        pass


def test_round_trip_preserves_identity_and_costs():
    catalog = Catalog(
        [
            Tool(server_id="github", name="search", description="find", input_schema={"a": 1}),
            Tool(server_id="notion", name="search", description="pages", input_schema={"b": 2}),
        ]
    )
    costs = {t.uid: 100 + i for i, t in enumerate(catalog)}

    payload = catalog_to_json(catalog, costs)
    restored = catalog_from_json(payload)

    assert len(restored) == 2
    assert {t.uid for t in restored} == {t.uid for t in catalog}
    assert payload["costs"] == costs


def test_payload_is_json_serialisable():
    catalog = Catalog([Tool(server_id="s", name="t", description="d", input_schema={})])
    json.dumps(catalog_to_json(catalog, {}))


async def test_harvest_aggregates_without_counting_tokens(tmp_path):
    config = GatewayConfig(
        upstreams=(UpstreamSpec(server_id="github", command="noop"),),
        mode="shadow",
        arm="passthrough",
        budget_tokens=3000,
        pinned=(),
        log_path=tmp_path / "exposure.jsonl",
    )

    async def factory(spec):
        return FakeSession([FakeTool("search", {"type": "object"})])

    catalog, costs = await harvest(config, count_tokens=False, session_factory=factory)

    assert len(catalog) == 1
    assert costs == {}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --extra dev --extra gateway pytest tests/test_harvest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_gateway_router.harvest'`

- [ ] **Step 3: Write the implementation**

Create `src/mcp_gateway_router/harvest.py`:

```python
"""Harvest a real MCP catalog and measure what its schemas actually cost.

Every token number in the repo before this ran assumed a flat 120 tokens per tool.
Real schemas vary by roughly 85x, and under a flat cost ``fill_budget`` is a top-K cut
wearing a disguise. This is what makes the knapsack a knapsack.

Built as a component rather than a one-off dump: this *is* the Phase 2 catalog service.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .catalog import Catalog, Tool
from .gateway.config import GatewayConfig
from .gateway.upstream import UpstreamPool


def catalog_to_json(catalog: Catalog, costs: dict[str, int]) -> dict:
    return {
        "tools": [
            {
                "server_id": t.server_id,
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
                "is_write": t.is_write,
            }
            for t in catalog
        ],
        "costs": costs,
    }


def catalog_from_json(payload: dict) -> Catalog:
    return Catalog(
        Tool(
            server_id=raw["server_id"],
            name=raw["name"],
            description=raw.get("description", ""),
            input_schema=raw.get("input_schema", {}),
            is_write=raw.get("is_write", False),
        )
        for raw in payload["tools"]
    )


async def harvest(
    config: GatewayConfig,
    count_tokens: bool,
    session_factory=None,
    cache_path: Path | None = None,
) -> tuple[Catalog, dict[str, int]]:
    pool = UpstreamPool(config.upstreams, session_factory=session_factory)
    await pool.start()
    try:
        catalog = await pool.aggregate()
    finally:
        await pool.aclose()

    if not count_tokens:
        return catalog, {}

    from .tokens import AnthropicTokenCounter

    counter = AnthropicTokenCounter(model=config.model, cache_path=cache_path)
    counter.prewarm(
        catalog,
        on_progress=lambda done, total: print(f"  counted {done}/{total}", flush=True),
    )
    return catalog, {t.uid: counter.cost(t) for t in catalog}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--count-tokens", action="store_true")
    args = parser.parse_args()

    config = GatewayConfig.from_file(args.config)
    catalog, costs = asyncio.run(
        harvest(
            config,
            count_tokens=args.count_tokens,
            cache_path=args.out.parent / "token_costs.json",
        )
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(catalog_to_json(catalog, costs), indent=2))

    print(f"{len(catalog)} tools from {len(config.upstreams)} servers -> {args.out}")
    if costs:
        values = sorted(costs.values())
        print(
            f"token cost: median {values[len(values) // 2]}, "
            f"min {values[0]}, max {values[-1]}, "
            f"spread {values[-1] / max(values[0], 1):.0f}x"
        )


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --extra dev --extra gateway pytest tests/test_harvest.py -v`
Expected: 3 passed

- [ ] **Step 5: Run the whole suite to confirm nothing regressed**

Run: `uv run --extra dev --extra gateway --with datasets --with sentence-transformers pytest -q`
Expected: all tests pass, count ≥ the number recorded in Task 1 Step 1.

- [ ] **Step 6: Commit**

```bash
git add src/mcp_gateway_router/harvest.py tests/test_harvest.py
git commit -m "feat: catalog harvester with real count_tokens measurement"
```

---

### Task 11: Run it against the real installed servers

**Files:**
- Create: `gateway.json`
- Modify: `.gitignore` (add `runs/`)
- Create: `docs/gateway-setup.md`

**Interfaces:**
- Consumes: everything above
- Produces: `results/catalog.json` — the real harvested catalog with measured costs

This task has no unit tests; its deliverable is a measurement. It is the first point at
which any of this touches reality.

- [ ] **Step 1: Write the gateway config for the currently installed servers**

Create `gateway.json`:

```json
{
  "mode": "shadow",
  "arm": "passthrough",
  "budget_tokens": 3000,
  "log_path": "runs/exposure.jsonl",
  "pinned": [],
  "model": "claude-opus-5",
  "upstreams": [
    {
      "server_id": "github",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"]
    },
    {
      "server_id": "playwright",
      "command": "npx",
      "args": ["-y", "@playwright/mcp@latest"]
    }
  ]
}
```

Note: `notion` is configured as a remote/HTTP server in `~/.claude.json` rather than
stdio. **Do not guess its stdio invocation.** Leave it out of this file and record it in
Task 12's open items — HTTP upstream support is not in this plan.

- [ ] **Step 2: Add the run directory to `.gitignore`**

Append to `.gitignore`:

```
runs/
```

- [ ] **Step 3: Harvest without token counting first, to confirm the transport works**

Run: `uv run --extra gateway python -m mcp_gateway_router.harvest --config gateway.json --out results/catalog.json`
Expected: prints a tool count > 0. If an upstream fails, the log line names it and the
run still succeeds with a smaller catalog — that is the designed behaviour, not a bug.

- [ ] **Step 4: Confirm credentials exist, then harvest with token counting**

Run: `uv run --extra gateway --extra tokens python -c "import anthropic; anthropic.Anthropic(); print('credentials ok')"`

If this fails, **stop and report**. `ANTHROPIC_API_KEY` is a known blocker in
`docs/session-handoff.md` and it is a human's to resolve. Everything above this step is
still complete and committed.

If it succeeds:

Run: `uv run --extra gateway --extra tokens python -m mcp_gateway_router.harvest --config gateway.json --out results/catalog.json --count-tokens`
Expected: prints the tool count and a `median / min / max / spread` line.

- [ ] **Step 5: Record the measured spread**

Create `docs/gateway-setup.md` containing: how to run the harvester, the config format,
the measured tool count per server, and the measured token-cost spread from Step 4.
**Write the actual numbers observed**, not a placeholder.

- [ ] **Step 6: Commit**

```bash
git add gateway.json .gitignore docs/gateway-setup.md results/catalog.json
git commit -m "feat: harvest the real installed MCP catalog with measured token costs"
```

---

### Task 12: Wire the gateway into Claude Code in shadow mode

**Files:**
- Create: `src/mcp_gateway_router/gateway/__main__.py`
- Modify: `docs/gateway-setup.md`
- Modify: `docs/session-handoff.md`

**Interfaces:**
- Consumes: `serve` (Task 8)
- Produces: a runnable `python -m mcp_gateway_router.gateway --config gateway.json`

- [ ] **Step 1: Write the entry point**

Create `src/mcp_gateway_router/gateway/__main__.py`:

```python
"""Run the gateway as an MCP server over stdio.

Logging goes to stderr and never stdout — stdout is the MCP transport, and a stray
print corrupts the protocol stream.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .server import serve


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        stream=sys.stderr,
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    asyncio.run(serve(args.config))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it starts and speaks MCP without a client**

Run: `uv run --extra gateway --extra tokens python -m mcp_gateway_router.gateway --config gateway.json --log-level DEBUG`

Expected: it starts, logs upstream connections to stderr, and blocks waiting on stdin.
Kill it with Ctrl-C. **If anything appears on stdout, that is a bug** — find the print
and route it to stderr before continuing.

- [ ] **Step 3: Register it with Claude Code as an additional server**

Add to `.claude/settings.local.json` or via `claude mcp add`. Keep the existing servers
in place for now — shadow mode passes everything through, so running both means
duplicate tools in the session. Prefer a **separate test project directory** with only
the gateway configured.

Document the exact invocation used in `docs/gateway-setup.md`.

- [ ] **Step 4: Open a session and confirm end-to-end behaviour**

In the test project, start Claude Code and ask it to list what tools it has, then use
one. Then check:

Run: `python -c "import json,sys; [print(json.loads(l)['kind'], json.loads(l).get('n_candidates',''), len(json.loads(l).get('exposed',[]))) for l in open('runs/exposure.jsonl')]"`

Expected: at least one `decision` record with a realistic `n_candidates`, and one
`call` record with `was_exposed`.

- [ ] **Step 5: Run the capture-rate probe (spec §11, Task 0b)**

Set `"mode": "live"` and `"budget_tokens": 400` in `gateway.json` so only a handful of
tools are exposed. Open a session and ask for something requiring a tool you can see is
*not* in the exposed set.

Record in `docs/gateway-setup.md` which of these happened:

- the call reached the gateway and appears with `was_exposed: false` → **the miss signal
  works on this client**
- no call record appears → **Claude Code filters unexposed calls; the miss signal is
  empty on this client**

**This is a prerequisite finding, not a nice-to-have.** The spec makes the entire
missing-demand thesis depend on it. Write down the answer either way.

- [ ] **Step 6: Update the handoff with what was learned**

In `docs/session-handoff.md`, update *Where things stand* and *Next actions* to reflect
that the data plane exists, and add to *Gotchas that will waste your time*:

- **Notion API tab-depth behaviour.** `update_content` injecting multi-line `new_str`
  puts the first line at the matched block's depth and every subsequent line at depth 0,
  silently un-nesting whatever followed. Prefixing a line with `\t` raises its depth by
  one, and there is no way to lower it. Repair mis-nested blocks by re-matching their
  text with a `\t` prefix; verify by fetching and counting leading tabs.

- [ ] **Step 7: Commit**

```bash
git add src/mcp_gateway_router/gateway/__main__.py docs/gateway-setup.md docs/session-handoff.md
git commit -m "feat(gateway): stdio entry point, wired into Claude Code in shadow mode"
```

---

## Deferred to later plans

- **Q2 temporal-replay harness** — `D-global`, `D-recent`, `D-context`, coverage curves.
  Consumes `runs/exposure.jsonl` and `results/catalog.json` from this plan.
- **Q1 Agent SDK benchmark** — arms A/B/C/R/O, the authored task battery, matched-cost
  comparison. Gated on spec Task 0 (native tool search controllable and observable under
  the Agent SDK), which is **not** covered by this plan.
- **HTTP/SSE upstream transport** — needed for `notion`, which is not stdio.
- **`find_tools` meta-tool** — arm B.
- **Warm upstream pool** — only matters once the benchmark pays boot cost per task.
- **Task-text injection channel.** Spec §4 requires the harness to hand the gateway the
  task before `tools/list`, which is what encodes decision point A versus C.
  `DecisionContext.task` already carries it and `Policy.decide` already reports point C
  when it is set, but nothing in this plan populates it — `Gateway._context()` always
  builds a point-A context. The channel (env var, config field, or a control tool)
  belongs with the Q1 benchmark that needs it.
- **Passthrough latency benchmark.** Spec §7 puts a sub-5ms p99 on the correctness bar.
  Task 9 covers fail-open; nothing here measures overhead. Needs the real transport
  under load, so it belongs after Task 11 has produced a real catalog.

## Open items this plan does not resolve

- The Gate 0 margin, the Q2 margin, and the collection stopping rule. All three are
  human decisions and all three must be numbers before results are read — but none of
  them block any task above.
- `notion` runs over HTTP, so this plan's gateway cannot proxy it. The catalog will be
  smaller than the spec's 300–500 target until either HTTP transport lands or more stdio
  servers are installed.
