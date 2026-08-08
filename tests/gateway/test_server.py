import json

import pytest

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

    assert [f"{t.server_id}__{t.name}" for t in tools] == ["github__a", "github__b"]


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


async def test_build_app_produces_valid_sdk_tools(tmp_path):
    """The SDK binding itself, not just the logic behind it.

    This is where the mcp 1.x -> 2.x break landed: the decorator API vanished and
    ``inputSchema`` became ``input_schema``. Constructing real ``MCPTool`` models
    through the real ``MCPServer`` subclass is what catches the next such change.
    """
    from mcp_gateway_router.gateway.server import build_app

    gateway, _, log, _ = await _gateway(tmp_path)
    app = build_app(gateway)

    tools = await app.list_tools()
    log.close()

    assert [t.name for t in tools] == ["github__a", "github__b"]
    assert tools[0].input_schema == {"type": "object"}
    assert tools[0].description == "does a"


async def test_build_app_call_tool_routes_through_the_gateway(tmp_path):
    from mcp_gateway_router.gateway.server import build_app

    gateway, sessions, log, _ = await _gateway(tmp_path)
    app = build_app(gateway)
    await app.list_tools()

    result = await app.call_tool("github__a", {"x": 1})
    log.close()

    assert result == {"ok": "a"}
    assert sessions["github"].calls == [("a", {"x": 1})]
