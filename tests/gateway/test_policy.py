import json

from mcp_gateway_router.catalog import Catalog, Tool
from mcp_gateway_router.gateway.config import GatewayConfig
from mcp_gateway_router.gateway.log import ExposureLog
from mcp_gateway_router.gateway.policy import Policy
from mcp_gateway_router.selector import DecisionContext
from mcp_gateway_router.tokens import StaticTokenCounter


def _catalog():
    return Catalog(
        [
            Tool(server_id="github", name=f"t{i}", description=f"tool {i}", input_schema={})
            for i in range(10)
        ]
    )


def _config(tmp_path, mode="live", pinned=(("github", "t0"),), budget=250):
    return GatewayConfig(
        upstreams=(),
        mode=mode,
        arm="test-arm",
        budget_tokens=budget,
        pinned=tuple(pinned),
        log_path=tmp_path / "exposure.jsonl",
    )


class HeadSelector:
    """Ranks tools in catalog order. Deterministic, so propensity is 1.0."""

    name = "head"

    def select(self, context, catalog, budget, counter):
        from mcp_gateway_router.selector import fill_budget

        return fill_budget(list(catalog), budget, counter)


class ExplodingSelector:
    name = "exploding"

    def select(self, context, catalog, budget, counter):
        raise RuntimeError("scorer is cold")


def _records(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_live_mode_returns_the_selection(tmp_path):
    config = _config(tmp_path, mode="live")
    log = ExposureLog(config.log_path)
    policy = Policy(config, HeadSelector(), StaticTokenCounter({}, default=100), log)

    exposed = policy.decide(_catalog(), "hash1", DecisionContext(session_id="s1"))
    log.close()

    # Pinned t0 is taken first and is exempt from the budget, spending 100 of 250.
    # t1 fits at 200; t2 would reach 300 and is skipped, as is everything after it.
    assert [t.name for t in exposed] == ["t0", "t1"]


def test_shadow_mode_returns_everything_but_logs_the_selection(tmp_path):
    config = _config(tmp_path, mode="shadow")
    log = ExposureLog(config.log_path)
    policy = Policy(config, HeadSelector(), StaticTokenCounter({}, default=100), log)

    exposed = policy.decide(_catalog(), "hash1", DecisionContext(session_id="s1"))
    log.close()

    assert len(exposed) == 10
    (record,) = _records(config.log_path)
    assert len(record["exposed"]) == 2
    # exposed=[] would otherwise be ambiguous between "shadow, client got all" and
    # "live, selector chose nothing" — mode and n_advertised disambiguate.
    assert record["mode"] == "shadow"
    assert record["n_advertised"] == 10


def test_live_mode_records_what_the_client_actually_received(tmp_path):
    config = _config(tmp_path, mode="live")
    log = ExposureLog(config.log_path)
    policy = Policy(config, HeadSelector(), StaticTokenCounter({}, default=100), log)

    policy.decide(_catalog(), "hash1", DecisionContext(session_id="s1"))
    log.close()

    (record,) = _records(config.log_path)
    assert record["mode"] == "live"
    assert record["n_advertised"] == 2
    assert record["n_candidates"] == 10


def test_a_raising_selector_fails_open_to_the_pinned_core(tmp_path):
    config = _config(tmp_path, mode="live")
    log = ExposureLog(config.log_path)
    policy = Policy(config, ExplodingSelector(), StaticTokenCounter({}, default=100), log)

    exposed = policy.decide(_catalog(), "hash1", DecisionContext(session_id="s1"))
    log.close()

    assert [t.name for t in exposed] == ["t0"]
    (record,) = _records(config.log_path)
    assert record["selector_version"] == "fail-open"


def test_decision_is_logged_with_propensity_and_context(tmp_path):
    config = _config(tmp_path, mode="live")
    log = ExposureLog(config.log_path)
    policy = Policy(config, HeadSelector(), StaticTokenCounter({}, default=100), log)

    context = DecisionContext(session_id="s1", client_name="claude-code")
    policy.decide(_catalog(), "hash1", context)
    log.close()

    (record,) = _records(config.log_path)
    assert record["arm"] == "test-arm"
    assert record["decision_point"] == "A"
    assert record["catalog_hash"] == "hash1"
    assert record["n_candidates"] == 10
    assert record["context"]["client_name"] == "claude-code"
    assert all(e["propensity"] == 1.0 for e in record["exposed"])


def test_decision_point_is_c_when_a_task_is_present(tmp_path):
    config = _config(tmp_path, mode="live")
    log = ExposureLog(config.log_path)
    policy = Policy(config, HeadSelector(), StaticTokenCounter({}, default=100), log)

    policy.decide(_catalog(), "h", DecisionContext(session_id="s1", task="find the PRs"))
    log.close()

    (record,) = _records(config.log_path)
    assert record["decision_point"] == "C"


def test_a_pinned_tool_missing_from_the_catalog_is_skipped_not_fatal(tmp_path):
    config = _config(tmp_path, mode="live", pinned=(("github", "t0"), ("slack", "gone")))
    log = ExposureLog(config.log_path)
    policy = Policy(config, HeadSelector(), StaticTokenCounter({}, default=100), log)

    exposed = policy.decide(_catalog(), "h", DecisionContext(session_id="s1"))
    log.close()

    assert "t0" in [t.name for t in exposed]
