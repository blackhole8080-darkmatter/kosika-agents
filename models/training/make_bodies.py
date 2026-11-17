"""Training pairs for the coder's body-only questions, from Kosika itself.

Every Text and Number method lives in both engines: its Python function in
kosika/builtin_methods.py and its Go entry in native/methods.go. Each method
gives two examples, asked exactly as the coder asks a model (fill_py and
fill_go in agents/coder/main.kos, the same system message Kosika sends):

  - the Python question, answered with the method's Python body;
  - the Go question, which shows the Python body, answered with its Go body,
    rewritten to use the variables the coder declares (text := textOf(r) ...).

Examples in the questions are made by running each method in the Python
engine on a few sample values, as a request's examples would read.

    python models/training/make_bodies.py --out ~/Aryan_Private/kosika-daily/training

writes kosika-bodies.train.jsonl and kosika-bodies.eval.jsonl (one method in
ten is held out for eval).
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENTS = HERE.parents[1]
KOSIKA = Path(os.environ.get("KOSIKA_HOME") or AGENTS.parent / "kosika").resolve()
sys.path.insert(0, str(KOSIKA))

from kosika.checker import check  # noqa: E402
from kosika.parser import parse  # noqa: E402
from kosika.runtime import run  # noqa: E402

#: What Kosika says to a model whose output is plain text (runtime.py), so
#: the examples are asked exactly as the coder asks.
SYSTEM = (
    "Answer with only the text asked for, in plain text: no JSON and nothing before or after it. "
    "When the answer is a file, give the whole file, every line, in one fenced code block."
)
RECEIVER = {"Text": "text", "Number": "number"}
GO_MAP = {"Text": "textMethods", "Number": "numberMethods"}
READ = {"Text": "textOf", "Number": "toFloat"}
SAMPLES = {
    "Text": ['"Kosika"', '""', '"  two words  "', '"a,b,c"', '"Hello World"'],
    "Number": ["7", "-2.5", "0", "12"],
}
ARG_SAMPLES = {"Text": ['"a"', '","', '"o"'], "Number": ["2", "0", "3"], "Bool": ["true"]}


# ---------------------------------------------------------------- Python side

class Renamer(ast.NodeTransformer):
    def __init__(self, names: dict[str, str]) -> None:
        self.names = names

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id in self.names:
            node.id = self.names[node.id]
        return node


def python_methods(source: str) -> list[dict]:
    tree = ast.parse(source)
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Method" and len(node.args) == 5):
            continue
        kind, name, params, result, impl = node.args
        if not (isinstance(kind, ast.Constant) and kind.value in RECEIVER and isinstance(name, ast.Constant)):
            continue
        if not isinstance(params, ast.Tuple) or not isinstance(result, ast.Constant):
            continue
        pairs = []
        for p in params.elts:
            if isinstance(p, ast.Tuple) and len(p.elts) == 2 and all(isinstance(e, ast.Constant) for e in p.elts):
                pairs.append((p.elts[0].value, p.elts[1].value))
        if len(pairs) != len(params.elts):
            continue
        target = [RECEIVER[kind.value]] + [p for p, _ in pairs]
        if isinstance(impl, ast.Lambda):
            own = [a.arg for a in impl.args.args]
            if len(own) != len(target):
                continue
            expr = Renamer(dict(zip(own, target))).visit(impl.body)
            body = "return " + ast.unparse(expr)
        elif isinstance(impl, ast.Name) and impl.id in functions:
            fn = functions[impl.id]
            own = [a.arg for a in fn.args.args]
            if len(own) != len(target) or fn.args.vararg or fn.args.kwarg:
                continue
            renamer = Renamer(dict(zip(own, target)))
            statements = [s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(getattr(s, "value", None), ast.Constant))]
            body = "\n".join(ast.unparse(renamer.visit(s)) for s in statements)
        else:
            continue
        if len(body.splitlines()) > 40:
            continue
        found.append({"kind": kind.value, "name": name.value, "params": pairs, "result": result.value,
                      "py_body": "\n".join("    " + line for line in body.splitlines())})
    return found


# ---------------------------------------------------------------- Go side

def go_entries(source: str) -> dict[tuple[str, str], str]:
    """The body of every Text and Number method's Go entry."""
    bodies: dict[tuple[str, str], str] = {}
    lines = source.replace("\r\n", "\n").split("\n")
    kind = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        for k, m in GO_MAP.items():
            if line.startswith(f"var {m} = map[string]method{{"):
                kind = k
        if line == "}":
            kind = ""
        head = re.match(r'^\t"(\w+)":\s*\{.*func\(r Value, a \[\]Value\) Value \{(.*)$', line)
        if kind and head:
            name, rest = head.group(1), head.group(2).strip()
            if rest.endswith("}},"):  # one line
                inner = rest[: -len("}},")].strip()
                bodies[(kind, name)] = "\t" + inner
            else:
                body = []
                j = i + 1
                while j < len(lines) and lines[j] != "\t}},":
                    body.append(lines[j])
                    j += 1
                bodies[(kind, name)] = "\n".join(b[1:] if b.startswith("\t") else b for b in body)
                i = j
        i += 1
    return bodies


