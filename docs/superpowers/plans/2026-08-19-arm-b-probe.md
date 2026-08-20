# Arm B Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a `find_tools` meta-tool in the gateway and run two probe sessions that answer whether an MCP client will emit a `tools/call` for a tool that was never in the `tools/list` array.

**Architecture:** A new `MetaTools` collaborator owns the meta-tool's advertisement, its search, and its result text — pure, no I/O, no `mcp` imports, testable against a hand-built `Catalog`. `Gateway` gains three small touch points: append the meta-tool at `tools/list`, dispatch meta calls before reaching the upstream pool, and resolve a call's exposure state. The `was_exposed` boolean becomes a three-state `exposure` field so a disclosure (arm B working) is never confused with a miss (the ranker was wrong).

**Tech Stack:** Python 3.11+, `mcp` 2.x, `pytest` + `anyio`, `uv` for running. Model runs go through `claude-agent-sdk`.

**Spec:** [`docs/superpowers/specs/2026-08-19-arm-b-probe-design.md`](../specs/2026-08-19-arm-b-probe-design.md)

## Global Constraints

- **Run tests with:** `uv run --extra dev --extra gateway pytest -q`. Bare `pytest` and bare `python` are wrong for this repo.
- **`claude-agent-sdk` must never enter `pyproject.toml`.** It pins an older `mcp` than the gateway needs; uv's universal resolver rejects the pair and breaks `uv run pytest` entirely. Pass `--with claude-agent-sdk` per invocation.
- **Tool identity is `(server_id, name)`.** Never a positional index, never a re-derived `sorted()`.
- **`mcp` 2.x names the field `input_schema`**, not `inputSchema`.
- **Fail-open is architectural.** Nothing added here may raise out of `tools/list` or `call_tool` for reasons unrelated to the upstream call itself.
- **The meta-tool is never a `Catalog` member.** It must not be scored, cut, or counted in `n_candidates` / `catalog_hash`.
- **Sentinels must be one unbroken token.** `GATEWAY-HINT-7F3A` came back paraphrased as `GATEWAY-7F3A` in a previous probe and a substring match reported a false negative.
- **Probe configs are written to the base config's parent directory**, never an output dir. `from_file` resolves `.env`, `log_dir` and `catalog_path` relative to the config's own parent, and `_expand` *raises* on an unresolvable `${VAR}` — a config written elsewhere kills the gateway at startup and the session silently sees no MCP tools at all.

---

### Task 1: Three-state exposure in the log

**Files:**
- Modify: `src/mcp_gateway_router/gateway/log.py:1-16` (module docstring), `:125-144` (`call`)
- Modify: `src/mcp_gateway_router/gateway/server.py:147`
- Test: `tests/gateway/test_log.py:36-51`
- Test (update assertions only): `tests/gateway/test_server.py:107,118`, `tests/gateway/test_description_overrides.py:116`, `tests/gateway/test_result_suggestions.py:142`, `tests/replay/test_sessions.py:34`

**Interfaces:**
- Consumes: nothing.
- Produces: `EXPOSURE_LISTED = "listed"`, `EXPOSURE_DISCLOSED = "disclosed"`, `EXPOSURE_UNEXPOSED = "unexposed"`, `EXPOSURES` tuple, all exported from `mcp_gateway_router.gateway.log`. New signature `ExposureLog.call(*, session_id: str, tool_uid: str, exposure: str, status: str, latency_ms: int, query: str | None = None, disclosed: list[str] | None = None) -> None`.

- [ ] **Step 1: Write the failing tests**

Replace `test_call_record_carries_was_exposed` in `tests/gateway/test_log.py` with:

```python
import pytest

from mcp_gateway_router.gateway.log import (
    EXPOSURE_DISCLOSED,
    EXPOSURE_LISTED,
    ExposedTool,
    ExposureLog,
)


def test_call_record_carries_a_three_state_exposure(tmp_path):
    log = ExposureLog(tmp_path, session_id="s1")

    log.call(
        session_id="s1",
        tool_uid="notion/search@bb",
        exposure=EXPOSURE_DISCLOSED,
        status="error",
        latency_ms=12,
    )
    log.close()

    (record,) = _records(log)
    assert record["kind"] == "call"
    assert record["exposure"] == "disclosed"
    assert record["status"] == "error"
    assert "was_exposed" not in record


def test_an_unknown_exposure_state_is_rejected(tmp_path):
    """A typo must not become a silent fourth state.

    The whole point of the enum is that `disclosed` and `unexposed` mean different
    things downstream; a misspelling that lands in the log unchallenged would be
    indistinguishable from real data months later.
    """
    log = ExposureLog(tmp_path, session_id="s1")

    with pytest.raises(ValueError):
        log.call(
            session_id="s1",
            tool_uid="notion/search@bb",
            exposure="maybe",
            status="ok",
            latency_ms=1,
        )
    log.close()


def test_find_tools_records_carry_the_query_and_what_it_disclosed(tmp_path):
    log = ExposureLog(tmp_path, session_id="s1")

    log.call(
        session_id="s1",
        tool_uid="_gateway/find_tools",
        exposure=EXPOSURE_LISTED,
        status="ok",
        latency_ms=3,
        query="recent releases",
        disclosed=["github/list_releases", "github/get_latest_release"],
    )
    log.close()

    (record,) = _records(log)
    assert record["query"] == "recent releases"
    assert record["disclosed"] == ["github/list_releases", "github/get_latest_release"]


def test_ordinary_calls_omit_the_meta_fields(tmp_path):
    """Absent, not null. A `query: null` on every upstream call is noise in a file
    that is read by eye as often as by code."""
    log = ExposureLog(tmp_path, session_id="s1")

    log.call(
        session_id="s1",
        tool_uid="github/list_releases",
        exposure=EXPOSURE_LISTED,
        status="ok",
        latency_ms=5,
    )
    log.close()

    (record,) = _records(log)
    assert "query" not in record
    assert "disclosed" not in record
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --extra dev --extra gateway pytest tests/gateway/test_log.py -q`
Expected: FAIL — `ImportError: cannot import name 'EXPOSURE_DISCLOSED'`

- [ ] **Step 3: Implement the enum and the new signature**

In `src/mcp_gateway_router/gateway/log.py`, add after the imports:

```python
#: A called tool was in the ``tools/list`` array.
EXPOSURE_LISTED = "listed"
#: A called tool was handed to the model by ``find_tools``. Arm B working as designed.
EXPOSURE_DISCLOSED = "disclosed"
#: Neither. The true miss — the point-A ranker was wrong.
EXPOSURE_UNEXPOSED = "unexposed"

EXPOSURES = (EXPOSURE_LISTED, EXPOSURE_DISCLOSED, EXPOSURE_UNEXPOSED)
```

Replace the `call` method:

