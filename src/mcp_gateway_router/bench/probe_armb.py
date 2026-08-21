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

from ..gateway.metatools import FIND_TOOLS
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

BASE_SYSTEM = (
    "Answer using the available MCP tools. Do not use Bash, WebFetch, or your own "
    "knowledge to answer — if no MCP tool can do it, say so."
)

TARGET_ADVERTISED = f"{TARGET[0]}__{TARGET[1]}"

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


def _write_config(base: Path, condition: Condition) -> Path:
    """One gateway config per condition, beside the base config.

    ⚠️ **Never into an output directory.** ``from_file`` resolves ``.env``, ``log_dir``
    and ``catalog_path`` relative to the config's own parent, and ``_expand`` *raises*
    on an unresolvable ``${VAR}`` — so a config written elsewhere fails to load, the
    gateway dies at startup, and the session sees no MCP tools at all. That failure is
    silent from here: both conditions come back empty and look like a clean negative.
    """
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
    """
    log_dir = base.parent / "runs" / "probe-armb" / condition.name
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
    """L1 — did the model emit a call for this tool? Names are client-namespaced."""
    tail = f"{key[0]}__{key[1]}"
    return any(tail in name for name in result.tool_calls)


def _received(records: list[dict], key: tuple[str, str]) -> dict | None:
    """L2/L3 — did the gateway receive it, and as what?"""
    uid = f"{key[0]}/{key[1]}"
    for record in records:
        if record.get("kind") == "call" and record.get("tool_uid") == uid:
            return record
    return None


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
        received = _received(records, TARGET)
        meta = _received(records, ("_gateway", FIND_TOOLS))

        row = {
            "emitted": _emitted(result, TARGET),
            "received": received is not None,
            "exposure": (received or {}).get("exposure"),
            "find_tools_called": meta is not None,
            "find_tools_query": (meta or {}).get("query"),
            "find_tools_disclosed": (meta or {}).get("disclosed"),
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
            f"  {condition.name:<10} "
            f"advertised={row['n_advertised']} "
            f"find_tools={row['find_tools_called']!s:<5} | "
            f"target: emitted={row['emitted']!s:<5} received={row['received']!s:<5} "
            f"exposure={row['exposure']}",
            flush=True,
        )

    results["verdict"] = _verdict(results)
    return results


def _verdict(results: dict) -> str:
    """State the reading, or refuse to. Pre-registered before the first run."""
    findtools, trusted = results["findtools"], results["trusted"]

    if findtools["n_advertised"] not in (len(CORE) + 1, None):
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

    if findtools["emitted"] and findtools["received"]:
        return (
            f"CHANNEL OPEN — the model called a tool it never saw advertised and the "
            f"call reached the gateway, logged as {findtools['exposure']!r}. Arm B is "
            f"real: find_tools + ranker + bandit, with no collection and no cache cost. "
            f"was_exposed:false is alive at last, as `disclosed`."
        )
    if findtools["emitted"] and not findtools["received"]:
        return (
            "CLIENT FILTERS — the model emitted the call and it never reached us, so "
            "the client resolves availability before dispatch. Registration is "
            "required: arm B becomes find_tools + list_changed, gated on measuring the "
            "cache-invalidation cost."
        )

    # The model did not try. The control says whether it could have.
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
