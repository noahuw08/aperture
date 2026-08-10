"""Where the gateway gets token costs from.

The data plane answers `tools/list` on every session open. Measuring costs there
would put an Anthropic API call in the critical path — a latency and availability
dependency on a service that has nothing to do with proxying. Costs are measured
once by `harvest.py` (control plane) and read from its artifact here.
"""

import json

from mcp_gateway_router.catalog import Tool
from mcp_gateway_router.gateway.config import GatewayConfig
from mcp_gateway_router.gateway.server import build_counter


def _config(tmp_path, catalog_path=None):
    return GatewayConfig(
        upstreams=(),
        mode="shadow",
        arm="passthrough",
        budget_tokens=3000,
        pinned=(),
        log_dir=tmp_path,
        catalog_path=catalog_path,
    )


def _tool():
    return Tool(server_id="github", name="search", description="find", input_schema={})


def test_costs_come_from_the_harvested_catalog(tmp_path):
    tool = _tool()
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "server_id": "github",
                        "name": "search",
                        "description": "find",
                        "input_schema": {},
                    }
                ],
                "costs": {tool.uid: 173},
            }
        )
    )

    counter = build_counter(_config(tmp_path, catalog_path))

    assert counter.cost(tool) == 173


def test_a_missing_catalog_still_yields_a_working_counter(tmp_path):
    counter = build_counter(_config(tmp_path, tmp_path / "absent.json"))

    assert isinstance(counter.cost(_tool()), int)


def test_no_catalog_configured_still_yields_a_working_counter(tmp_path):
    counter = build_counter(_config(tmp_path))

    assert isinstance(counter.cost(_tool()), int)


def test_a_catalog_without_measured_costs_still_yields_a_working_counter(tmp_path):
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps({"tools": [], "costs": {}}))

    counter = build_counter(_config(tmp_path, catalog_path))

    assert isinstance(counter.cost(_tool()), int)


def test_the_counter_never_reaches_the_network(tmp_path, monkeypatch):
    """Constructing and using the counter must not import or call the Anthropic SDK."""

    def explode(*args, **kwargs):
        raise AssertionError("the data plane must not call the Anthropic API")

    import mcp_gateway_router.tokens as tokens

    monkeypatch.setattr(tokens, "AnthropicTokenCounter", explode)

    counter = build_counter(_config(tmp_path))
    counter.cost(_tool())