```python
    def call(
        self,
        *,
        session_id: str,
        tool_uid: str,
        exposure: str,
        status: str,
        latency_ms: int,
        query: str | None = None,
        disclosed: list[str] | None = None,
    ) -> None:
        """One tool call.

        ``query`` and ``disclosed`` are written only for meta-tool calls, where they
        are the whole point: the query is the task-grain text decision point A cannot
        see, and pairing it with what was disclosed — and then with which of those the
        model went on to call — is what makes the record training data rather than an
        audit trail.
        """
        if exposure not in EXPOSURES:
            raise ValueError(f"unknown exposure {exposure!r}; expected one of {EXPOSURES}")

        record: dict[str, Any] = {
            "kind": "call",
            "ts": _now(),
            "session_id": session_id,
            "tool_uid": tool_uid,
            "exposure": exposure,
            "status": status,
            "latency_ms": latency_ms,
        }
        if query is not None:
            record["query"] = query
        if disclosed is not None:
            record["disclosed"] = disclosed
        self._write(record)
```

Replace the `was_exposed` paragraph in the module docstring (lines 12-13) with:

```
``exposure``              ``listed`` (was in the tools/list array), ``disclosed``
                          (handed over by find_tools), or ``unexposed``. Only the
                          last is a miss. Collapsing the first two into a boolean
                          would mean a working find_tools floods the miss channel
                          with its own successes and destroys the signal in the same
                          feature that creates it.
```

- [ ] **Step 4: Update `server.py` to the new keyword**

At `src/mcp_gateway_router/gateway/server.py:147`, change `was_exposed=key in self._exposed,` to:

```python
                exposure=EXPOSURE_LISTED if key in self._exposed else EXPOSURE_UNEXPOSED,
```

and add to the imports at line 26:

```python
from .log import EXPOSURE_LISTED, EXPOSURE_UNEXPOSED, ExposureLog
```

This is a placeholder shape — Task 4 replaces it with `self._exposure(key)`. It exists so the suite is green between tasks.

- [ ] **Step 5: Update the four downstream assertion sites**

`tests/gateway/test_server.py:107` → `assert calls[0]["exposure"] == "unexposed"`
`tests/gateway/test_server.py:118` → `assert calls[0]["exposure"] == "listed"`
`tests/gateway/test_description_overrides.py:116` → `assert [c["exposure"] for c in calls] == ["listed"]`
`tests/gateway/test_result_suggestions.py:142` → change the tuple's middle element from `True` to `"listed"` and the key from `c["was_exposed"]` to `c["exposure"]`
`tests/replay/test_sessions.py:34` → `"exposure": "listed",`

Rename `test_calling_an_unexposed_tool_is_logged_as_a_miss` is unnecessary — the name is still accurate. Rename `test_was_exposed_survives_a_rewrite` in `test_description_overrides.py:100` to `test_exposure_survives_a_rewrite`, and update its docstring's first line to `"""``exposure`` keys on identity, so a rewritten tool must still read as listed.`"""

- [ ] **Step 6: Run the full suite**

Run: `uv run --extra dev --extra gateway pytest -q`
Expected: PASS, 272 passed (269 existing, minus 1 replaced, plus 4 new)

- [ ] **Step 7: Commit**

```bash
git add src/mcp_gateway_router/gateway/log.py src/mcp_gateway_router/gateway/server.py tests/
git commit -m "feat(gateway): three exposure states, because a disclosure is not a miss

was_exposed collapsed two events that need to stay apart. A tool handed over
by find_tools is arm B working; a tool called that we never offered is the
ranker being wrong. Under a boolean the first floods the second the moment
arm B ships, destroying the signal in the same feature that creates it.

Meta-tool calls also carry the query and what they disclosed — the pair is
what makes the record training data rather than an audit trail.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Config — `find_tools` block and a reserved server id

**Files:**
- Modify: `src/mcp_gateway_router/gateway/config.py:92-132` (dataclass), `:144-155` (upstream validation), `:196-214` (`from_file`)
- Test: `tests/gateway/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `GatewayConfig.find_tools_enabled: bool` (default `False`), `GatewayConfig.find_tools_k: int` (default `5`), and `GATEWAY_SERVER_ID = "_gateway"` exported from `mcp_gateway_router.gateway.config`.

Two flat fields rather than a nested dataclass: `GatewayConfig` is frozen and flat everywhere else, and one nested object for two scalars would be the only exception.

- [ ] **Step 1: Write the failing tests**

Append to `tests/gateway/test_config.py`:

```python
import json

import pytest

from mcp_gateway_router.gateway.config import GATEWAY_SERVER_ID, GatewayConfig


def _write(tmp_path, payload):
    path = tmp_path / "gateway.json"
    path.write_text(json.dumps(payload))
    return path


def test_find_tools_is_disabled_by_default(tmp_path):
    """Every existing arm and all 28 collected sessions must behave identically."""
    path = _write(tmp_path, {"upstreams": [{"server_id": "github", "command": "x"}]})

    config = GatewayConfig.from_file(path)

    assert config.find_tools_enabled is False
    assert config.find_tools_k == 5


def test_find_tools_block_is_read(tmp_path):
    path = _write(
        tmp_path,
        {
            "upstreams": [{"server_id": "github", "command": "x"}],
            "find_tools": {"enabled": True, "k": 3},
        },
    )

    config = GatewayConfig.from_file(path)

    assert config.find_tools_enabled is True
    assert config.find_tools_k == 3


def test_an_upstream_may_not_claim_the_gateway_namespace(tmp_path):
    """`_gateway` addresses the meta-tool. An upstream with that id would shadow it,
    and calls meant for find_tools would route to a real server."""
    path = _write(
        tmp_path,
        {"upstreams": [{"server_id": GATEWAY_SERVER_ID, "command": "x"}]},
    )

    with pytest.raises(ValueError, match=GATEWAY_SERVER_ID):
        GatewayConfig.from_file(path)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --extra dev --extra gateway pytest tests/gateway/test_config.py -q`
Expected: FAIL — `ImportError: cannot import name 'GATEWAY_SERVER_ID'`

- [ ] **Step 3: Implement**

In `src/mcp_gateway_router/gateway/config.py`, add below `NAMESPACE_SEP` (line 69):

```python
# The server id the gateway answers for itself. Reserved: an upstream using it would
# shadow the meta-tool namespace, and calls meant for find_tools would route to a real
# server instead.
GATEWAY_SERVER_ID = "_gateway"
```

Add to the `GatewayConfig` dataclass, after `result_suggestions`:

```python
    #: Whether to advertise the ``find_tools`` meta-tool alongside the selected set.
    #:
    #: Default off. This changes what the client sees, so every arm that predates it
    #: must be unaffected — otherwise arm A's numbers stop being comparable across the
    #: change that introduced arm B.
    find_tools_enabled: bool = False
    #: How many tool schemas ``find_tools`` returns per call.
    #:
    #: Swept offline across 0.176 → 0.549 satisfied, so this is a real parameter and
    #: not a detail. 5 is a probe default, not a justified production value — a default
    #: chosen from our own chart would be circular.
    find_tools_k: int = 5
```

In the upstream loop, after the `NAMESPACE_SEP` check (line 148-151), add:

```python
            if server_id == GATEWAY_SERVER_ID:
                raise ValueError(
                    f"server_id {server_id!r} is reserved for the gateway's own meta-tools"
                )
```

In the `return cls(...)` call, after `result_suggestions=...`:

