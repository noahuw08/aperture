"""Run one task under one arm and report what it cost and whether it worked.

Everything an arm differs by is config: which gateway config the proxy loads (and
therefore which tools reach the client), and the model. Anything else that differs
between arms is a confound.

**Token cost is measured on both sides, never computed for ours and estimated for
theirs.** `ResultMessage.usage` carries the real per-run accounting, with cache
creation and cache reads separated — which is also where the prompt-cache invalidation
number comes from.
"""

from __future__ import annotations

import collections
import os
from dataclasses import dataclass, field
from typing import Any

GATEWAY_SHIM = "/Users/nguyenvietkhoi/mcp-gateway-router/bin/mcp-gateway"

# The tool the client uses to search deferred MCP schemas. It arrives as an ordinary
# client ToolUseBlock, *not* a ServerToolUseBlock — the SDK's server-tool enum lists
# `tool_search_tool_regex` / `_bm25`, which is a different (API-side) mechanism.
TOOL_SEARCH = "ToolSearch"


def _as_text(content: Any) -> str:
    """Flatten a tool result to searchable text.

    The SDK returns tool-result content as a string, a list of blocks, or ``None``
    depending on the tool. Callers only ever scan this for tool names, so a lossy
    flatten is the right trade — and it keeps the probe working if the shape of a
    ``ToolSearch`` result changes upstream.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        return "\n".join(_as_text(item) for item in content)
    for attr in ("text", "content"):
        value = getattr(content, attr, None)
        if value is not None and value is not content:
            return _as_text(value)
    return str(content)


@dataclass
class ArmResult:
    arm: str
    task_id: str
    ok: bool
    answer: str | None
    turns: int
    cost_usd: float
    input_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    output_tokens: int
    tool_search_calls: int
    tool_calls: dict[str, int] = field(default_factory=dict)
    #: Raw text of each ``ToolSearch`` result, in order.
    #:
    #: Counting searches says *how often* the client retrieved; this says *what came
    #: back*. That distinction is the difference between "our text changed what the
    #: ranker surfaced" and "our text changed what the model picked from what it was
    #: already shown" — two different levers, only one of which can improve recall.
    search_results: list[str] = field(default_factory=list)
    error: str | None = None
    #: Set by the matrix once the task's assertion has been applied. `ok` means the
    #: run completed; `passed` means it produced the right answer. A run can be `ok`
    #: and wrong, which is exactly the case worth counting.
    passed: bool = False

    @property
    def prefix_tokens(self) -> int:
        """What the prompt prefix actually cost this run.

        Creation plus reads: a cached read still had to be written once, and Q1's
        matched-cost comparison is about total prefix weight, not marginal billing.
        """
        return self.cache_creation_tokens + self.cache_read_tokens


async def run_arm(
    *,
    arm: str,
    task_id: str,
    prompt: str,
    gateway_config: str | None = None,
    model: str = "claude-opus-5",
    max_turns: int = 12,
    max_budget_usd: float = 1.0,
    system_prompt: str = "Use the available MCP tools. Answer concisely.",
    cwd: str | None = None,
) -> ArmResult:
    """Run one task through the Agent SDK with the gateway attached.

    Auth note: with `ANTHROPIC_API_KEY` unset the SDK authenticates through the Claude
    Code CLI's own credential, so this runs on a subscription rather than API credit.
    `total_cost_usd` is still reported — as the equivalent API cost, which is what the
    cost model needs.
    """
    from claude_agent_sdk import ClaudeAgentOptions, query

    # The prompt is handed to the gateway explicitly. At tools/list the MCP protocol
    # supplies none, so an arm only reaches decision point C because the harness put it
    # there — which is what makes A-vs-C a comparison rather than a category error.
    env: dict[str, str] = {"MCP_GATEWAY_TASK": prompt}
    if gateway_config:
        env["MCP_GATEWAY_CONFIG"] = gateway_config

    options = ClaudeAgentOptions(
        model=model,
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        # Ignore the developer's own settings and MCP registrations — an arm must be
        # defined entirely by this call, or the measurement is not reproducible.
        setting_sources=[],
        strict_mcp_config=True,
        permission_mode="bypassPermissions",
        mcp_servers={"gw": {"type": "stdio", "command": GATEWAY_SHIM, "args": [], "env": env}},
        system_prompt=system_prompt,
        cwd=cwd or os.getcwd(),
    )

    tool_calls: collections.Counter[str] = collections.Counter()
    # Tool results arrive in a later message than the call, so the ids have to be
    # carried forward to tell a ToolSearch result from any other tool's result.
    search_ids: set[str] = set()
    search_results: list[str] = []
    result = None
    async for message in query(prompt=prompt, options=options):
        for block in getattr(message, "content", None) or []:
            kind = type(block).__name__
            if kind == "ToolUseBlock":
                name = getattr(block, "name", "?")
                tool_calls[name] += 1
                if name == TOOL_SEARCH:
                    block_id = getattr(block, "id", None)
                    if block_id is not None:
                        search_ids.add(block_id)
            elif kind == "ToolResultBlock":
                if getattr(block, "tool_use_id", None) in search_ids:
                    search_results.append(_as_text(getattr(block, "content", None)))
        if type(message).__name__ == "ResultMessage":
            result = message

    if result is None:
        return ArmResult(
            arm=arm, task_id=task_id, ok=False, answer=None, turns=0, cost_usd=0.0,
            input_tokens=0, cache_creation_tokens=0, cache_read_tokens=0,
            output_tokens=0, tool_search_calls=0, search_results=search_results,
            error="no ResultMessage",
        )

    usage: dict[str, Any] = result.usage or {}
    searches = tool_calls.pop(TOOL_SEARCH, 0)

    return ArmResult(
        arm=arm,
        task_id=task_id,
        ok=not result.is_error,
        answer=result.result,
        turns=result.num_turns,
        cost_usd=result.total_cost_usd or 0.0,
        input_tokens=usage.get("input_tokens", 0),
        cache_creation_tokens=usage.get("cache_creation_input_tokens", 0),
        cache_read_tokens=usage.get("cache_read_input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        tool_search_calls=searches,
        tool_calls=dict(tool_calls),
        search_results=search_results,
    )
