"""Gateway configuration.

Upstream set, mode and arm are *configuration*, never code. Every benchmark arm runs
the same binary; anything that differs between arms other than tool selection is a
confound.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

MODES = ("shadow", "live")
TRANSPORTS = ("stdio", "http")

_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _load_dotenv(path: Path) -> dict[str, str]:
    """Read ``KEY=value`` lines from a ``.env`` beside the config.

    Claude Code spawns the gateway as a subprocess, so relying on shell exports means
    "works in my terminal, not in the client". A file next to the config removes that
    whole class of problem. The real environment still wins, so CI and one-off overrides
    behave as expected.
    """
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _expand(value: str, where: str, extra_env: dict[str, str] | None = None) -> str:
    """Substitute ``${VAR}`` from the environment.

    Raises rather than leaving the placeholder in place. A config file is committed to
    the repo, so secrets have to come from the environment — and a header that silently
    ships the literal string ``Bearer ${TOKEN}`` fails as a confusing 401 much later
    instead of at load.
    """

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        resolved = os.environ.get(name)
        if resolved is None and extra_env is not None:
            resolved = extra_env.get(name)
        if resolved is None:
            raise ValueError(
                f"{where} references ${{{name}}}, which is not set "
                f"in the environment or in a .env beside the config"
            )
        return resolved

    return _PLACEHOLDER.sub(replace, value)

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
    # Harvested catalog with measured per-schema token costs. Produced by
    # ``harvest.py`` (control plane) and read by the gateway (data plane), so that
    # answering tools/list never requires a network call to measure costs.
    catalog_path: Path | None = None

    @classmethod
    def from_file(cls, path: Path) -> "GatewayConfig":
        path = Path(path)
        payload = json.loads(path.read_text())
        dotenv = _load_dotenv(path.parent / ".env")

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

            where = f"upstream {server_id!r}"
            upstreams.append(
                UpstreamSpec(
                    server_id=server_id,
                    transport=transport,
                    command=raw.get("command", ""),
                    args=tuple(raw.get("args", ())),
                    env={
                        k: _expand(v, f"{where} env {k!r}", dotenv)
                        for k, v in raw.get("env", {}).items()
                    },
                    url=_expand(raw.get("url", ""), f"{where} url", dotenv),
                    headers={
                        k: _expand(v, f"{where} header {k!r}", dotenv)
                        for k, v in raw.get("headers", {}).items()
                    },
                )
            )

        log_path = Path(payload.get("log_path", "runs/exposure.jsonl"))
        if not log_path.is_absolute():
            log_path = path.parent / log_path

        catalog_path = payload.get("catalog_path")
        if catalog_path is not None:
            catalog_path = Path(catalog_path)
            if not catalog_path.is_absolute():
                catalog_path = path.parent / catalog_path

        return cls(
            upstreams=tuple(upstreams),
            mode=mode,
            arm=payload.get("arm", "passthrough"),
            budget_tokens=int(payload.get("budget_tokens", 3000)),
            pinned=tuple(tuple(p) for p in payload.get("pinned", [])),
            log_path=log_path,
            model=payload.get("model", "claude-opus-5"),
            catalog_path=catalog_path,
        )
