"""Schema token measurement — caching, concurrency, and the marginal-cost contract.

The `count_tokens` endpoint is free but rate-limited per minute, so the thing worth
testing is call *count*, not cost: a sweep that re-measures a cached tool, or flushes
the whole cache file per tool, is slow in a way no assertion on the returned number
would catch.
"""

import json
import threading

from mcp_gateway_router.catalog import Tool
from mcp_gateway_router.tokens import AnthropicTokenCounter, StaticTokenCounter, total_cost

SCHEMA = {"type": "object", "properties": {"q": {"type": "string"}}}


def tool(name, desc="d", schema=None):
    return Tool(server_id="s", name=name, description=desc, input_schema=schema or SCHEMA)


class FakeClient:
    """Counts calls and returns a size-dependent token count.

    ``count_tokens`` is reached as ``client.messages.count_tokens(...)``.
    """

    def __init__(self, baseline=10):
        self.calls = []
        self.baseline = baseline
        self.messages = self
        self._lock = threading.Lock()

    def count_tokens(self, model, messages, tools):
        with self._lock:
            self.calls.append(tools)
        total = self.baseline + sum(len(json.dumps(t)) for t in tools)
        return type("R", (), {"input_tokens": total})()


def counter(tmp_path, client=None, **kw):
    return AnthropicTokenCounter(
        client=client or FakeClient(), cache_path=tmp_path / "c.json", **kw
    )


# --- marginal cost ------------------------------------------------------------


def test_cost_is_marginal_over_an_empty_tools_baseline():
    """The number must be the tool's *added* cost, not the whole request."""
    client = FakeClient(baseline=10)
    c = AnthropicTokenCounter(client=client)
    t = tool("a")
    expected = len(json.dumps(t.as_api_tool()))
    assert c.cost(t) == expected  # baseline subtracted out


def test_baseline_is_measured_once_across_many_tools(tmp_path):
    client = FakeClient()
    c = counter(tmp_path, client)
    for name in ("a", "b", "c"):
        c.cost(tool(name))
    assert sum(1 for call in client.calls if call == []) == 1


# --- caching ------------------------------------------------------------------


def test_repeat_cost_does_not_hit_the_api_again(tmp_path):
    client = FakeClient()
    c = counter(tmp_path, client)
    t = tool("a")
    c.cost(t)
    before = len(client.calls)
    for _ in range(5):
        c.cost(t)
    assert len(client.calls) == before


def test_cache_is_keyed_by_schema_so_a_changed_schema_remeasures(tmp_path):
    """`uid` carries the schema hash — a changed schema must invalidate its own
    entry and nothing else."""
    client = FakeClient()
    c = counter(tmp_path, client)
    c.cost(tool("a", schema={"type": "object", "properties": {"x": {"type": "string"}}}))
    calls = len(client.calls)
    c.cost(tool("a", schema={"type": "object", "properties": {"y": {"type": "number"}}}))
    assert len(client.calls) == calls + 1


def test_cache_survives_a_new_counter_over_the_same_path(tmp_path):
    client = FakeClient()
    counter(tmp_path, client).cost(tool("a"))
    calls = len(client.calls)
    counter(tmp_path, FakeClient()).cost(tool("a"))  # fresh client: any call would show
    assert len(client.calls) == calls


# --- prewarm ------------------------------------------------------------------


def test_prewarm_measures_every_tool_once(tmp_path):
    client = FakeClient()
    c = counter(tmp_path, client)
    tools = [tool(f"t{i}") for i in range(25)]
    assert c.prewarm(tools, max_workers=4) == 25
    assert sum(1 for call in client.calls if call != []) == 25


def test_prewarm_then_cost_makes_no_further_calls(tmp_path):
    """The point of prewarming: the sweep afterwards is pure cache reads."""
    client = FakeClient()
    c = counter(tmp_path, client)
    tools = [tool(f"t{i}") for i in range(10)]
    c.prewarm(tools, max_workers=4)
    calls = len(client.calls)
    assert total_cost(c, tools) > 0
    assert len(client.calls) == calls


def test_prewarm_skips_already_cached_tools(tmp_path):
    client = FakeClient()
    c = counter(tmp_path, client)
    tools = [tool(f"t{i}") for i in range(5)]
    c.cost(tools[0])
    assert c.prewarm(tools, max_workers=2) == 4


def test_prewarm_deduplicates_repeated_tools(tmp_path):
    """`ExposeAll` plus `PopularityTopK` both walk the whole catalog — the same tool
    arriving twice must not cost two calls."""
    client = FakeClient()
    c = counter(tmp_path, client)
    t = tool("a")
    assert c.prewarm([t, t, t], max_workers=2) == 1


def test_prewarm_writes_the_cache_file_once_not_per_tool(tmp_path, monkeypatch):
    """Flushing per tool rewrites the whole JSON each time — quadratic over 37k
    tools, and invisible in any assertion on the returned numbers."""
    c = counter(tmp_path, FakeClient())
    writes = []
    real_write = type(tmp_path).write_text
    monkeypatch.setattr(
        type(tmp_path),
        "write_text",
        lambda self, data, *a, **k: (writes.append(1), real_write(self, data, *a, **k))[1],
    )
    c.prewarm([tool(f"t{i}") for i in range(30)], max_workers=4)
    assert len(writes) == 1


def test_prewarm_result_matches_sequential_cost(tmp_path):
    """Concurrency must not change the measurement."""
    tools = [tool(f"t{i}", desc="x" * i) for i in range(12)]
    warmed = counter(tmp_path, FakeClient())
    warmed.prewarm(tools, max_workers=6)
    serial = AnthropicTokenCounter(client=FakeClient())
    assert [warmed.cost(t) for t in tools] == [serial.cost(t) for t in tools]


def test_prewarm_on_empty_input_makes_no_calls(tmp_path):
    client = FakeClient()
    assert counter(tmp_path, client).prewarm([], max_workers=4) == 0
    assert client.calls == []


def test_prewarm_reports_progress(tmp_path):
    seen = []
    c = counter(tmp_path, FakeClient())
    c.prewarm(
        [tool(f"t{i}") for i in range(600)],
        max_workers=8,
        on_progress=lambda done, total: seen.append((done, total)),
    )
    assert seen and all(total == 600 for _, total in seen)


# --- static counter -----------------------------------------------------------


def test_static_counter_falls_back_to_its_default():
    c = StaticTokenCounter({}, default=120)
    assert c.cost(tool("anything")) == 120