```python
            find_tools_enabled=bool((payload.get("find_tools") or {}).get("enabled", False)),
            find_tools_k=int((payload.get("find_tools") or {}).get("k", 5)),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --extra dev --extra gateway pytest tests/gateway/test_config.py -q`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `uv run --extra dev --extra gateway pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/mcp_gateway_router/gateway/config.py tests/gateway/test_config.py
git commit -m "feat(gateway): config for find_tools, and reserve the _gateway namespace

Default off, so every existing arm and all 28 collected sessions behave
identically — arm A's numbers have to stay comparable across the change that
introduces arm B.

An upstream named _gateway would shadow the meta-tool and silently route
find_tools calls to a real server, so it is rejected at load.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: The `MetaTools` module

**Files:**
- Create: `src/mcp_gateway_router/gateway/metatools.py`
- Test: `tests/gateway/test_metatools.py`

**Interfaces:**
- Consumes: `GATEWAY_SERVER_ID` from `gateway.config` (Task 2); `Tool`, `Catalog` from `mcp_gateway_router.catalog`; `LexicalScorer` from `mcp_gateway_router.baselines` (constructor `LexicalScorer(catalog)`, method `score(task: str, tool: Tool) -> float`); `advertised_name(tool) -> str` from `gateway.naming`.
- Produces: `FIND_TOOLS = "find_tools"`, `SENTINEL = "GATEWAYFINDTOOLS7F3A"`, and

```python
class MetaTools:
    def __init__(self, *, scorer_factory=LexicalScorer, k: int = 5, enabled: bool = False)
    def advertise(self) -> list[Tool]
    def handles(self, server_id: str) -> bool
    def call(self, name: str, arguments: dict, catalog: Catalog) -> tuple[str, set[tuple[str, str]]]
```

`scorer_factory` is a callable taking a `Catalog` and returning an object with `.score(query, tool)`. `LexicalScorer` needs the catalog at construction, so the scorer cannot be injected pre-built; it is built on first use and cached against the catalog *object*, exactly as `SemanticSelector._ensure` does.

- [ ] **Step 1: Write the failing tests**

Create `tests/gateway/test_metatools.py`:

```python
"""``find_tools`` — the search, its result text, and what it reports disclosing.

Pure tests: a hand-built ``Catalog``, no pool, no transport, no SDK, no subprocess.
That isolation is the reason this is a module rather than a branch inside
``Gateway.call_tool``.
"""

import json

import pytest

from mcp_gateway_router.catalog import Catalog, Tool
from mcp_gateway_router.gateway.config import GATEWAY_SERVER_ID
from mcp_gateway_router.gateway.metatools import FIND_TOOLS, SENTINEL, MetaTools


def _catalog():
    return Catalog(
        [
            Tool("github", "list_releases", "List the releases published in a repository",
                 {"type": "object", "properties": {"owner": {"type": "string"}}}),
            Tool("github", "get_me", "Details about the authenticated user", {"type": "object"}),
            Tool("notion", "API-post-search", "Search Notion pages by title", {"type": "object"}),
            Tool("playwright", "browser_close", "Close the browser page", {"type": "object"}),
        ]
    )


def test_disabled_advertises_nothing_and_handles_nothing():
    """Default-off must be indistinguishable from the meta-tool not existing."""
    meta = MetaTools(enabled=False)

    assert meta.advertise() == []
    assert meta.handles(GATEWAY_SERVER_ID) is False


def test_enabled_advertises_one_tool_in_the_gateway_namespace():
    meta = MetaTools(enabled=True)

    (tool,) = meta.advertise()

    assert tool.key == (GATEWAY_SERVER_ID, FIND_TOOLS)
    assert tool.input_schema["required"] == ["query"]
    assert "query" in tool.input_schema["properties"]
    assert meta.handles(GATEWAY_SERVER_ID) is True
    assert meta.handles("github") is False


def test_search_returns_matching_schemas_and_reports_them_disclosed():
    meta = MetaTools(enabled=True, k=2)

    text, disclosed = meta.call(FIND_TOOLS, {"query": "list releases"}, _catalog())

    assert ("github", "list_releases") in disclosed
    assert "github__list_releases" in text
    # The schema, not just the name — the model has to construct a call from this.
    assert '"owner"' in text


def test_the_result_carries_an_unbroken_sentinel():
    """One token, no separators. `GATEWAY-HINT-7F3A` came back as `GATEWAY-7F3A` in an
    earlier probe, so a substring match reported a hint the model had quoted in full as
    never seen."""
    meta = MetaTools(enabled=True)

    text, _ = meta.call(FIND_TOOLS, {"query": "releases"}, _catalog())

    assert SENTINEL in text
    assert "-" not in SENTINEL


def test_k_bounds_how_many_are_disclosed():
    meta = MetaTools(enabled=True, k=2)

    _, disclosed = meta.call(FIND_TOOLS, {"query": "search"}, _catalog())

    assert len(disclosed) == 2


def test_a_missing_query_explains_itself_and_discloses_nothing():
    meta = MetaTools(enabled=True)

    text, disclosed = meta.call(FIND_TOOLS, {}, _catalog())

    assert disclosed == set()
    assert "query" in text.lower()


def test_an_empty_catalog_says_so_rather_than_returning_nothing_silently():
    meta = MetaTools(enabled=True)

    text, disclosed = meta.call(FIND_TOOLS, {"query": "releases"}, Catalog([]))

    assert disclosed == set()
    assert "no tools" in text.lower()


def test_an_unknown_meta_tool_name_is_reported_not_raised():
    meta = MetaTools(enabled=True)

    text, disclosed = meta.call("nope", {"query": "x"}, _catalog())

    assert disclosed == set()
    assert "nope" in text


def test_a_raising_scorer_degrades_to_an_honest_message():
    """Never fabricate. Returning the first k tools on a broken search is worse than
    failing, because the agent cannot tell the difference."""

    def broken(catalog):
        raise RuntimeError("no model")

    meta = MetaTools(enabled=True, scorer_factory=broken)

    text, disclosed = meta.call(FIND_TOOLS, {"query": "releases"}, _catalog())

    assert disclosed == set()
    assert "could not" in text.lower()


def test_call_never_raises_whatever_the_arguments():
    meta = MetaTools(enabled=True)

    for arguments in ({}, {"query": None}, {"query": ""}, {"query": 7}):
        text, disclosed = meta.call(FIND_TOOLS, arguments, _catalog())
        assert isinstance(text, str)
        assert isinstance(disclosed, set)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --extra dev --extra gateway pytest tests/gateway/test_metatools.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_gateway_router.gateway.metatools'`

- [ ] **Step 3: Implement**

Create `src/mcp_gateway_router/gateway/metatools.py`:

