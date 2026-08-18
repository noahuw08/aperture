"""Appending to a tool result, and the guarantee that it can never cost a call.

A suggestion is an optimisation; the tool result is the answer the user is waiting for.
These tests pin that asymmetry: the text is appended when it can be, the original is
returned untouched when it can't, and nothing about routing or logging moves either way.
"""

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


class FakeResult:
    """Stands in for ``CallToolResult`` — content list plus an error flag."""

    def __init__(self, text):
        from mcp.types import TextContent

        self.content = [TextContent(type="text", text=text)]
        self.isError = False


class FakeSession:
    def __init__(self, names, result_factory=FakeResult):
        self._tools = [FakeTool(n) for n in names]
        self._result_factory = result_factory
        self.calls = []

    async def list_tools(self):
        return self._tools

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self._result_factory(f"upstream said {name}")

    async def aclose(self):
        pass


class AllSelector:
    name = "all"

    def select(self, context, catalog, budget, counter):
        return list(catalog)


async def _gateway(tmp_path, suggestions, result_factory=FakeResult):
    sessions = {"github": FakeSession(["list_releases", "list_tags"], result_factory)}

    async def factory(spec):
        return sessions[spec.server_id]

    config = GatewayConfig(
        upstreams=(UpstreamSpec(server_id="github", command="noop"),),
        mode="shadow",
        arm="suggest",
        budget_tokens=3000,
        pinned=(),
        log_dir=tmp_path,
        result_suggestions=suggestions,
    )
    pool = UpstreamPool(config.upstreams, session_factory=factory)
    await pool.start()
    log = ExposureLog(config.log_dir)
    policy = Policy(config, AllSelector(), StaticTokenCounter({}, default=10), log)
    return Gateway(config, pool, policy, log), sessions, log


def _texts(result):
    return [getattr(b, "text", None) for b in result.content]


async def test_a_suggestion_is_appended_after_the_upstream_content(tmp_path):
    """Appended, not substituted — the upstream's answer must still be first."""
    gateway, _, _ = await _gateway(
        tmp_path, {"github/list_releases": "[gateway] try list_tags"}
    )
    result = await gateway.call_tool("github__list_releases", {})

    assert _texts(result) == ["upstream said list_releases", "[gateway] try list_tags"]


async def test_tools_without_a_suggestion_are_untouched(tmp_path):
    gateway, _, _ = await _gateway(
        tmp_path, {"github/list_releases": "[gateway] try list_tags"}
    )
    result = await gateway.call_tool("github__list_tags", {})

    assert _texts(result) == ["upstream said list_tags"]


async def test_an_empty_mapping_is_todays_behaviour(tmp_path):
    gateway, _, _ = await _gateway(tmp_path, {})
    result = await gateway.call_tool("github__list_releases", {})

    assert _texts(result) == ["upstream said list_releases"]


async def test_an_unexpected_result_shape_returns_the_original_untouched(tmp_path):
    """Fail open. A hint must never be able to cost the caller their answer."""

    class Opaque:
        def __init__(self, text):
            self.payload = text  # no `.content` at all

    gateway, _, _ = await _gateway(
        tmp_path, {"github/list_releases": "[gateway] hint"}, result_factory=Opaque
    )
    result = await gateway.call_tool("github__list_releases", {})

    assert result.payload == "upstream said list_releases"


async def test_the_call_still_routes_and_logs_normally(tmp_path):
    """The suggestion is invisible to routing and to the exposure log."""
    gateway, sessions, log = await _gateway(
        tmp_path, {"github/list_releases": "[gateway] hint"}
    )
    await gateway.list_tools()
    await gateway.call_tool("github__list_releases", {})

    assert sessions["github"].calls == [("list_releases", {})]
    calls = [
        json.loads(line)
        for line in log.path.read_text().splitlines()
        if line.strip() and json.loads(line)["kind"] == "call"
    ]
    assert [(c["tool_uid"], c["was_exposed"], c["status"]) for c in calls] == [
        ("github/list_releases", True, "ok")
    ]


def test_suggestions_load_from_config(tmp_path):
    path = tmp_path / "gateway.json"
    path.write_text(
        json.dumps(
            {
                "mode": "shadow",
                "upstreams": [{"server_id": "github", "command": "noop"}],
                "result_suggestions": {"github/list_releases": "hint"},
            }
        )
    )
    assert GatewayConfig.from_file(path).result_suggestions == {
        "github/list_releases": "hint"
    }


@pytest.mark.parametrize(
    "control,suggest,expected",
    [
        ({"suggested_called": False}, {"skipped": True}, "UNREADABLE"),
        ({"suggested_called": True}, {"sentinel_seen": True}, "UNREADABLE"),
        (
            {"suggested_called": False},
            {"sentinel_seen": False, "suggested_called": False},
            "CHANNEL DEAD",
        ),
        (
            {"suggested_called": False},
            {"sentinel_seen": True, "suggested_called": True},
            "ACTIONABLE",
        ),
        (
            {"suggested_called": False},
            {"sentinel_seen": True, "suggested_called": False},
            "VISIBLE BUT NOT TAKEN",
        ),
    ],
)
def test_verdict_reads_each_outcome(control, suggest, expected):
    """Both unreadable cases come before any conclusion: no trigger, and no headroom."""
    from mcp_gateway_router.bench.probe_suggestions import _verdict

    assert expected in _verdict({"control": control, "suggest": suggest})
