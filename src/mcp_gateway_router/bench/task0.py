"""Task 0 — the gate on all of Q1.

Q1's design assumes four things that were assumptions, not facts. This script turns each
into a measurement. Run it before building anything else in `bench/`.

    uv run --with claude-agent-sdk python -m mcp_gateway_router.bench.task0

Deliberately runs with `ANTHROPIC_API_KEY` unset: part of what it checks is whether the
harness can authenticate without one.
"""

from __future__ import annotations

import asyncio
import os

from .runner import run_arm

PROBE = (
    "Using the github MCP tools, search GitHub code for \"modelcontextprotocol\" "
    "and report only the total_count number."
)


async def main() -> None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        print("⚠️  ANTHROPIC_API_KEY is set — this cannot verify subscription auth.")
        print("    Re-run as: env -u ANTHROPIC_API_KEY uv run ...\n")

    print("Task 0 — verifying Q1 is buildable\n")
    result = await run_arm(
        arm="A-full-catalog",
        task_id="probe",
        prompt=PROBE,
        max_turns=8,
        max_budget_usd=0.60,
        system_prompt="Use the available MCP tools. Answer with just the number.",
    )

    checks = [
        ("0a  authenticates without an API key", not result.error),
        ("0b  gateway tools reachable through the SDK",
         any(k.startswith("mcp__gw__") for k in result.tool_calls)),
        ("0c  per-run token usage is observable", result.prefix_tokens > 0),
        ("0d  tool search is observable", result.tool_search_calls > 0),
        ("--  the agent actually completed the task", result.ok and bool(result.answer)),
    ]
    for label, passed in checks:
        print(f"  {'PASS' if passed else 'FAIL'}  {label}")

    print(f"\n  turns              {result.turns}")
    print(f"  cost               ${result.cost_usd:.4f}")
    print(f"  cache created      {result.cache_creation_tokens:,}")
    print(f"  cache read         {result.cache_read_tokens:,}")
    print(f"  prefix total       {result.prefix_tokens:,}")
    print(f"  output             {result.output_tokens:,}")
    print(f"  ToolSearch calls   {result.tool_search_calls}")
    print(f"  tool calls         {result.tool_calls}")
    print(f"  answer             {result.answer!r}")

    if all(passed for _, passed in checks):
        print("\n✅ Task 0 passes — Q1 is buildable. Arm A is real and measurable.")
    else:
        print("\n❌ Task 0 fails — do not build on this until resolved.")


if __name__ == "__main__":
    asyncio.run(main())
