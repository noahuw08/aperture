"""Score point-A selectors against collected sessions.

    uv run --extra gateway python -m mcp_gateway_router.replay.run \
        --log runs/exposure.jsonl --catalog results/catalog.json

Add your own engine with ``--engine module:factory``, where ``factory`` is a callable
taking the prior sessions and returning something with ``.select(context, catalog,
budget, counter)`` — the existing ``Selector`` protocol.
"""

from __future__ import annotations

import argparse
import importlib
import json
from collections import Counter
from pathlib import Path

from ..harvest import catalog_from_json
from ..tokens import StaticTokenCounter
from .baselines import global_frequency, oracle_factory, random_selector, recency_weighted
from .harness import replay
from .sessions import load_sessions

DEFAULT_BUDGETS = (500, 1000, 2000, 3000, 5000, 10000)


def _load_engine(spec: str):
    module_name, _, attribute = spec.partition(":")
    if not attribute:
        raise SystemExit(f"--engine must look like 'module:factory', got {spec!r}")
    return getattr(importlib.import_module(module_name), attribute)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, default=Path("runs"))
    parser.add_argument("--catalog", type=Path, default=Path("results/catalog.json"))
    parser.add_argument("--budgets", type=int, nargs="+", default=list(DEFAULT_BUDGETS))
    parser.add_argument("--engine", help="module:factory for your own selector")
    parser.add_argument("--out", type=Path, help="write results as JSON")
    args = parser.parse_args()

    sessions = load_sessions(args.log)
    payload = json.loads(args.catalog.read_text())
    catalog = catalog_from_json(payload)
    costs = {k: int(v) for k, v in payload.get("costs", {}).items()}
    counter = StaticTokenCounter(costs, default=100)

    scored = [s for s in sessions if s.called]
    print(f"{len(sessions)} sessions, {len(scored)} with calls, {len(catalog)} tools")

    # Calls to tools the catalog no longer carries can never be covered, so they drag
    # every arm down — including the oracle. Silent, that reads as a broken harness.
    missing: Counter[tuple[str, str]] = Counter()
    for session in scored:
        for key in session.distinct_called:
            if catalog.get(*key) is None:
                missing[key] += 1
    if missing:
        print(
            f"⚠️  {sum(missing.values())} calls to {len(missing)} tools absent from the "
            "catalog — uncoverable by any arm, including the oracle:"
        )
        for (server, tool), n in missing.most_common(5):
            print(f"      {server}/{tool}  ({n} session(s))")
        print("    Either the catalog is stale, or those servers are not proxied.")
    if not costs:
        print("⚠️  no measured token costs in the catalog — budgets are approximate")
    if len(scored) < 20:
        print(
            f"⚠️  only {len(scored)} scored sessions; differences below ~20 points are "
            "not readable at this n"
        )
    print()

    arms = {
        "random (null)": random_selector(),
        "d-global": global_frequency,
        "d-recent": recency_weighted(),
        "oracle (ceiling)": oracle_factory(sorted(sessions, key=lambda s: (s.opened_at, s.session_id))),
    }
    if args.engine:
        arms[f"engine:{args.engine}"] = _load_engine(args.engine)

    header = "arm".ljust(26) + "".join(f"{b:>9}" for b in args.budgets)
    print(header)
    print("-" * len(header))

    results: dict[str, dict[int, float]] = {}
    for label, factory in arms.items():
        row = {}
        for budget in args.budgets:
            row[budget] = replay(sessions, factory, catalog, budget, counter).mean_coverage
        results[label] = row
        print(label.ljust(26) + "".join(f"{row[b]:>9.3f}" for b in args.budgets))

    print()
    print("coverage = fraction of a session's distinct called tools exposed at its open.")
    print("NOT task success: sessions were collected with the whole catalog available,")
    print("so this is a floor — 'would we have kept what you used' — not a ceiling.")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(results, indent=2))
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
