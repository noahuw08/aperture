"""The probe's readout, tested without paying for a run.

Nothing here can tell us whether a client forwards a call for an unadvertised tool —
that is what the money is for. What it can pin is that the probe reports *its own*
failures as failures rather than laundering them into the pre-registered verdicts, which
is the difference between a $0.14 instrument and a $0.14 story.
"""

import json

from mcp_gateway_router.bench.probe_armb import (
    CONDITIONS,
    CORE,
    TARGET,
    _disclosed_outside_core,
    _emitted,
    _log_dir,
    _log_records,
    _meta_calls,
    _uid,
    _unadvertised_emitted,
    _unadvertised_received,
    _verdict,
    _write_config,
)

#: An unadvertised GitHub tool that is *not* the withheld target. The live pre-flight had
#: the stub retriever answer a release query with exactly this class of substitute.
OTHER = ("github", "get_latest_release")


class FakeResult:
    def __init__(self, *names):
        self.tool_calls = {n: 1 for n in names}


def _meta_record(query, disclosed):
    return {
        "kind": "call",
        "tool_uid": _uid(("_gateway", "find_tools")),
        "query": query,
        "disclosed": disclosed,
    }


def _row(**overrides):
    """A readable `findtools` row: config fine, find_tools called, something offered.

    `disclosed_outside_core` is *derived* from `find_tools_disclosed` unless a test says
    otherwise, so a test that narrows what the retriever returned cannot accidentally
    keep gate 4's input wide and test a state the probe can never produce.
    """
    row = {
        "emitted": False,
        "received": False,
        "exposure": None,
        "unadvertised_emitted": [],
        "unadvertised_received": [],
        "target_emitted": False,
        "target_received": False,
        "target_exposure": None,
        "find_tools_called": True,
        "find_tools_calls": 1,
        "find_tools_queries": ["releases"],
        "find_tools_disclosed": [_uid(TARGET)],
        "target_disclosed": True,
        "n_advertised": len(CORE) + 1,
    }
    row.update(overrides)
    if "disclosed_outside_core" not in overrides:
        row["disclosed_outside_core"] = _disclosed_outside_core(row["find_tools_disclosed"])
    return row


def _trusted(**overrides):
    row = _row(find_tools_called=False, find_tools_calls=0, n_advertised=len(CORE))
    row.update(overrides)
    return row


def test_emitted_matches_the_namespaced_tail_not_a_substring():
    """The client prefixes our name with its own namespace, so the match is on the
    tail. Under a substring match a sibling tool whose name merely starts with the
    target's would manufacture a CHANNEL OPEN out of a call for something else."""
    assert _emitted(FakeResult("mcp__gateway__github__list_releases"), TARGET)
    assert _emitted(FakeResult("github__list_releases"), TARGET)
    assert not _emitted(FakeResult("mcp__gateway__github__list_releases_v2"), TARGET)
    assert not _emitted(FakeResult("mcp__gateway__github__get_me"), TARGET)


def test_meta_calls_unions_every_find_tools_call():
    """A second call that does surface the target must not be invisible behind the
    first. Reading only the first record throws away a run that worked."""
    summary = _meta_calls(
        [
            _meta_record("releases", ["github/get_me"]),
            {"kind": "decision", "n_advertised": 4},
            _meta_record("list the releases of a repo", [_uid(TARGET)]),
        ]
    )

    assert summary["n"] == 2
    assert summary["queries"] == ["releases", "list the releases of a repo"]
    assert summary["disclosed"] == ["github/get_me", _uid(TARGET)]


def test_meta_calls_is_none_when_find_tools_was_never_called():
    assert _meta_calls([{"kind": "call", "tool_uid": "github/get_me"}]) is None


def test_a_run_that_disclosed_nothing_outside_the_core_is_unreadable_not_structural():
    """The finding this probe would otherwise report with most confidence, drawn from a
    run that never showed the model any tool it had not already been given."""
    verdict = _verdict(
        {
            "findtools": _row(
                target_disclosed=False,
                find_tools_disclosed=["github/get_me"],
                find_tools_queries=["releases"],
            ),
            "trusted": _trusted(),
        }
    )

    assert verdict.startswith("UNREADABLE")
    assert "STRUCTURAL" not in verdict
    assert "outside the advertised core" in verdict


def test_gate_four_passes_on_a_disclosure_that_is_not_the_target():
    """The live pre-flight's actual outcome: the stub retriever answered a plausible
    release query with `get_latest_release` and friends, never `list_releases`. Those are
    just as withheld, so the model *was* told a callable tool it had not been advertised
    and the run is readable. Gating on TARGET threw such a run away as UNREADABLE."""
    verdict = _verdict(
        {
            "findtools": _row(
                target_disclosed=False,
                find_tools_disclosed=["github/get_me", _uid(OTHER)],
            ),
            "trusted": _trusted(),
        }
    )

    assert verdict.startswith("STRUCTURAL")


def test_an_emitted_and_received_call_reads_open_even_without_a_disclosure():
    """The disclosure gate guards the *non*-emission branch only. If the model produced
    the name and the call arrived, the channel is demonstrably open however it learned
    the name, and discarding that as UNREADABLE would throw away a positive."""
    verdict = _verdict(
        {
            "findtools": _row(
                emitted=True,
                received=True,
                exposure="unexposed",
                unadvertised_received=[{"tool_uid": _uid(TARGET), "exposure": "unexposed"}],
                target_received=True,
                target_disclosed=False,
                find_tools_disclosed=["github/get_me"],
            ),
            "trusted": _trusted(),
        }
    )

    assert verdict.startswith("CHANNEL OPEN")


