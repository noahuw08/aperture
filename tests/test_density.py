"""The rank-greedy vs density-greedy gap.

The claim being measured is that dividing by cost buys real value on a catalog with a
39x schema spread. A measurement that reported a gap when the two orders are identical,
or missed one when they diverge, would make the whole comparison unreadable.
"""

from mcp_gateway_router.catalog import Catalog, Tool
from mcp_gateway_router.density import Gap, measure_gap
from mcp_gateway_router.tokens import StaticTokenCounter

SCHEMA = {"type": "object", "properties": {"q": {"type": "string"}}}


def tool(name):
    return Tool(server_id="s", name=name, description=name, input_schema=SCHEMA)


CHEAP = [tool(f"cheap{i}") for i in range(4)]
DEAR = tool("dear")
CATALOG = Catalog(CHEAP + [DEAR])
COUNTER = StaticTokenCounter(
    {"cheap0": 50, "cheap1": 50, "cheap2": 50, "cheap3": 50, "dear": 400}
)
# The expensive tool is the single most valuable, so rank order takes it first.
SCORES = {DEAR.key: 1.0, **{t.key: 0.6 for t in CHEAP}}
RANKED = [DEAR] + CHEAP


def test_gap_is_zero_under_a_flat_cost():
    """Flat cost is the placeholder the old code was written against: no divergence."""
    flat = StaticTokenCounter({}, default=100)
    gap = measure_gap(RANKED, budget=300, counter=flat, scores=SCORES)
    assert gap.rank_value == gap.density_value
    assert gap.value_delta == 0.0


def test_density_captures_more_value_when_costs_spread():
    gap = measure_gap(RANKED, budget=440, counter=COUNTER, scores=SCORES)
    assert gap.rank_value == 1.0  # dear only; 40 tokens stranded
    assert gap.density_value == 2.4  # four cheap tools
    assert gap.value_delta == 1.4


def test_density_exposes_more_tools_for_the_same_spend():
    gap = measure_gap(RANKED, budget=440, counter=COUNTER, scores=SCORES)
    assert gap.rank_n == 1
    assert gap.density_n == 4
    assert gap.density_spent <= 440


def test_a_budget_that_fits_everything_shows_no_gap():
    """Both orders take the whole catalog; only the order differs, not the set."""
    gap = measure_gap(RANKED, budget=10_000, counter=COUNTER, scores=SCORES)
    assert gap.value_delta == 0.0
    assert gap.rank_n == gap.density_n == 5


def test_relative_gain_is_reported_against_the_rank_baseline():
    gap = measure_gap(RANKED, budget=440, counter=COUNTER, scores=SCORES)
    assert gap.relative_gain == (2.4 - 1.0) / 1.0


def test_relative_gain_is_zero_rather_than_infinite_when_rank_captures_nothing():
    gap = measure_gap(RANKED, budget=10, counter=COUNTER, scores=SCORES)
    assert gap.rank_value == 0.0
    assert isinstance(gap, Gap)
    assert gap.relative_gain == 0.0
