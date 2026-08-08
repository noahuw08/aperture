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
