"""Tool catalog and tool identity.

Tool identity is ``(server_id, name)`` plus a content hash of the schema. Never a
positional index and never a re-derived ``sorted()`` over the observed population —
see ``docs/decisions.md``, 2026-08-05.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Iterable, Iterator


def schema_hash(input_schema: dict) -> str:
    """Stable content hash of a tool's input schema.

    Sorted keys and no whitespace, so logically identical schemas hash identically
    regardless of how the upstream server serialised them.
    """
    canonical = json.dumps(input_schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Tool:
    """One tool as advertised by an upstream MCP server."""

    server_id: str
    name: str
    description: str
    input_schema: dict = field(default_factory=dict)

    # Tool-side features. Populated by the catalog service in Phase 2; defaults keep
    # the harness runnable on datasets that don't carry them.
    is_write: bool = False
    depends_on: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, str]:
        return (self.server_id, self.name)

    @property
    def version(self) -> str:
        return schema_hash(self.input_schema)

    @property
    def uid(self) -> str:
        """Identity including schema version — changes when the schema changes."""
        return f"{self.server_id}/{self.name}@{self.version}"

    def as_api_tool(self) -> dict:
        """Anthropic tool-definition shape, for token counting."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema or {"type": "object", "properties": {}},
        }


class Catalog:
    """An immutable set of tools, addressable by key."""

    def __init__(self, tools: Iterable[Tool]) -> None:
        self._tools: dict[tuple[str, str], Tool] = {}
        for tool in tools:
            if tool.key in self._tools:
                raise ValueError(f"duplicate tool key: {tool.key}")
            self._tools[tool.key] = tool

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self) -> Iterator[Tool]:
        return iter(self._tools.values())

    def __contains__(self, key: tuple[str, str]) -> bool:
        return key in self._tools

    def get(self, server_id: str, name: str) -> Tool | None:
        return self._tools.get((server_id, name))

    def by_name(self, name: str) -> list[Tool]:
        """All tools with this name, across servers. Names collide between servers."""
        return [t for t in self._tools.values() if t.name == name]

    def subset(self, keys: Iterable[tuple[str, str]]) -> "Catalog":
        return Catalog(self._tools[k] for k in keys)