```python
"""Tools the gateway answers for itself.

A meta-tool's subject is the catalog rather than the world. ``find_tools`` searches the
catalog and returns schemas, which trades one schema in the prompt prefix for an extra
round trip whenever the agent needs something it was not given.

**Pure by construction.** No I/O, no ``mcp`` imports, no pool — the catalog arrives as an
argument and the result leaves as text. ``Gateway`` does the wrapping. That is what lets
the only part of arm B with real logic be tested against a hand-built ``Catalog``.

**It never raises and it never fabricates.** A failed search returns explanatory text as
tool *content*, which is the normal MCP shape and leaves the agent able to retry with a
different query; an exception would kill the turn. And on a broken scorer it says so
rather than returning arbitrary tools — the agent cannot tell the difference between a
bad match and a broken search, so silently substituting one for the other would be a way
to mislead it.
"""

from __future__ import annotations

import json
import logging

from ..catalog import Catalog, Tool
from .config import GATEWAY_SERVER_ID
from .naming import advertised_name

logger = logging.getLogger(__name__)

FIND_TOOLS = "find_tools"

#: One unbroken token. Separators get paraphrased — an earlier probe's
#: ``GATEWAY-HINT-7F3A`` came back as ``GATEWAY-7F3A``, so a substring match reported a
#: hint the model had quoted in full as never seen.
SENTINEL = "GATEWAYFINDTOOLS7F3A"

_DESCRIPTION = (
    "Search the full catalog of available tools and return the schemas of those "
    "matching a natural-language query. Only a small core set of tools is listed "
    "directly; every other tool must be found through this one first."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "What you are trying to do, in natural language.",
        }
    },
    "required": ["query"],
}

Key = tuple[str, str]


class MetaTools:
    def __init__(self, *, scorer_factory=None, k: int = 5, enabled: bool = False) -> None:
        if scorer_factory is None:
            from ..baselines import LexicalScorer

            scorer_factory = LexicalScorer
        self._scorer_factory = scorer_factory
        self._k = k
        self._enabled = enabled
        # Cached against the catalog *object*, not its hash: Gateway holds one catalog
        # per session, so identity is stable, and holding the reference keeps the
        # identity check safe from id() reuse after garbage collection.
        self._cached_catalog: Catalog | None = None
        self._cached_scorer = None

    def advertise(self) -> list[Tool]:
        if not self._enabled:
            return []
        return [
            Tool(
                server_id=GATEWAY_SERVER_ID,
                name=FIND_TOOLS,
                description=_DESCRIPTION,
                input_schema=_SCHEMA,
            )
        ]

    def handles(self, server_id: str) -> bool:
        return self._enabled and server_id == GATEWAY_SERVER_ID

    def call(self, name: str, arguments: dict, catalog: Catalog) -> tuple[str, set[Key]]:
        if name != FIND_TOOLS:
            return f"{SENTINEL} no meta-tool named {name!r}.", set()

        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return (
                f"{SENTINEL} find_tools needs a non-empty 'query' string describing "
                f"what you are trying to do.",
                set(),
            )

        if len(catalog) == 0:
            return f"{SENTINEL} no tools are available to search.", set()

        scorer = self._scorer(catalog)
        if scorer is None:
            return (
                f"{SENTINEL} could not search the catalog — the search index is "
                f"unavailable. No tools are being suggested.",
                set(),
            )

        ranked = sorted(catalog, key=lambda t: -scorer.score(query, t))[: self._k]
        return self._render(query, ranked), {t.key for t in ranked}

    def _scorer(self, catalog: Catalog):
        """Build on first use, cached per catalog. ``None`` if it cannot be built."""
        if self._cached_scorer is not None and self._cached_catalog is catalog:
            return self._cached_scorer
        try:
            self._cached_scorer = self._scorer_factory(catalog)
            self._cached_catalog = catalog
        except Exception:
            logger.exception("find_tools could not build a scorer")
            return None
        return self._cached_scorer

    def _render(self, query: str, tools: list[Tool]) -> str:
        blocks = [
            "\n".join(
                [
                    f"name: {advertised_name(tool)}",
                    f"description: {tool.description}",
                    f"input_schema: {json.dumps(tool.input_schema, separators=(',', ':'))}",
                ]
            )
            for tool in tools
        ]
        header = (
            f"{SENTINEL} {len(tools)} tool(s) matched {query!r}. "
            f"Each is callable by the exact name shown."
        )
        return "\n\n".join([header, *blocks])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --extra dev --extra gateway pytest tests/gateway/test_metatools.py -q`
Expected: PASS, 10 passed

- [ ] **Step 5: Commit**

```bash
git add src/mcp_gateway_router/gateway/metatools.py tests/gateway/test_metatools.py
git commit -m "feat(gateway): find_tools, as a pure module rather than a branch

The search is the only part of arm B with real logic and it is pure, so it
lives where it can be tested against a hand-built Catalog with no pool, no
transport and no SDK. Gateway keeps the two lines of dispatch.

It never raises — a failed search is tool content, not a protocol error, so
the agent can retry. And it never fabricates: on a broken scorer it says so
rather than returning arbitrary tools, because the agent cannot tell a bad
match from a broken search.

The retriever is deliberately a stub. The probe tests whether the channel
exists; retrieval quality cannot change that answer, and a good retriever
here would be benchmark-that-cannot-lose number five.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Wire `MetaTools` into `Gateway`

**Files:**
- Modify: `src/mcp_gateway_router/gateway/server.py:35-53` (`__init__`), `:94-99` (`list_tools`), `:131-150` (`call_tool`), `:221-238` (`serve`)
- Test: `tests/gateway/test_exposure_states.py`

**Interfaces:**
- Consumes: `MetaTools`, `FIND_TOOLS`, `SENTINEL` (Task 3); `EXPOSURE_*` (Task 1); `find_tools_enabled` / `find_tools_k` (Task 2).
- Produces: `Gateway.__init__(config, pool, policy, log, meta: MetaTools | None = None)` — `None` builds one from `config`. `Gateway._exposure(key: tuple[str, str]) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/gateway/test_exposure_states.py`:

```python
"""The three exposure states, through a real ``Gateway`` with a fake pool.

The invariant under the most pressure here is that ``_exposed`` is built from catalog
keys and nothing else. Arm B adds a second route by which tools reach the model, and the
obvious implementation — treating anything the model can call as exposed — passes every
other test while destroying the one field the missing-demand signal depends on.
"""

import json

from mcp_gateway_router.gateway.config import GatewayConfig, UpstreamSpec
from mcp_gateway_router.gateway.log import ExposureLog
from mcp_gateway_router.gateway.metatools import FIND_TOOLS, SENTINEL, MetaTools
from mcp_gateway_router.gateway.policy import Policy
from mcp_gateway_router.gateway.server import Gateway
from mcp_gateway_router.gateway.upstream import UpstreamPool
from mcp_gateway_router.tokens import StaticTokenCounter


class FakeTool:
    def __init__(self, name):
        self.name = name
        self.description = f"does {name}"
        self.input_schema = {"type": "object"}


class FakeSession:
    def __init__(self, names):
        self._tools = [FakeTool(n) for n in names]
        self.calls = []

    async def list_tools(self):
        return self._tools

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return {"ok": name}

    async def aclose(self):
        pass


