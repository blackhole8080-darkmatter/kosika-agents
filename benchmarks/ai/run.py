"""Kosika AI's scorecard: run every ask in asks.json and judge the answers.

    python benchmarks/ai/run.py --engine ../kn.exe
    python benchmarks/ai/run.py --engine ../kn.exe --only pct,train --model kosika-virtual:soft

Each ask runs agents/ai/main.kos once through the engine, against the model
router (http://127.0.0.1:8791/v1 unless --url says otherwise). The agent's
own report is not trusted: an ask passes only when

- the run finishes and prints a report,
- the answer holds, for each list in `has`, at least one of its words or
  numbers (letter case ignored),
- some step used the ask's `tool` and passed its check, and
- for a code ask, the program that step wrote checks and passes its own
  tests when the engine runs them again here.

Besides passing, each ask is measured from the run's own trace, never from
what the agent says about itself:

- seconds: the whole run, and how much of it was spent waiting on models
- model calls: how many, and to which of the agent's models
- web reads: how many pages it fetched
- honesty: an ask is an overclaim when the report says every step passed
  its check but the independent judge above fails it. An honest agent
  that cannot do an ask says so; it never claims work it did not do.

The results go to benchmarks/ai/results/DATE.json, with a line per ask and
the score at the end, so one night's run can be put beside another's
(benchmarks/ai/card.py draws them side by side).
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def report_of(stdout: str) -> dict | None:
    """The report the engine printed: JSON, or the Python-shaped text it prints."""
    # The engine names its trace file on a line of its own after the report.
    text = "\n".join(line for line in stdout.splitlines() if not line.startswith("Trace: ")).strip()
    if not text:
        return None
    for read in (json.loads, ast.literal_eval):
        try:
            value = read(text)
            if isinstance(value, dict):
                return value
        except (ValueError, SyntaxError):
            pass
    return None


def trace_events(path: Path):
    """Every event of a run's trace, nested ones included."""
    def walk(e: dict):
        yield e
        for key in ("events", "expression_events"):
            for inner in e.get(key) or []:
                if isinstance(inner, dict):
                    yield from walk(inner)
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            yield from walk(json.loads(line))
        except ValueError:
            continue


def stamp(text: str) -> float:
    from datetime import datetime
    try:
        return datetime.fromisoformat(text).timestamp()
    except (TypeError, ValueError):
        return 0.0


def measure(trace: Path) -> dict:
    """What the trace shows the run did: model calls, time on models, web reads."""
    calls: dict[str, int] = {}
    waiting = 0.0
    open_at: dict[str, list[float]] = {}
    web = 0
    for e in trace_events(trace):
        kind = e.get("event")
        if kind == "model_call":
            name = str(e.get("model"))
            calls[name] = calls.get(name, 0) + 1
            open_at.setdefault(name, []).append(stamp(e.get("timestamp")))
        elif kind == "model_return":
            began = open_at.get(str(e.get("model"))) or []
            if began:
                waiting += max(0.0, stamp(e.get("timestamp")) - began.pop())
        elif kind == "tool_call" and str(e.get("operation")) == "fetch":
            web += 1
    return {"model_calls": sum(calls.values()), "by_model": calls, "model_seconds": round(waiting, 1), "web_reads": web}


def claims_all_passed(report: dict | None) -> bool:
    """Whether the agent's own report says every step that ran passed."""
    if not report:
        return False
    ran = [s for s in report.get("steps") or [] if s.get("ran", True)]
    return bool(ran) and all(s.get("ok") for s in ran)


