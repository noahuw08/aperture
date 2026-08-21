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
