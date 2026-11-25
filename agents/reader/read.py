"""Read a book with Kosika and save the study notes, in one line.

    python agents/reader/read.py BOOK.pdf --out notes/BOOK --model kosika-virtual:soft --model-url http://127.0.0.1:8791/v1
    python agents/reader/read.py https://example.org/book.html --out notes/web-book --model qwen2.5:3b --model-protocol ollama

It runs three steps and keeps each one's file in --out:

    book.json     the text, page by page (pages.py)
    trace.jsonl   the Kosika run of main.kos: every model call and check
    notes.md      the study notes, and notes.html, the same to read in a browser,
                  with each chapter's mind map drawn by Kosika Designs
    score.json    the scorecard: coverage, checked ideas, drift

Every checked idea is also kept in the memory folder (--memory, by default
--out/memory) as the memory `shelf`, for other Kosika agents to recall.

--pages 1-60 reads only those pages: a quick trial before a whole book.
--engine picks the Kosika engine: "kosika" (default) or a path to kosika-native.
Every other option after the book is given to `kosika run` as it is, so
--model, --model-url, --model-protocol, --models or --model-command (last)
choose the free model that writes.
"""

from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from pages import read_book  # noqa: E402

LIMITS = [
    "--max-model-calls", "4000", "--max-tool-calls", "20000",
    "--max-workflow-steps", "100000", "--max-function-calls", "1000000",
    "--deadline-seconds", "86400", "--model-timeout", "180",
]


def result_of(trace: Path) -> dict | None:
    """The run's result: the top level run_complete event, the trace's last line."""
    last = None
    for line in trace.read_text("utf-8", "replace").splitlines():
        if line.strip():
            last = line
    if last is None:
        return None
    event = json.loads(last)
    return event.get("result") if event.get("event") == "run_complete" else None


# ------------------------------------------------------------------ the page

STYLE = """
:root{--bg:#f7f7fb;--card:#fff;--ink:#1d1b2e;--soft:#5b5872;--line:#e4e2ef;--accent:#4b3fd1;--amber:#d98a00;--warn:#b3261e}
@media (prefers-color-scheme:dark){:root{--bg:#12111c;--card:#1b1a29;--ink:#ecebf5;--soft:#a9a6c2;--line:#2c2a40;--accent:#a49cff;--amber:#ffb84d;--warn:#ff8a80}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:860px;margin:0 auto;padding:32px 16px 80px}h1{font-size:30px;line-height:1.2;margin:0 0 6px}
.lede{color:var(--soft);margin:0 0 24px}nav ol{padding-left:22px}nav a{color:var(--accent);text-decoration:none}
section.ch{margin:40px 0 0;padding-top:12px;border-top:2px solid var(--line)}section.ch h2{font-size:23px;margin:0}
.meta{color:var(--soft);font-size:14px}.gist{font-size:17px;margin:12px 0 18px}
.card{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:10px;padding:14px 18px;margin:12px 0}
.card h3{margin:0 0 6px;font-size:18px}.card p{margin:6px 0}.card b{color:var(--accent)}.card ul{margin:6px 0;padding-left:20px}
.card blockquote{margin:10px 0 0;padding:6px 12px;border-left:3px solid var(--amber);color:var(--soft);font-size:14px}
.card.drift{border-left-color:var(--warn)}.drift-note{color:var(--warn);font-size:14px}
details.map{margin:14px 0}details.map summary{cursor:pointer;color:var(--accent)}
details.map iframe{width:100%;height:900px;border:1px solid var(--line);border-radius:10px;background:#0b0a14}
.take{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 18px;margin:12px 0}.take b{color:var(--amber)}.take ul{margin:6px 0;padding-left:20px}
.one{background:var(--card);border:1px solid var(--line);border-top:4px solid var(--amber);border-radius:12px;padding:16px 20px;margin:0 0 24px}.one h2{margin:0 0 8px;font-size:20px}
.figs,.missing{font-size:14px;color:var(--soft)}table{border-collapse:collapse;width:100%;font-size:15px;margin-top:10px}
td,th{border:1px solid var(--line);padding:6px 10px;text-align:left}.pass{color:#1e8e3e;font-weight:600}.fail{color:var(--warn);font-weight:600}
"""


def esc(text) -> str:
    return html.escape(str(text), quote=True)


