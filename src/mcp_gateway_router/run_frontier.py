"""Phase 0 entry point: build the baseline ladder, sweep budgets, write results.

    uv run --extra bench python -m mcp_gateway_router.run_frontier --help

The X axis of the frontier is **mean tokens actually spent**, not the budget knob.
``expose-all`` ignores the budget entirely, so plotting against the knob would put it
at every X while it only ever spends one amount. Realized cost is the honest axis.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path

from .baselines import (
    ExposeAll,
    InstructionScorer,
    LexicalScorer,
    Oracle,
    PopularityTopK,
    ProgressiveDisclosure,
    SemanticRetrieval,
    StaticSet,
)
from .catalog import Catalog
from .data import load_catalog, load_examples, restrict_to_catalog
from .frontier import Example, sweep
from .tokens import DEFAULT_MODEL, AnthropicTokenCounter, StaticTokenCounter

DEFAULT_BUDGETS = [500, 1000, 2000, 4000, 8000, 16000, 32000]


def split_examples(
    examples: list[Example], fit_fraction: float = 0.5, seed: int = 0
) -> tuple[list[Example], list[Example]]:
    """Split into a fit half and an eval half.

    ``static-set`` and ``popularity`` are *history-dependent* — they need past usage to
    pick their set. Fitting them on the same examples they're scored on is train-on-test:
    they memorise the answer key and their curves become meaningless. Only these two
    consume the fit half; the other four baselines don't learn anything.
    """
    shuffled = list(examples)
    random.Random(seed).shuffle(shuffled)
    cut = int(len(shuffled) * fit_fraction)
    return shuffled[:cut], shuffled[cut:]


def build_selectors(
    catalog: Catalog,
    fit_examples: list[Example],
    eval_examples: list[Example],
    static_size: int,
    use_embeddings: bool,
    seed: int = 0,
    pd_ks: list[int] | None = None,
    instructions: dict[str, str] | None = None,
) -> list:
    """The six baselines.

    History-dependent baselines are fitted on ``fit_examples`` only. That is still
    generous to them — a vendor's hand-picked 25 is not random, and a popularity counter
    has real history behind it — but it is no longer leakage.

    ``pd_ks`` sweeps progressive disclosure's retrieval depth, emitting one named variant
    per value. ``instructions`` wraps the scorer so baseline 4 and 5 see ToolRet's
    per-query instruction — a benchmark artifact, off by default; see ``InstructionScorer``.
    """
    pd_ks = list(pd_ks or [10])
    gold = Counter(key for e in fit_examples for key in e.required)

    static_keys = [k for k, _ in gold.most_common(static_size)]
    if not static_keys:
        rng = random.Random(seed)
        static_keys = [t.key for t in rng.sample(list(catalog), min(static_size, len(catalog)))]

    if use_embeddings:
        from .embedding import EmbeddingScorer

        scorer = EmbeddingScorer(catalog)
    else:
        scorer = LexicalScorer(catalog)

    if instructions:
        scorer = InstructionScorer(scorer, instructions)

    disclosure = [
        ProgressiveDisclosure(
            scorer,
            core_keys=static_keys[:3],
            k=k,
            name=None if len(pd_ks) == 1 else f"progressive-disclosure(k={k})",
        )
        for k in pd_ks
    ]

    return [
        ExposeAll(),
        StaticSet(static_keys),
        PopularityTopK(dict(gold)),
        SemanticRetrieval(scorer),
        *disclosure,
        Oracle({e.task: list(e.required) for e in eval_examples}),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Phase 0 frontier sweep.")
    parser.add_argument("--tools-subset", default="customized", help="ToolRet-Tools subset")
    parser.add_argument("--queries-subset", default="apibank", help="ToolRet-Queries subset")
    parser.add_argument("--tool-limit", type=int, default=None)
    parser.add_argument("--query-limit", type=int, default=200)
    parser.add_argument("--static-size", type=int, default=25, help="size of the hand-picked set")
    parser.add_argument("--budgets", type=int, nargs="+", default=DEFAULT_BUDGETS)
    parser.add_argument(
        "--embeddings",
        action="store_true",
        help="use the bi-encoder for baseline 4. Required for the kill gate — "
        "the lexical fallback understates the competitor.",
    )
    parser.add_argument(
        "--count-tokens",
        action="store_true",
        help="measure real schema cost via the Anthropic API instead of a flat estimate",
    )
    parser.add_argument(
        "--token-workers",
        type=int,
        default=8,
        help="concurrent count_tokens requests. The endpoint is free but rate-limited "
        "per minute (2k/4k/8k RPM by usage tier); 8 suits Start tier, raise on higher.",
    )
    parser.add_argument(
        "--pd-k",
        type=int,
        nargs="+",
        default=[10],
        help="progressive-disclosure retrieval depth. Pass several to sweep — the knob "
        "sets how strong the existential baseline is, so an unjustified default is a "
        "benchmark that cannot lose.",
    )
    parser.add_argument(
        "--retrieval-instruction",
        action="store_true",
        help="let baselines 4 and 5 see ToolRet's per-query instruction. A BENCHMARK "
        "ARTIFACT (a gateway never gets one) — run it as a sensitivity check reported "
        "alongside the query-only headline, never instead of it.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--out", type=Path, default=Path("results/frontier.json"))
    args = parser.parse_args(argv)

    catalog = load_catalog(args.tools_subset, limit=args.tool_limit)
    examples = restrict_to_catalog(
        load_examples(args.queries_subset, limit=args.query_limit), catalog
    )
    if not examples:
        raise SystemExit(
            f"No examples survived restriction: none of the '{args.queries_subset}' gold "
            f"tools are in the '{args.tools_subset}' catalog. Match the subsets."
        )

    if args.count_tokens:
        counter = AnthropicTokenCounter(
            model=args.model, cache_path=Path(".cache/token_costs.json")
        )
        # Measure up front rather than lazily inside the sweep: the sweep would issue
        # ~one call per catalog tool sequentially. Free endpoint, so the ceiling is
        # requests/minute, not spend.
        print(f"measuring schema cost for {len(catalog)} tools ({args.token_workers} workers)...")
        measured = counter.prewarm(
            catalog,
            max_workers=args.token_workers,
            on_progress=lambda done, total: print(f"  {done}/{total}", flush=True),
        )
        print(f"measured {measured} new tools; cache at .cache/token_costs.json")
    else:
        counter = StaticTokenCounter({}, default=120)

    instructions = (
        {e.task: e.instruction for e in examples if e.instruction}
        if args.retrieval_instruction
        else None
    )
    if args.retrieval_instruction and not instructions:
        raise SystemExit(
            "--retrieval-instruction was passed but no example carries an instruction; "
            "the run would silently be identical to the query-only headline."
        )

    fit_examples, eval_examples = split_examples(examples)
    selectors = build_selectors(
        catalog,
        fit_examples,
        eval_examples,
        args.static_size,
        args.embeddings,
        pd_ks=args.pd_k,
        instructions=instructions,
    )
    points = sweep(selectors, eval_examples, catalog, args.budgets, counter)

    payload = {
        "meta": {
            "tools_subset": args.tools_subset,
            "queries_subset": args.queries_subset,
            "catalog_size": len(catalog),
            "n_fit": len(fit_examples),
            "n_examples": len(eval_examples),
            "budgets": args.budgets,
            "scorer": "bi-encoder" if args.embeddings else "lexical (PLACEHOLDER)",
            "token_costs": f"measured ({args.model})" if args.count_tokens else "flat estimate",
            "pd_k": args.pd_k,
            "retrieval_query": "instruction + query (BENCHMARK ARTIFACT)"
            if args.retrieval_instruction
            else "query only",
        },
        "points": [p.__dict__ for p in points],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2))

    print(f"catalog={len(catalog)} tools  fit={len(fit_examples)} eval={len(eval_examples)}")
    print(f"{'selector':<24}{'budget':>8}{'tokens':>10}{'satisfied':>12}{'recall':>9}")
    for p in points:
        print(
            f"{p.selector:<24}{p.budget:>8}{p.mean_tokens:>10.0f}"
            f"{p.satisfied:>12.3f}{p.recall:>9.3f}"
        )
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
