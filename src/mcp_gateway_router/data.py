"""ToolRet loader.

HumanMCP was the first choice — persona-varied queries over 2,800 MCP tools — but it
has no public release. ToolRet is the nearest released equivalent and is better
distributed: HuggingFace datasets, an ACL 2025 paper, a public leaderboard.

What matters for the Phase 0 gate is that the queries were **not** written by whoever
wrote the tool descriptions: ToolRet's 7.6k tasks are sampled from 35 pre-existing
tool-use datasets, so query vocabulary and description vocabulary are independent.
Self-authored ground truth would make retrieval look perfect for the wrong reason.

Shapes (from the dataset cards):

* ``ToolRet-Tools``   — ``id``, ``documentation`` (JSON string). Subsets: code, web,
  customized. Split ``tools``. 44,453 rows total.
* ``ToolRet-Queries`` — ``id``, ``query``, ``instruction``, ``labels`` (JSON string of
  gold tool objects, each with ``id``), ``category``. 35 subsets. Split ``queries``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .catalog import Catalog, Tool
from .frontier import Example

TOOLS_REPO = "mangopy/ToolRet-Tools"
QUERIES_REPO = "mangopy/ToolRet-Queries"

# ToolRet documentation blobs are heterogeneous — each source dataset kept its own
# shape. These are the keys worth indexing, in preference order.
_NAME_KEYS = ("name", "api_name", "tool_name", "function", "api_call")
_DESC_KEYS = (
    "description",
    "functionality",
    "doc",
    "summary",
    "domain",
    "framework",
    "example_code",
)
_ARG_KEYS = ("api_arguments", "parameters", "arguments", "input_schema", "required_parameters")


def _loads(value: Any) -> Any:
    """ToolRet stores nested structures as JSON strings. Some rows are already parsed."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _first_str(doc: dict, keys: Iterable[str]) -> str | None:
    for key in keys:
        value = doc.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _description(doc: dict) -> str:
    """Join every indexable field — this is what a retriever actually sees."""
    parts = []
    for key in _DESC_KEYS:
        value = doc.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return "\n".join(parts) if parts else json.dumps(doc)[:2000]


def _arguments(doc: dict) -> dict:
    for key in _ARG_KEYS:
        value = _loads(doc.get(key))
        if isinstance(value, dict) and value:
            return value
        if isinstance(value, list) and value:
            return {"type": "object", "properties": {"args": value}}
    return {}


def _server_id(tool_id: str) -> str:
    """``apibank_tool_33`` -> ``apibank``. Keeps our (server_id, name) identity intact."""
    head = tool_id.split("_tool_", 1)[0]
    return head or "toolret"


def tool_from_row(row: dict) -> Tool:
    tool_id = row["id"]
    doc = _loads(row.get("documentation")) or {}
    if not isinstance(doc, dict):
        doc = {"description": str(doc)}
    return Tool(
        server_id=_server_id(tool_id),
        name=tool_id,
        description=_description(doc),
        input_schema=_arguments(doc),
    )


def example_from_row(row: dict) -> Example:
    labels = _loads(row.get("labels")) or []
    if isinstance(labels, dict):
        labels = [labels]
    required = []
    for label in labels:
        if isinstance(label, dict) and "id" in label:
            tool_id = label["id"]
            required.append((_server_id(tool_id), tool_id))
    return Example(
        task=row["query"],
        required=tuple(required),
        instruction=(row.get("instruction") or "").strip(),
    )


def load_catalog(subset: str = "customized", limit: int | None = None) -> Catalog:
    rows = _load_hf(TOOLS_REPO, subset, "tools", limit)
    return Catalog(tool_from_row(r) for r in rows)


def load_examples(subset: str = "apibank", limit: int | None = None) -> list[Example]:
    rows = _load_hf(QUERIES_REPO, subset, "queries", limit)
    return [example_from_row(r) for r in rows]


def _load_hf(repo: str, subset: str, split: str, limit: int | None) -> list[dict]:
    from datasets import load_dataset  # optional extra; see pyproject [bench]

    dataset = load_dataset(repo, subset, split=split)
    if limit is not None:
        dataset = dataset.select(range(min(limit, len(dataset))))
    return [dict(row) for row in dataset]


# --- local fixtures ---------------------------------------------------------


def load_catalog_jsonl(path: Path) -> Catalog:
    return Catalog(tool_from_row(json.loads(line)) for line in _lines(path))


def load_examples_jsonl(path: Path) -> list[Example]:
    return [example_from_row(json.loads(line)) for line in _lines(path)]


def _lines(path: Path) -> Iterable[str]:
    for line in path.read_text().splitlines():
        if line.strip():
            yield line


def restrict_to_catalog(examples: list[Example], catalog: Catalog) -> list[Example]:
    """Drop examples whose gold tools aren't in the loaded catalog subset.

    ToolRet queries and tools are subsetted independently, so a query can reference a
    tool from a subset you didn't load. Scoring those as misses would understate every
    selector equally — but it would also make the oracle unreachable, which breaks the
    frontier's reference line.
    """
    return [e for e in examples if e.required and all(k in catalog for k in e.required)]