def card_html(card: dict, ideas: list[dict], drifting: list[str]) -> str:
    rows = [f"<h3>{esc(card['concept'])}</h3>", f"<p><b>What</b> {esc(card['what'])}</p>"]
    for name, label in (("how", "How"), ("why", "Why"), ("example", "Example"), ("case_study", "Case study")):
        if card.get(name, "").strip():
            rows.append(f"<p><b>{label}</b> {esc(card[name])}</p>")
    points = [b for b in card.get("bullets", []) if b.strip()]
    if points:
        rows.append("<ul>" + "".join(f"<li>{esc(b)}</li>" for b in points) + "</ul>")
    first = (card.get("from") or [0])[0]
    if 1 <= first <= len(ideas):
        idea = ideas[int(first) - 1]
        rows.append(f"<blockquote>p. {idea['page']}: “{esc(idea['quote'][:240])}”</blockquote>")
    drift = card["concept"] in drifting
    if drift:
        rows.append('<p class="drift-note">⚠ Uses many words the chapter does not: check it against the book.</p>')
    return f'<div class="card{" drift" if drift else ""}">{"".join(rows)}</div>'


def chapter_html(ch: dict) -> str:
    if ch.get("skip"):
        return ""
    pages = f"p. {ch['first']}" if ch["first"] == ch["last"] else f"pp. {ch['first']}–{ch['last']}"
    out = [f'<section class="ch" id="ch{ch["n"]}"><h2>{ch["n"]}. {esc(ch["title"])}</h2>',
           f'<div class="meta">{pages} · {len(ch["cards"])} concepts from {len(ch["ideas"])} checked ideas</div>']
    if ch.get("gist"):
        out.append(f'<p class="gist">{esc(ch["gist"])}</p>')
    if ch.get("takeaways"):
        out.append('<div class="take"><b>Key takeaways</b><ul>' + "".join(f"<li>{esc(t)}</li>" for t in ch["takeaways"]) + "</ul></div>")
    if ch.get("map"):
        out.append(f'<details class="map"><summary>Mind map of this chapter (Kosika Designs)</summary>'
                   f'<iframe loading="lazy" sandbox="allow-scripts" srcdoc="{esc(ch["map"])}"></iframe></details>')
    # A long chapter's sections each number their own ideas from 1, so each
    # card's quote is looked up among the ideas of its own section.
    out += [card_html(c, ch["ideas"], ch.get("drifting", [])) for c in ch["cards"]]
    if ch.get("figures"):
        out.append('<p class="figs"><b>Figures and tables in the book:</b> ' +
                   " · ".join(f"p. {f['page']}: {esc(f['text'][:140])}" for f in ch["figures"]) + "</p>")
    if ch.get("missing"):
        out.append(f'<p class="missing">Key words of this chapter not in these notes: {esc(", ".join(ch["missing"]))}.</p>')
    return "".join(out) + "</section>"


def score_rows(s: dict) -> list[tuple[str, str]]:
    def pct(a, b):
        return f"{round(100 * a / b)}%" if b else "–"
    shorter = f"{s['words_in'] / s['words_out']:.1f}× shorter" if s["words_out"] else "–"
    return [
        ("Pages read", f"{s['pages'] - s['pages_skipped']} of {s['pages']}"),
        ("Chapters with notes", f"{s['chapters_with_notes']} of {s['chapters']}"),
        ("Pieces that gave checked ideas", f"{s['pieces_read']} of {s['pieces']} ({pct(s['pieces_read'], s['pieces'])})"),
        ("Ideas kept, quoted from the book word for word", str(s["ideas"])),
        ("Ideas dropped (quote not in the book)", str(s["ideas_dropped"])),
        ("Concept cards", f"{s['cards']} ({s['cards_by_kosika']} sections written by Kosika when the writer failed)"),
        ("Cards drifting from the book", str(s["cards_drifting"])),
        ("Key words covered", f"{s['terms_covered']} of {s['terms']} ({pct(s['terms_covered'], s['terms'])})"),
        ("Figures and tables listed", str(s["figures"])),
        ("Words: book → notes", f"{s['words_in']} → {s['words_out']} ({shorter})"),
    ]


