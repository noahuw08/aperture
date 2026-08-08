import json

import pytest

from mcp_gateway_router.gateway.config import GatewayConfig, UpstreamSpec


def _payload(**overrides):
    base = {
        "mode": "shadow",
        "arm": "passthrough",
        "budget_tokens": 3000,
        "log_path": "runs/exposure.jsonl",
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


def test_log_path_is_resolved_relative_to_the_config_file(tmp_path):
    path = tmp_path / "gateway.json"
    path.write_text(json.dumps(_payload()))

    config = GatewayConfig.from_file(path)

    assert config.log_path == tmp_path / "runs" / "exposure.jsonl"


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
