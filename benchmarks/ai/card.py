"""Kosika AI's scorecard as a picture: runs side by side.

    python benchmarks/ai/card.py results/A.json results/B.json ... > card.html

Each run is a column: the score, then speed, model calls and honesty, and
a row of dots for the asks (green passed, red failed, amber an overclaim:
the agent said it passed but the judge found it did not). Numbers come
only from the results files that run.py wrote.
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path


def load(path: str) -> dict:
    run = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = run.get("rows") or []
    run.setdefault("label", Path(path).stem)
    run["label"] = run["label"] or Path(path).stem
    times = sorted(r.get("seconds", 0) for r in rows) or [0]
    run.setdefault("median_seconds", times[len(times) // 2])
    run.setdefault("model_calls", sum(r.get("model_calls", 0) for r in rows))
    run.setdefault("overclaims", sum(bool(r.get("overclaim")) for r in rows))
    run.setdefault("web_reads", sum(r.get("web_reads", 0) for r in rows))
    return run


def bar(value: float, most: float, colour: str) -> str:
    width = 0 if most <= 0 else max(2.0, 100 * value / most)
    return f'<div class="track"><div class="fill" style="width:{width:.0f}%;background:{colour}"></div></div>'


def card(runs: list[dict]) -> str:
    most_time = max(r["median_seconds"] for r in runs) or 1
    most_calls = max(r["model_calls"] for r in runs) or 1
    columns = []
    for r in runs:
        dots = []
        for row in r.get("rows") or []:
            state = "over" if row.get("overclaim") else ("pass" if row.get("passed") else "fail")
            tip = f"{row['id']} ({row.get('kind', '')}): {state}, {row.get('seconds', 0)} s, {row.get('model_calls', 0)} calls"
            if row.get("why"):
                tip += " - " + row["why"]
            dots.append(f'<span class="dot {state}" title="{html.escape(tip)}"></span>')
        kinds = " · ".join(f"{k} {v}" for k, v in (r.get("by_kind") or {}).items())
        columns.append(f"""
<section class="run">
  <h2>{html.escape(str(r['label']))}</h2>
  <div class="score">{r.get('score', 0)}<span>/{r.get('of', 0)}</span></div>
  <p class="kinds">{html.escape(kinds)}</p>
  <h3>Median seconds an ask <b>{r['median_seconds']}</b></h3>{bar(r['median_seconds'], most_time, 'var(--time)')}
  <h3>Model calls <b>{r['model_calls']}</b></h3>{bar(r['model_calls'], most_calls, 'var(--calls)')}
  <h3>Overclaims <b>{r['overclaims']}</b></h3>
  <h3>Pages read from the web <b>{r['web_reads']}</b></h3>
  <div class="dots">{''.join(dots)}</div>
</section>""")
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kosika AI scorecard</title><style>
:root{{--bg:#f7f7f8;--card:#fff;--ink:#16161d;--soft:#6b6b78;--line:#e4e4ea;--pass:#1f9d55;--fail:#d64545;--over:#e0a100;--time:#4f46e5;--calls:#0ea5e9}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f0f17;--card:#191926;--ink:#ececf4;--soft:#9a9ab0;--line:#2a2a3c}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,sans-serif;padding:24px 16px}}
h1{{font-size:20px;margin:0 0 4px}} .sub{{color:var(--soft);margin:0 0 20px}}
.runs{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}}
.run{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}}
h2{{font-size:15px;margin:0;color:var(--soft);font-weight:600}} .score{{font-size:44px;font-weight:700}} .score span{{font-size:20px;color:var(--soft)}}
.kinds{{color:var(--soft);font-size:13px;margin:0 0 12px}} h3{{font-size:13px;font-weight:500;margin:12px 0 4px;display:flex;justify-content:space-between}}
.track{{height:8px;background:var(--line);border-radius:4px;overflow:hidden}} .fill{{height:100%;border-radius:4px}}
.dots{{display:flex;flex-wrap:wrap;gap:5px;margin-top:14px}} .dot{{width:13px;height:13px;border-radius:50%}}
.pass{{background:var(--pass)}} .fail{{background:var(--fail)}} .over{{background:var(--over)}}
</style></head><body><h1>Kosika AI scorecard</h1>
<p class="sub">20 real asks, judged by code, not by the agent. Dots: green passed, red failed, amber an overclaim. Hover a dot for the ask.</p>
<div class="runs">{''.join(columns)}</div></body></html>"""


if __name__ == "__main__":
    sys.stdout.write(card([load(p) for p in sys.argv[1:]]))
