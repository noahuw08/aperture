"""Schema token cost measurement.

The budget is a *token* budget, not a tool count, so every selector needs a real
per-tool cost. Counts are model-specific and must come from the Anthropic
``count_tokens`` endpoint — ``tiktoken`` is OpenAI's tokenizer and undercounts Claude
tokens by ~15-20% on prose and considerably more on JSON schemas.

Costs are cached on disk keyed by ``(model, tool.uid)``. Since ``uid`` carries the
schema content hash, a changed schema invalidates its own entry and nothing else.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Iterable, Protocol

from .catalog import Tool

DEFAULT_MODEL = "claude-opus-5"

# count_tokens requires a messages array; this is the constant floor we subtract off
# so a tool's cost is its marginal contribution rather than the whole request.
_PROBE_MESSAGES = [{"role": "user", "content": "."}]


class TokenCounter(Protocol):
    def cost(self, tool: Tool) -> int:
        """Marginal tokens this tool's schema adds to the prompt prefix."""
        ...


def total_cost(counter: TokenCounter, tools: Iterable[Tool]) -> int:
    return sum(counter.cost(t) for t in tools)


class AnthropicTokenCounter:
    """Measures real schema cost via ``messages.count_tokens``.

    Requires the ``anthropic`` package and credentials (``ANTHROPIC_API_KEY``, or an
    ``ant auth login`` profile — the zero-arg client resolves either).
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        cache_path: Path | None = None,
        client=None,
    ) -> None:
        if client is None:
            import anthropic  # imported lazily so the core package stays dep-free

            client = anthropic.Anthropic()
        self._client = client
        self._model = model
        self._cache_path = cache_path
        self._cache: dict[str, int] = {}
        if cache_path and cache_path.exists():
            self._cache = json.loads(cache_path.read_text())
        self._baseline: int | None = None
        self._lock = threading.Lock()

    def _count(self, tools: list[dict]) -> int:
        response = self._client.messages.count_tokens(
            model=self._model,
            messages=_PROBE_MESSAGES,
            tools=tools,
        )
        return response.input_tokens

    def _key(self, tool: Tool) -> str:
        return f"{self._model}:{tool.uid}"

    def _ensure_baseline(self) -> int:
        if self._baseline is None:
            self._baseline = self._count([])
        return self._baseline

    def cost(self, tool: Tool) -> int:
        key = self._key(tool)
        with self._lock:
            if key in self._cache:
                return self._cache[key]

        measured = self._count([tool.as_api_tool()]) - self._ensure_baseline()
        with self._lock:
            self._cache[key] = measured
        self.flush()
        return measured

    def prewarm(
        self,
        tools: Iterable[Tool],
        max_workers: int = 8,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> int:
        """Measure many tools concurrently and fill the cache. Returns tools measured.

        ``cost`` is one API call per tool, and a full sweep touches *every* tool in the
        catalog — ``ExposeAll`` sums over all of them, and ``fill_budget`` skips rather
        than stops, so ``PopularityTopK`` walks the whole ranked list too. Sequentially
        that is hours.

        The endpoint is **free**, so the constraint is requests per minute (2k/4k/8k by
        usage tier, a pool separate from the Messages API) rather than spend — which
        makes concurrency the whole answer. Eight workers keeps a Start-tier account
        near its ceiling without leaning on the SDK's 429 retries; raise it on a higher
        tier. Writes are batched into a single flush at the end: the per-call flush in
        ``cost`` rewrites the whole JSON file each time, which is quadratic across 37k
        tools.
        """
        pending = []
        seen: set[str] = set()
        for tool in tools:
            key = self._key(tool)
            if key in self._cache or key in seen:
                continue
            seen.add(key)
            pending.append(tool)

        if not pending:
            return 0

        self._ensure_baseline()
        done = 0

        def measure(tool: Tool) -> tuple[str, int]:
            return self._key(tool), self._count([tool.as_api_tool()]) - self._baseline

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for key, measured in pool.map(measure, pending):
                with self._lock:
                    self._cache[key] = measured
                done += 1
                if on_progress is not None and done % 250 == 0:
                    on_progress(done, len(pending))

        self.flush()
        return done

    def flush(self) -> None:
        if self._cache_path is None:
            return
        with self._lock:
            payload = json.dumps(self._cache, indent=2, sort_keys=True)
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(payload)


class StaticTokenCounter:
    """Fixed costs, for tests and for replaying a measured cache offline."""

    def __init__(self, costs: dict[str, int], default: int = 100) -> None:
        self._costs = costs
        self._default = default

    def cost(self, tool: Tool) -> int:
        return self._costs.get(tool.uid, self._costs.get(tool.name, self._default))
