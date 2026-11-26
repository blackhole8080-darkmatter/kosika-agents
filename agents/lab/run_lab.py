"""Run the lab (main.kos) once: read the newest papers near Kosika, keep the
day's notes, and give the coders at most two new tasks.

    python run_lab.py [--most 2] [--no-tasks]

Everything is decided in main.kos; this only runs it and saves what it says:

- the day's notes to ~/Aryan_Private/kosika-daily/lab/DATE.md (for the agents
  and for Aryan), and
- each task (already in the swarm's shape, checked in main.kos) to the end of
  the coders' to-do list, kosika-daily/queue.md, under the same lock folder
  the swarm uses (queue.lock), unless a request of the same words is there.

Models come from Kosika's router (KOSIKA_ROUTER_PORT, 8791); the engine from
KOSIKA_HOME (the kosika checkout beside this repo by default).
"""

from __future__ import annotations

import argparse
import ast
import datetime
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("KOSIKA_HOME") or HERE.parents[2].parent / "kosika").resolve()
WORK = Path.home() / "Aryan_Private" / "kosika-daily"
TODO = Path(os.environ.get("KOSIKA_CODER_TODO") or WORK / "queue.md")
PORT = int(os.environ.get("KOSIKA_ROUTER_PORT", "8791"))
MOST_TASKS = 2


def run_lab(most: int) -> dict:
    memory = WORK / "lab" / "memory"
    memory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as work:
        ask = Path(work) / "ask.json"
        ask.write_text(json.dumps({"a": {"most": most}}), encoding="utf-8")
        done = subprocess.run(
            [sys.executable, "-m", "kosika.cli", "run", str(HERE / "main.kos"), "--input", str(ask),
             "--memory", str(memory), "--allow-site", "export.arxiv.org",
             "--model", "kosika-virtual", "--model-url", f"http://127.0.0.1:{PORT}/v1", "--model-timeout", "240"],
            cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1900,
            env={**os.environ, "PYTHONPATH": str(REPO)})
    for line in reversed(done.stdout.splitlines()):
        if line.strip().startswith("{") and "notes" in line:
            return ast.literal_eval(line.strip())
    raise SystemExit("The lab did not finish: " + (done.stderr or done.stdout)[-800:])


def plain(line: str) -> str:
    """A to-do line's request, without its mark or its notes."""
    return line[6:].split(" (20")[0].split(" (from the lab:")[0].strip()


def add_tasks(tasks: list[str]) -> list[str]:
    """Add new tasks to the to-do list under the swarm's lock; the ones added."""
    lock = TODO.with_suffix(".lock")
    for _ in range(600):
        try:
            lock.mkdir()
            break
        except FileExistsError:
            time.sleep(0.1)
    else:
        raise SystemExit("The to-do list stayed locked; no task was added.")
    try:
        lines = TODO.read_text(encoding="utf-8").splitlines() if TODO.exists() else []
        there = {plain(line) for line in lines if line.startswith("- [")}
        added = [t for t in tasks if t.startswith("- [ ] ") and plain(t) not in there][:MOST_TASKS]
        if added:
            TODO.write_text("\n".join(lines + added) + "\n", encoding="utf-8")
        return added
    finally:
        lock.rmdir()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--most", type=int, default=2, help="papers to read (1 to 3)")
    parser.add_argument("--no-tasks", action="store_true", help="keep the notes, give the coders nothing")
    args = parser.parse_args()
    lab = run_lab(args.most)
    day = lab.get("date") or datetime.date.today().isoformat()
    notes = WORK / "lab" / f"{day}.md"
    notes.parent.mkdir(parents=True, exist_ok=True)
    notes.write_text(str(lab["notes"]) + "\n", encoding="utf-8")
    added = [] if args.no_tasks else add_tasks([str(t) for t in lab.get("tasks", [])])
    print(lab.get("said", ""))
    print("Notes:", notes)
    print(f"Tasks added to {TODO.name}: {len(added)}")
    for line in added:
        print(" ", line[6:120])


if __name__ == "__main__":
    main()
