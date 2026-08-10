# Diagrams

Source for the mermaid diagrams in the Notion doc (*🧭 How it actually fits together*).
Kept here so they are versioned alongside the code they describe.

- `runtime.mmd` — one gateway per session: what it intakes, shadow vs live, what it logs.
- `eval.mmd` — what the collected log feeds: Q2 replay (free) and Q1 bench (model runs).

Validate before publishing — a broken diagram renders as an error box in Notion with no
other warning:

```sh
npx -y @mermaid-js/mermaid-cli -i docs/diagrams/runtime.mmd -o /tmp/runtime.svg
grep -q "Syntax error" /tmp/runtime.svg && echo BROKEN || echo OK
```

Note `.error-icon` appears in mermaid's boilerplate CSS and is a false positive — match
on `Syntax error`, not on the class name.
