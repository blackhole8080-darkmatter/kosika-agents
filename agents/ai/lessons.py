"""Kosika AI's kept runs as training data for Kosika's own planner model.

    python agents/ai/lessons.py FOLDER [OUT.jsonl]

FOLDER is the --memory folder Kosika AI ran with; its lessons.json holds
one entry per ask. Only runs Aryan kept (agents/ai/mark.kos, verdict
"keep") become examples by default; --checked adds runs whose every step
passed Kosika's own checks but that nobody has looked at yet. Dropped runs
are never used. Each line is a chat example: the ask in, the plan out.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SYSTEM = ("You are the planner of Kosika AI. Split the ask into at most 4 small steps; "
          "each uses one tool: calc, code, look, draw or think.")


def plan_json(text: str) -> str:
    """The plan as Kosika AI keeps it ("goal: ..." then "- tool: what (done when ...)") as the planner's JSON."""
    lines = text.splitlines()
    goal = lines[0].removeprefix("goal: ").strip() if lines else ""
    steps = []
    for line in lines[1:]:
        if not line.startswith("- ") or ": " not in line:
            continue
        tool, rest = line[2:].split(": ", 1)
        what, _, done = rest.rpartition(" (done when ")
        if not what:
            what, done = rest, ""
        steps.append({"tool": tool.strip(), "what": what.strip(), "done_when": done.removesuffix(")").strip()})
    return json.dumps({"goal": goal, "steps": steps}, ensure_ascii=False)


def examples(lessons: dict, checked: bool) -> list[dict]:
    wanted = {"keep", "checked"} if checked else {"keep"}
    out = []
    for key in sorted(lessons):
        lesson = lessons[key]
        if lesson.get("verdict") not in wanted or not lesson.get("plan"):
            continue
        out.append({"messages": [{"role": "system", "content": SYSTEM},
                                 {"role": "user", "content": lesson["ask"]},
                                 {"role": "assistant", "content": plan_json(lesson["plan"])}],
                    "verdict": lesson["verdict"], "kind": lesson.get("kind", "")})
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder")
    parser.add_argument("out", nargs="?", default="kosika-ai-lessons.jsonl")
    parser.add_argument("--checked", action="store_true", help="also use runs that passed every check")
    args = parser.parse_args()
    store = Path(args.folder) / "lessons.json"
    lessons = json.loads(store.read_text(encoding="utf-8")) if store.exists() else {}
    rows = examples(lessons, args.checked)
    Path(args.out).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    kinds = {}
    for r in rows:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    print(f"{len(rows)} examples from {len(lessons)} runs -> {args.out}  " + "  ".join(f"{k} {v}" for k, v in sorted(kinds.items())))


if __name__ == "__main__":
    main()
