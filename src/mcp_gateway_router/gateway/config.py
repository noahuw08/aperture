"""Gateway configuration.

Upstream set, mode and arm are *configuration*, never code. Every benchmark arm runs
the same binary; anything that differs between arms other than tool selection is a
confound.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

MODES = ("shadow", "live")
TRANSPORTS = ("stdio", "http")

# Separates server id from tool name in the name advertised to the client. Server ids
# may not contain it, so a single split from the left always recovers the pair even
# when the tool's own name contains a double underscore.
NAMESPACE_SEP = "__"


@dataclass(frozen=True)
class UpstreamSpec:
    """How to reach one upstream MCP server.

    Two transports, because real installations use both — on this machine `playwright`
    is a stdio subprocess while `github` and `notion` are hosted HTTP endpoints. A
    stdio-only gateway would silently proxy a third of the catalog.
    """

    server_id: str
    transport: str = "stdio"
    # stdio
    command: str = ""
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict, hash=False, compare=True)
    # http
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict, hash=False, compare=True)


@dataclass(frozen=True)
class GatewayConfig:
    upstreams: tuple[UpstreamSpec, ...]
    mode: str
    arm: str
    budget_tokens: int
    pinned: tuple[tuple[str, str], ...]
    log_path: Path
    model: str = "claude-opus-5"

    @classmethod
    def from_file(cls, path: Path) -> "GatewayConfig":
        path = Path(path)
        payload = json.loads(path.read_text())

        mode = payload.get("mode", "shadow")
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")

        upstreams: list[UpstreamSpec] = []
        seen: set[str] = set()
        for raw in payload.get("upstreams", []):
            server_id = raw["server_id"]
            if NAMESPACE_SEP in server_id:
                raise ValueError(
                    f"server_id {server_id!r} may not contain {NAMESPACE_SEP!r}"
                )
            if server_id in seen:
                raise ValueError(f"duplicate server_id: {server_id!r}")
            seen.add(server_id)

            transport = raw.get("transport", "stdio")
            if transport not in TRANSPORTS:
                raise ValueError(
                    f"unknown transport {transport!r} for {server_id!r}; "
                    f"expected one of {TRANSPORTS}"
                )
            if transport == "stdio" and not raw.get("command"):
                raise ValueError(f"stdio upstream {server_id!r} needs a command")
            if transport == "http" and not raw.get("url"):
                raise ValueError(f"http upstream {server_id!r} needs a url")

            upstreams.append(
                UpstreamSpec(
                    server_id=server_id,
                    transport=transport,
                    command=raw.get("command", ""),
                    args=tuple(raw.get("args", ())),
                    env=dict(raw.get("env", {})),
                    url=raw.get("url", ""),
                    headers=dict(raw.get("headers", {})),
                )
            )

        log_path = Path(payload.get("log_path", "runs/exposure.jsonl"))
        if not log_path.is_absolute():
            log_path = path.parent / log_path

        return cls(
            upstreams=tuple(upstreams),
            mode=mode,
            arm=payload.get("arm", "passthrough"),
            budget_tokens=int(payload.get("budget_tokens", 3000)),
            pinned=tuple(tuple(p) for p in payload.get("pinned", [])),
            log_path=log_path,
            model=payload.get("model", "claude-opus-5"),
        )
