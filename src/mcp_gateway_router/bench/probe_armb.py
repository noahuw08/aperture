"""Will the client call a tool it never saw advertised?

Arm B — a small pinned core plus a ``find_tools`` meta-tool — rests on one unverified
assumption: that a client will emit a ``tools/call`` for a tool it learned about from a
tool *result* rather than from the ``tools/list`` array. If it will not, arm B needs
registration via ``list_changed``, which costs a prompt-cache invalidation nobody has
measured, and the design changes shape.

**The measurement is three-layered, because model intent and client forwarding are
different events.**

L1  did the model *emit* the call?     ``ArmResult.tool_calls`` — the ToolUseBlock is in
                                       the assistant message whether or not the client
                                       executes it.
L2  did the gateway *receive* it?      a ``kind: call`` record in the session log.
L3  what state did we log it as?       ``disclosed`` vs ``unexposed``.

**L1 without L2 is the discriminator**: the model tried and the client blocked it. That
is why the authoritative readout is the gateway's own log and not ``ArmResult`` alone.

**The unit of measurement is the phenomenon, not one tool.** The gateway advertises a
3-tool core, so *every other tool in the 95-tool catalog* is one the client never saw. An
early version keyed the whole verdict on ``TARGET`` alone, and a live pre-flight caught
what that costs: the stub retriever, asked "get recent release tag names for a github
repo", returned ``get_latest_release``, ``get_release_by_tag`` and ``get_tag`` — sensible
substitutes, every one of them just as unadvertised as ``list_releases``. If the agent
calls one of those the channel is demonstrably open, and the narrow probe would have
recorded ``emitted=False, received=False`` and reported STRUCTURAL, its strongest
negative, off a run in which the thing under test visibly worked. So L1 and L2 both ask
"any tool outside the advertised set?"; ``TARGET`` survives only as a reported
sub-signal.

**L0 — was the model ever told about a callable tool it had not been advertised?**
``find_tools`` being called is not the same as anything useful being returned: the
retriever is a deliberate stub over ~95 tools and a malformed call discloses nothing. A
non-emission only reads as a refusal once ``find_tools`` disclosed at least one tool
outside the core; otherwise the run is UNREADABLE.

**Two conditions.**

``findtools``  core + ``find_tools``. The primary run.
``trusted``    core only; the withheld tool's exact advertised name is given in the
               *system prompt*. The system prompt is an operator instruction, so the
               refusal measured in ``probe_suggestions`` does not apply to it. This is a
               positive control: it establishes whether the client filters at all,
               independent of how the agent feels about ``find_tools``.

⚠️ **This is the first live-mode run in the project.** All 27 decision records on disk
are ``mode: shadow``. Live mode is covered by tests but has never served a real client,
so the advertised count is checked before anything is read into the result.

Run (subscription auth; ``ANTHROPIC_API_KEY`` must be UNSET)::

    cd /tmp && env -u ANTHROPIC_API_KEY uv run --directory <repo> \\
        --with claude-agent-sdk --extra gateway \\
        python -m mcp_gateway_router.bench.probe_armb

Two runs, ~$0.07 each.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from ..gateway.config import GATEWAY_SERVER_ID, NAMESPACE_SEP
from ..gateway.log import EXPOSURE_DISCLOSED, EXPOSURE_UNEXPOSED
from ..gateway.metatools import FIND_TOOLS
from ..gateway.naming import AdvertisedNameError, parse_advertised
from .runner import run_arm

REPO = Path(__file__).resolve().parents[3]

#: Validated in probe_descriptions: it forces a tool call, and its control reached the
#: target in 3 turns. A probe task has exactly one hard requirement — it must force a
#: call — and reusing a task that already met it removes a whole class of false null.
TASK = (
    "List the tag names of the 3 most recent releases in the "
    "modelcontextprotocol/servers repository."
)

#: Withheld from the advertised set in both conditions.
TARGET = ("github", "list_releases")

#: GitHub stays visibly reachable so the agent does not conclude the server is absent
#: and stop under the "say so if no MCP tool can do it" instruction — but nothing here
#: can list releases. A probe fixture, not a proposal: production core membership is an
#: open design question and nothing in this probe answers it.
CORE = [["github", "get_me"], ["notion", "API-get-self"], ["playwright", "browser_close"]]

#: The meta-tool's own key. Excluded from both L1 and L2 *by name*, never by relying on
#: the gateway logging it as ``listed``: with ``find_tools`` disabled — which is exactly
#: what the ``trusted`` condition does — a call naming it is a hallucination and logs as
#: ``unexposed`` (``server._exposure``). Leaning on the accident would turn the control's
#: own miss into a CHANNEL OPEN.
META_KEY = (GATEWAY_SERVER_ID, FIND_TOOLS)

#: Everything the client was actually shown. A call for anything else is the phenomenon.
CORE_KEYS = frozenset(tuple(key) for key in CORE)
ADVERTISED_KEYS = CORE_KEYS | {META_KEY}

#: The server ids a gateway tool call can legitimately carry. ``CORE`` spans every server
#: in the probe catalog (github, notion, playwright), so this is the whole set — and
#: requiring membership is what stops a client built-in that happens to contain ``__``
#: from parsing as an unadvertised MCP tool and manufacturing a false CHANNEL OPEN.
KNOWN_SERVERS = frozenset({key[0] for key in CORE_KEYS} | {GATEWAY_SERVER_ID})

#: The two exposure states that *are* the hypothesis. A ``kind: call`` record logged as
#: either one is, by the gateway's own definition (``log.py``), a call for a tool that was
#: never in the ``tools/list`` array — which is the thing this probe exists to detect.
#: ``listed`` is the core, and is therefore not it.
UNADVERTISED_EXPOSURES = (EXPOSURE_DISCLOSED, EXPOSURE_UNEXPOSED)

BASE_SYSTEM = (
    "Answer using the available MCP tools. Do not use Bash, WebFetch, or your own "
    "knowledge to answer — if no MCP tool can do it, say so."
)


def _advertised(key: tuple[str, str]) -> str:
    """The name the gateway puts in the ``tools/list`` array, per ``naming.py``."""
    return f"{key[0]}{NAMESPACE_SEP}{key[1]}"


def _uid(key: tuple[str, str]) -> str:
    """The ``tool_uid`` the exposure log keys a call record on (``server/name``)."""
    return f"{key[0]}/{key[1]}"


TARGET_ADVERTISED = _advertised(TARGET)

TRUSTED_SYSTEM = (
    f"{BASE_SYSTEM} The tool `{TARGET_ADVERTISED}` is available to you through the MCP "
    f"server even though it is not in your listed tools. Call it directly by that exact "
    f"name when you need it."
)


@dataclass(frozen=True)
class Condition:
    name: str
    find_tools: bool
    system: str
    reads: str


CONDITIONS = (
    Condition("findtools", True, BASE_SYSTEM, "will the model call what find_tools disclosed?"),
    Condition("trusted", False, TRUSTED_SYSTEM, "does the client filter at all? (control)"),
)


def _log_dir(base: Path, condition: Condition) -> Path:
    """Where this condition's records land. One definition, used by writer and reader.

    Must stay equal to the ``log_dir`` written into the config below, which
    ``from_file`` resolves relative to the config's own parent.
    """
    return base.parent / "runs" / "probe-armb" / condition.name


def _write_config(base: Path, condition: Condition) -> Path:
    """One gateway config per condition, beside the base config.

    ⚠️ **Never into an output directory.** ``from_file`` resolves ``.env``, ``log_dir``
    and ``catalog_path`` relative to the config's own parent, and ``_expand`` *raises*
    on an unresolvable ``${VAR}`` — so a config written elsewhere fails to load, the
    gateway dies at startup, and the session sees no MCP tools at all. That failure is
    silent from here: both conditions come back empty and look like a clean negative.

    **The condition's log directory is emptied here**, because a run must read only its
    own records. ``ExposureLog`` opens a *new* per-session file every run and never
    truncates, so a second run leaves the first run's files in place: ``decisions[0]``
    would be the oldest run's decision record, and ``_received`` would happily match a
    target call that arrived last time. The pre-registered workflow is explicitly
    "UNREADABLE → fix framing → rerun", so the dirty directory is the *normal* case,
    not the edge case — and the failure it produces is the worst one available, a
    CHANNEL OPEN verdict for a rerun in which the call never arrived. Clearing beats
    picking the newest file: it also removes half-written logs from a crashed run.
    """
    log_dir = _log_dir(base, condition)
    for stale in log_dir.glob("*.jsonl"):
        stale.unlink()

    out = base.parent / f"gateway.armb-{condition.name}.json"
    config = json.loads(base.read_text())
    config.update(
        {
            "mode": "live",
            "selector": "static-set",
            "pinned": CORE,
            "arm": f"probe-armb/{condition.name}",
            "log_dir": f"runs/probe-armb/{condition.name}",
            "find_tools": {"enabled": condition.find_tools, "k": 5},
        }
    )
    out.write_text(json.dumps(config, indent=2) + "\n")
    return out


def _log_records(base: Path, condition: Condition) -> list[dict]:
    """Every record the gateway wrote for this condition.

    This is the authoritative readout. ``ArmResult`` reports what the *client* did with
    the model's output; only the gateway's own log says what actually arrived.

    Every ``*.jsonl`` in the directory belongs to this run, because ``_write_config``
    emptied it immediately beforehand.
    """
    log_dir = _log_dir(base, condition)
    if not log_dir.exists():
        return []
    records: list[dict] = []
    for file in sorted(log_dir.glob("*.jsonl")):
        for line in file.read_text().splitlines():
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def _emitted(result, key: tuple[str, str]) -> bool:
    """L1 — did the model emit a call for this tool?

    The client prefixes our advertised name with its own namespace
    (``mcp__gateway__github__list_releases``), so the match is on the *tail*, not a
    substring: under ``in``, a catalog containing ``github__list_releases_v2`` would
    report the withheld target as called and manufacture a CHANNEL OPEN out of a call
    for a different tool.
    """
    tail = _advertised(key)
    return any(
        name == tail or name.endswith(f"{NAMESPACE_SEP}{tail}") for name in result.tool_calls
    )


def _received(records: list[dict], key: tuple[str, str]) -> dict | None:
    """L2/L3 — did the gateway receive it, and as what?"""
    uid = _uid(key)
    for record in records:
        if record.get("kind") == "call" and record.get("tool_uid") == uid:
            return record
    return None


def _parse_emitted(name: str) -> tuple[str, str] | None:
    """Resolve a name the *client* reported back to a catalog key, or ``None``.

    The SDK namespaces MCP tools with its own prefix — ``mcp__gw__github__get_tag`` for
    what we advertised as ``github__get_tag`` — and mixes them in ``tool_calls`` with the
    client's own built-ins (``ToolSearch``, ``Bash``, ``Read``). Those built-ins are not
    gateway tools at all, and counting one as an unadvertised MCP call would manufacture
    a CHANNEL OPEN out of a ``Bash`` invocation: the worst failure available here, because
    CHANNEL OPEN is the verdict that says arm B needs no further work.

    So peel prefix segments with ``parse_advertised`` — the same helper that produced the
    name — and accept only when the head is a server the gateway actually fronts. A name
    with no ``__`` at all fails on the first parse; one with ``__`` but no known server id
    is peeled to nothing and rejected. Both return ``None``, which reads as "not ours",
    never as "unadvertised".
    """
    remainder = name
    while True:
        try:
            server_id, tool_name = parse_advertised(remainder)
        except AdvertisedNameError:
            return None
        if server_id in KNOWN_SERVERS:
            return server_id, tool_name
        remainder = tool_name


def _unadvertised_emitted(result) -> list[str]:
    """L1 — every call the model emitted for a tool it was never advertised.

    The hypothesis is about the *class* of tool, not about ``TARGET``. A pre-flight over
    the real catalog had the stub retriever answer a plausible release query with
    ``get_latest_release`` / ``get_release_by_tag`` / ``get_tag``; calling any of those
    proves the same point as calling ``list_releases``, and a TARGET-only reading would
    have scored it as a non-emission.

    The meta-tool is excluded explicitly: it *is* advertised in the ``findtools``
    condition, and in ``trusted`` a call naming it is a hallucination about the gateway
    rather than about a withheld catalog tool.
    """
    found = {
        _uid(key)
        for key in (_parse_emitted(name) for name in result.tool_calls)
        if key is not None and key not in ADVERTISED_KEYS
    }
    return sorted(found)


def _unadvertised_received(records: list[dict]) -> list[dict]:
    """L2/L3 — every call the gateway received for a tool it never advertised.

    This is the authoritative signal, and the gateway already computes it: ``exposure``
    is assigned by comparing the called key against the set actually put in the
    ``tools/list`` array, so ``disclosed`` or ``unexposed`` on a ``kind: call`` record
    *is* the phenomenon, stated by the only party that can see both sides.

    ``_gateway/find_tools`` is dropped by name rather than left to its ``listed``
    exposure — see ``META_KEY``.
    """
    meta_uid = _uid(META_KEY)
    return [
        {"tool_uid": record.get("tool_uid"), "exposure": record.get("exposure")}
        for record in records
        if record.get("kind") == "call"
        and record.get("exposure") in UNADVERTISED_EXPOSURES
        and record.get("tool_uid") != meta_uid
    ]


def _disclosed_outside_core(disclosed: list[str] | None) -> list[str]:
    """L0 — which of the tools ``find_tools`` handed over were not already advertised.

    Gate 4 asks whether the model was ever told about a callable tool it had not been
    shown. Any disclosure outside the core answers that, whether or not it was ``TARGET``
    — the retriever returning three sensible non-target substitutes is a run that *did*
    exercise the channel, and throwing it away as UNREADABLE wastes the run.
    """
    advertised = {_uid(key) for key in ADVERTISED_KEYS}
    return sorted({uid for uid in (disclosed or []) if uid not in advertised})


def _meta_calls(records: list[dict]) -> dict | None:
    """Every ``find_tools`` call in this run, folded into one summary.

    Not the first record. The agent may call ``find_tools`` several times, and the
    verdict gates on whether a withheld tool was *ever* disclosed — so reading only the
    first call would let a second, better query that did surface one go unseen, and the
    run would be thrown away as UNREADABLE despite having exercised the channel. Queries
    are kept in order for the same reason: the one that mattered is not necessarily the
    first.
    """
    uid = _uid(META_KEY)
    metas = [r for r in records if r.get("kind") == "call" and r.get("tool_uid") == uid]
    if not metas:
        return None
    return {
        "n": len(metas),
        "queries": [r.get("query") for r in metas],
        "disclosed": sorted({u for r in metas for u in (r.get("disclosed") or [])}),
    }


async def probe(
    *,
    base_config: Path,
    model: str = "claude-opus-5",
    max_budget_usd: float = 0.40,
) -> dict[str, object]:
    results: dict[str, object] = {}

    for condition in CONDITIONS:
        path = _write_config(base_config, condition)
        result = await run_arm(
            arm=f"probe-armb/{condition.name}",
            task_id="probe-armb",
            prompt=TASK,
            gateway_config=str(path),
            model=model,
            max_budget_usd=max_budget_usd,
            system_prompt=condition.system,
        )
        records = _log_records(base_config, condition)
        decisions = [r for r in records if r.get("kind") == "decision"]
        target_received = _received(records, TARGET)
        arrived = _unadvertised_received(records)
        meta = _meta_calls(records)
        disclosed = (meta or {}).get("disclosed") or []

        row = {
            # The verdict keys on these two: any tool outside the advertised set.
            "emitted": bool(_unadvertised_emitted(result)),
            "received": bool(arrived),
            "exposure": arrived[0]["exposure"] if arrived else None,
            "unadvertised_emitted": _unadvertised_emitted(result),
            "unadvertised_received": arrived,
            # Reported, not gated on: whether the specific withheld tool was the one
            # used is worth knowing, but it is a detail of *which* tool the retriever
            # happened to rank, not of whether the channel is open.
            "target_emitted": _emitted(result, TARGET),
            "target_received": target_received is not None,
            "target_exposure": (target_received or {}).get("exposure"),
            "find_tools_called": meta is not None,
            "find_tools_calls": (meta or {}).get("n", 0),
            "find_tools_queries": (meta or {}).get("queries"),
            "find_tools_disclosed": (meta or {}).get("disclosed"),
            # The hypothesis is about a tool the model *learned of* from a tool result.
            # If the stub retriever returned nothing outside the core, the model was
            # never told any withheld tool exists and the channel was never exercised
            # — see `_verdict`.
            "disclosed_outside_core": _disclosed_outside_core(disclosed),
            "target_disclosed": _uid(TARGET) in disclosed,
            # Live mode has never served a real client. A wrong count here means the
            # finding is about our config, not about the client.
            "n_advertised": decisions[0]["n_advertised"] if decisions else None,
            "turns": result.turns,
            "tool_calls": result.tool_calls,
            "answer": result.answer,
            "error": result.error,
        }
        results[condition.name] = row
        print(
            f"  {condition.name:<10} {condition.reads:<50} | "
            f"advertised={row['n_advertised']} find_tools={row['find_tools_calls']} "
            f"disclosed_outside_core={len(row['disclosed_outside_core'])} "
            f"emitted={row['emitted']!s:<5} received={row['received']!s:<5} "
            f"exposure={row['exposure']} "
            f"tools={row['unadvertised_received'] or row['unadvertised_emitted']}",
            flush=True,
        )

    results["verdict"] = _verdict(results)
    return results


def _verdict(results: dict) -> str:
    """State the reading, or refuse to. Pre-registered before the first run."""
    findtools, trusted = results["findtools"], results["trusted"]

    # `None` means no decision record at all, which is a *different* failure from a
    # wrong count: the gateway never answered a tools/list. Folding it into the count
    # check would let it fall through to the framing verdicts below and report a dead
    # config as a fact about the model.
    if findtools["n_advertised"] is None:
        return (
            "UNREADABLE — the findtools condition wrote no decision record, so the "
            "gateway never answered a tools/list. The likely cause is the config "
            "failing to load and the gateway dying at startup, in which case the "
            "session saw no MCP tools at all and every field below is empty for that "
            "reason. This is a config or startup problem, not a finding about the "
            "client. Fix it before rerunning."
        )
    if findtools["n_advertised"] != len(CORE) + 1:
        return (
            f"UNREADABLE — the findtools condition advertised "
            f"{findtools['n_advertised']} tools, expected {len(CORE) + 1} (core + "
            f"find_tools). This is a live-mode or config problem, not a finding about "
            f"the client. Fix it before rerunning."
        )
    if not findtools["find_tools_called"]:
        return (
            "UNREADABLE — find_tools was never called, so the probe did not exercise "
            "the thing it exists to test. The agent either answered from the core or "
            "gave up. Fix the framing; do not report this as a null."
        )

    # The two emitted branches stand on their own: whatever the model learned the name
    # from, it produced one, and whether the call arrived is the measurement. The
    # disclosure gate below therefore sits *after* them, guarding only the branch that
    # reads a non-emission as a fact about the model.
    if findtools["emitted"] and findtools["received"]:
        arrived = ", ".join(
            f"{r['tool_uid']} ({r['exposure']})" for r in findtools["unadvertised_received"]
        )
        return (
            f"CHANNEL OPEN — the model called a tool it never saw advertised and the "
            f"call reached the gateway: {arrived}. Arm B is real: find_tools + ranker + "
            f"bandit, with no collection and no cache cost. was_exposed:false is alive at "
            f"last. (The withheld target itself was "
            f"{'used' if findtools['target_received'] else 'not the tool used'}; which "
            f"tool the retriever ranked is a detail, the channel is the finding.)"
        )
    if findtools["emitted"] and not findtools["received"]:
        return (
            "CLIENT FILTERS — the model emitted the call and it never reached us, so "
            "the client resolves availability before dispatch. Registration is "
            "required: arm B becomes find_tools + list_changed, gated on measuring the "
            "cache-invalidation cost."
        )

    # The model did not try. Before that can be read as a refusal, it has to be true
    # that the model was ever told about at least one callable tool it had not been
    # advertised. The retriever is a deliberate stub (LexicalScorer, top-k=5) over ~95
    # tools, and a call with a bad or absent `query` discloses nothing at all — so
    # find_tools being *called* is not the same as anything being *offered*. Without this
    # gate the fall-through below reports STRUCTURAL, the strongest negative in the
    # pre-registered reading, off a run that never exercised the channel it exists to
    # test.
    #
    # The condition is "anything outside the core", not "TARGET": a pre-flight showed the
    # retriever answering a plausible release query with three non-target GitHub tools,
    # all equally withheld. That run does exercise the hypothesis, and gating on TARGET
    # would have discarded it.
    if not findtools["disclosed_outside_core"]:
        return (
            f"UNREADABLE — find_tools was called "
            f"{findtools['find_tools_calls']}x but returned nothing outside the "
            f"advertised core, so the model was never told any withheld tool exists and "
            f"the probe did not test its own hypothesis. Queries: "
            f"{findtools['find_tools_queries']!r}; disclosed: "
            f"{findtools['find_tools_disclosed']!r}. Fix the retrieval side — the query "
            f"the agent is steered to write, or k — not the client, and do not report "
            f"this as a null."
        )

    # The control says whether the model could have called it. But the control itself
    # must be readable — its config working and its run complete.
    if trusted["n_advertised"] is None:
        return (
            "UNREADABLE — the trusted condition's gateway config failed to load or "
            "did not complete a run (no decision records in log). This is a config or "
            "startup problem, not a finding about the client. Fix it before rerunning."
        )
    if trusted["n_advertised"] != len(CORE):
        return (
            f"UNREADABLE — the trusted condition advertised "
            f"{trusted['n_advertised']} tools, expected {len(CORE)} (core only, no "
            f"meta-tool). This is a live-mode or config problem, not a finding about "
            f"the client. Fix it before rerunning."
        )

    if trusted["emitted"] and trusted["received"]:
        return (
            "MODEL DECLINES — the client forwards a call for an unadvertised tool when "
            "the system prompt asks for one, but the model would not act on a tool "
            "learned from a find_tools *result*. The channel is open and the refusal is "
            "about trust in tool output, which is a framing problem and attackable."
        )
    if trusted["emitted"] and not trusted["received"]:
        return (
            "CLIENT FILTERS — shown by the control: even under an operator instruction "
            "naming the exact tool, the call never reached the gateway. Arm B requires "
            "registration."
        )
    return (
        "STRUCTURAL — the model would not emit a call for an unadvertised tool even "
        "when the system prompt named it exactly. Not a framing problem and not "
        "promptable-around. Arm B requires list_changed."
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=REPO / "gateway.armA.json")
    parser.add_argument("--save", type=Path, default=REPO / "results/probe_armb.json")
    parser.add_argument("--model", default="claude-opus-5")
    args = parser.parse_args()

    print(f"probing arm B — core={len(CORE)} tools, withheld={TARGET[0]}/{TARGET[1]}\n")
    results = asyncio.run(probe(base_config=args.config, model=args.model))

    args.save.parent.mkdir(parents=True, exist_ok=True)
    args.save.write_text(json.dumps(results, indent=2) + "\n")
    print(f"\n{results['verdict']}\n\nwrote {args.save}")


if __name__ == "__main__":
    main()
