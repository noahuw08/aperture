"""Metric math, tested against hand-computed values.

The point of calibration is to catch a silent pipeline bug. That only works if the
metrics themselves are right, so they get checked against numbers worked out by hand
rather than against the implementation's own output.
"""

import math

from mcp_gateway_router.calibrate import evaluate_retriever, ndcg_at_k, rank_of_required
from mcp_gateway_router.catalog import Catalog, Tool
from mcp_gateway_router.embedding import EmbeddingScorer
from mcp_gateway_router.frontier import Example

CATALOG = Catalog(
    [
        Tool("s", "alpha", "object detection in images"),
        Tool("s", "beta", "translate text between languages"),
        Tool("s", "gamma", "object tracking in video"),
    ]
)


class RankedEncoder:
    """Encodes so that cosine similarity orders alpha > gamma > beta for any query."""

    _ANGLES = {"alpha": 0.0, "gamma": 0.3, "beta": 1.4}

    def encode(self, texts, normalize_embeddings=True, show_progress_bar=False):
        out = []
        for text in texts:
            angle = next((a for name, a in self._ANGLES.items() if name in text), 0.0)
            out.append((math.cos(angle), math.sin(angle)))
        return out


def scorer():
    return EmbeddingScorer(CATALOG, model=RankedEncoder(), use_prefixes=False)


# --- nDCG ------------------------------------------------------------------


def test_ndcg_is_one_when_all_relevant_are_at_the_top():
    assert ndcg_at_k([1, 2], n_relevant=2, k=10) == 1.0


def test_ndcg_is_zero_when_nothing_is_retrieved_within_k():
    assert ndcg_at_k([11, 12], n_relevant=2, k=10) == 0.0


def test_ndcg_matches_hand_computation_for_a_single_hit_at_rank_3():
    # DCG = 1/log2(4) = 0.5 ; IDCG (1 relevant) = 1/log2(2) = 1.0
    assert ndcg_at_k([3], n_relevant=1, k=10) == 0.5


def test_ndcg_penalises_a_lower_rank():
    assert ndcg_at_k([1], 1, 10) > ndcg_at_k([2], 1, 10) > ndcg_at_k([5], 1, 10)


def test_ndcg_of_empty_relevant_set_is_zero_not_a_crash():
    assert ndcg_at_k([], n_relevant=0, k=10) == 0.0


# --- ranks -----------------------------------------------------------------


def test_rank_of_required_reports_one_based_positions():
    example = Example("q", (("s", "alpha"), ("s", "gamma")))
    assert rank_of_required(scorer(), example, CATALOG, depth=10) == [1, 2]


def test_unranked_tool_gets_depth_plus_one():
    example = Example("q", (("s", "beta"),))
    assert rank_of_required(scorer(), example, CATALOG, depth=2) == [3]


# --- end to end ------------------------------------------------------------


def test_evaluate_retriever_reports_perfect_scores_for_a_top_ranked_gold():
    examples = [Example("q", (("s", "alpha"),))]
    m = evaluate_retriever(scorer(), examples, CATALOG, k=10)
    assert m["ndcg@10"] == 1.0
    assert m["recall@10"] == 1.0
    assert m["hit@1"] == 1.0


def test_recall_counts_partial_coverage():
    """k=1 can only cover one of two required tools."""
    examples = [Example("q", (("s", "alpha"), ("s", "gamma")))]
    m = evaluate_retriever(scorer(), examples, CATALOG, k=1)
    assert m["recall@1"] == 0.5
    assert m["hit@1"] == 1.0  # the best-ranked required tool is still at position 1


def test_metrics_carry_the_shape_of_the_run():
    examples = [Example("q", (("s", "alpha"),))]
    m = evaluate_retriever(scorer(), examples, CATALOG, k=10)
    assert m["n_examples"] == 1
    assert m["catalog_size"] == 3


# --- published-band verdict -------------------------------------------------


def test_verdict_reproduces_a_published_model():
    """Our measured e5-base (26.22 w/ inst) against its published 24.59."""
    from mcp_gateway_router.calibrate import verdict

    assert "REPRODUCES" in verdict(26.22, "intfloat/e5-base-v2", use_instruction=True)


def test_verdict_flags_a_published_model_that_misses():
    """Our BM25 w/ inst (52.67) against its published 28.74 — a like-for-like miss."""
    from mcp_gateway_router.calibrate import verdict

    out = verdict(52.67, "bm25", use_instruction=True)
    assert "DOES NOT REPRODUCE" in out


def test_verdict_selects_the_band_by_setting():
    """The whole 2026-08-07 correction: query-only must not be judged against
    with-instruction numbers. BM25 publishes 28.74 w/ inst and 20.20 w/o."""
    from mcp_gateway_router.calibrate import verdict

    assert "REPRODUCES" in verdict(20.5, "bm25", use_instruction=False)
    assert "DOES NOT REPRODUCE" in verdict(20.5, "bm25", use_instruction=True)


def test_our_first_calibration_run_would_still_be_flagged():
    """25.52 was the query-only number produced while the w/-inst protocol was
    intended — the bug that started all this. The tuned checkpoint has no published
    row, so the base-model floor is what has to keep catching it."""
    from mcp_gateway_router.calibrate import verdict

    out = verdict(25.52, "mangopy/ToolRet-trained-e5-base-v2", use_instruction=True)
    assert "SUSPECT" in out


def test_verdict_accepts_a_genuine_finetuning_gain():
    """56.19 is the corrected number for the same model and setting."""
    from mcp_gateway_router.calibrate import verdict

    out = verdict(56.19, "mangopy/ToolRet-trained-e5-base-v2", use_instruction=True)
    assert "SUSPECT" not in out
    assert "beats its base" in out


def test_every_published_model_reproduces_itself():
    """A model scoring exactly its published number must never be flagged, in either
    setting. Guards against a typo in the transcribed tables."""
    from mcp_gateway_router.calibrate import (
        PUBLISHED_API_NO_INST,
        PUBLISHED_API_WITH_INST,
        verdict,
    )

    for table, inst in ((PUBLISHED_API_WITH_INST, True), (PUBLISHED_API_NO_INST, False)):
        for model, (ndcg, _) in table.items():
            assert "REPRODUCES" in verdict(ndcg, model, use_instruction=inst), model
