import json

from mcp_gateway_router.gateway.log import ExposedTool, ExposureLog


def _records(log):
    return [json.loads(line) for line in log.path.read_text().splitlines() if line.strip()]


def test_decision_record_carries_propensity_and_token_cost(tmp_path):
    log = ExposureLog(tmp_path, session_id="s1")

    log.decision(
        session_id="s1",
        arm="passthrough",
        mode="live",
        n_advertised=1,
        decision_point="A",
        catalog_hash="abc123",
        selector_version="passthrough/1",
        budget_tokens=3000,
        context={"repo": "mcp-gateway-router", "branch": "main"},
        n_candidates=400,
        exposed=[ExposedTool("github/search_code@aa", 1.0, 1.0, 180)],
    )
    log.close()

    (record,) = _records(log)
    assert record["kind"] == "decision"
    assert record["decision_point"] == "A"
    assert record["context"]["repo"] == "mcp-gateway-router"
    assert record["exposed"][0]["propensity"] == 1.0
    assert record["exposed"][0]["token_cost"] == 180


def test_call_record_carries_was_exposed(tmp_path):
    log = ExposureLog(tmp_path, session_id="s1")

    log.call(
        session_id="s1",
        tool_uid="notion/search@bb",
        was_exposed=False,
        status="error",
        latency_ms=12,
    )
    log.close()

    (record,) = _records(log)
    assert record["kind"] == "call"
    assert record["was_exposed"] is False
    assert record["status"] == "error"


def test_turn_record_separates_cached_input_tokens(tmp_path):
    log = ExposureLog(tmp_path, session_id="s1")

    log.turn(
        session_id="s1",
        input_tokens=1200,
        cached_input_tokens=900,
        output_tokens=64,
    )
    log.close()

    (record,) = _records(log)
    assert record["kind"] == "turn"
    assert record["cached_input_tokens"] == 900


def test_every_record_is_timestamped_and_appends(tmp_path):
    log = ExposureLog(tmp_path, session_id="s1")
    log.turn(session_id="s1", input_tokens=1, cached_input_tokens=0, output_tokens=1)
    log.close()

    # Reopening the same session appends rather than truncating — a resumed or
    # reconnected session must not lose what it already wrote.
    reopened = ExposureLog(tmp_path, session_id="s1")
    reopened.turn(session_id="s1", input_tokens=2, cached_input_tokens=0, output_tokens=1)
    reopened.close()

    records = _records(reopened)
    assert [r["input_tokens"] for r in records] == [1, 2]
    assert all(r["ts"] for r in records)


def test_parent_directory_is_created(tmp_path):
    log = ExposureLog(tmp_path / "nested" / "deeper", session_id="s1")
    log.turn(session_id="s1", input_tokens=1, cached_input_tokens=0, output_tokens=1)
    log.close()

    assert log.path.exists()
    assert log.path.name == "s1.jsonl"


def test_two_logs_created_in_the_same_second_do_not_share_a_file(tmp_path):
    """Second-resolution ids collided, so concurrent sessions interleaved into one file."""
    a = ExposureLog(tmp_path)
    b = ExposureLog(tmp_path)
    a.close()
    b.close()

    assert a.path != b.path
    assert a.session_id != b.session_id
