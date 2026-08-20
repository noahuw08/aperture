import json

import pytest

from mcp_gateway_router.gateway.config import GATEWAY_SERVER_ID, GatewayConfig, UpstreamSpec


def _payload(**overrides):
    base = {
        "mode": "shadow",
        "arm": "passthrough",
        "budget_tokens": 3000,
        "log_dir": "runs",
        "pinned": [["github", "search_code"]],
        "upstreams": [
            {"server_id": "github", "command": "npx", "args": ["-y", "gh-mcp"]},
        ],
    }
    base.update(overrides)
    return base


def test_from_file_parses_upstreams_and_pinned(tmp_path):
    path = tmp_path / "gateway.json"
    path.write_text(json.dumps(_payload()))

    config = GatewayConfig.from_file(path)

    assert config.mode == "shadow"
    assert config.budget_tokens == 3000
    assert config.pinned == (("github", "search_code"),)
    assert config.upstreams == (
        UpstreamSpec(server_id="github", command="npx", args=("-y", "gh-mcp"), env={}),
    )


def test_log_dir_is_resolved_relative_to_the_config_file(tmp_path):
    path = tmp_path / "gateway.json"
    path.write_text(json.dumps(_payload()))

    config = GatewayConfig.from_file(path)

    assert config.log_dir == tmp_path / "runs"


def test_unknown_mode_is_rejected(tmp_path):
    path = tmp_path / "gateway.json"
    path.write_text(json.dumps(_payload(mode="turbo")))

    with pytest.raises(ValueError, match="turbo"):
        GatewayConfig.from_file(path)


def test_server_id_containing_the_namespace_separator_is_rejected(tmp_path):
    path = tmp_path / "gateway.json"
    path.write_text(
        json.dumps(
            _payload(upstreams=[{"server_id": "git__hub", "command": "npx", "args": []}])
        )
    )

    with pytest.raises(ValueError, match="__"):
        GatewayConfig.from_file(path)


def test_http_upstream_is_parsed(tmp_path):
    path = tmp_path / "gateway.json"
    path.write_text(
        json.dumps(
            _payload(
                upstreams=[
                    {
                        "server_id": "notion",
                        "transport": "http",
                        "url": "https://mcp.notion.com/mcp",
                        "headers": {"Authorization": "Bearer x"},
                    }
                ]
            )
        )
    )

    config = GatewayConfig.from_file(path)
    (upstream,) = config.upstreams

    assert upstream.transport == "http"
    assert upstream.url == "https://mcp.notion.com/mcp"
    assert upstream.headers == {"Authorization": "Bearer x"}


def test_upstreams_default_to_stdio(tmp_path):
    path = tmp_path / "gateway.json"
    path.write_text(json.dumps(_payload()))

    (upstream,) = GatewayConfig.from_file(path).upstreams

    assert upstream.transport == "stdio"


def test_stdio_upstream_without_a_command_is_rejected(tmp_path):
    path = tmp_path / "gateway.json"
    path.write_text(json.dumps(_payload(upstreams=[{"server_id": "github"}])))

    with pytest.raises(ValueError, match="command"):
        GatewayConfig.from_file(path)


def test_http_upstream_without_a_url_is_rejected(tmp_path):
    path = tmp_path / "gateway.json"
    path.write_text(
        json.dumps(_payload(upstreams=[{"server_id": "notion", "transport": "http"}]))
    )

    with pytest.raises(ValueError, match="url"):
        GatewayConfig.from_file(path)


def test_unknown_transport_is_rejected(tmp_path):
    path = tmp_path / "gateway.json"
    path.write_text(
        json.dumps(
            _payload(upstreams=[{"server_id": "x", "transport": "carrier-pigeon"}])
        )
    )

    with pytest.raises(ValueError, match="carrier-pigeon"):
        GatewayConfig.from_file(path)


def test_env_placeholders_in_headers_are_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("GW_TEST_TOKEN", "secret-value")
    path = tmp_path / "gateway.json"
    path.write_text(
        json.dumps(
            _payload(
                upstreams=[
                    {
                        "server_id": "github",
                        "transport": "http",
                        "url": "https://example.test/mcp",
                        "headers": {"Authorization": "Bearer ${GW_TEST_TOKEN}"},
                    }
                ]
            )
        )
    )

    (upstream,) = GatewayConfig.from_file(path).upstreams

    assert upstream.headers == {"Authorization": "Bearer secret-value"}


