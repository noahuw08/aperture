"""What the gateway can know about a session before the user types anything.

This is the entire input to decision point A. There is no prompt at `tools/list`, so
whatever is captured here *is* the feature vector the point-A ranker gets — and anything
not captured is permanently unavailable to it, because a session cannot be re-observed.

**Why ``PWD`` and not ``os.getcwd()``.** The gateway is launched through a shim that runs
``uv --directory <repo>``, which sets the *process* working directory to the gateway's own
repo. ``PWD`` is inherited from the client's shell and still holds the directory the user
is actually working in. Measured: ``getcwd()`` gives the gateway repo, ``PWD`` gives the
client's project.

Every lookup is best-effort. A session with partial context is useful; a session that
failed to open because context capture raised is not.
"""

from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_GIT_TIMEOUT_SECONDS = 2.0


def _git(cwd: str, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def capture_environment(now: datetime | None = None) -> dict[str, str]:
    """Ambient signals available at session open.

    Returns only keys it could resolve — an absent key means "unknown", which is
    different from a key present with an empty value.
    """
    env: dict[str, str] = {}

    # The client's directory, not the gateway's. See the module docstring.
    # The launcher shim exports MCP_GATEWAY_CLIENT_CWD before anything can change it;
    # PWD is the fallback for a gateway started without the shim.
    project_dir = os.environ.get("MCP_GATEWAY_CLIENT_CWD") or os.environ.get("PWD")
    if project_dir:
        env["cwd"] = project_dir
        env["project"] = Path(project_dir).name

        toplevel = _git(project_dir, "rev-parse", "--show-toplevel")
        if toplevel:
            env["repo"] = Path(toplevel).name
            env["repo_path"] = toplevel
        # `branch --show-current` resolves an unborn HEAD (a repo with no commits
        # yet), where `rev-parse --abbrev-ref HEAD` fails outright. It returns empty
        # on a detached HEAD, which we record as unknown rather than as a sha.
        branch = _git(project_dir, "branch", "--show-current") or _git(
            project_dir, "rev-parse", "--abbrev-ref", "HEAD"
        )
        if branch:
            env["branch"] = branch

    stamp = now or datetime.now().astimezone()
    env["hour"] = f"{stamp.hour:02d}"
    env["weekday"] = stamp.strftime("%a")

    return env


def safe_capture_environment() -> dict[str, str]:
    """``capture_environment`` that can never take down ``tools/list``."""
    try:
        return capture_environment()
    except Exception:
        logger.exception("environment capture failed; continuing without it")
        return {}