def _records(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _calls(log):
    return [r for r in _records(log.path) if r["kind"] == "call"]


async def _gateway(tmp_path, *, find_tools=True, k=2, overrides=None):
    """Live mode, pinned to `a` only — so `b`..`d` exist but are not listed."""
    sessions = {"github": FakeSession(["a", "b", "c", "d"])}

    async def factory(spec):
        return sessions[spec.server_id]

    config = GatewayConfig(
        upstreams=(UpstreamSpec(server_id="github", command="noop"),),
        mode="live",
        arm="test-arm",
        budget_tokens=3000,
        pinned=(("github", "a"),),
        log_dir=tmp_path,
        selector="static-set",
        find_tools_enabled=find_tools,
        find_tools_k=k,
        description_overrides=overrides or {},
    )
    pool = UpstreamPool(config.upstreams, session_factory=factory)
    await pool.start()
    log = ExposureLog(config.log_dir)
    from mcp_gateway_router.baselines import StaticSet

    policy = Policy(config, StaticSet(config.pinned), StaticTokenCounter({}, default=10), log)
    return Gateway(config, pool, policy, log), sessions, log


async def test_the_meta_tool_is_advertised_alongside_the_selected_set(tmp_path):
    gateway, _, log = await _gateway(tmp_path)

    tools = await gateway.list_tools()
    log.close()

    assert [f"{t.server_id}__{t.name}" for t in tools] == ["github__a", f"_gateway__{FIND_TOOLS}"]


async def test_the_meta_tool_is_not_a_catalog_member(tmp_path):
    """It must not be scored, cut, or counted — or arm A's numbers stop being
    comparable across the change that introduced arm B."""
    on, _, log_on = await _gateway(tmp_path / "on", find_tools=True)
    await on.list_tools()
    log_on.close()

    off, _, log_off = await _gateway(tmp_path / "off", find_tools=False)
    await off.list_tools()
    log_off.close()

    (rec_on,) = [r for r in _records(log_on.path) if r["kind"] == "decision"]
    (rec_off,) = [r for r in _records(log_off.path) if r["kind"] == "decision"]

    assert rec_on["n_candidates"] == rec_off["n_candidates"] == 4
    assert rec_on["catalog_hash"] == rec_off["catalog_hash"]


async def test_a_listed_tool_logs_listed(tmp_path):
    gateway, _, log = await _gateway(tmp_path)
    await gateway.list_tools()

    await gateway.call_tool("github__a", {})
    log.close()

    assert _calls(log)[0]["exposure"] == "listed"


async def test_an_undisclosed_tool_logs_unexposed(tmp_path):
    """Disclosure is not retroactive. `b` is findable, but nobody asked."""
    gateway, _, log = await _gateway(tmp_path)
    await gateway.list_tools()

    await gateway.call_tool("github__b", {})
    log.close()

    assert _calls(log)[0]["exposure"] == "unexposed"


async def test_a_tool_disclosed_by_find_tools_logs_disclosed(tmp_path):
    """The measurement arm B exists to produce."""
    gateway, _, log = await _gateway(tmp_path)
    await gateway.list_tools()

    await gateway.call_tool(f"_gateway__{FIND_TOOLS}", {"query": "does b"})
    await gateway.call_tool("github__b", {})
    log.close()

    calls = _calls(log)
    assert calls[0]["tool_uid"] == f"_gateway/{FIND_TOOLS}"
    assert calls[0]["exposure"] == "listed"
    assert calls[0]["query"] == "does b"
    assert "github/b" in calls[0]["disclosed"]
    assert calls[1]["exposure"] == "disclosed"


async def test_listed_beats_disclosed(tmp_path):
    gateway, _, log = await _gateway(tmp_path, k=4)
    await gateway.list_tools()

    await gateway.call_tool(f"_gateway__{FIND_TOOLS}", {"query": "does a"})
    await gateway.call_tool("github__a", {})
    log.close()

    assert _calls(log)[1]["exposure"] == "listed"


async def test_find_tools_returns_the_sentinel_to_the_caller(tmp_path):
    gateway, _, log = await _gateway(tmp_path)
    await gateway.list_tools()

    result = await gateway.call_tool(f"_gateway__{FIND_TOOLS}", {"query": "does b"})
    log.close()

    assert SENTINEL in result.content[0].text


async def test_a_meta_call_never_reaches_the_pool(tmp_path):
    gateway, sessions, log = await _gateway(tmp_path)
    await gateway.list_tools()

    await gateway.call_tool(f"_gateway__{FIND_TOOLS}", {"query": "anything"})
    log.close()

    assert sessions["github"].calls == []


async def test_disabled_is_unchanged(tmp_path):
    """Byte-for-byte the old behaviour: no meta-tool advertised, and a call addressed
    to it routes to the pool and fails as an unknown upstream."""
    gateway, _, log = await _gateway(tmp_path, find_tools=False)

    tools = await gateway.list_tools()
    log.close()

    assert [f"{t.server_id}__{t.name}" for t in tools] == ["github__a"]


async def test_exposure_survives_a_disclosure_and_a_rewrite_together(tmp_path):
    """Pins the old invariant and the new one at once: `_exposed` keys on pre-rewrite
    identity, and disclosure is a separate set that does not contaminate it."""
    gateway, _, log = await _gateway(
        tmp_path, overrides={"github/a": "totally different text"}
    )
    await gateway.list_tools()

    await gateway.call_tool(f"_gateway__{FIND_TOOLS}", {"query": "does b"})
    await gateway.call_tool("github__a", {})
    log.close()

    assert _calls(log)[1]["exposure"] == "listed"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --extra dev --extra gateway pytest tests/gateway/test_exposure_states.py -q`
Expected: FAIL — `TypeError: GatewayConfig.__init__() got an unexpected keyword argument 'find_tools_enabled'` if Task 2 is not done, otherwise `AssertionError` on the advertised list.

- [ ] **Step 3: Implement the `Gateway` changes**

In `src/mcp_gateway_router/gateway/server.py`, update the imports:

```python
from .log import EXPOSURE_DISCLOSED, EXPOSURE_LISTED, EXPOSURE_UNEXPOSED, ExposureLog
from .metatools import MetaTools
```

Replace `__init__`'s signature and add two fields:

```python
    def __init__(
        self,
        config: GatewayConfig,
        pool: UpstreamPool,
        policy: Policy,
        log: ExposureLog,
        meta: MetaTools | None = None,
    ) -> None:
        self._config = config
        self._pool = pool
        self._policy = policy
        self._log = log
        self._meta = meta or MetaTools(
            k=config.find_tools_k, enabled=config.find_tools_enabled
        )
        # Take the id from the log so the filename and the records agree.
        self._session_id = log.session_id
        self._exposed: set[tuple[str, str]] = set()
        # Tools handed to the model by a meta-tool. Kept apart from `_exposed` on
        # purpose: merging them would make every successful disclosure read as a miss.
        self._disclosed: set[tuple[str, str]] = set()
        self._catalog = None
        self._called: list[tuple[str, str]] = []
        self._environment = safe_capture_environment()
```

Replace `list_tools`:

```python
    async def list_tools(self) -> list[Tool]:
        catalog = await self._pool.aggregate()
        # Held for find_tools, which searches the same catalog the selector cut from.
        self._catalog = catalog
        exposed = self._policy.decide(catalog, self._pool.catalog_hash(), self._context())
        # Keys, not rewritten tools: exposure must survive a description change.
        self._exposed = {t.key for t in exposed}
        # Appended after selection and never a Catalog member, so the selector cannot
        # score or cut it and `n_candidates` / `catalog_hash` are unaffected.
        return self._rewrite(exposed) + self._meta.advertise()
```

Add `_exposure` and `_call_meta`, and replace `call_tool`:

```python
    def _exposure(self, key: tuple[str, str]) -> str:
        """Which of the three states a called tool was in.

        ``listed`` wins over ``disclosed``: a tool in the advertised set was available
        whether or not find_tools also happened to return it.
        """
        if key in self._exposed or self._meta.handles(key[0]):
            return EXPOSURE_LISTED
        if key in self._disclosed:
            return EXPOSURE_DISCLOSED
        return EXPOSURE_UNEXPOSED

    async def _call_meta(self, tool_name: str, arguments: dict):
        """Serve a meta-tool. Returns (result, query, disclosed uids).

        Wrapped even though ``MetaTools.call`` is documented never to raise — this sits
        in the critical path of a live session, and a bug here must degrade the call
        rather than the client.
        """
        if self._catalog is None:
            self._catalog = await self._pool.aggregate()
        try:
            text, disclosed = self._meta.call(tool_name, arguments, self._catalog)
        except Exception:
            logger.exception("meta-tool %s failed", tool_name)
            return self._as_result("find_tools failed unexpectedly."), None, None

        self._disclosed |= disclosed
        query = arguments.get("query")
        return (
            self._as_result(text),
            query if isinstance(query, str) else "",
            sorted(f"{s}/{n}" for s, n in disclosed),
        )

    @staticmethod
    def _as_result(text: str):
        from mcp.types import CallToolResult, TextContent

        return CallToolResult(content=[TextContent(type="text", text=text)])

    async def call_tool(self, advertised: str, arguments: dict) -> Any:
        server_id, tool_name = parse_advertised(advertised)
        key = (server_id, tool_name)
        started = time.monotonic()
        status = "ok"
        query: str | None = None
        disclosed: list[str] | None = None
        try:
            if self._meta.handles(server_id):
                result, query, disclosed = await self._call_meta(tool_name, arguments)
                return result
            result = await self._pool.call(server_id, tool_name, arguments)
            return self._augment(result, key)
        except Exception:
            status = "error"
            raise
        finally:
            self._called.append(key)
            self._log.call(
                session_id=self._session_id,
                tool_uid=f"{server_id}/{tool_name}",
                exposure=self._exposure(key),
                status=status,
                latency_ms=int((time.monotonic() - started) * 1000),
                query=query,
                disclosed=disclosed,
            )
```

In `serve`, pass the meta-tools object explicitly so the wiring is visible at the top level:

```python
    gateway = Gateway(
        config,
        pool,
        policy,
        log,
        MetaTools(k=config.find_tools_k, enabled=config.find_tools_enabled),
    )
```

- [ ] **Step 4: Run the new tests**

Run: `uv run --extra dev --extra gateway pytest tests/gateway/test_exposure_states.py -q`
Expected: PASS, 10 passed

- [ ] **Step 5: Run the full suite**

Run: `uv run --extra dev --extra gateway pytest -q`
Expected: PASS — no regressions in `test_server.py`, `test_description_overrides.py`, `test_result_suggestions.py`

- [ ] **Step 6: Commit**

```bash
git add src/mcp_gateway_router/gateway/server.py tests/gateway/test_exposure_states.py
git commit -m "feat(gateway): serve find_tools, and tell a disclosure from a miss

Three touch points: append the meta-tool after selection, dispatch meta calls
before the pool, resolve the exposure state in one place.

_disclosed is kept apart from _exposed rather than merged into it. Merging is
the obvious implementation and it passes every other test while making every
successful disclosure read as a miss — the one field the missing-demand signal
depends on. There is a test that pins it alongside the description-rewrite
invariant, since both key on pre-rewrite identity.

The meta-tool is appended, never a Catalog member, so the selector cannot
score or cut it and n_candidates and catalog_hash are untouched. That is what
keeps arm A comparable across this change.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: The probe

**Files:**
- Create: `src/mcp_gateway_router/bench/probe_armb.py`
- Test: none. This module is a measurement instrument; it is exercised by running it.

**Interfaces:**
- Consumes: `run_arm` from `bench.runner` (returns `ArmResult` with `.tool_calls: dict[str, int]`, `.turns`, `.answer`, `.error`, `.search_results`); `SENTINEL`, `FIND_TOOLS` from `gateway.metatools`.
- Produces: `results/probe_armb.json`.

- [ ] **Step 1: Write the probe module**

Create `src/mcp_gateway_router/bench/probe_armb.py`:

```python
"""Will the client call a tool it never saw advertised?

Arm B — a small pinned core plus a ``find_tools`` meta-tool — rests on one unverified
assumption: that a client will emit a ``tools/call`` for a tool it learned about from a
tool *result* rather than from the ``tools/list`` array. If it will not, arm B needs
registration via ``list_changed``, which costs a prompt-cache invalidation nobody has
measured, and the design changes shape.

**The measurement is three-layered, because model intent and client forwarding are
different events.**

L1  did the model *emit* the call?     ``ArmResult.tool_calls`` — the ToolUseBlock is in
                                       the assistant message whether or not the client
                                       executes it.
L2  did the gateway *receive* it?      a ``kind: call`` record in the session log.
L3  what state did we log it as?       ``disclosed`` vs ``unexposed``.

**L1 without L2 is the discriminator**: the model tried and the client blocked it. That
is why the authoritative readout is the gateway's own log and not ``ArmResult`` alone.

**Two conditions.**

``findtools``  core + ``find_tools``. The primary run.
``trusted``    core only; the withheld tool's exact advertised name is given in the
               *system prompt*. The system prompt is an operator instruction, so the
               refusal measured in ``probe_suggestions`` does not apply to it. This is a
               positive control: it establishes whether the client filters at all,
               independent of how the agent feels about ``find_tools``.

⚠️ **This is the first live-mode run in the project.** All 27 decision records on disk
are ``mode: shadow``. Live mode is covered by tests but has never served a real client,
so the advertised count is checked before anything is read into the result.

Run (subscription auth; ``ANTHROPIC_API_KEY`` must be UNSET)::

    cd /tmp && env -u ANTHROPIC_API_KEY uv run --directory <repo> \\
        --with claude-agent-sdk --extra gateway \\
        python -m mcp_gateway_router.bench.probe_armb

Two runs, ~$0.07 each.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from ..gateway.metatools import FIND_TOOLS
from .runner import run_arm

REPO = Path(__file__).resolve().parents[3]

#: Validated in probe_descriptions: it forces a tool call, and its control reached the
#: target in 3 turns. A probe task has exactly one hard requirement — it must force a
#: call — and reusing a task that already met it removes a whole class of false null.
TASK = (
    "List the tag names of the 3 most recent releases in the "
    "modelcontextprotocol/servers repository."
)

#: Withheld from the advertised set in both conditions.
TARGET = ("github", "list_releases")

#: GitHub stays visibly reachable so the agent does not conclude the server is absent
#: and stop under the "say so if no MCP tool can do it" instruction — but nothing here
#: can list releases. A probe fixture, not a proposal: production core membership is an
#: open design question and nothing in this probe answers it.
CORE = [["github", "get_me"], ["notion", "API-get-self"], ["playwright", "browser_close"]]

BASE_SYSTEM = (
    "Answer using the available MCP tools. Do not use Bash, WebFetch, or your own "
    "knowledge to answer — if no MCP tool can do it, say so."
)

TARGET_ADVERTISED = f"{TARGET[0]}__{TARGET[1]}"

TRUSTED_SYSTEM = (
    f"{BASE_SYSTEM} The tool `{TARGET_ADVERTISED}` is available to you through the MCP "
    f"server even though it is not in your listed tools. Call it directly by that exact "
    f"name when you need it."
)


@dataclass(frozen=True)
class Condition:
    name: str
    find_tools: bool
    system: str
    reads: str


CONDITIONS = (
    Condition("findtools", True, BASE_SYSTEM, "will the model call what find_tools disclosed?"),
    Condition("trusted", False, TRUSTED_SYSTEM, "does the client filter at all? (control)"),
)


def _write_config(base: Path, condition: Condition) -> Path:
    """One gateway config per condition, beside the base config.

    ⚠️ **Never into an output directory.** ``from_file`` resolves ``.env``, ``log_dir``
    and ``catalog_path`` relative to the config's own parent, and ``_expand`` *raises*
    on an unresolvable ``${VAR}`` — so a config written elsewhere fails to load, the
    gateway dies at startup, and the session sees no MCP tools at all. That failure is
    silent from here: both conditions come back empty and look like a clean negative.
    """
    out = base.parent / f"gateway.armb-{condition.name}.json"
    config = json.loads(base.read_text())
    config.update(
        {
            "mode": "live",
            "selector": "static-set",
            "pinned": CORE,
            "arm": f"probe-armb/{condition.name}",
            "log_dir": f"runs/probe-armb/{condition.name}",
            "find_tools": {"enabled": condition.find_tools, "k": 5},
        }
    )
    out.write_text(json.dumps(config, indent=2) + "\n")
    return out


def _log_records(base: Path, condition: Condition) -> list[dict]:
    """Every record the gateway wrote for this condition.

    This is the authoritative readout. ``ArmResult`` reports what the *client* did with
    the model's output; only the gateway's own log says what actually arrived.
    """
    log_dir = base.parent / "runs" / "probe-armb" / condition.name
    if not log_dir.exists():
        return []
    records: list[dict] = []
    for file in sorted(log_dir.glob("*.jsonl")):
        for line in file.read_text().splitlines():
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def _emitted(result, key: tuple[str, str]) -> bool:
    """L1 — did the model emit a call for this tool? Names are client-namespaced."""
    tail = f"{key[0]}__{key[1]}"
    return any(tail in name for name in result.tool_calls)


def _received(records: list[dict], key: tuple[str, str]) -> dict | None:
    """L2/L3 — did the gateway receive it, and as what?"""
    uid = f"{key[0]}/{key[1]}"
    for record in records:
        if record.get("kind") == "call" and record.get("tool_uid") == uid:
            return record
    return None


async def probe(
    *,
    base_config: Path,
    model: str = "claude-opus-5",
    max_budget_usd: float = 0.40,
) -> dict[str, object]:
    results: dict[str, object] = {}

    for condition in CONDITIONS:
        path = _write_config(base_config, condition)
        result = await run_arm(
            arm=f"probe-armb/{condition.name}",
            task_id="probe-armb",
            prompt=TASK,
            gateway_config=str(path),
            model=model,
            max_budget_usd=max_budget_usd,
            system_prompt=condition.system,
        )
        records = _log_records(base_config, condition)
        decisions = [r for r in records if r.get("kind") == "decision"]
        received = _received(records, TARGET)
        meta = _received(records, ("_gateway", FIND_TOOLS))

        row = {
            "emitted": _emitted(result, TARGET),
            "received": received is not None,
            "exposure": (received or {}).get("exposure"),
            "find_tools_called": meta is not None,
            "find_tools_query": (meta or {}).get("query"),
            "find_tools_disclosed": (meta or {}).get("disclosed"),
            # Live mode has never served a real client. A wrong count here means the
            # finding is about our config, not about the client.
            "n_advertised": decisions[0]["n_advertised"] if decisions else None,
            "turns": result.turns,
            "tool_calls": result.tool_calls,
            "answer": result.answer,
            "error": result.error,
        }
        results[condition.name] = row
        print(
            f"  {condition.name:<10} "
            f"advertised={row['n_advertised']} "
            f"find_tools={row['find_tools_called']!s:<5} | "
            f"target: emitted={row['emitted']!s:<5} received={row['received']!s:<5} "
            f"exposure={row['exposure']}",
            flush=True,
        )

    results["verdict"] = _verdict(results)
    return results


def _verdict(results: dict) -> str:
    """State the reading, or refuse to. Pre-registered before the first run."""
    findtools, trusted = results["findtools"], results["trusted"]

    if findtools["n_advertised"] not in (len(CORE) + 1, None):
        return (
            f"UNREADABLE — the findtools condition advertised "
            f"{findtools['n_advertised']} tools, expected {len(CORE) + 1} (core + "
            f"find_tools). This is a live-mode or config problem, not a finding about "
            f"the client. Fix it before rerunning."
        )
    if not findtools["find_tools_called"]:
        return (
            "UNREADABLE — find_tools was never called, so the probe did not exercise "
            "the thing it exists to test. The agent either answered from the core or "
            "gave up. Fix the framing; do not report this as a null."
        )

    if findtools["emitted"] and findtools["received"]:
        return (
            f"CHANNEL OPEN — the model called a tool it never saw advertised and the "
            f"call reached the gateway, logged as {findtools['exposure']!r}. Arm B is "
            f"real: find_tools + ranker + bandit, with no collection and no cache cost. "
            f"was_exposed:false is alive at last, as `disclosed`."
        )
    if findtools["emitted"] and not findtools["received"]:
        return (
            "CLIENT FILTERS — the model emitted the call and it never reached us, so "
            "the client resolves availability before dispatch. Registration is "
            "required: arm B becomes find_tools + list_changed, gated on measuring the "
            "cache-invalidation cost."
        )

    # The model did not try. The control says whether it could have.
    if trusted["emitted"] and trusted["received"]:
        return (
            "MODEL DECLINES — the client forwards a call for an unadvertised tool when "
            "the system prompt asks for one, but the model would not act on a tool "
            "learned from a find_tools *result*. The channel is open and the refusal is "
            "about trust in tool output, which is a framing problem and attackable."
        )
    if trusted["emitted"] and not trusted["received"]:
        return (
            "CLIENT FILTERS — shown by the control: even under an operator instruction "
            "naming the exact tool, the call never reached the gateway. Arm B requires "
            "registration."
        )
    return (
        "STRUCTURAL — the model would not emit a call for an unadvertised tool even "
        "when the system prompt named it exactly. Not a framing problem and not "
        "promptable-around. Arm B requires list_changed."
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=REPO / "gateway.armA.json")
    parser.add_argument("--save", type=Path, default=REPO / "results/probe_armb.json")
    parser.add_argument("--model", default="claude-opus-5")
    args = parser.parse_args()

    print(f"probing arm B — core={len(CORE)} tools, withheld={TARGET[0]}/{TARGET[1]}\n")
    results = asyncio.run(probe(base_config=args.config, model=args.model))

    args.save.parent.mkdir(parents=True, exist_ok=True)
    args.save.write_text(json.dumps(results, indent=2) + "\n")
    print(f"\n{results['verdict']}\n\nwrote {args.save}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it imports and the config writer behaves, without spending money**

Run:

```bash
uv run --extra gateway python -c "
from pathlib import Path
from mcp_gateway_router.bench.probe_armb import CONDITIONS, _write_config, _verdict
base = Path('gateway.armA.json')
for c in CONDITIONS:
    p = _write_config(base, c)
    print(c.name, '->', p, p.parent == base.resolve().parent or p.parent == base.parent)
print(_verdict({'findtools': {'n_advertised': 4, 'find_tools_called': True, 'emitted': True, 'received': True, 'exposure': 'disclosed'}, 'trusted': {}}))
"
```

Expected: both configs written beside `gateway.armA.json`, and the verdict prints `CHANNEL OPEN`.

- [ ] **Step 3: Add the generated configs to `.gitignore`**

Append to `.gitignore`, beside the existing probe entries:

```
gateway.armb-*.json
```

- [ ] **Step 4: Confirm the gitignore works**

Run: `git status --short`
Expected: `gateway.armb-findtools.json` and `gateway.armb-trusted.json` do not appear.

- [ ] **Step 5: Commit**

```bash
git add src/mcp_gateway_router/bench/probe_armb.py .gitignore
git commit -m "feat(bench): probe whether a client calls what it never saw

Three-layered, because model intent and client forwarding are different
events: the ToolUseBlock is in the assistant message whether or not the
client executes it, so L1-without-L2 proves the client filtered — inside the
primary run, without needing the control to say so.

The trusted-channel control runs anyway, as a positive control. The system
prompt is an operator instruction, so the refusal probe_suggestions measured
does not apply to it; it establishes whether the client filters at all,
independent of how the agent feels about find_tools.

The verdict is pre-registered and refuses to read two situations: an
advertised count that is not core+1, since live mode has never served a real
client, and a run where find_tools was never called, which tested nothing.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Run it

**Files:** none — this task produces `results/probe_armb.json`.

- [ ] **Step 1: Confirm the suite is green before spending money**

Run: `uv run --extra dev --extra gateway pytest -q`
Expected: PASS

- [ ] **Step 2: Confirm the upstreams are reachable**

The probe needs real `github`, `notion` and `playwright` upstreams, since it withholds a real GitHub tool. Check `gateway.armA.json` resolves and the `.env` beside it has the tokens it references.

Run: `uv run --extra gateway python -c "
from pathlib import Path
from mcp_gateway_router.gateway.config import GatewayConfig
c = GatewayConfig.from_file(Path('gateway.armA.json'))
print([u.server_id for u in c.upstreams], c.mode, c.find_tools_enabled)
"`
Expected: the three server ids print without raising. A `ValueError` about `${VAR}` means the `.env` is missing a token — fix that before running, or the gateway dies at startup and both conditions come back as a false negative.

- [ ] **Step 3: Run the probe**

Run:

```bash
cd /tmp && env -u ANTHROPIC_API_KEY uv run --directory /Users/nguyenvietkhoi/mcp-gateway-router \
    --with claude-agent-sdk --extra gateway \
    python -m mcp_gateway_router.bench.probe_armb
```

Expected: two lines of per-condition output, then a verdict. ~$0.14.

- [ ] **Step 4: Check the readability guards before believing the verdict**

Confirm `n_advertised == 4` in the `findtools` row. If it is 95, the config did not take and the run is about shadow mode, not arm B. If it is `None`, no decision record was written and the gateway never started.

- [ ] **Step 5: Commit the result**

```bash
git add results/probe_armb.json
git commit -m "feat(bench): arm B probe result — <verdict in five words>

<Paste the verdict paragraph, then the two rows of the table: emitted,
received, exposure, and the find_tools query if one was made.>

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

- [ ] **Step 6: Write the finding into the docs**

The verdict changes what the project does next, so it does not live only in a JSON file:

- `docs/PICKUP.md` §6.1 — replace the starred "probe arm B's core assumption" item with the result and the branch it selects.
- `docs/intuitions.md` — the *Miss, and missing-tool demand* section currently says the client-forwarding assumption is unverified. If the probe verified it, say so and name the evidence.
- `docs/decisions.md` — a new entry, newest first. Corrections spawn entries rather than editing old ones.

---

## Self-Review

**Spec coverage.** §4.1 `MetaTools` → Task 3. §4.2 three touch points → Task 4. §4.3 config → Task 2. §4.4 stub retriever → Task 3 (`scorer_factory` defaults to `LexicalScorer`). §5 three-state field → Task 1; §5's "migration — none needed" is honoured by there being no migration task. §6.1–6.2 conditions and fixture → Task 5 (`CONDITIONS`, `CORE`, `TASK`). §6.3 three layers → Task 5 (`_emitted`, `_received`). §6.4 pre-registered reading → Task 5 (`_verdict`). §6.5 traps → Task 5 (`_write_config` docstring, `SENTINEL` in Task 3, task reuse). §6.6 output → Task 5. §7 invariants 1–6 → Task 4 tests (`test_the_meta_tool_is_not_a_catalog_member`, `test_exposure_survives_a_disclosure_and_a_rewrite_together`, `test_an_undisclosed_tool_logs_unexposed`, `test_listed_beats_disclosed`, `test_disabled_is_unchanged`) and Task 2 (`test_an_upstream_may_not_claim_the_gateway_namespace`). §8 failure behaviour → Task 3 tests + Task 4 `_call_meta`. §9 tests → Tasks 1–4.

**Placeholder scan.** One deliberate placeholder remains: Task 6 Step 5's commit message, which cannot be written before the result exists. Every other step carries its actual content.

**Type consistency.** `exposure` is a `str` from `EXPOSURES` everywhere. `MetaTools.call` returns `tuple[str, set[tuple[str, str]]]` in Task 3 and is destructured as `text, disclosed` in Task 4. `disclosed` reaches `ExposureLog.call` as `list[str]` of `server/name` uids, matching Task 1's signature — Task 4 converts the set of key tuples with `sorted(f"{s}/{n}" for s, n in disclosed)`. `find_tools_enabled` / `find_tools_k` are named identically in Tasks 2, 4 and 5.

**Two gaps found and fixed while reviewing:**

1. Task 1 Step 4 originally left `server.py` calling `self._exposure(key)`, which Task 4 introduces — the suite would have been red between tasks. It now writes the two-state expression inline, and Task 4 replaces it.
2. Task 4's rewrite-invariant test reconstructed a frozen `GatewayConfig` by hand through `__dataclass_fields__`. The `_gateway` helper now takes an `overrides` argument instead. The original would have coupled the test to the dataclass's field list, so adding any config field later would have broken a test that has nothing to do with config.

**Verified against the repo while writing, not assumed:** `tests/gateway/test_config.py` exists (so Task 2 appends rather than creates); `.gitignore:19-20` already carries `gateway.probe-*.json` and `gateway.suggest-*.json`, so Task 5's entry follows an established pattern; `replay/sessions.py:112-116` reads only `tool_uid` from call records, which is what makes the spec's "no migration" claim true.