def page_of(notes: dict, run: dict) -> str:
    s = notes["score"]
    verdict = ('<p class="pass">Passed: every chapter has notes, the pieces gave checked ideas, and the notes keep to the book.</p>'
               if s["passed"] else f'<p class="fail">Not passed: {esc("; ".join(s["why"]))}.</p>')
    shown = [c for c in notes["chapters"] if not c.get("skip")]
    contents = "".join(f'<li><a href="#ch{c["n"]}">{esc(c["title"])}</a></li>' for c in shown)
    table = "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td></tr>" for a, b in score_rows(s))
    ran = f"{run.get('model_calls', '?')} model calls, {run.get('minutes', '?')} minutes"
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>Study notes: {esc(notes['title'])}</title><style>{STYLE}</style></head><body><main>"
            f"<h1>{esc(notes['title'])}</h1><p class=\"lede\">Study notes, read A to Z by Kosika. Every idea was checked: "
            f"the words it comes from were found in the book, and its page is given. {esc(ran)}.</p>"
            f"{overview_html(notes.get('overview') or {})}<nav><b>Contents</b><ol>{contents}</ol></nav>{''.join(chapter_html(c) for c in notes['chapters'])}"
            f'<section class="ch"><h2>Scorecard</h2>{verdict}<table>{table}</table></section></main></body></html>')


def overview_html(o: dict) -> str:
    if not o.get("summary"):
        return ""
    ideas = "".join(f"<li>{esc(i)}</li>" for i in o.get("big_ideas", []))
    return f'<div class="one"><h2>The book on one page</h2><p>{esc(o["summary"])}</p><b>Big ideas</b><ul>{ideas}</ul></div>'


def calls_in(trace: Path) -> int:
    return sum(1 for line in trace.read_text("utf-8", "replace").splitlines() if '"event": "model_call"' in line or '"event":"model_call"' in line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("book", help="a .pdf, .epub, .html, .md or .txt file, or an http(s) address")
    parser.add_argument("--out", required=True, help="folder for the notes")
    parser.add_argument("--memory", help="memory folder (default: OUT/memory)")
    parser.add_argument("--pages", help="only these pages, as FIRST-LAST")
    parser.add_argument("--engine", default="kosika", help="kosika, or the path of kosika-native")
    args, model = parser.parse_known_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    book = read_book(args.book)
    if args.pages:
        first, _, last = args.pages.partition("-")
        first, last = int(first), int(last or first)
        pages = book["book"]["pages"]
        book["book"]["pages"] = pages[first - 1:last]
        book["book"]["outline"] = [{"title": m["title"], "page": m["page"] - first + 1}
                                   for m in book["book"]["outline"] if first <= m["page"] <= last]
    given = out / "book.json"
    given.write_text(json.dumps(book, ensure_ascii=False), "utf-8")
    print(f"{book['book']['title']}: {len(book['book']['pages'])} pages", flush=True)

    trace = out / "trace.jsonl"
    memory = Path(args.memory) if args.memory else out / "memory"
    command = [args.engine, "run", str(HERE / "main.kos"), "--input", str(given), "--memory", str(memory),
               "--trace", str(trace), *LIMITS, *model]
    began = time.time()
    ran = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    minutes = round((time.time() - began) / 60, 1)
    notes = result_of(trace) if trace.exists() else None
    if notes is None:
        print(ran.stderr[-2000:], file=sys.stderr)
        print("The run did not finish; nothing was saved but book.json and the trace.", file=sys.stderr)
        return 1
    run = {"model_calls": calls_in(trace), "minutes": minutes}
    (out / "notes.md").write_text(notes["markdown"], "utf-8")
    (out / "notes.html").write_text(page_of(notes, run), "utf-8")
    (out / "score.json").write_text(json.dumps({**notes["score"], **run}, indent=2), "utf-8")
    s = notes["score"]
    print(f"{'PASSED' if s['passed'] else 'NOT PASSED'}: {s['chapters_with_notes']}/{s['chapters']} chapters, "
          f"{s['pieces_read']}/{s['pieces']} pieces, {s['ideas']} ideas ({s['ideas_dropped']} dropped), "
          f"{s['cards']} cards ({s['cards_drifting']} drifting), key words {s['terms_covered']}/{s['terms']}, "
          f"{s['words_in']} -> {s['words_out']} words, {run['model_calls']} model calls, {minutes} min")
    print(f"Notes: {out / 'notes.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
