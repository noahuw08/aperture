import json

from mcp_gateway_router.replay.sessions import load_sessions


def _write(path, records):
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def _decision(session_id, ts, environment=None, n_candidates=95):
    return {
        "kind": "decision",
        "ts": ts,
        "session_id": session_id,
        "arm": "passthrough",
        "mode": "shadow",
        "n_advertised": n_candidates,
        "decision_point": "A",
        "catalog_hash": "h",
        "selector_version": "static-set",
        "budget_tokens": 3000,
        "context": {"environment": environment or {}},
        "n_candidates": n_candidates,
        "exposed": [],
    }


def _call(session_id, ts, tool_uid, status="ok"):
    return {
        "kind": "call",
        "ts": ts,
        "session_id": session_id,
        "tool_uid": tool_uid,
        "exposure": "listed",
        "status": status,
        "latency_ms": 5,
    }


def test_a_session_carries_its_environment_and_its_calls(tmp_path):
    path = tmp_path / "exposure.jsonl"
    _write(
        path,
        [
            _decision("s1", "2026-08-09T10:00:00Z", {"project": "alpha", "hour": "10"}),
            _call("s1", "2026-08-09T10:00:05Z", "github/search_code"),
            _call("s1", "2026-08-09T10:00:09Z", "github/get_me"),
        ],
    )

    (session,) = load_sessions(path)

    assert session.session_id == "s1"
    assert session.environment["project"] == "alpha"
    assert session.called == (("github", "search_code"), ("github", "get_me"))


def test_sessions_are_ordered_by_when_they_opened(tmp_path):
    path = tmp_path / "exposure.jsonl"
    _write(
        path,
        [
            _decision("later", "2026-08-09T12:00:00Z"),
            _decision("earlier", "2026-08-09T09:00:00Z"),
        ],
    )

    assert [s.session_id for s in load_sessions(path)] == ["earlier", "later"]


def test_repeated_calls_are_preserved_in_order(tmp_path):
    """Frequency within a session is signal — do not collapse to a set."""
    path = tmp_path / "exposure.jsonl"
    _write(
        path,
        [
            _decision("s1", "2026-08-09T10:00:00Z"),
            _call("s1", "2026-08-09T10:00:01Z", "github/search_code"),
            _call("s1", "2026-08-09T10:00:02Z", "github/search_code"),
        ],
    )

    (session,) = load_sessions(path)

    assert session.called == (("github", "search_code"), ("github", "search_code"))


def test_a_session_with_no_calls_is_still_a_session(tmp_path):
    """It is evidence about context that led nowhere, and it must not be dropped."""
    path = tmp_path / "exposure.jsonl"
    _write(path, [_decision("quiet", "2026-08-09T10:00:00Z", {"project": "beta"})])

    (session,) = load_sessions(path)

    assert session.called == ()
    assert session.environment["project"] == "beta"


def test_repeated_decisions_in_one_session_do_not_duplicate_it(tmp_path):
    """tools/list can fire more than once; the session opened only once."""
    path = tmp_path / "exposure.jsonl"
    _write(
        path,
        [
            _decision("s1", "2026-08-09T10:00:00Z", {"project": "alpha"}),
            _decision("s1", "2026-08-09T10:05:00Z", {"project": "alpha"}),
        ],
    )

    sessions = load_sessions(path)

    assert len(sessions) == 1
    assert sessions[0].opened_at.hour == 10


def test_calls_without_a_decision_record_are_still_captured(tmp_path):
    """A crash before the first decision flush must not silently drop the calls."""
    path = tmp_path / "exposure.jsonl"
    _write(path, [_call("orphan", "2026-08-09T11:00:00Z", "github/get_me")])

    (session,) = load_sessions(path)

    assert session.session_id == "orphan"
    assert session.called == (("github", "get_me"),)


def test_turn_records_are_ignored_without_error(tmp_path):
    path = tmp_path / "exposure.jsonl"
    _write(
        path,
        [
            _decision("s1", "2026-08-09T10:00:00Z"),
            {
                "kind": "turn",
                "ts": "2026-08-09T10:00:03Z",
                "session_id": "s1",
                "input_tokens": 10,
                "cached_input_tokens": 0,
                "output_tokens": 2,
            },
        ],
    )

    assert len(load_sessions(path)) == 1


def test_a_truncated_final_line_does_not_lose_the_file(tmp_path):
    """The log is flushed per record, but a kill mid-write can still truncate one."""
    path = tmp_path / "exposure.jsonl"
    path.write_text(
        json.dumps(_decision("s1", "2026-08-09T10:00:00Z")) + "\n" + '{"kind": "call", "ses'
    )

    sessions = load_sessions(path)

    assert [s.session_id for s in sessions] == ["s1"]


def test_a_directory_of_per_session_files_is_merged(tmp_path):
    """The gateway writes one file per session; the loader reads the whole directory."""
    logs = tmp_path / "runs"
    logs.mkdir()
    _write(
        logs / "s1.jsonl",
        [
            _decision("s1", "2026-08-09T10:00:00Z", {"project": "alpha"}),
            _call("s1", "2026-08-09T10:00:05Z", "github/get_me"),
        ],
    )
    _write(
        logs / "s2.jsonl",
        [
            _decision("s2", "2026-08-09T09:00:00Z", {"project": "beta"}),
            _call("s2", "2026-08-09T09:00:05Z", "github/search_code"),
        ],
    )

    sessions = load_sessions(logs)

    assert [s.session_id for s in sessions] == ["s2", "s1"]
    assert sessions[0].environment["project"] == "beta"


def test_one_corrupt_session_file_does_not_lose_the_others(tmp_path):
    logs = tmp_path / "runs"
    logs.mkdir()
    _write(logs / "good.jsonl", [_decision("good", "2026-08-09T10:00:00Z")])
    (logs / "bad.jsonl").write_text('{"kind": "decision", "ses')

    assert [s.session_id for s in load_sessions(logs)] == ["good"]
