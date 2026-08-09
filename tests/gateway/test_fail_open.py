"""Fail-open is a spec requirement, not a test's opinion.

The gateway sits in the critical path of every session. If any of these fail, fix
``policy.py`` or ``server.py`` — never the test.
"""

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

    async def list_tools(self):
        return self._tools

    async def call_tool(self, name, arguments):
        return {"ok": name}

    async def aclose(self):
        pass


class HeadSelector:
    name = "head"

    def select(self, context, catalog, budget, counter):
        from mcp_gateway_router.selector import fill_budget

        return fill_budget(list(catalog), budget, counter)


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


async def _build(tmp_path, selector, session=None):
    async def factory(spec):
        return session if session is not None else FakeSession(["a", "b", "c"])

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
    policy = Policy(config, selector, StaticTokenCounter({}, default=100), log)
    return Gateway(config, pool, policy, log), log, config


async def test_client_still_gets_a_working_set_when_the_ranker_dies_mid_session(tmp_path):
    gateway, log, _ = await _build(tmp_path, MidSessionFailure())

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

    gateway, log, _ = await _build(tmp_path, Exploding())

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

    gateway, log, _ = await _build(tmp_path, HeadSelector(), session=Dead())

    exposed = await gateway.list_tools()
    log.close()

    assert exposed == []


class BrokenCounter:
    """A token counter that always raises.

    Stands in for AnthropicTokenCounter with no credit balance, no network, or a
    cold cache — all of which surface as an exception from cost().
    """

    def cost(self, tool):
        raise RuntimeError("count_tokens unreachable")


async def test_a_broken_token_counter_does_not_take_down_tools_list(tmp_path):
    """The counter is consulted while *logging*, outside selection's try/except.

    An unreachable counter must degrade the log record, never the client's tool set.
    """
    config = GatewayConfig(
        upstreams=(UpstreamSpec(server_id="github", command="noop"),),
        mode="live",
        arm="chaos",
        budget_tokens=250,
        pinned=(("github", "a"),),
        log_path=tmp_path / "exposure.jsonl",
    )

    async def factory(spec):
        return FakeSession(["a", "b", "c"])

    pool = UpstreamPool(config.upstreams, session_factory=factory)
    await pool.start()
    log = ExposureLog(config.log_path)
    policy = Policy(config, HeadSelector(), BrokenCounter(), log)
    gateway = Gateway(config, pool, policy, log)

    exposed = await gateway.list_tools()
    log.close()

    # Selection itself fails (fill_budget needs costs), so we fail open to pinned.
    assert [t.name for t in exposed] == ["a"]


async def test_an_unmeasurable_tool_is_logged_with_a_null_cost(tmp_path):
    """A cost we could not measure is recorded as null, never as a fake number."""
    import json

    config = GatewayConfig(
        upstreams=(UpstreamSpec(server_id="github", command="noop"),),
        mode="live",
        arm="chaos",
        budget_tokens=250,
        pinned=(("github", "a"),),
        log_path=tmp_path / "exposure.jsonl",
    )

    async def factory(spec):
        return FakeSession(["a", "b", "c"])

    pool = UpstreamPool(config.upstreams, session_factory=factory)
    await pool.start()
    log = ExposureLog(config.log_path)
    policy = Policy(config, HeadSelector(), BrokenCounter(), log)
    gateway = Gateway(config, pool, policy, log)

    await gateway.list_tools()
    log.close()

    record = json.loads((tmp_path / "exposure.jsonl").read_text().splitlines()[0])
    assert record["exposed"][0]["token_cost"] is None


async def test_an_upstream_that_fails_to_start_does_not_prevent_serving(tmp_path):
    async def factory(spec):
        raise RuntimeError("spawn failed")

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
    policy = Policy(config, HeadSelector(), StaticTokenCounter({}, default=100), log)
    gateway = Gateway(config, pool, policy, log)

    exposed = await gateway.list_tools()
    log.close()

    assert exposed == []
