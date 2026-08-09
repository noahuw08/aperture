"""Append-only exposure log.

A gateway is the single choke point that sees what it exposed, what got called, and
what came back. This is that record.

Three fields exist for phases that have not been built yet and are **not
retrofittable** — their absence cannot be reconstructed from later data:

``propensity``            the probability this tool was exposed on this decision. 1.0
                          for every deterministic arm; recorded anyway, because it is
                          the field's absence that cannot be undone, not its value.
``was_exposed``           whether a called tool was in the advertised set. The only
                          direct evidence of demand for tools we are not serving.
``cached_input_tokens``   prompt-cache behaviour, and therefore the invalidation cost
                          of changing the tool list.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ExposedTool:
    tool_uid: str
    score: float
    propensity: float
    # None when the cost could not be measured. Never a placeholder number — an
    # unmeasured cost must not be mistakable for a measured one downstream.
    token_cost: int | None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExposureLog:
    """One JSONL file, opened in append mode, flushed per record.

    Flushing every record costs throughput and buys the thing that matters here: a
    crashed or killed session still leaves a readable log.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._path.open("a", encoding="utf-8")

    def _write(self, record: dict[str, Any]) -> None:
        self._handle.write(json.dumps(record, sort_keys=True) + "\n")
        self._handle.flush()

    def decision(
        self,
        *,
        session_id: str,
        arm: str,
        decision_point: str,
        catalog_hash: str,
        selector_version: str,
        budget_tokens: int,
        context: dict[str, Any],
        n_candidates: int,
        exposed: list[ExposedTool],
    ) -> None:
        self._write(
            {
                "kind": "decision",
                "ts": _now(),
                "session_id": session_id,
                "arm": arm,
                "decision_point": decision_point,
                "catalog_hash": catalog_hash,
                "selector_version": selector_version,
                "budget_tokens": budget_tokens,
                "context": context,
                "n_candidates": n_candidates,
                "exposed": [asdict(e) for e in exposed],
            }
        )

    def call(
        self,
        *,
        session_id: str,
        tool_uid: str,
        was_exposed: bool,
        status: str,
        latency_ms: int,
    ) -> None:
        self._write(
            {
                "kind": "call",
                "ts": _now(),
                "session_id": session_id,
                "tool_uid": tool_uid,
                "was_exposed": was_exposed,
                "status": status,
                "latency_ms": latency_ms,
            }
        )

    def turn(
        self,
        *,
        session_id: str,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
    ) -> None:
        self._write(
            {
                "kind": "turn",
                "ts": _now(),
                "session_id": session_id,
                "input_tokens": input_tokens,
                "cached_input_tokens": cached_input_tokens,
                "output_tokens": output_tokens,
            }
        )

    def close(self) -> None:
        self._handle.close()
