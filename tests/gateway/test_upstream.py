import pytest

from mcp_gateway_router.gateway.config import UpstreamSpec
from mcp_gateway_router.gateway.upstream import UnknownUpstreamError, UpstreamPool


class FakeTool:
    def __init__(self, name, description, schema):
        self.name = name
        self.description = description
        self.input_schema = schema


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
    pool = UpstreamPool(
        [_spec("github")], session_factory=_factory({"github": FakeSession([])})
    )
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
