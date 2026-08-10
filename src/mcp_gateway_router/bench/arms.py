"""The Q1 arms.

An arm is a **gateway config plus nothing else**. Every arm runs the same binary, the
same prompt, the same model; the only thing that differs is which tools reach the client.
Anything else that varies between arms is a confound.

Arm files are generated from the harvested catalog rather than hand-written, so a change
to the catalog cannot silently desynchronise them from what the gateway actually serves.

**What Gate 0 compares.** `A` is the incumbent: hand the client everything and let
Claude's own tool search do the cut. `C` is ours: hand it a pre-selected set at a matched
budget. `R` and `O` bound the scale — if a real arm cannot separate from `R`, the harness
is broken; `O` says what perfect selection would have achieved.

⚠️ **Measured on this client, tool search defers schemas at near-zero prefix cost.** So
`A` is not merely a cheaper competitor, it is close to free, and `C` has to justify
spending any budget at all. The arms below make that comparison possible; they do not
make it winnable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..harvest import catalog_from_json


@dataclass(frozen=True)
class Arm:
    name: str
    description: str
    config_path: Path
    #: None means "no cut" — the client receives the whole catalog.
    budget_tokens: int | None


def _write_config(
    path: Path,
    *,
    base: dict,
    arm: str,
    mode: str,
    budget_tokens: int,
    pinned: list[list[str]],
) -> None:
    config = dict(base)
    config.update(
        {
            "arm": arm,
            "mode": mode,
            "budget_tokens": budget_tokens,
            "pinned": pinned,
            # Each arm writes to its own directory so a run is trivially separable and
            # a re-run of one arm cannot contaminate another's records.
            "log_dir": f"runs/bench/{arm}",
        }
    )
    path.write_text(json.dumps(config, indent=2) + "\n")


def build_arms(
    *,
    base_config: Path,
    catalog_path: Path,
    out_dir: Path,
    budget_tokens: int,
    oracle_tools: dict[str, list[list[str]]] | None = None,
    seed: int = 0,
) -> list[Arm]:
    """Materialise one gateway config per arm.

    ``oracle_tools`` maps task id -> required tool keys. The oracle is necessarily
    per-task, so it cannot share a single config with the others; callers that want it
    generate a config per task.
    """
    import random

    base = json.loads(Path(base_config).read_text())
    catalog = catalog_from_json(json.loads(Path(catalog_path).read_text()))
    all_keys = [[t.server_id, t.name] for t in catalog]

    out_dir.mkdir(parents=True, exist_ok=True)
    arms: list[Arm] = []

    # A — the incumbent. Shadow mode advertises the entire catalog, so the client's own
    # tool search performs the cut. This is the arm we have to beat.
    path = out_dir / "arm_A.json"
    _write_config(path, base=base, arm="A-toolsearch", mode="shadow",
                  budget_tokens=budget_tokens, pinned=[])
    arms.append(Arm("A-toolsearch", "full catalog; Claude's tool search cuts", path, None))

    # R — the null control. A random draw at the same budget. If a real arm cannot beat
    # this, the harness is broken rather than the world.
    rng = random.Random(seed)
    sample = all_keys[:]
    rng.shuffle(sample)
    path = out_dir / "arm_R.json"
    _write_config(path, base=base, arm="R-random", mode="live",
                  budget_tokens=budget_tokens, pinned=sample)
    arms.append(Arm("R-random", "random draw at the budget", path, budget_tokens))

    return arms


def build_oracle_arm(
    *,
    base_config: Path,
    out_dir: Path,
    task_id: str,
    required: list[list[str]],
    budget_tokens: int,
) -> Arm:
    """Exactly the tools this task needs, and nothing else.

    ⚠️ Reads the future by construction — it is a ceiling, never a candidate. A small
    gap between the oracle and a real arm is decisive; a large one only means the
    headroom exists, not that anything can reach it.
    """
    base = json.loads(Path(base_config).read_text())
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"arm_O_{task_id}.json"
    _write_config(path, base=base, arm=f"O-oracle/{task_id}", mode="live",
                  budget_tokens=budget_tokens, pinned=required)
    return Arm(f"O-oracle/{task_id}", "exactly the required tools", path, budget_tokens)
