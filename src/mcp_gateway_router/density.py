"""How much the `p·v/c` fix is worth against plain rank order.

    uv run python -m mcp_gateway_router.density --catalog results/catalog.json

``algorithm.md`` [4] specifies greedy by density; ``fill_budget`` took rank order until
2026-08-11. Under the old flat-120 placeholder those are the same operation, so the
divergence only appeared once ``harvest`` measured real schemas and found a 39x spread.
``decisions.md`` says the gap is worth measuring rather than assuming — this measures it.

**The value term is the hard part, not the arithmetic.** Density needs calibrated
scores to mean anything (``algorithm.md`` [3]), and this project does not have them yet:
there are 7 call records total. So the CLI reports the *uniform-value* case, where every
tool is equally likely to be needed. That is assumption-light — it needs no model and no
sessions — and it is the honest floor: with uniform value, density-greedy is exactly
"cheapest first", and the gap it shows is pure packing efficiency. A real value term can
only concentrate value further, so the structural gap is real but the operational one
stays unknown until sessions land.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .catalog import Tool
from .deferral import load_catalog, loaded_chars
from .selector import fill_budget
from .tokens import StaticTokenCounter, TokenCounter

ToolKey = tuple[str, str]


@dataclass(frozen=True)
class Gap:
    """One budget point, both orders."""

    budget: int
    rank_n: int
    rank_value: float
    rank_spent: int
    density_n: int
    density_value: float
    density_spent: int

    @property
    def value_delta(self) -> float:
        return self.density_value - self.rank_value

    @property
    def relative_gain(self) -> float:
        """Gain over the rank baseline. Zero when rank captured nothing — not infinite.

        A budget too small for anything in rank order is a degenerate point, not an
        infinite win, and letting it report ``inf`` would poison any mean taken over a
        sweep.
        """
        if self.rank_value <= 0:
            return 0.0
        return self.value_delta / self.rank_value


def _summarise(
    selected: Sequence[Tool], counter: TokenCounter, scores: dict[ToolKey, float]
) -> tuple[int, float, int]:
    value = sum(scores.get(t.key, 0.0) for t in selected)
    spent = sum(counter.cost(t) for t in selected)
    return len(selected), value, spent


def measure_gap(
    ranked: list[Tool],
    budget: int,
    counter: TokenCounter,
    scores: dict[ToolKey, float],
) -> Gap:
    """Fill the same budget both ways and compare what each captured."""
    rank_n, rank_value, rank_spent = _summarise(
        fill_budget(ranked, budget, counter), counter, scores
    )
    density_n, density_value, density_spent = _summarise(
        fill_budget(ranked, budget, counter, scores=scores), counter, scores
    )
    return Gap(
        budget=budget,
        rank_n=rank_n,
        rank_value=rank_value,
        rank_spent=rank_spent,
        density_n=density_n,
        density_value=density_value,
        density_spent=density_spent,
    )


def uniform_scores(tools: Sequence[Tool]) -> dict[ToolKey, float]:
    """Every tool equally likely. Value then reduces to a count of tools exposed."""
    return {t.key: 1.0 for t in tools}


def chars_counter(tools: Sequence[Tool], chars_per_token: float = 3.5) -> TokenCounter:
    """Real measured schema sizes, converted at a fixed rate.

    ``count_tokens`` is still gated by the account balance (``deferral.py``), and the
    conversion is uniform, so it cannot change the *ordering* — which is all density
    depends on. It only affects where the budget boundary falls.
    """
    return StaticTokenCounter(
        {t.uid: max(1, round(loaded_chars(t) / chars_per_token)) for t in tools}
    )


def render(tools: list[Tool], budgets: Sequence[int]) -> str:
    # Rank order is the gateway's own default: stable, by server then name.
    ranked = sorted(tools, key=lambda t: (t.server_id, t.name))
    counter = chars_counter(tools)
    scores = uniform_scores(tools)

    lines = [
        f"{'budget':>8}{'rank n':>9}{'density n':>11}{'delta':>8}{'gain':>9}",
        "-" * 45,
    ]
    for budget in budgets:
        gap = measure_gap(ranked, budget, counter, scores)
        lines.append(
            f"{gap.budget:>8,}{gap.rank_n:>9}{gap.density_n:>11}"
            f"{gap.density_n - gap.rank_n:>+8}{gap.relative_gain:>8.0%}"
        )

    lines += [
        "",
        "Uniform value, so 'n' is tools exposed and density-greedy is cheapest-first.",
        "This is the packing-efficiency floor, not an operational estimate — a real",
        "value term needs calibrated scores, which need sessions. See the module docs.",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", type=Path, default=Path("results/catalog.json"))
    parser.add_argument(
        "--budgets",
        type=int,
        nargs="+",
        default=[1_000, 2_000, 3_000, 5_000, 8_000, 12_000, 20_000],
    )
    args = parser.parse_args(argv)

    print(render(load_catalog(args.catalog), args.budgets))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
