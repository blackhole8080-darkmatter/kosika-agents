"""The v4 training set: what v3 lacked, without what misled it.

v3 learned the coder's body-only answers from Kosika's own methods, but 17
of those 68 answers called the engines' private helpers (_count, isSpace,
count(a[0], ...)), which a model never sees defined; it learned to call
helpers that do not exist, and scored 0 of 8. v4 is made of:

  - the body pairs from make_bodies.py whose answers stand on their own,
    calling nothing private;
  - every body a coder of the swarm wrote that then passed all its checks
    (its example tests, go vet, both engines agreeing, develop.kos), read
    from the lessons the coder keeps in each run folder;
  - v3's edit examples, so the model keeps what it knew.

    python models/training/make_v4.py --out ~/Aryan_Private/kosika-daily/training/kaggle-upload-5

writes kosika-coder.train.jsonl and kosika-coder.eval.jsonl there, and
prints how many of each kind went in.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
TRAINING = Path.home() / "Aryan_Private" / "kosika-daily" / "training"
WORK = Path.home() / "Aryan_Private" / "kosika-daily"
SYSTEM = (
    "Answer with only the text asked for, in plain text: no JSON and nothing before or after it. "
    "When the answer is a file, give the whole file, every line, in one fenced code block."
)
#: Calls into either engine's private helpers: a body that makes one is not
#: one a model can write from what it is shown.
PRIVATE = re.compile(r"(?<![\w.])_[a-z]\w*\(|\bisSpace\b|\b(count|whole|number|bigInt)\(a\[|\brunes?Of\(|\btoInt\(|\bplainNumber\(")


def private(answer: str) -> bool:
    return bool(PRIVATE.search(answer))


def passed(run: Path) -> bool:
    report = run / "report.md"
    if not report.exists():
        return False
    lines = report.read_text(encoding="utf-8", errors="replace").splitlines()
    return len(lines) > 2 and "passed its checks" in lines[2]


def swarm_bodies(work: Path) -> list[dict]:
    """The body questions and answers of every run that passed."""
    found = []
    for run in sorted(p for p in work.iterdir() if p.is_dir() and re.match(r"\d{4}-\d{2}-\d{2}-\d+$", p.name)):
        if not passed(run):
            continue
        lessons = run / "_agents" / "coder" / "memory" / "lessons"
        for file in sorted(lessons.glob("fill_*.json")) if lessons.is_dir() else []:
            try:
                entries = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            # The last answer is the one that was kept; earlier ones were mended.
            last = entries[-1] if entries else None
            if not last:
                continue
            answer = (last.get("answer") or {}).get("text", "")
            if answer.strip() and not private(answer):
                found.append({"messages": [{"role": "system", "content": SYSTEM},
                                           {"role": "user", "content": last["question"]},
                                           {"role": "assistant", "content": answer}]})
    return found


def read(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(TRAINING / "kaggle-upload-5"))
    parser.add_argument("--bodies", default=str(TRAINING / "kosika-bodies.train.jsonl"))
    parser.add_argument("--base", default=str(TRAINING / "kosika-coder-v3.train.jsonl"))
    parser.add_argument("--base-eval", default=str(TRAINING / "kosika-coder-v3.eval.jsonl"))
    parser.add_argument("--repeat", type=int, default=5, help="how many times the body examples are shown")
    args = parser.parse_args()

    bodies = [json.loads(line) for line in read(Path(args.bodies))]
    clean = [b for b in bodies if not private(b["messages"][2]["content"])]
    swarm = swarm_bodies(WORK)
    base = read(Path(args.base))
    rng = random.Random(4)
    taught = [json.dumps(e, ensure_ascii=False) for e in clean + swarm] * args.repeat
    train = base + taught
    rng.shuffle(train)
    held = read(Path(args.base_eval)) + read(Path(args.bodies).with_name("kosika-bodies.eval.jsonl"))
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    (out / "kosika-coder.train.jsonl").write_text("\n".join(train) + "\n", encoding="utf-8")
    (out / "kosika-coder.eval.jsonl").write_text("\n".join(held) + "\n", encoding="utf-8")
    print(f"body pairs kept {len(clean)} of {len(bodies)} (left out {len(bodies) - len(clean)} that call private helpers)")
    print(f"passing swarm answers {len(swarm)}")
    print(f"train {len(train)} lines ({len(base)} v3 edits + {len(taught)} body answers x{args.repeat}); eval {len(held)}; in {out}")


if __name__ == "__main__":
    main()
