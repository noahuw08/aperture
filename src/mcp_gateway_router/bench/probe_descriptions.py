"""Does the client's tool search read our descriptions, or only our names?

Everything the "ToolSearch + personalization" arm proposes rests on one unverified
assumption: that the text we put in a tool's ``description`` reaches the ranker the
client uses to answer a ``ToolSearch`` call. If it does, the gateway can steer the
incumbent retriever with signals it cannot otherwise have. If it does not, the only
lever left is renaming tools, and the arm has no mechanism.

**Why this is not already known.** Deferral puts the tool *names* in the prompt prefix
and holds the schemas back — measured at 3,858 chars for 95 tools, which is 40.6 chars
each, almost exactly one namespaced name. Descriptions are therefore not in the prefix.
They are still *declared* in the request, so the ranker can index them, but that is an
inference about a client we do not control, and the arm is too expensive to build on an
inference.

**The design is a manipulation, not an observation.** Asking "can the agent find tool X"
tells you little, because the name alone may carry it. Instead each condition changes
the descriptions and watches whether the *choice of tool* moves:

``control``  nothing overridden. Establishes that the task reaches the target at all —
             and that the retriever actually ran. If it didn't, the run stops here
             rather than paying for two more conditions that cannot be read.
``blind``    the target's description is replaced with unrelated text. Its name is
             untouched, so a hit here means names alone are sufficient.
``steer``    the target stays blinded *and* an unrelated decoy is described as the tool
             the task wants. This is the decisive one: it pits a well-described wrong
             tool against a badly-described right one.

**Retrieval has two stages, and they are different levers.** The ranker surfaces a
candidate set; the model then picks from what it was shown. A description could act at
either. So the probe captures the ``ToolSearch`` *result*, not just the call — if the
decoy appears in the candidate set, our text reached the index; if it was only *called*
without ever being surfaced, our text merely persuaded the model after the fact.

That distinction decides what the arm can do:

* **ranker-level** — we can improve *recall*, putting tools in front of the agent that
  the query alone would not have matched. This is the lever the sequence and
  co-occurrence blind spots need.
* **model-level only** — we can improve *precision among what was already retrieved*.
  Real, but it cannot surface a tool the ranker didn't return, which is most of the
  attack surface.

Reading the result:

* decoy **surfaced** in ``steer``     -> ranker-level. Build it.
* decoy called but never surfaced     -> model-level only. Weaker lever, narrower arm.
* target surfaced in ``control`` but not in ``blind`` -> ranker-level, shown by
  suppression rather than promotion.
* none of the above                   -> names dominate; the lever is dead.

Run (subscription auth; ``ANTHROPIC_API_KEY`` must be UNSET)::

    cd /tmp && env -u ANTHROPIC_API_KEY uv run --directory <repo> \\
        --with claude-agent-sdk --extra gateway \\
        python -m mcp_gateway_router.bench.probe_descriptions

Three runs, ~$0.07 each.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from .runner import run_arm

REPO = Path(__file__).resolve().parents[3]

#: Must be unanswerable without a tool. The first version of this probe asked which
#: login handle the session was signed in with; Claude Code injects the account
#: identity, so the agent answered from context in one turn with zero tool calls and
#: zero searches — the retriever was never exercised and all three conditions were
#: indistinguishable. A probe task has exactly one requirement: it must *force* a call.
TASK = (
    "List the tag names of the 3 most recent releases in the "
    "modelcontextprotocol/servers repository."
)

#: Remote state, unambiguous, and not derivable from the local environment.
TARGET = "github/list_releases"
#: Same argument shape as the target — ``(owner, repo)`` — so a boosted description
#: yields a call the agent can actually construct, and semantically distant enough
#: that making it is unambiguous evidence the rewrite, not residual relevance, moved
#: the pick.
DECOY = "github/list_repository_collaborators"

#: Answers are never checked — the probe measures *which tool was reached for*, not
#: whether the reply was right. That is why a task whose answer drifts is fine here
#: and would not be in the battery.
SYSTEM = (
    "Answer using the available MCP tools. Do not use Bash, WebFetch, or your own "
    "knowledge to answer — if no MCP tool can do it, say so."
)

_BLIND = (
    "Returns the current server time as an ISO-8601 timestamp. Takes no arguments and "
    "reports nothing about repositories, releases, tags, or versions."
)
_DECOY_BOOST = (
    "Lists the releases published in a GitHub repository, including each release's tag "
    "name, title, and publication date. Use this to find recent releases or versions."
)


@dataclass(frozen=True)
class Condition:
    name: str
    overrides: dict[str, str]
    reads: str


CONDITIONS = (
    Condition("control", {}, "does the task reach the target at all?"),
    Condition("blind", {TARGET: _BLIND}, "is the name alone sufficient?"),
    Condition(
        "steer",
        {TARGET: _BLIND, DECOY: _DECOY_BOOST},
        "can description text move the pick? (decisive)",
    ),
)


def _write_config(base: Path, condition: Condition) -> Path:
    """One gateway config per condition, identical except for the overrides.

    Shadow mode throughout: every tool stays advertised, so the only thing that varies
    is the text. A cut here would confound the manipulation with availability.

    **Written beside the base config, not into an output directory.** ``from_file``
    resolves ``.env``, ``log_dir`` and ``catalog_path`` relative to the config's own
    parent, and ``_expand`` *raises* on an unresolvable ``${VAR}`` — so a config written
    anywhere else fails to load, the gateway dies at startup, and the session sees no
    MCP tools at all. That failure is silent from the harness's side: three conditions
    come back identical and look like a null result about descriptions.
    """
    out = base.parent / f"gateway.probe-{condition.name}.json"
    config = json.loads(base.read_text())
    config.update(
        {
            "mode": "shadow",
            "arm": f"probe/{condition.name}",
            "log_dir": f"runs/probe/{condition.name}",
            "description_overrides": condition.overrides,
        }
    )
    out.write_text(json.dumps(config, indent=2) + "\n")
    return out


def _called(result, needle: str) -> bool:
    """Advertised names are namespaced by the client, so match on the suffix."""
    tail = needle.replace("/", "__")
    return any(tail in name for name in result.tool_calls)


def _surfaced(result, needle: str) -> bool:
    """Did the tool appear in a ``ToolSearch`` result — i.e. did the *ranker* return it?

    Scanned as text rather than parsed: the result shape is the client's, not ours, and
    a substring match on the tool name is both sufficient here and robust to it changing.
    """
    tail = needle.replace("/", "__")
    return any(tail in blob or needle in blob for blob in result.search_results)


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
            arm=f"probe/{condition.name}",
            task_id="probe-descriptions",
            prompt=TASK,
            gateway_config=str(path),
            model=model,
            max_budget_usd=max_budget_usd,
            system_prompt=SYSTEM,
        )
        row = {
            "target_called": _called(result, TARGET),
            "decoy_called": _called(result, DECOY),
            "target_surfaced": _surfaced(result, TARGET),
            "decoy_surfaced": _surfaced(result, DECOY),
            "searched": result.tool_search_calls > 0,
            "searches": result.tool_search_calls,
            "turns": result.turns,
            "tool_calls": result.tool_calls,
            "search_results": result.search_results,
            "answer": result.answer,
            "error": result.error,
        }
        results[condition.name] = row
        print(
            f"  {condition.name:<8} "
            f"target: surfaced={row['target_surfaced']!s:<5} called={row['target_called']!s:<5} | "
            f"decoy: surfaced={row['decoy_surfaced']!s:<5} called={row['decoy_called']!s:<5} | "
            f"searches={row['searches']}",
            flush=True,
        )

        # Bail after the control rather than paying for two more unreadable runs. A
        # control that reaches the target without searching is just as useless as one
        # that misses it: both mean the retriever never ran, so nothing was tested.
        if condition.name == "control" and not (
            row["target_called"] and row["searched"]
        ):
            print(
                "\n  control did not exercise the retriever — stopping before the "
                "remaining conditions.",
                flush=True,
            )
            for skipped in CONDITIONS[1:]:
                results[skipped.name] = {
                    "target_called": False,
                    "decoy_called": False,
                    "target_surfaced": False,
                    "decoy_surfaced": False,
                    "searched": False,
                    "skipped": True,
                }
            break

    results["verdict"] = _verdict(results)
    return results


def _verdict(results: dict) -> str:
    """State the reading, or refuse to.

    Two ways a run is unreadable rather than negative, and both are checked before any
    conclusion: a control that never reached the target (nothing to attribute a miss
    to), and a run where the retriever was never exercised at all (nothing was tested).
    """
    control, blind, steer = results["control"], results["blind"], results["steer"]

    if not control["target_called"]:
        return (
            "UNREADABLE — the control condition never reached the target, so a miss "
            "under manipulation cannot be attributed. Fix the task before rerunning."
        )
    if not any(c.get("searched") for c in (control, blind, steer)):
        return (
            "UNREADABLE — no ToolSearch call in any condition, so the retriever was "
            "never exercised. The catalog may be small enough to load eagerly; retry "
            "with the full 95-tool config."
        )

    if steer["decoy_surfaced"]:
        return (
            "RANKER-LEVEL — the rewritten decoy was returned by ToolSearch itself. Our "
            "descriptions reach the index, so the arm can improve recall: it can put "
            "tools in front of the agent that the query alone would not match. This is "
            "the mechanism sequence and co-occurrence need. Build it."
        )
    # Model-level effects show up in two directions, and the first version of this
    # only checked one. Promotion: the decoy takes the call. Suppression: the target
    # is surfaced but declined. Either proves the text reached the model and not the
    # index; missing the second reported a real effect as "names dominate".
    model_promoted = steer["decoy_called"] and not steer["decoy_surfaced"]
    model_suppressed = steer["target_surfaced"] and not steer["target_called"]
    if model_promoted or model_suppressed:
        how = "promoted the decoy" if model_promoted else "suppressed the target"
        return (
            f"MODEL-LEVEL ONLY — our text {how} after retrieval, but never changed the "
            "candidate set: the ranker matched on names regardless of description. "
            "That makes the lever *suppressive* — it can steer the agent away from a "
            "tool (failure memory, entitlement) but cannot surface one the ranker "
            "didn't return (sequence, co-occurrence, identity). Half the attack "
            "surface is out of reach. Scope the arm down before building."
        )
    if control["target_surfaced"] and not blind["target_surfaced"]:
        return (
            "RANKER-LEVEL (suppression only) — blinding the target removed it from the "
            "candidate set, so descriptions are indexed, but the boosted decoy did not "
            "get promoted. The lever demonstrably works downward; promotion is unproven "
            "and is the direction the arm actually needs."
        )
    return (
        "NAMES DOMINATE — the target kept the call with an unrelated description while "
        "a boosted decoy was present, and the decoy was never surfaced. The description "
        "lever is dead on this client; the personalization arm needs a different "
        "mechanism."
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=REPO / "gateway.armA.json")
    parser.add_argument("--save", type=Path, default=REPO / "results/probe_descriptions.json")
    parser.add_argument("--model", default="claude-opus-5")
    args = parser.parse_args()

    print(f"probing description indexing — target={TARGET} decoy={DECOY}\n")
    results = asyncio.run(probe(base_config=args.config, model=args.model))

    args.save.parent.mkdir(parents=True, exist_ok=True)
    args.save.write_text(json.dumps(results, indent=2) + "\n")
    print(f"\n{results['verdict']}\n\nwrote {args.save}")


if __name__ == "__main__":
    main()
