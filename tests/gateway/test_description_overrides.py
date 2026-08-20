"""The description lever, and the two things it must not disturb.

Rewriting descriptions is the only mechanism the "tool search + personalization" arm
has. These tests pin the two invariants that make it safe to use: identity is untouched
(so routing and ``was_exposed`` still work), and an empty mapping is byte-for-byte
today's behaviour (so the lever cannot silently change an arm that didn't ask for it).
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


class AllSelector:
    name = "all"

    def select(self, context, catalog, budget, counter):
        return list(catalog)


async def _gateway(tmp_path, overrides):
    sessions = {"github": FakeSession(["get_me", "get_label"])}

    async def factory(spec):
        return sessions[spec.server_id]

    config = GatewayConfig(
        upstreams=(UpstreamSpec(server_id="github", command="noop"),),
        mode="shadow",
        arm="probe",
        budget_tokens=3000,
        pinned=(),
        log_dir=tmp_path,
        description_overrides=overrides,
    )
    pool = UpstreamPool(config.upstreams, session_factory=factory)
    await pool.start()
    log = ExposureLog(config.log_dir)
    policy = Policy(config, AllSelector(), StaticTokenCounter({}, default=10), log)
    return Gateway(config, pool, policy, log), sessions, log


async def test_an_override_replaces_only_the_named_tools_description(tmp_path):
    gateway, _, _ = await _gateway(tmp_path, {"github/get_me": "REWRITTEN"})
    by_name = {t.name: t for t in await gateway.list_tools()}

    assert by_name["get_me"].description == "REWRITTEN"
    assert by_name["get_label"].description == "does get_label"


async def test_an_empty_mapping_is_todays_behaviour(tmp_path):
    gateway, _, _ = await _gateway(tmp_path, {})
    assert [t.description for t in await gateway.list_tools()] == [
        "does get_me",
        "does get_label",
    ]


async def test_a_rewritten_tool_keeps_its_identity_and_still_routes(tmp_path):
    """The description is the only thing that may move — ``(server_id, name)`` anchors
    routing, the log key, and the catalog reconciliation."""
    gateway, sessions, _ = await _gateway(tmp_path, {"github/get_me": "REWRITTEN"})
    (tool,) = [t for t in await gateway.list_tools() if t.name == "get_me"]

    assert tool.key == ("github", "get_me")
    await gateway.call_tool("github__get_me", {})
    assert sessions["github"].calls == [("get_me", {})]


async def test_exposure_survives_a_rewrite(tmp_path):
    """`exposure` keys on identity, so a rewritten tool must still read as listed.

    Recorded as a test because the natural implementation — building ``_exposed`` from
    the rewritten list — would also pass every other test here while silently making
    the one field the missing-demand signal depends on unreliable.
    """
    gateway, _, log = await _gateway(tmp_path, {"github/get_me": "REWRITTEN"})
    await gateway.list_tools()
    await gateway.call_tool("github__get_me", {})

    calls = [
        json.loads(line)
        for line in log.path.read_text().splitlines()
        if line.strip() and json.loads(line)["kind"] == "call"
    ]
    assert [c["exposure"] for c in calls] == ["listed"]


async def test_an_override_for_an_absent_tool_is_ignored(tmp_path):
    """A stale key is not fatal — same posture as a stale pin."""
    gateway, _, _ = await _gateway(tmp_path, {"github/nope": "REWRITTEN"})
    assert len(await gateway.list_tools()) == 2


def test_overrides_load_from_config(tmp_path):
    path = tmp_path / "gateway.json"
    path.write_text(
        json.dumps(
            {
                "mode": "shadow",
                "upstreams": [{"server_id": "github", "command": "noop"}],
                "description_overrides": {"github/get_me": "REWRITTEN"},
            }
        )
    )
    assert GatewayConfig.from_file(path).description_overrides == {
        "github/get_me": "REWRITTEN"
    }


def test_config_without_overrides_defaults_to_empty(tmp_path):
    path = tmp_path / "gateway.json"
    path.write_text(
        json.dumps(
            {"mode": "shadow", "upstreams": [{"server_id": "github", "command": "noop"}]}
        )
    )
    assert GatewayConfig.from_file(path).description_overrides == {}


def _row(**over):
    row = {
        "target_called": True,
        "decoy_called": False,
        "target_surfaced": True,
        "decoy_surfaced": False,
        "searched": True,
    }
    row.update(over)
    return row


@pytest.mark.parametrize(
    "control,blind,steer,expected",
    [
        # A control that never reached the target attributes nothing.
        (_row(target_called=False), _row(), _row(), "UNREADABLE"),
        # Nothing searched means the retriever was never exercised.
        (
            _row(searched=False),
            _row(searched=False),
            _row(searched=False),
            "UNREADABLE",
        ),
        # The decisive win: the ranker itself returned the rewritten decoy.
        (_row(), _row(), _row(decoy_surfaced=True, decoy_called=True), "RANKER-LEVEL"),
        # Called but never surfaced — the model was persuaded, not the index.
        (_row(), _row(), _row(decoy_called=True), "MODEL-LEVEL ONLY"),
        # Blinding removed the target from the candidate set; promotion unproven.
        (_row(), _row(target_surfaced=False), _row(), "suppression only"),
        # Target held on with an unrelated description and a boosted decoy present.
        (_row(), _row(), _row(), "NAMES DOMINATE"),
    ],
)
def test_verdict_reads_each_outcome(control, blind, steer, expected):
    from mcp_gateway_router.bench.probe_descriptions import _verdict

    assert expected in _verdict({"control": control, "blind": blind, "steer": steer})


class _Block:
    def __init__(self, text):
        self.text = text


@pytest.mark.parametrize(
    "content,expected",
    [
        (None, ""),
        ("plain", "plain"),
        (["a", "b"], "a\nb"),
        ([_Block("x"), _Block("y")], "x\ny"),
    ],
)
def test_tool_result_content_flattens_to_text(content, expected):
    """The result shape is the client's, so the flatten has to tolerate all of them."""
    from mcp_gateway_router.bench.runner import _as_text

    assert _as_text(content) == expected


def test_surfaced_matches_either_naming_form():
    """Search results may carry the namespaced or the raw `server/name` form."""
    from mcp_gateway_router.bench.runner import ArmResult
    from mcp_gateway_router.bench.probe_descriptions import _surfaced

    def result(*blobs):
        return ArmResult(
            arm="p", task_id="t", ok=True, answer=None, turns=1, cost_usd=0.0,
            input_tokens=0, cache_creation_tokens=0, cache_read_tokens=0,
            output_tokens=0, tool_search_calls=1, search_results=list(blobs),
        )

    assert _surfaced(result("... github__get_me ..."), "github/get_me")
    assert _surfaced(result("... github/get_me ..."), "github/get_me")
    assert not _surfaced(result("... github__get_label ..."), "github/get_me")
    assert not _surfaced(result(), "github/get_me")
