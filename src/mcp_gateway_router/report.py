"""Render the frontier as a standalone HTML chart.

    uv run python -m mcp_gateway_router.report results/frontier.json

X is **mean tokens actually spent** on a log scale (the range spans 193 to 4.4M), Y is
task success. A selector that dominates sits up and to the left.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

# Categorical slots 1-5, validated for the adjacent pairlist in both modes
# (scripts/validate_palette.js: worst adjacent CVD ΔE 9.1 light / 8.4 dark).
SERIES = [
    ("expose-all", "#2a78d6", "#3987e5"),
    ("static-set", "#eb6834", "#d95926"),
    ("popularity", "#1baf7a", "#199e70"),
    ("semantic-retrieval", "#eda100", "#c98500"),
    ("progressive-disclosure", "#e87ba4", "#d55181"),
]
ORACLE = "oracle"  # the ceiling — a dashed neutral reference, not a peer series

TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tool-exposure frontier</title>
<style>
  .viz-root {
    color-scheme: light;
    --surface-1: #fcfcfb; --surface-2: #f0efec;
    --text-primary: #0b0b0b; --text-secondary: #52514e; --text-muted: #78766f;
    --grid: #e5e4e0; --ref: #9a9890;
__LIGHT_VARS__
  }
  @media (prefers-color-scheme: dark) {
    :root:where(:not([data-theme="light"])) .viz-root {
      color-scheme: dark;
      --surface-1: #1a1a19; --surface-2: #262625;
      --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #8f8e85;
      --grid: #33332f; --ref: #6d6c65;
__DARK_VARS__
    }
  }
  :root[data-theme="dark"] .viz-root {
    color-scheme: dark;
    --surface-1: #1a1a19; --surface-2: #262625;
    --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #8f8e85;
    --grid: #33332f; --ref: #6d6c65;
__DARK_VARS__
  }
  body { margin:0; background:var(--surface-1); }
  .viz-root {
    font: 14px/1.5 ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif;
    background: var(--surface-1); color: var(--text-primary);
    padding: 32px; max-width: 940px; margin: 0 auto;
  }
  h1 { font-size: 19px; margin: 0 0 4px; letter-spacing: -0.01em; }
  .sub { color: var(--text-secondary); margin: 0 0 4px; font-size: 13px; }
  .meta { color: var(--text-muted); margin: 0 0 20px; font-size: 12px; }
  .warn {
    border-left: 3px solid #eb6834; background: var(--surface-2);
    padding: 10px 14px; margin: 0 0 20px; font-size: 12.5px; color: var(--text-secondary);
    border-radius: 0 4px 4px 0;
  }
  .warn strong { color: var(--text-primary); }
  .controls { display:flex; gap:8px; align-items:center; margin-bottom:12px; }
  button {
    font: inherit; font-size: 12px; padding: 5px 11px; border-radius: 6px;
    border: 1px solid var(--grid); background: var(--surface-1);
    color: var(--text-secondary); cursor: pointer;
  }
  button:hover { color: var(--text-primary); }
  .wrap { position: relative; }
  svg { display: block; width: 100%; height: auto; overflow: visible; }
  .legend { display:flex; flex-wrap:wrap; gap:16px; margin:14px 0 0; font-size:12.5px; }
  .legend span { display:flex; align-items:center; gap:7px; color: var(--text-secondary); }
  .swatch { width:16px; height:2px; border-radius:1px; flex:none; }
  .tip {
    position:absolute; pointer-events:none; opacity:0; transition:opacity .1s;
    background:var(--surface-1); border:1px solid var(--grid); border-radius:7px;
    padding:9px 11px; font-size:12px; box-shadow:0 3px 14px rgba(0,0,0,.13);
    min-width:186px; z-index:5;
  }
  .tip h4 { margin:0 0 6px; font-size:11px; font-weight:600; color:var(--text-muted);
            text-transform:uppercase; letter-spacing:.05em; }
  .tip-row { display:flex; align-items:center; gap:7px; margin:3px 0; }
  .tip-key { width:12px; height:2px; border-radius:1px; flex:none; }
  .tip-val { font-variant-numeric:tabular-nums; font-weight:600; color:var(--text-primary); }
  .tip-name { color:var(--text-secondary); margin-left:auto; }
  table { border-collapse:collapse; width:100%; font-size:12.5px; margin-top:18px; }
  th, td { text-align:right; padding:6px 10px; border-bottom:1px solid var(--grid); }
  th:first-child, td:first-child { text-align:left; }
  th { color:var(--text-muted); font-weight:600; font-size:11px;
       text-transform:uppercase; letter-spacing:.05em; }
  td { font-variant-numeric:tabular-nums; color:var(--text-secondary); }
  [hidden] { display:none !important; }
</style></head>
<body><div class="viz-root">
  <h1>Tool-exposure frontier</h1>
  <p class="sub">Task success against tokens actually spent on tool schemas. Up and to the left is better.</p>
  <p class="meta" id="meta"></p>
  <div class="warn" id="caveat"></div>
  <div class="controls">
    <button id="toggle-table" aria-expanded="false">Show data table</button>
    <button id="toggle-theme">Toggle dark mode</button>
  </div>
  <div class="wrap" id="wrap">
    <svg id="chart" viewBox="0 0 900 460" role="img" aria-labelledby="chart-title">
      <title id="chart-title">Task success versus schema tokens spent, by selector</title>
    </svg>
    <div class="tip" id="tip"></div>
  </div>
  <div class="legend" id="legend"></div>
  <table id="table" hidden><thead><tr>
    <th>Selector</th><th>Budget</th><th>Tokens</th><th>Satisfied</th><th>Recall</th><th>Round trips</th>
  </tr></thead><tbody id="tbody"></tbody></table>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const RAW = JSON.parse(document.getElementById('data').textContent);
const SERIES = __SERIES__, ORACLE = __ORACLE__;
const P = RAW.points, M = RAW.meta;

document.getElementById('meta').textContent =
  `${M.catalog_size.toLocaleString()} tools · ${M.n_examples} eval examples ` +
  `(${M.n_fit} held out for fitting) · ToolRet ${M.tools_subset}/${M.queries_subset}`;
document.getElementById('caveat').innerHTML =
  '<strong>Smoke test, not a result.</strong> Scorer: ' + M.scorer + '. Token costs: ' +
  M.token_costs + '. The kill gate needs a bi-encoder and measured token costs; ' +
  'progressive disclosure still assumes its internal retrieval never fails.';

const W = 900, H = 460, L = 62, R = 132, T = 18, B = 52;
const iw = W - L - R, ih = H - T - B;
const xs = P.map(p => p.mean_tokens).filter(v => v > 0);
const lo = Math.log10(Math.min(...xs) * 0.75), hi = Math.log10(Math.max(...xs) * 1.4);
const X = v => L + (Math.log10(Math.max(v, 1)) - lo) / (hi - lo) * iw;
const Y = v => T + (1 - v) * ih;
const svg = document.getElementById('chart');
const ns = 'http://www.w3.org/2000/svg';
const el = (t, a) => { const n = document.createElementNS(ns, t);
  for (const k in a) n.setAttribute(k, a[k]); return n; };

// grid + axes (recessive)
for (let i = 0; i <= 5; i++) {
  const v = i / 5;
  svg.appendChild(el('line', {x1: L, x2: L + iw, y1: Y(v), y2: Y(v),
    stroke: 'var(--grid)', 'stroke-width': 1}));
  const t = el('text', {x: L - 10, y: Y(v) + 4, 'text-anchor': 'end',
    fill: 'var(--text-muted)', 'font-size': 11});
  t.textContent = (v * 100).toFixed(0) + '%'; svg.appendChild(t);
}
for (let e = Math.ceil(lo); e <= Math.floor(hi); e++) {
  const x = X(Math.pow(10, e));
  svg.appendChild(el('line', {x1: x, x2: x, y1: T, y2: T + ih,
    stroke: 'var(--grid)', 'stroke-width': 1}));
  const t = el('text', {x, y: T + ih + 20, 'text-anchor': 'middle',
    fill: 'var(--text-muted)', 'font-size': 11});
  t.textContent = e >= 6 ? (10 ** (e - 6)) + 'M' : e >= 3 ? (10 ** (e - 3)) + 'k' : 10 ** e;
  svg.appendChild(t);
}
const ax = el('text', {x: L + iw / 2, y: H - 8, 'text-anchor': 'middle',
  fill: 'var(--text-secondary)', 'font-size': 12});
ax.textContent = 'Mean schema tokens spent per request (log scale)';
svg.appendChild(ax);

// oracle: the ceiling, drawn as a dashed neutral reference rather than a peer series
const orc = P.filter(p => p.selector === ORACLE);
if (orc.length) {
  const ox = X(orc[0].mean_tokens);
  svg.appendChild(el('line', {x1: ox, x2: ox, y1: T, y2: T + ih, stroke: 'var(--ref)',
    'stroke-width': 1.5, 'stroke-dasharray': '4 4'}));
  const t = el('text', {x: ox + 6, y: T + 12, fill: 'var(--text-muted)', 'font-size': 11});
  t.textContent = 'oracle'; svg.appendChild(t);
}

const drawn = [];
for (const [name] of SERIES) {
  const pts = P.filter(p => p.selector === name)
               .map(p => ({x: X(p.mean_tokens), y: Y(p.satisfied), p}))
               .sort((a, b) => a.x - b.x);
  if (!pts.length) continue;
  const color = `var(--s-${name})`;
  if (pts.length > 1) {
    svg.appendChild(el('path', {d: 'M' + pts.map(q => `${q.x},${q.y}`).join('L'),
      fill: 'none', stroke: color, 'stroke-width': 2,
      'stroke-linejoin': 'round', 'stroke-linecap': 'round'}));
  }
  // 2px surface ring keeps overlapping markers legible
  for (const q of pts) {
    svg.appendChild(el('circle', {cx: q.x, cy: q.y, r: 5, fill: color,
      stroke: 'var(--surface-1)', 'stroke-width': 2}));
  }
  const last = pts[pts.length - 1];
  const lbl = el('text', {x: last.x + 10, y: last.y + 4, fill: 'var(--text-secondary)',
    'font-size': 11.5});
  lbl.textContent = name;             // text tokens, never the series color
  svg.appendChild(lbl);
  drawn.push({name, pts});
}

// legend — identity is never colour-alone
const lg = document.getElementById('legend');
for (const [name] of SERIES) {
  const s = document.createElement('span');
  const sw = document.createElement('i');
  sw.className = 'swatch'; sw.style.background = `var(--s-${name})`;
  s.appendChild(sw); s.appendChild(document.createTextNode(name));
  lg.appendChild(s);
}
const os = document.createElement('span');
const osw = document.createElement('i');
osw.className = 'swatch';
osw.style.background = 'repeating-linear-gradient(90deg,var(--ref) 0 4px,transparent 4px 8px)';
os.appendChild(osw); os.appendChild(document.createTextNode('oracle (ceiling)'));
lg.appendChild(os);

// crosshair + one tooltip listing every series at that X
const hair = el('line', {y1: T, y2: T + ih, stroke: 'var(--text-muted)',
  'stroke-width': 1, 'stroke-dasharray': '3 3', opacity: 0});
svg.appendChild(hair);
const tip = document.getElementById('tip'), wrap = document.getElementById('wrap');

function move(ev) {
  const r = svg.getBoundingClientRect();
  const sx = (ev.clientX - r.left) / r.width * W;
  if (sx < L || sx > L + iw) return hide();
  hair.setAttribute('x1', sx); hair.setAttribute('x2', sx);
  hair.setAttribute('opacity', 1);
  tip.replaceChildren();
  const h = document.createElement('h4');
  h.textContent = Math.round(Math.pow(10, lo + (sx - L) / iw * (hi - lo))).toLocaleString()
    + ' tokens';
  tip.appendChild(h);
  for (const s of drawn) {
    let best = s.pts[0];
    for (const q of s.pts) if (Math.abs(q.x - sx) < Math.abs(best.x - sx)) best = q;
    const row = document.createElement('div'); row.className = 'tip-row';
    const k = document.createElement('i');
    k.className = 'tip-key'; k.style.background = `var(--s-${s.name})`;
    const v = document.createElement('span');
    v.className = 'tip-val'; v.textContent = (best.p.satisfied * 100).toFixed(1) + '%';
    const n = document.createElement('span');
    n.className = 'tip-name'; n.textContent = s.name;   // untrusted → textContent
    row.append(k, v, n); tip.appendChild(row);
  }
  tip.style.opacity = 1;
  const left = Math.min(sx / W * wrap.clientWidth + 16, wrap.clientWidth - 200);
  tip.style.left = Math.max(0, left) + 'px';
  tip.style.top = '12px';
}
function hide() { tip.style.opacity = 0; hair.setAttribute('opacity', 0); }
svg.addEventListener('pointermove', move);
svg.addEventListener('pointerleave', hide);

// table view — the relief for light-mode slots below 3:1 contrast
const tb = document.getElementById('tbody');
for (const p of P) {
  const tr = document.createElement('tr');
  for (const v of [p.selector, p.budget.toLocaleString(),
                   Math.round(p.mean_tokens).toLocaleString(),
                   (p.satisfied * 100).toFixed(1) + '%',
                   (p.recall * 100).toFixed(1) + '%',
                   p.mean_round_trips.toFixed(2)]) {
    const td = document.createElement('td'); td.textContent = v; tr.appendChild(td);
  }
  tb.appendChild(tr);
}
const tbtn = document.getElementById('toggle-table'), tbl = document.getElementById('table');
tbtn.onclick = () => {
  const shown = !tbl.hidden;
  tbl.hidden = shown; tbtn.setAttribute('aria-expanded', String(!shown));
  tbtn.textContent = shown ? 'Show data table' : 'Hide data table';
};
document.getElementById('toggle-theme').onclick = () => {
  const dark = document.documentElement.getAttribute('data-theme') === 'dark';
  document.documentElement.setAttribute('data-theme', dark ? 'light' : 'dark');
};
</script></body></html>
"""


def render(results: dict) -> str:
    light = "\n".join(f"    --s-{n}: {lt};" for n, lt, _ in SERIES)
    dark = "\n".join(f"      --s-{n}: {dk};" for n, _, dk in SERIES)
    return (
        TEMPLATE.replace("__LIGHT_VARS__", light)
        .replace("__DARK_VARS__", dark)
        .replace("__DATA__", json.dumps(results))
        .replace("__SERIES__", json.dumps([[n] for n, _, _ in SERIES]))
        .replace("__ORACLE__", json.dumps(ORACLE))
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the frontier chart.")
    parser.add_argument("results", type=Path, nargs="?", default=Path("results/frontier.json"))
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    out = args.out or args.results.with_suffix(".html")
    out.write_text(render(json.loads(args.results.read_text())))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
