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

The results go to benchmarks/ai/results/DATE.json, with a line per ask and
the score at the end, so one night's run can be put beside another's.
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
    args = parser.parse_args()
    asks = json.loads((HERE / "asks.json").read_text(encoding="utf-8"))["asks"]
    if args.only:
        wanted = set(args.only.split(","))
        asks = [a for a in asks if a["id"] in wanted]
    rows = []
    for ask in asks:
        with tempfile.TemporaryDirectory() as folder:
            given = Path(folder) / "ask.json"
            given.write_text(json.dumps({"ask": {"text": ask["text"], "most": 3}}), encoding="utf-8")
            began = time.time()
            try:
                ran = subprocess.run([args.engine, "run", str(ROOT / "agents" / "ai" / "main.kos"), "--input", str(given),
                                      "--model", args.model, "--model-url", args.url, "--model-timeout", "120",
                                      "--max-output-tokens", "2000", "--allow-site", "en.wikipedia.org"],
                                     capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=args.cap, cwd=ROOT)
                report, stderr, code = report_of(ran.stdout), ran.stderr.strip(), ran.returncode
            except subprocess.TimeoutExpired:
                report, stderr, code = None, f"stopped after {args.cap:.0f} s", -1
        seconds = round(time.time() - began, 1)
        passed, why = judge(ask, report, args.engine)
        rows.append({"id": ask["id"], "kind": ask["kind"], "passed": passed, "why": why, "seconds": seconds,
                     "exit": code, "report": report, "stderr": stderr[-1500:]})
        print(f"{'PASS' if passed else 'FAIL'}  {ask['id']:<10} {seconds:>6} s  {why}", flush=True)
    score = sum(r["passed"] for r in rows)
    kinds = sorted({r["kind"] for r in rows})
    by_kind = {k: f"{sum(r['passed'] for r in rows if r['kind'] == k)}/{sum(1 for r in rows if r['kind'] == k)}" for k in kinds}
    print(f"\n{score}/{len(rows)} passed  " + "  ".join(f"{k} {v}" for k, v in by_kind.items()))
    out = HERE / "results" / (time.strftime("%Y-%m-%d-%H%M") + ".json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"model": args.model, "score": score, "of": len(rows), "by_kind": by_kind, "rows": rows}, indent=1), encoding="utf-8")
    print(f"saved {out}")
    sys.exit(0)


if __name__ == "__main__":
    main()
