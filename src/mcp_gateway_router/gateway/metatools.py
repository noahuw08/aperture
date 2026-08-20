"""Tools the gateway answers for itself.

A meta-tool's subject is the catalog rather than the world. ``find_tools`` searches the
catalog and returns schemas, which trades one schema in the prompt prefix for an extra
round trip whenever the agent needs something it was not given.

**Pure by construction.** No I/O, no ``mcp`` imports, no pool — the catalog arrives as an
argument and the result leaves as text. ``Gateway`` does the wrapping. That is what lets
the only part of arm B with real logic be tested against a hand-built ``Catalog``.

**It never raises and it never fabricates.** A failed search returns explanatory text as
tool *content*, which is the normal MCP shape and leaves the agent able to retry with a
different query; an exception would kill the turn. And on a broken scorer it says so
rather than returning arbitrary tools — the agent cannot tell the difference between a
bad match and a broken search, so silently substituting one for the other would be a way
to mislead it.
"""

from __future__ import annotations

import json
import logging

from ..catalog import Catalog, Tool
from .config import GATEWAY_SERVER_ID
from .naming import advertised_name

logger = logging.getLogger(__name__)

FIND_TOOLS = "find_tools"

#: One unbroken token. Separators get paraphrased — an earlier probe's
#: ``GATEWAY-HINT-7F3A`` came back as ``GATEWAY-7F3A``, so a substring match reported a
#: hint the model had quoted in full as never seen.
SENTINEL = "GATEWAYFINDTOOLS7F3A"

_DESCRIPTION = (
    "Search the full catalog of available tools and return the schemas of those "
    "matching a natural-language query. Only a small core set of tools is listed "
    "directly; every other tool must be found through this one first."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "What you are trying to do, in natural language.",
        }
    },
    "required": ["query"],
}

Key = tuple[str, str]


class MetaTools:
    def __init__(self, *, scorer_factory=None, k: int = 5, enabled: bool = False) -> None:
        if scorer_factory is None:
            from ..baselines import LexicalScorer

            scorer_factory = LexicalScorer
        self._scorer_factory = scorer_factory
        self._k = k
        self._enabled = enabled
        # Cached against the catalog *object*, not its hash: Gateway holds one catalog
        # per session, so identity is stable, and holding the reference keeps the
        # identity check safe from id() reuse after garbage collection.
        self._cached_catalog: Catalog | None = None
        self._cached_scorer = None

    def advertise(self) -> list[Tool]:
        if not self._enabled:
            return []
        return [
            Tool(
                server_id=GATEWAY_SERVER_ID,
                name=FIND_TOOLS,
                description=_DESCRIPTION,
                input_schema=_SCHEMA,
            )
        ]

    def handles(self, server_id: str) -> bool:
        return self._enabled and server_id == GATEWAY_SERVER_ID

    def call(self, name: str, arguments: dict, catalog: Catalog) -> tuple[str, set[Key]]:
        if name != FIND_TOOLS:
            return f"{SENTINEL} no meta-tool named {name!r}.", set()

        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return (
                f"{SENTINEL} find_tools needs a non-empty 'query' string describing "
                f"what you are trying to do.",
                set(),
            )

        if len(catalog) == 0:
            return f"{SENTINEL} no tools are available to search.", set()

        scorer = self._scorer(catalog)
        if scorer is None:
            return (
                f"{SENTINEL} could not search the catalog — the search index is "
                f"unavailable. No tools are being suggested.",
                set(),
            )

        ranked = sorted(catalog, key=lambda t: -scorer.score(query, t))[: self._k]
        return self._render(query, ranked), {t.key for t in ranked}

    def _scorer(self, catalog: Catalog):
        """Build on first use, cached per catalog. ``None`` if it cannot be built."""
        if self._cached_scorer is not None and self._cached_catalog is catalog:
            return self._cached_scorer
        try:
            self._cached_scorer = self._scorer_factory(catalog)
            self._cached_catalog = catalog
        except Exception:
            logger.exception("find_tools could not build a scorer")
            return None
        return self._cached_scorer

    def _render(self, query: str, tools: list[Tool]) -> str:
        blocks = [
            "\n".join(
                [
                    f"name: {advertised_name(tool)}",
                    f"description: {tool.description}",
                    f"input_schema: {json.dumps(tool.input_schema, separators=(',', ':'))}",
                ]
            )
            for tool in tools
        ]
        header = (
            f"{SENTINEL} {len(tools)} tool(s) matched {query!r}. "
            f"Each is callable by the exact name shown."
        )
        return "\n\n".join([header, *blocks])