def with_bound_names(go: str, method: dict) -> str:
    """The Go body using the variables the coder declares: textOf(r) is
    `text`, textOf(a[0]) the first argument's name, and so on."""
    out = go.replace(f"{READ[method['kind']]}(r)", RECEIVER[method["kind"]])
    for i, (name, type_name) in enumerate(method["params"]):
        if type_name in READ:
            out = out.replace(f"{READ[type_name]}(a[{i}])", name)
    return out


# ---------------------------------------------------------------- questions

def descriptions(book: str) -> dict[tuple[str, str], str]:
    said: dict[tuple[str, str], str] = {}
    for line in book.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        kinds = [k for k in ("Text", "Number") if f"`{k}`" in cells[0]]
        what = re.sub(r"`", "", cells[-1]).strip()
        for kind in kinds:
            for name in re.findall(r"`(\w+)\(", cells[1]):
                said[(kind, name)] = what
    return said


def request_of(method: dict, said: str) -> str:
    params = ", ".join(f"{p}: {t}" for p, t in method["params"])
    what = said[0].lower() + said[1:] if said else "does what its name says"
    return f"add a {method['kind']} method {method['name']}({params}) that gives this: {what}. It is giving a {method['result']}"


def value_text(value) -> str | None:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(int(value)) if float(value).is_integer() else repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return None


def examples_for(method: dict, limit: int = 3) -> list[str]:
    """Up to LIMIT calls that work, run in the Python engine, as a request's
    examples are written: `call` is `value`."""
    shown = []
    receivers = SAMPLES[method["kind"]]
    for receiver in receivers:
        args = [ARG_SAMPLES.get(t, [None])[0] for _, t in method["params"]]
        if None in args:
            return []
        call = f"({receiver}).{method['name']}({', '.join(args)})"
        program = f"record Out {{ value: {method['result']} }}\nfn f() -> Out {{\nreturn {{value: {call}}}\n}}\nworkflow main() -> Out {{ out = f() }}\n"
        try:
            got = run(check_and(parse(program)), "main", None, inputs={})["value"]
        except Exception:  # noqa: BLE001 - a call that fails is not an example
            continue
        text = value_text(got)
        if text is not None and len(text) < 60:
            shown.append(f"`{call}` is `{text}`")
        if len(shown) >= limit:
            break
    return shown


def check_and(program):
    check(program)
    return program


PROBE = """
record ProbeIn { text: Text python: Text }
record ProbeOut { py: Text go: Text ok: Bool }
fn probe(t: ProbeIn) -> ProbeOut {
  let asked = method_asked({text: t.text})
  let sig = signature(asked)
  let job: Filling = {asked: asked, sig: sig, failure: ""}
  return {py: py_body_task(job).text, go: go_body_task(job, t.python).text, ok: sig.ok and asked.name.length() > 0}
}
workflow probe_main(t: ProbeIn) -> ProbeOut { out = probe(t) }
"""


def python_lines(source: str, name: str) -> str:
    """As the coder's python_lines shows the Python engine to the Go
    question: the lines around each that names the method."""
    lines = source.replace("\r\n", "\n").split("\n")
    found: list[str] = []
    for i, line in enumerate(lines):
        if f'"{name}"' in line or f"def _{name}" in line:
            for each in lines[max(0, i - 2): max(0, i - 2) + 8]:
                if each not in found:
                    found.append(each)
    return "\n".join(found[:40])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(Path.home() / "Aryan_Private" / "kosika-daily" / "training"))
    parser.add_argument("--name", default="kosika-bodies")
    args = parser.parse_args()

    source = (KOSIKA / "kosika" / "builtin_methods.py").read_text(encoding="utf-8")
    go = (KOSIKA / "native" / "methods.go").read_text(encoding="utf-8")
    said = descriptions((KOSIKA / "KOSIKA.md").read_text(encoding="utf-8"))
    coder = (AGENTS / "agents" / "coder" / "main.kos").read_text(encoding="utf-8")
    prober = parse(coder + PROBE)
    check(prober)

    methods = python_methods(source)
    bodies = go_entries(go)
    rows = []
    for m in methods:
        key = (m["kind"], m["name"])
        if key not in bodies:
            continue
        request = request_of(m, said.get(key, ""))
        shown = examples_for(m)
        if shown:
            request += "; examples: " + ", ".join(shown)
        asked = run(prober, "probe_main", None, inputs={"t": {"text": request, "python": python_lines(source, m["name"])}})
        if not asked["ok"]:
            continue
        go_answer = with_bound_names(bodies[key], m)
        rows.append({"name": m["name"], "kind": m["kind"], "pairs": [
            {"messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": asked["py"]},
                          {"role": "assistant", "content": m["py_body"]}]},
            {"messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": asked["go"]},
                          {"role": "assistant", "content": go_answer}]},
        ]})
    random.Random(7).shuffle(rows)
    held = rows[: max(1, len(rows) // 10)]
    kept = rows[len(held):]
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    for part, chosen in (("train", kept), ("eval", held)):
        with (out / f"{args.name}.{part}.jsonl").open("w", encoding="utf-8") as stream:
            for row in chosen:
                for pair in row["pairs"]:
                    stream.write(json.dumps(pair, ensure_ascii=False) + "\n")
    print(f"{len(methods)} methods in Python, {len(rows)} with a Go entry and a question; "
          f"{len(kept) * 2} training and {len(held) * 2} eval examples in {out}")
    print("held out:", ", ".join(r["kind"] + "." + r["name"] for r in held))


if __name__ == "__main__":
    main()
