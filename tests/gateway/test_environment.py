"""Point-A context capture.

Anything missing here is permanently unavailable to the point-A ranker — a session
cannot be re-observed after the fact.
"""

import subprocess
from datetime import datetime

from mcp_gateway_router.gateway.environment import (
    capture_environment,
    safe_capture_environment,
)


def test_project_comes_from_the_shim_not_the_process_cwd(tmp_path, monkeypatch):
    """The shim runs `uv --directory <repo>`, so getcwd() is the gateway's own repo."""
    project = tmp_path / "some-project"
    project.mkdir()
    monkeypatch.setenv("MCP_GATEWAY_CLIENT_CWD", str(project))
    monkeypatch.delenv("PWD", raising=False)
    monkeypatch.chdir(tmp_path)  # process cwd deliberately different

    env = capture_environment()

    assert env["cwd"] == str(project)
    assert env["project"] == "some-project"


def test_the_shim_variable_wins_over_pwd(tmp_path, monkeypatch):
    """bash overwrites PWD with its own cwd, so the explicit variable is authoritative."""
    real = tmp_path / "real-project"
    real.mkdir()
    stale = tmp_path / "stale"
    stale.mkdir()
    monkeypatch.setenv("MCP_GATEWAY_CLIENT_CWD", str(real))
    monkeypatch.setenv("PWD", str(stale))

    assert capture_environment()["project"] == "real-project"


def test_pwd_is_the_fallback_without_the_shim(tmp_path, monkeypatch):
    project = tmp_path / "via-pwd"
    project.mkdir()
    monkeypatch.delenv("MCP_GATEWAY_CLIENT_CWD", raising=False)
    monkeypatch.setenv("PWD", str(project))

    assert capture_environment()["project"] == "via-pwd"


def test_git_repo_and_branch_are_captured(tmp_path, monkeypatch):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "trunk", str(repo)], check=True)
    monkeypatch.setenv("MCP_GATEWAY_CLIENT_CWD", str(repo))

    env = capture_environment()

    assert env["repo"] == "myrepo"
    assert env["branch"] == "trunk"


def test_a_non_git_directory_yields_no_repo_keys(tmp_path, monkeypatch):
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setenv("MCP_GATEWAY_CLIENT_CWD", str(plain))

    env = capture_environment()

    assert env["project"] == "plain"
    assert "repo" not in env
    assert "branch" not in env


def test_time_signals_are_always_present(tmp_path, monkeypatch):
    monkeypatch.delenv("PWD", raising=False)
    monkeypatch.delenv("MCP_GATEWAY_CLIENT_CWD", raising=False)

    env = capture_environment(now=datetime(2026, 8, 9, 14, 30))

    assert env["hour"] == "14"
    assert env["weekday"] == "Sun"


def test_absent_pwd_yields_no_project_keys(monkeypatch):
    monkeypatch.delenv("PWD", raising=False)
    monkeypatch.delenv("MCP_GATEWAY_CLIENT_CWD", raising=False)

    env = capture_environment()

    assert "cwd" not in env
    assert "project" not in env


def test_capture_never_raises(monkeypatch):
    import mcp_gateway_router.gateway.environment as mod

    def explode(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(mod, "capture_environment", explode)

    assert safe_capture_environment() == {}
