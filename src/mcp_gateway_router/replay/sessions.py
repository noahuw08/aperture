"""Reconstruct sessions from the exposure log.

One session is: when it opened, the ambient context at that moment, and the ordered
tools it went on to call. That triple is the whole substrate for point-A evaluation —
predict the third from the second, using only sessions whose first element is earlier.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

ToolKey = tuple[str, str]


@dataclass
class SessionRecord:
    session_id: str
    opened_at: datetime
    environment: dict[str, str] = field(default_factory=dict)
    # Ordered and *not* deduplicated: how often a tool is used within a session is
    # signal, and collapsing to a set throws it away.
    called: tuple[ToolKey, ...] = ()
    n_candidates: int = 0

    @property
    def distinct_called(self) -> frozenset[ToolKey]:
        return frozenset(self.called)


def _parse_ts(raw: str) -> datetime:
    stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def _parse_tool_uid(uid: str) -> ToolKey | None:
    """``server/tool`` as written by the gateway. Split once — tool names may contain ``/``."""
    server_id, separator, name = uid.partition("/")
    if not separator or not server_id or not name:
        logger.warning("unparseable tool_uid %r", uid)
        return None
    return (server_id, name)


def load_sessions(path: Path) -> list[SessionRecord]:
    """Read an exposure log into chronologically ordered sessions.

    ``path`` may be a directory of per-session ``*.jsonl`` files (how the gateway writes
    them) or a single file (older single-file logs, and test fixtures).

    Tolerant by design: a malformed or truncated line is skipped rather than fatal. The
    log is append-only and flushed per record, but a process killed mid-write can still
    leave a partial final line, and losing a week of collection to one bad byte would be
    a poor trade.
    """
    path = Path(path)
    files = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]

    sessions: dict[str, SessionRecord] = {}
    calls: dict[str, list[ToolKey]] = {}

    for file in files:
        _absorb(file, sessions, calls)

    for session_id, keys in calls.items():
        sessions[session_id].called = tuple(keys)

    return sorted(sessions.values(), key=lambda s: (s.opened_at, s.session_id))


def _absorb(
    path: Path,
    sessions: dict[str, SessionRecord],
    calls: dict[str, list[ToolKey]],
) -> None:
    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("skipping malformed line %d of %s", lineno, path)
            continue

        session_id = record.get("session_id")
        if not session_id:
            continue
        kind = record.get("kind")

        if kind == "decision":
            existing = sessions.get(session_id)
            opened_at = _parse_ts(record["ts"])
            # tools/list can fire more than once; the session opened at the first.
            if existing is None:
                sessions[session_id] = SessionRecord(
                    session_id=session_id,
                    opened_at=opened_at,
                    environment=dict(record.get("context", {}).get("environment") or {}),
                    n_candidates=record.get("n_candidates", 0),
                )
            elif opened_at < existing.opened_at:
                existing.opened_at = opened_at

        elif kind == "call":
            key = _parse_tool_uid(record.get("tool_uid", ""))
            if key is None:
                continue
            calls.setdefault(session_id, []).append(key)
            # A crash before the first decision flush must not lose the calls.
            if session_id not in sessions:
                sessions[session_id] = SessionRecord(
                    session_id=session_id, opened_at=_parse_ts(record["ts"])
                )
