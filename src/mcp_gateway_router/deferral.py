"""What tool-search deferral actually costs in the prompt prefix.

    uv run python -m mcp_gateway_router.deferral --catalog results/catalog.json

The project was founded on schemas being a recurring per-call tax, so cutting the
catalog would save real tokens. Client-side tool search breaks that: a deferred tool
contributes only its *name* to the prefix, and its schema is fetched on demand. This
module measures the gap.

The headline is a **ratio** — full-definition size over name-only size — which is why
it survives having no tokenizer. ``count_tokens`` is the only trustworthy absolute
(see ``tokens.py``: tiktoken undercounts Claude on JSON schemas), and it needs a funded
account. A chars-per-token constant cancels out of a ratio, so the ratio is reportable
now and the absolutes are estimates flagged as such.

What this does *not* measure: the fetch. Searching a tool loads its full schema, so a
catalog that makes the agent search more converts directly into tokens. That is a
selection-quality question and belongs to the Q1 benchmark, not here.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .catalog import Tool
from .gateway.naming import advertised_name

# The client namespaces every MCP tool under its server's registered name, and it's
# that full string — not the bare tool name — that lands in the deferred listing.
CLIENT_PREFIX = "mcp__gateway__"

# Bracketing estimate only. English prose runs ~4.0; dense JSON with punctuation and
# camelCase identifiers tokenizes worse, so the true figure sits at or below the low
# end. Used for the absolutes, never for the ratio.
CHARS_PER_TOKEN = (3.5, 4.0)

# Empirical anchor for the deferred side, from the 2026-08-10 probe in
# gateway-setup.md: dropping 91 tools moved the prefix by 405 tokens. Names beat the
# prose ratio by ~2x because every one of them opens with the same
# ``mcp__gateway__<server>__`` run, which the tokenizer reuses. Applying CHARS_PER_TOKEN
# to names overstates them, so the deferred absolutes use this instead.
TOKENS_PER_DEFERRED_NAME = 405 / 91


@dataclass(frozen=True)
class ServerCost:
    server_id: str
    n_tools: int
    loaded_chars: int
    deferred_chars: int

    @property
    def ratio(self) -> float:
        return self.loaded_chars / self.deferred_chars


def loaded_chars(tool: Tool) -> int:
    """Prefix cost of a tool the client has actually loaded: the whole definition."""
    body = json.dumps(tool.as_api_tool()["input_schema"], separators=(",", ":"))
    return len(tool.name) + len(tool.description) + len(body)


def deferred_chars(tool: Tool) -> int:
    """Prefix cost of a tool held behind tool search: the namespaced name alone."""
    return len(f"{CLIENT_PREFIX}{advertised_name(tool)}")


def by_server(tools: Iterable[Tool]) -> list[ServerCost]:
    """Per-server costs, heaviest first. Cost concentrates by server, not by count."""
    grouped: dict[str, list[Tool]] = {}
    for tool in tools:
        grouped.setdefault(tool.server_id, []).append(tool)

    costs = [
        ServerCost(
            server_id=server_id,
            n_tools=len(group),
            loaded_chars=sum(loaded_chars(t) for t in group),
            deferred_chars=sum(deferred_chars(t) for t in group),
        )
        for server_id, group in grouped.items()
    ]
    return sorted(costs, key=lambda c: -c.loaded_chars)


def cut_saving(tools: Iterable[Tool], keep: int) -> dict[str, int]:
    """Chars saved by cutting the catalog to ``keep`` tools, under both regimes.

    The comparison the roadmap assumed was ``loaded``. Under deferral the live-mode cut
    only shortens the name list, and ``deferred`` is what it actually buys.
    """
    tools = list(tools)
    dropped = tools[keep:]
    return {
        "n_dropped": len(dropped),
        "loaded": sum(loaded_chars(t) for t in dropped),
        "deferred": sum(deferred_chars(t) for t in dropped),
    }


def load_catalog(path: Path) -> list[Tool]:
    payload = json.loads(path.read_text())
    return [
        Tool(
            server_id=t["server_id"],
            name=t["name"],
            description=t.get("description") or "",
            input_schema=t.get("input_schema") or {},
            is_write=t.get("is_write", False),
        )
        for t in payload["tools"]
    ]


def _tokens(chars: int) -> str:
    lo, hi = (chars / c for c in sorted(CHARS_PER_TOKEN, reverse=True))
    return f"{lo:,.0f}-{hi:,.0f}"


def _name_tokens(n_tools: int) -> str:
    return f"~{n_tools * TOKENS_PER_DEFERRED_NAME:,.0f}"


def render(tools: list[Tool], keep: int) -> str:
    costs = by_server(tools)
    total_loaded = sum(c.loaded_chars for c in costs)
    total_deferred = sum(c.deferred_chars for c in costs)

    lines = [
        f"{'server':<12}{'n':>5}{'loaded ch':>12}{'deferred ch':>13}{'ratio':>9}",
        "-" * 51,
    ]
    for c in costs:
        lines.append(
            f"{c.server_id:<12}{c.n_tools:>5}{c.loaded_chars:>12,}"
            f"{c.deferred_chars:>13,}{c.ratio:>8.0f}x"
        )
    lines += [
        "-" * 51,
        f"{'TOTAL':<12}{len(tools):>5}{total_loaded:>12,}{total_deferred:>13,}"
        f"{total_loaded / total_deferred:>8.0f}x",
        "",
        f"prefix cost, all loaded    ~{_tokens(total_loaded)} tokens (chars/token est.)",
        f"prefix cost, all deferred  {_name_tokens(len(tools))} tokens (probe-anchored)",
        "",
    ]

    saving = cut_saving(tools, keep)
    lines += [
        f"cutting {len(tools)} -> {keep} drops {saving['n_dropped']} tools and saves:",
        f"  if they were loaded    ~{_tokens(saving['loaded'])} tokens",
        f"  as deferred names      {_name_tokens(saving['n_dropped'])} tokens",
        "",
        "The ratio is exact. Loaded absolutes are chars/token estimates; deferred",
        "absolutes use the measured per-name cost. Re-run with count_tokens once",
        "the account is funded.",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", type=Path, default=Path("results/catalog.json"))
    parser.add_argument(
        "--keep",
        type=int,
        default=25,
        help="live-mode budget to price the cut against (default: 25)",
    )
    args = parser.parse_args(argv)

    print(render(load_catalog(args.catalog), args.keep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