def test_a_missing_env_placeholder_is_loud_not_literal(tmp_path, monkeypatch):
    monkeypatch.delenv("GW_ABSENT_TOKEN", raising=False)
    path = tmp_path / "gateway.json"
    path.write_text(
        json.dumps(
            _payload(
                upstreams=[
                    {
                        "server_id": "github",
                        "transport": "http",
                        "url": "https://example.test/mcp",
                        "headers": {"Authorization": "Bearer ${GW_ABSENT_TOKEN}"},
                    }
                ]
            )
        )
    )

    with pytest.raises(ValueError, match="GW_ABSENT_TOKEN"):
        GatewayConfig.from_file(path)


def test_env_placeholders_are_expanded_in_stdio_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GW_TEST_TOKEN", "abc")
    path = tmp_path / "gateway.json"
    path.write_text(
        json.dumps(
            _payload(
                upstreams=[
                    {
                        "server_id": "github",
                        "command": "npx",
                        "env": {"API_KEY": "${GW_TEST_TOKEN}"},
                    }
                ]
            )
        )
    )

    (upstream,) = GatewayConfig.from_file(path).upstreams

    assert upstream.env == {"API_KEY": "abc"}


def test_a_dotenv_beside_the_config_supplies_placeholders(tmp_path, monkeypatch):
    monkeypatch.delenv("GW_DOTENV_TOKEN", raising=False)
    (tmp_path / ".env").write_text(
        "# a comment\n"
        "\n"
        'GW_DOTENV_TOKEN="from-dotenv"\n'
        "GW_UNUSED=whatever\n"
    )
    path = tmp_path / "gateway.json"
    path.write_text(
        json.dumps(
            _payload(
                upstreams=[
                    {
                        "server_id": "notion",
                        "transport": "http",
                        "url": "https://example.test/mcp",
                        "headers": {"Authorization": "Bearer ${GW_DOTENV_TOKEN}"},
                    }
                ]
            )
        )
    )

    (upstream,) = GatewayConfig.from_file(path).upstreams

    assert upstream.headers == {"Authorization": "Bearer from-dotenv"}


def test_the_real_environment_wins_over_the_dotenv(tmp_path, monkeypatch):
    monkeypatch.setenv("GW_DOTENV_TOKEN", "from-environment")
    (tmp_path / ".env").write_text("GW_DOTENV_TOKEN=from-dotenv\n")
    path = tmp_path / "gateway.json"
    path.write_text(
        json.dumps(
            _payload(
                upstreams=[
                    {
                        "server_id": "notion",
                        "transport": "http",
                        "url": "https://example.test/mcp",
                        "headers": {"Authorization": "Bearer ${GW_DOTENV_TOKEN}"},
                    }
                ]
            )
        )
    )

    (upstream,) = GatewayConfig.from_file(path).upstreams

    assert upstream.headers == {"Authorization": "Bearer from-environment"}


def test_duplicate_server_ids_are_rejected(tmp_path):
    path = tmp_path / "gateway.json"
    path.write_text(
        json.dumps(
            _payload(
                upstreams=[
                    {"server_id": "github", "command": "a", "args": []},
                    {"server_id": "github", "command": "b", "args": []},
                ]
            )
        )
    )

    with pytest.raises(ValueError, match="duplicate"):
        GatewayConfig.from_file(path)


def _write(tmp_path, payload):
    path = tmp_path / "gateway.json"
    path.write_text(json.dumps(payload))
    return path


def test_find_tools_is_disabled_by_default(tmp_path):
    """Every existing arm and all 28 collected sessions must behave identically."""
    path = _write(tmp_path, {"upstreams": [{"server_id": "github", "command": "x"}]})

    config = GatewayConfig.from_file(path)

    assert config.find_tools_enabled is False
    assert config.find_tools_k == 5


def test_find_tools_block_is_read(tmp_path):
    path = _write(
        tmp_path,
        {
            "upstreams": [{"server_id": "github", "command": "x"}],
            "find_tools": {"enabled": True, "k": 3},
        },
    )

    config = GatewayConfig.from_file(path)

    assert config.find_tools_enabled is True
    assert config.find_tools_k == 3


def test_an_upstream_may_not_claim_the_gateway_namespace(tmp_path):
    """`_gateway` addresses the meta-tool. An upstream with that id would shadow it,
    and calls meant for find_tools would route to a real server."""
    path = _write(
        tmp_path,
        {"upstreams": [{"server_id": GATEWAY_SERVER_ID, "command": "x"}]},
    )

    with pytest.raises(ValueError, match=GATEWAY_SERVER_ID):
        GatewayConfig.from_file(path)
