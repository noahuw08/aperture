import json

from mcp_gateway_router.bench.arms import build_arms, build_oracle_arm


def _base(tmp_path):
    path = tmp_path / "gateway.json"
    path.write_text(
        json.dumps(
            {
                "mode": "shadow",
                "arm": "passthrough",
                "budget_tokens": 3000,
                "log_dir": "runs",
                "pinned": [],
                "upstreams": [
                    {"server_id": "github", "command": "npx", "args": ["-y", "x"]}
                ],
            }
        )
    )
    return path


def _catalog(tmp_path, n=6):
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "server_id": "github",
                        "name": f"t{i}",
                        "description": "d",
                        "input_schema": {},
                    }
                    for i in range(n)
                ],
                "costs": {},
            }
        )
    )
    return path


def test_arm_a_advertises_the_whole_catalog(tmp_path):
    arms = build_arms(
        base_config=_base(tmp_path),
        catalog_path=_catalog(tmp_path),
        out_dir=tmp_path / "arms",
        budget_tokens=2000,
    )
    a = next(x for x in arms if x.name == "A-toolsearch")
    config = json.loads(a.config_path.read_text())

    # Shadow mode is what leaves the cut to the client's own tool search.
    assert config["mode"] == "shadow"
    assert config["pinned"] == []


def test_the_null_arm_cuts_at_the_budget(tmp_path):
    arms = build_arms(
        base_config=_base(tmp_path),
        catalog_path=_catalog(tmp_path),
        out_dir=tmp_path / "arms",
        budget_tokens=2000,
    )
    r = next(x for x in arms if x.name == "R-random")
    config = json.loads(r.config_path.read_text())

    assert config["mode"] == "live"
    assert config["budget_tokens"] == 2000
    assert len(config["pinned"]) == 6


def test_arms_inherit_upstreams_from_the_base_config(tmp_path):
    """An arm that proxies a different set of servers is not the same experiment."""
    arms = build_arms(
        base_config=_base(tmp_path),
        catalog_path=_catalog(tmp_path),
        out_dir=tmp_path / "arms",
        budget_tokens=2000,
    )
    for arm in arms:
        config = json.loads(arm.config_path.read_text())
        assert config["upstreams"] == [
            {"server_id": "github", "command": "npx", "args": ["-y", "x"]}
        ]


def test_each_arm_logs_to_its_own_directory(tmp_path):
    """A re-run of one arm must not contaminate another's records."""
    arms = build_arms(
        base_config=_base(tmp_path),
        catalog_path=_catalog(tmp_path),
        out_dir=tmp_path / "arms",
        budget_tokens=2000,
    )
    dirs = {json.loads(a.config_path.read_text())["log_dir"] for a in arms}

    assert len(dirs) == len(arms)


def test_the_null_arm_is_reproducible(tmp_path):
    kwargs = dict(
        base_config=_base(tmp_path),
        catalog_path=_catalog(tmp_path),
        budget_tokens=2000,
        seed=7,
    )
    first = build_arms(out_dir=tmp_path / "a", **kwargs)
    second = build_arms(out_dir=tmp_path / "b", **kwargs)

    pinned = [
        json.loads(next(x for x in arms if x.name == "R-random").config_path.read_text())["pinned"]
        for arms in (first, second)
    ]
    assert pinned[0] == pinned[1]


def test_the_oracle_pins_exactly_the_required_tools(tmp_path):
    arm = build_oracle_arm(
        base_config=_base(tmp_path),
        out_dir=tmp_path / "arms",
        task_id="find-prs",
        required=[["github", "list_pull_requests"]],
        budget_tokens=2000,
    )
    config = json.loads(arm.config_path.read_text())

    assert config["mode"] == "live"
    assert config["pinned"] == [["github", "list_pull_requests"]]