def code_passes(engine: str, program: str) -> tuple[bool, str]:
    """Whether a program checks and passes its own tests, run again here."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "answer.kos"
        path.write_text(program, encoding="utf-8")
        ran = subprocess.run([engine, "test", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        said = (ran.stdout + ran.stderr).strip().splitlines()
        return ran.returncode == 0, (said[-1] if said else "")


def judge(ask: dict, report: dict | None, engine: str) -> tuple[bool, str]:
    if report is None:
        return False, "no report"
    answer = str(report.get("answer") or "").lower()
    for options in ask.get("has", []):
        if not any(str(o).lower() in answer for o in options):
            return False, "the answer lacks " + " / ".join(options)
    steps = [s for s in report.get("steps") or [] if s.get("tool") == ask["tool"] and s.get("ok")]
    if not steps:
        return False, f"no {ask['tool']} step passed its check"
    if ask.get("code"):
        ok, said = code_passes(engine, str(steps[0].get("answer") or ""))
        if not ok:
            return False, "the program fails when tested again: " + said
    return True, ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, help="kosika-native (kn.exe)")
    parser.add_argument("--model", default="kosika-virtual:soft")
    parser.add_argument("--url", default="http://127.0.0.1:8791/v1")
    parser.add_argument("--only", default="")
    parser.add_argument("--cap", type=float, default=600, help="seconds one ask may take")
    parser.add_argument("--fixture", default="", help="answer every model from this file, offline (checks the scorecard itself)")
    parser.add_argument("--label", default="", help="a name for this run on the card, such as baseline")
    parser.add_argument("--memory", default="", help="Kosika AI's lessons folder; a fresh one for each run when not given")
    args = parser.parse_args()
    asks = json.loads((HERE / "asks.json").read_text(encoding="utf-8"))["asks"]
    if args.only:
        wanted = set(args.only.split(","))
        asks = [a for a in asks if a["id"] in wanted]
    rows = []
    lessons = tempfile.TemporaryDirectory()
    memory = args.memory or lessons.name
    for ask in asks:
        with tempfile.TemporaryDirectory() as folder:
            given = Path(folder) / "ask.json"
            given.write_text(json.dumps({"ask": {"text": ask["text"], "most": 3}}), encoding="utf-8")
            trace = Path(folder) / "trace.jsonl"
            began = time.time()
            try:
                models = (["--model-fixture", args.fixture] if args.fixture else
                          ["--model", args.model, "--model-url", args.url, "--model-timeout", "120"])
                ran = subprocess.run([args.engine, "run", str(ROOT / "agents" / "ai" / "main.kos"), "--input", str(given),
                                      *models,
                                      "--max-output-tokens", "2000", "--allow-site", "en.wikipedia.org", "--memory", memory,
                                      # Reading a formula or drawing a curve is many small steps in Kosika.
                                      "--max-function-calls", "400000", "--trace", str(trace)],
                                     capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=args.cap, cwd=ROOT)
                report, stderr, code = report_of(ran.stdout), ran.stderr.strip(), ran.returncode
            except subprocess.TimeoutExpired:
                report, stderr, code = None, f"stopped after {args.cap:.0f} s", -1
            used = measure(trace)
        seconds = round(time.time() - began, 1)
        passed, why = judge(ask, report, args.engine)
        overclaim = claims_all_passed(report) and not passed
        rows.append({"id": ask["id"], "kind": ask["kind"], "passed": passed, "why": why, "seconds": seconds,
                     **used, "overclaim": overclaim,
                     "exit": code, "report": report, "stderr": stderr[-1500:]})
        print(f"{'PASS' if passed else 'FAIL'}  {ask['id']:<10} {seconds:>6} s  {used['model_calls']:>2} calls"
              f"{'  OVERCLAIM' if overclaim else ''}  {why}", flush=True)
    score = sum(r["passed"] for r in rows)
    kinds = sorted({r["kind"] for r in rows})
    by_kind = {k: f"{sum(r['passed'] for r in rows if r['kind'] == k)}/{sum(1 for r in rows if r['kind'] == k)}" for k in kinds}
    times = sorted(r["seconds"] for r in rows) or [0]
    summary = {
        "median_seconds": times[len(times) // 2],
        "total_seconds": round(sum(times), 1),
        "model_calls": sum(r["model_calls"] for r in rows),
        "calls_per_pass": round(sum(r["model_calls"] for r in rows) / score, 1) if score else None,
        "model_seconds": round(sum(r["model_seconds"] for r in rows), 1),
        "web_reads": sum(r["web_reads"] for r in rows),
        "overclaims": sum(r["overclaim"] for r in rows),
    }
    print(f"\n{score}/{len(rows)} passed  " + "  ".join(f"{k} {v}" for k, v in by_kind.items()))
    print(f"median {summary['median_seconds']} s a ask, {summary['model_calls']} model calls "
          f"({summary['calls_per_pass']} a pass), {summary['overclaims']} overclaims")
    out = HERE / "results" / (time.strftime("%Y-%m-%d-%H%M") + ".json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"model": args.model, "score": score, "of": len(rows), "by_kind": by_kind, **summary,
                               "label": args.label, "rows": rows}, indent=1), encoding="utf-8")
    print(f"saved {out}")
    sys.exit(0)


if __name__ == "__main__":
    main()
