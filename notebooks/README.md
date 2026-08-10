# Notebooks

Generated from the `build_*.py` scripts beside them, not hand-edited — a hand-maintained
notebook drifts from the code it documents and then teaches the wrong thing. Change the
builder, regenerate, re-execute.

| Notebook | Answers |
|---|---|
| `gateway_walkthrough.ipynb` | What the gateway intakes, shadow vs live, and why it cannot see Claude's tool search |
| `arms_walkthrough.ipynb` | How each Q1 arm differs, and A vs C head to head |
| `frontier_walkthrough.ipynb` | Phase 0 offline frontier (predates the gateway) |

## Running them

Select the **`mcp-gateway (.venv)`** kernel. The first cell checks for it and prints the
fix rather than failing later with a bare `ModuleNotFoundError`.

Set it up once:

```sh
uv sync --extra gateway --extra bench --extra dev
.venv/bin/python -m ipykernel install --user --name mcp-gateway --display-name "mcp-gateway (.venv)"
```

Headless:

```sh
set -a; . ./.env; set +a
uv run --with jupyter --with nbconvert --extra gateway --extra bench \
    jupyter nbconvert --to notebook --execute --inplace notebooks/<name>.ipynb
```

## Cost

`gateway_walkthrough` is free — it launches real gateways but never calls a model.

`arms_walkthrough` reads cached results from `results/arms_smoke.json` by default. Set
`RERUN = True` to re-measure, which spends real model usage.

## Regenerating

```sh
uv run --with nbformat python notebooks/build_gateway_walkthrough.py
uv run --with nbformat python notebooks/build_arms_walkthrough.py
```

Two escaping traps if you edit a builder: notebook cell source passes through a
triple-quoted Python string, so a backslash needs doubling and an `f"...\n..."` inside a
cell will terminate early. Prefer several `print()` calls over one multi-line f-string.
Always re-execute and check `execution_count` is non-null — a notebook can be regenerated
and silently left unexecuted.
