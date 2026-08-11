"""Deferral accounting — the ratio is the claim, so the two cost sides must not drift.

The absolutes here are chars, not tokens, and are only ever reported as estimates. What
the project actually leans on is that a deferred tool costs its name and a loaded tool
costs its whole definition; a regression that quietly counted schemas on the deferred
side would leave every printed number plausible and the conclusion inverted.
"""

import json

from mcp_gateway_router.catalog import Tool
from mcp_gateway_router.deferral import (
    by_server,
    cut_saving,
    deferred_chars,
    load_catalog,
    loaded_chars,
)

BIG_SCHEMA = {"type": "object", "properties": {"q": {"type": "string" * 20}}}


def tool(server_id, name, desc="d", schema=None):
    return Tool(
        server_id=server_id,
        name=name,
        description=desc,
        input_schema=schema if schema is not None else {"type": "object"},
    )


def test_deferred_cost_ignores_schema_and_description():
    bare = tool("s", "t", desc="", schema={})
    fat = tool("s", "t", desc="x" * 500, schema=BIG_SCHEMA)
    assert deferred_chars(bare) == deferred_chars(fat)


def test_deferred_cost_counts_the_client_namespaced_name():
    # mcp__gateway__ + s__t
    assert deferred_chars(tool("s", "t")) == len("mcp__gateway__s__t")


def test_loaded_cost_grows_with_schema_and_description():
    small = tool("s", "t", desc="d", schema={})
    large = tool("s", "t", desc="d" * 100, schema=BIG_SCHEMA)
    assert loaded_chars(large) > loaded_chars(small) + 100


def test_by_server_orders_by_loaded_bytes_not_tool_count():
    tools = [tool("heavy", "a", desc="x" * 900)] + [
        tool("many", f"t{i}") for i in range(10)
    ]
    costs = by_server(tools)
    assert [c.server_id for c in costs] == ["heavy", "many"]
    assert costs[0].n_tools < costs[1].n_tools


def test_cut_saving_is_far_smaller_when_deferred():
    tools = [tool("s", f"t{i}", desc="x" * 400, schema=BIG_SCHEMA) for i in range(20)]
    saving = cut_saving(tools, keep=5)
    assert saving["n_dropped"] == 15
    assert saving["deferred"] < saving["loaded"] / 10


def test_cut_saving_is_zero_when_nothing_is_dropped():
    tools = [tool("s", f"t{i}") for i in range(3)]
    assert cut_saving(tools, keep=10) == {"n_dropped": 0, "loaded": 0, "deferred": 0}


def test_load_catalog_tolerates_a_tool_with_no_description(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            {"tools": [{"server_id": "s", "name": "t", "input_schema": {}}], "costs": {}}
        )
    )
    (loaded,) = load_catalog(path)
    assert loaded.description == ""
    assert loaded_chars(loaded) > 0
