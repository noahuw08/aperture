"""Harvest a real MCP catalog and measure what its schemas actually cost.

Every token number in the repo before this ran assumed a flat 120 tokens per tool.
Real schemas vary by roughly 85x, and under a flat cost ``fill_budget`` is a top-K cut
wearing a disguise. This is what makes the knapsack a knapsack.

Built as a component rather than a one-off dump: this *is* the Phase 2 catalog service.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .catalog import Catalog, Tool
from .gateway.config import GatewayConfig
from .gateway.upstream import UpstreamPool


def catalog_to_json(catalog: Catalog, costs: dict[str, int]) -> dict:
    return {
        "tools": [
            {
                "server_id": t.server_id,
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
                "is_write": t.is_write,
            }
            for t in catalog
        ],
        "costs": costs,
    }


def catalog_from_json(payload: dict) -> Catalog:
    return Catalog(
        Tool(
            server_id=raw["server_id"],
            name=raw["name"],
            description=raw.get("description", ""),
            input_schema=raw.get("input_schema", {}),
            is_write=raw.get("is_write", False),
        )
        for raw in payload["tools"]
    )


async def harvest(
    config: GatewayConfig,
    count_tokens: bool,
    session_factory=None,
    cache_path: Path | None = None,
) -> tuple[Catalog, dict[str, int]]:
    pool = UpstreamPool(config.upstreams, session_factory=session_factory)
    await pool.start()
    try:
        catalog = await pool.aggregate()
    finally:
        await pool.aclose()

    if not count_tokens:
        return catalog, {}

    from .tokens import AnthropicTokenCounter

    counter = AnthropicTokenCounter(model=config.model, cache_path=cache_path)
    counter.prewarm(
        catalog,
        on_progress=lambda done, total: print(f"  counted {done}/{total}", flush=True),
    )
    return catalog, {t.uid: counter.cost(t) for t in catalog}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--count-tokens", action="store_true")
    args = parser.parse_args()

    config = GatewayConfig.from_file(args.config)
    catalog, costs = asyncio.run(
        harvest(
            config,
            count_tokens=args.count_tokens,
            cache_path=args.out.parent / "token_costs.json",
        )
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(catalog_to_json(catalog, costs), indent=2))

    by_server: dict[str, int] = {}
    for tool in catalog:
        by_server[tool.server_id] = by_server.get(tool.server_id, 0) + 1

    print(f"{len(catalog)} tools from {len(config.upstreams)} servers -> {args.out}")
    for server_id, count in sorted(by_server.items()):
        print(f"  {server_id}: {count}")
    if costs:
        values = sorted(costs.values())
        print(
            f"token cost: median {values[len(values) // 2]}, "
            f"min {values[0]}, max {values[-1]}, "
            f"spread {values[-1] / max(values[0], 1):.0f}x"
        )


if __name__ == "__main__":
    main()