def test_channel_open_on_a_non_target_tool_names_the_tool_that_arrived():
    """The whole point of the widening. A call for any withheld tool proves the channel;
    the verdict must say which one arrived and in what state, rather than implying the
    withheld target was used when it was not."""
    verdict = _verdict(
        {
            "findtools": _row(
                emitted=True,
                received=True,
                exposure="disclosed",
                unadvertised_emitted=[_uid(OTHER)],
                unadvertised_received=[{"tool_uid": _uid(OTHER), "exposure": "disclosed"}],
                target_emitted=False,
                target_received=False,
                target_disclosed=False,
                find_tools_disclosed=[_uid(OTHER)],
            ),
            "trusted": _trusted(),
        }
    )

    assert verdict.startswith("CHANNEL OPEN")
    assert _uid(OTHER) in verdict
    assert "disclosed" in verdict


def test_unadvertised_emitted_counts_any_tool_outside_the_advertised_set():
    """L1 is about the class of tool, not about TARGET. A substitute the retriever ranked
    above the withheld target exercises the hypothesis just as well."""
    emitted = _unadvertised_emitted(
        FakeResult(
            "mcp__gw__github__get_latest_release",
            "mcp__gw__github__get_me",
            "mcp__gw___gateway__find_tools",
        )
    )

    assert emitted == [_uid(OTHER)]


def test_unadvertised_emitted_ignores_the_clients_own_builtins():
    """The worst failure available here: counting a `Bash` or `ToolSearch` call as an
    unadvertised MCP call manufactures a CHANNEL OPEN — the one verdict that says arm B
    needs no further work — out of a run in which the gateway was never touched."""
    assert _unadvertised_emitted(FakeResult("ToolSearch", "Bash", "Read", "WebFetch")) == []


def test_unadvertised_received_reads_the_gateways_own_exposure_field():
    """L2 is authoritative because the gateway assigns `exposure` by comparing the called
    key against what it actually put in the tools/list array. `listed` is the core;
    `disclosed`/`unexposed` *is* the phenomenon. find_tools is excluded by name, not by
    trusting that it happens to log as `listed` — with the meta-tool disabled, as in the
    `trusted` condition, a call naming it logs `unexposed`."""
    arrived = _unadvertised_received(
        [
            {"kind": "call", "tool_uid": "github/get_me", "exposure": "listed"},
            {"kind": "call", "tool_uid": _uid(OTHER), "exposure": "disclosed"},
            {"kind": "call", "tool_uid": _uid(TARGET), "exposure": "unexposed"},
            {"kind": "call", "tool_uid": "_gateway/find_tools", "exposure": "unexposed"},
            {"kind": "decision", "n_advertised": 4},
        ]
    )

    assert arrived == [
        {"tool_uid": _uid(OTHER), "exposure": "disclosed"},
        {"tool_uid": _uid(TARGET), "exposure": "unexposed"},
    ]


def test_structural_still_reachable_once_the_target_was_actually_disclosed():
    verdict = _verdict({"findtools": _row(), "trusted": _trusted()})

    assert verdict.startswith("STRUCTURAL")


def test_a_findtools_run_with_no_decision_records_names_startup_failure():
    """`None` is not a wrong count, it is no gateway at all — the silent config death
    `_write_config` warns about. Letting it pass the count check reported a dead
    gateway as a fact about the model."""
    verdict = _verdict({"findtools": _row(n_advertised=None), "trusted": _trusted()})

    assert verdict.startswith("UNREADABLE")
    assert "startup" in verdict


def test_a_findtools_run_with_the_wrong_advertised_count_is_unreadable():
    verdict = _verdict({"findtools": _row(n_advertised=99), "trusted": _trusted()})

    assert verdict.startswith("UNREADABLE")
    assert "99" in verdict


def test_a_trusted_run_with_no_decision_records_is_unreadable():
    verdict = _verdict({"findtools": _row(), "trusted": _trusted(n_advertised=None)})

    assert verdict.startswith("UNREADABLE")
    assert "trusted" in verdict


def _base(tmp_path):
    path = tmp_path / "gateway.armA.json"
    path.write_text(json.dumps({"mode": "shadow", "upstreams": [], "pinned": []}))
    return path


def test_write_config_clears_the_previous_runs_records(tmp_path):
    """Otherwise `decisions[0]` is the oldest run's record and a target call from a
    previous run reads as received — a CHANNEL OPEN for a rerun in which nothing
    arrived. Reruns are the documented workflow, so the dirty directory is normal."""
    base = _base(tmp_path)
    condition = CONDITIONS[0]
    log_dir = _log_dir(base, condition)
    log_dir.mkdir(parents=True)
    (log_dir / "s-1-old.jsonl").write_text(
        json.dumps({"kind": "call", "tool_uid": _uid(TARGET), "exposure": "disclosed"}) + "\n"
    )

    _write_config(base, condition)

    assert _log_records(base, condition) == []


def test_write_config_lands_beside_the_base_config(tmp_path):
    """`from_file` resolves `.env`, `log_dir` and `catalog_path` against the config's
    own parent; anywhere else and the gateway dies at startup, silently."""
    base = _base(tmp_path)
    out = _write_config(base, CONDITIONS[0])

    assert out.parent == base.parent
    payload = json.loads(out.read_text())
    assert payload["mode"] == "live"
    assert payload["log_dir"] == f"runs/probe-armb/{CONDITIONS[0].name}"
    assert payload["find_tools"]["enabled"] is CONDITIONS[0].find_tools
