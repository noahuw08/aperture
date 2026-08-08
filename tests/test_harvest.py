import json

from mcp_gateway_router.catalog import Catalog, Tool
from mcp_gateway_router.gateway.config import GatewayConfig, UpstreamSpec
from mcp_gateway_router.harvest import catalog_from_json, catalog_to_json, harvest


class FakeTool:
    def __init__(self, name, schema):
        self.name = name
        self.description = f"does {name}"
        self.input_schema = schema


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
