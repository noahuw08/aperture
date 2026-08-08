"""Loader tests run against inline fixtures — no network, no dataset download."""

import json

from mcp_gateway_router.catalog import Catalog
from mcp_gateway_router.data import (
    example_from_row,
    load_catalog_jsonl,
    load_examples_jsonl,
    restrict_to_catalog,
    tool_from_row,
)
from mcp_gateway_router.embedding import EmbeddingScorer

# Shapes taken from the two ToolRet dataset cards: gorilla-style rows carry
# domain/framework/functionality, craft-style rows carry name/description.
GORILLA_ROW = {
    "id": "gorilla_tensor_tool_0",
    "documentation": json.dumps(
        {
            "domain": "Image object detection",
            "framework": "TensorFlow Hub",
            "functionality": "Detect objects in an image and return bounding boxes",
            "api_call": "hub.load('...')",
            "api_arguments": {"image": "a decoded image tensor"},
        }
    ),
}

CRAFT_ROW = {
    "id": "craft_Vqa_tool_0",
    "documentation": json.dumps(
        {"name": "answer_question(image, question)", "description": "Answer a question about an image"}
    ),
}

QUERY_ROW = {
    "id": "apibank_query_1",
    "query": "Can you tell me about the historical events of April 21st?",
    "instruction": "Find a tool that returns historical events for a date.",
    "labels": json.dumps([{"id": "apibank_tool_33", "doc": {"name": "get_historical_events"}}]),
    "category": "web",
}


def test_tool_id_prefix_becomes_server_id():
    tool = tool_from_row(GORILLA_ROW)
    assert tool.server_id == "gorilla_tensor"
    assert tool.name == "gorilla_tensor_tool_0"
    assert tool.key == ("gorilla_tensor", "gorilla_tensor_tool_0")


def test_description_joins_every_indexable_field():
    tool = tool_from_row(GORILLA_ROW)
    assert "Image object detection" in tool.description
    assert "TensorFlow Hub" in tool.description
    assert "Detect objects" in tool.description


def test_arguments_become_input_schema():
    assert tool_from_row(GORILLA_ROW).input_schema == {"image": "a decoded image tensor"}


def test_heterogeneous_shapes_both_load():
    craft = tool_from_row(CRAFT_ROW)
    assert "Answer a question about an image" in craft.description
    assert craft.server_id == "craft_Vqa"


def test_row_without_known_keys_still_yields_a_description():
    tool = tool_from_row({"id": "odd_tool_1", "documentation": json.dumps({"weird": "shape"})})
    assert tool.description  # falls back to the raw blob rather than empty


def test_documentation_may_arrive_already_parsed():
    row = {"id": "x_tool_0", "documentation": {"description": "already a dict"}}
    assert "already a dict" in tool_from_row(row).description


def test_gold_labels_become_required_keys():
    example = example_from_row(QUERY_ROW)
    assert example.task.startswith("Can you tell me")
    assert example.required == (("apibank", "apibank_tool_33"),)


def test_restrict_drops_examples_whose_gold_tool_is_absent():
    catalog = Catalog([tool_from_row(GORILLA_ROW)])
    examples = [example_from_row(QUERY_ROW)]  # needs apibank_tool_33, not loaded
    assert restrict_to_catalog(examples, catalog) == []


def test_restrict_keeps_examples_fully_covered():
    row = {"id": "gorilla_tensor_query_0", "query": "detect objects", "labels": json.dumps(
        [{"id": "gorilla_tensor_tool_0"}]
    )}
    catalog = Catalog([tool_from_row(GORILLA_ROW)])
    assert len(restrict_to_catalog([example_from_row(row)], catalog)) == 1


def test_jsonl_roundtrip(tmp_path):
    tools_path = tmp_path / "tools.jsonl"
    tools_path.write_text("\n".join(json.dumps(r) for r in [GORILLA_ROW, CRAFT_ROW]))
    queries_path = tmp_path / "queries.jsonl"
    queries_path.write_text(json.dumps(QUERY_ROW))

    catalog = load_catalog_jsonl(tools_path)
    assert len(catalog) == 2
    assert len(load_examples_jsonl(queries_path)) == 1


# --- embedding scorer (fake encoder, no model download) ---------------------


class FakeEncoder:
    """Deterministic 2-d unit vectors keyed on a substring, to test wiring only."""

    def encode(self, texts, normalize_embeddings=True, show_progress_bar=False):
        return [(1.0, 0.0) if "object" in t.lower() else (0.0, 1.0) for t in texts]


def test_embedding_scorer_prefers_matching_tool():
    catalog = Catalog([tool_from_row(GORILLA_ROW), tool_from_row(CRAFT_ROW)])
    scorer = EmbeddingScorer(catalog, model=FakeEncoder())
    gorilla = catalog.get("gorilla_tensor", "gorilla_tensor_tool_0")
    craft = catalog.get("craft_Vqa", "craft_Vqa_tool_0")
    assert scorer.score("detect the object", gorilla) > scorer.score("detect the object", craft)


def test_embedding_scorer_caches_query_encoding():
    catalog = Catalog([tool_from_row(GORILLA_ROW)])
    encoder = FakeEncoder()
    calls = []
    original = encoder.encode

    def counting(texts, **kwargs):
        calls.append(len(texts))
        return original(texts, **kwargs)

    encoder.encode = counting
    scorer = EmbeddingScorer(catalog, model=encoder)
    tool = catalog.get("gorilla_tensor", "gorilla_tensor_tool_0")
    after_init = len(calls)  # catalog encoded once during __init__

    scorer.score("same query", tool)
    after_first = len(calls)
    assert after_first == after_init + 1  # query encoded

    scorer.score("same query", tool)
    assert len(calls) == after_first  # repeat query served from cache
