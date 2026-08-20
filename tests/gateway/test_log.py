import json

import pytest

from mcp_gateway_router.gateway.log import (
    EXPOSURE_DISCLOSED,
    EXPOSURE_LISTED,
    ExposedTool,
    ExposureLog,
)


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


def test_call_record_carries_a_three_state_exposure(tmp_path):
    log = ExposureLog(tmp_path, session_id="s1")

    log.call(
        session_id="s1",
        tool_uid="notion/search@bb",
        exposure=EXPOSURE_DISCLOSED,
        status="error",
        latency_ms=12,
    )
    log.close()

    (record,) = _records(log)
    assert record["kind"] == "call"
    assert record["exposure"] == "disclosed"
    assert record["status"] == "error"
    assert "was_exposed" not in record


def test_an_unknown_exposure_state_is_rejected(tmp_path):
    """A typo must not become a silent fourth state.

    The whole point of the enum is that `disclosed` and `unexposed` mean different
    things downstream; a misspelling that lands in the log unchallenged would be
    indistinguishable from real data months later.
    """
    log = ExposureLog(tmp_path, session_id="s1")

    with pytest.raises(ValueError):
        log.call(
            session_id="s1",
            tool_uid="notion/search@bb",
            exposure="maybe",
            status="ok",
            latency_ms=1,
        )
    log.close()


def test_find_tools_records_carry_the_query_and_what_it_disclosed(tmp_path):
    log = ExposureLog(tmp_path, session_id="s1")

    log.call(
        session_id="s1",
        tool_uid="_gateway/find_tools",
        exposure=EXPOSURE_LISTED,
        status="ok",
        latency_ms=3,
        query="recent releases",
        disclosed=["github/list_releases", "github/get_latest_release"],
    )
    log.close()

    (record,) = _records(log)
    assert record["query"] == "recent releases"
    assert record["disclosed"] == ["github/list_releases", "github/get_latest_release"]


def test_ordinary_calls_omit_the_meta_fields(tmp_path):
    """Absent, not null. A `query: null` on every upstream call is noise in a file
    that is read by eye as often as by code."""
    log = ExposureLog(tmp_path, session_id="s1")

    log.call(
        session_id="s1",
        tool_uid="github/list_releases",
        exposure=EXPOSURE_LISTED,
        status="ok",
        latency_ms=5,
    )
    log.close()

    (record,) = _records(log)
    assert "query" not in record
    assert "disclosed" not in record


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
