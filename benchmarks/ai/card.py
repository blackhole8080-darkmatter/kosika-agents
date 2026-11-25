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
    <div class="dots">{''.join(dots)}</div>
</section>""")
    asks = [row["id"] for row in (runs[0].get("rows") or [])]
    kinds = {row["id"]: row.get("kind", "") for r in runs for row in (r.get("rows") or [])}
    table = []
    for ask in asks:
        cells = []
        for r in runs:
            row = next((x for x in r.get("rows") or [] if x["id"] == ask), None)
            if row is None:
                cells.append("<td></td>")
                continue
            state = "over" if row.get("overclaim") else ("pass" if row.get("passed") else "fail")
            word = {"pass": "passed", "fail": "failed", "over": "overclaim"}[state]
            tip = html.escape(row.get("why") or "")
            cells.append(f'<td title="{tip}"><span class="pill {state}">{word}</span>'
                         f'<span class="num">{row.get("seconds", 0)} s · {row.get("model_calls", 0)} calls</span></td>')
        table.append(f'<tr><th scope="row">{html.escape(ask)}<small>{html.escape(kinds.get(ask, ""))}</small></th>{"".join(cells)}</tr>')
    heads = "".join(f"<th scope=\"col\">{html.escape(str(r['label']))}</th>" for r in runs)
    return f"""<title>Kosika AI Scorecard</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400&display=swap">
<style>
/* layout: one row of run cards (wrapping), then an ask-by-run table */
:root{{--bg:#f6f6fb;--card:#ffffff;--ink:#17172b;--soft:#62627a;--line:#e2e2ee;--pass:#16834a;--fail:#c93838;--over:#b07800;--time:#4f46e5;--calls:#d08a00;
--display:"Space Grotesk",system-ui,sans-serif;--body:Inter,system-ui,sans-serif;--mono:"JetBrains Mono",ui-monospace,monospace}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#0e0e1c;--card:#17172a;--ink:#ececf6;--soft:#9d9db6;--line:#2a2a44;--pass:#3fbf78;--fail:#ef6a6a;--over:#f0b429;--time:#8f88ff;--calls:#f5b638;color-scheme:dark}}}}
:root[data-theme="dark"]{{--bg:#0e0e1c;--card:#17172a;--ink:#ececf6;--soft:#9d9db6;--line:#2a2a44;--pass:#3fbf78;--fail:#ef6a6a;--over:#f0b429;--time:#8f88ff;--calls:#f5b638;color-scheme:dark}}
body{{background:var(--bg);color:var(--ink);font:15px/1.5 var(--body);padding-block:28px 40px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}}
main{{max-width:1080px;margin:0 auto;display:grid;gap:22px}}
h1{{font:700 26px/1.2 var(--display);margin:0;text-wrap:balance}} .sub{{color:var(--soft);margin:6px 0 0;max-width:70ch}}
.runs{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px}}
.run{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;display:grid;gap:6px;min-width:0}}
h2{{font:600 13px/1.3 var(--body);margin:0;color:var(--soft);letter-spacing:.02em}}
.score{{font:700 44px/1 var(--display);font-variant-numeric:tabular-nums}} .score span{{font-size:20px;color:var(--soft)}}
.kinds{{color:var(--soft);font-size:13px;margin:0 0 6px}}
h3{{font:500 13px/1.3 var(--body);margin:6px 0 0;display:flex;justify-content:space-between}} h3 b{{font-variant-numeric:tabular-nums}}
.track{{height:8px;background:var(--line);border-radius:4px;overflow:hidden}} .fill{{height:100%;border-radius:4px}}
.dots{{display:flex;flex-wrap:wrap;gap:5px;margin-top:10px}} .dot{{width:13px;height:13px;border-radius:50%}}
.pass{{background:var(--pass)}} .fail{{background:var(--fail)}} .over{{background:var(--over)}}
.wrap{{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:12px}}
table{{border-collapse:collapse;width:100%;min-width:620px;font-size:13px}}
th,td{{padding:7px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle}}
thead th{{font:600 12px var(--body);color:var(--soft)}} tbody th{{font-weight:600}} th small{{display:block;font-weight:400;color:var(--soft)}}
.pill{{display:inline-block;padding:1px 8px;border-radius:999px;color:#fff;font-size:12px;margin-right:8px}}
.num{{font:12px var(--mono);color:var(--soft);font-variant-numeric:tabular-nums}}
.note{{color:var(--soft);font-size:13px;max-width:75ch;margin:0}}
</style>
<main>
<header><h1>Kosika AI scorecard</h1>
<p class="sub">The same 20 real asks, judged by code on your laptop with free models, not by the agent itself. An overclaim means the agent said every step passed but the independent check disagreed.</p></header>
<div class="runs">{''.join(columns)}</div>
<div class="wrap"><table><thead><tr><th scope="col">Ask</th>{heads}</tr></thead><tbody>{''.join(table)}</tbody></table></div>
<p class="note">Runs marked cold start with empty memory; warm runs reuse what the cold run before them read and checked. An ask whose every step was checked by code is answered again from memory with no model call; think-only answers are never reused. From the strict check on, a think step passes only when a second model quotes the answer for every part the ask needs and Kosika finds each quote in it. Times depend on how busy the free models were that day.</p>
</main>"""

if __name__ == "__main__":
    sys.stdout.write(card([load(p) for p in sys.argv[1:]]))
