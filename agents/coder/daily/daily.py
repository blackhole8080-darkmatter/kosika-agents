"""Kosika's daily development: the coder takes the next request in CODER_TODO.md
(in the kosika repo).

    python daily.py              # the next open request
    python daily.py --dry-run    # say which request it would take

Each run works on a fresh copy of Kosika as studio has it on GitHub, in
~/Aryan_Private/kosika-daily/DATE-N, never in the checkout itself. The coder
runs there with every one of its steps allowed, because nothing it does can
leave that copy. What it changed is saved beside the copy as change.patch,
with report.md: the request, what the coder said, and what was checked
afterwards (the test files it wrote, and go vet for the Go engine). A
change reaches Kosika only when its owner reads it and applies it:

    git apply ~/Aryan_Private/kosika-daily/DATE-N/change.patch

The model is the virtual model router (models/ in kosika-agents), started
here when it is not already running, so a day's free allowance used up on
one model moves the work to the next free one, and at last to a model on
this computer, instead of stopping it.

In todo.md, an open request is a line "- [ ] ...". A run marks the line it
took "- [x]" when the checks passed and "- [!]" when they did not, with the
date and the folder, so the next run takes the next request and a failed one
is not tried again until its line is opened once more.
"""

from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The Kosika engine: KOSIKA_HOME, or the kosika folder beside kosika-agents.
REPO = Path(os.environ.get("KOSIKA_HOME") or HERE.parents[2].parent / "kosika").resolve()
sys.path.insert(0, str(REPO))

from kosika.app import App  # noqa: E402
from kosika.model import OpenAICompatibleProvider  # noqa: E402

# The to-do list is about Kosika itself, so it lives in the kosika repo as
# CODER_TODO.md (KOSIKA_CODER_TODO points somewhere else).
TODO = Path(os.environ.get("KOSIKA_CODER_TODO") or REPO / "CODER_TODO.md")
WORK = Path.home() / "Aryan_Private" / "kosika-daily"
# What a coder gets a copy of, and from which branch on GitHub. The Studio
# job (studio_job.ps1) copies the desktop app as well, from the app's branch.
COPIED = [p for p in os.environ.get("KOSIKA_COPIED", "kosika,native,tests/test_short_methods.py,KOSIKA.md").split(",") if p]
SOURCE = os.environ.get("KOSIKA_SOURCE_BRANCH", "studio")
# The overlay that shows each coder at work (agents/hands/overlay.py).
OVERLAY = HERE.parents[1] / "hands" / "overlay.py"
#: The kits' agents, in the order a swarm's members take their names.
NAMES = ["coder-1", "coder-2", "coder-3", "coder-4", "scout", "Kosika"]
ROUTER = HERE.parents[2] / "models" / "learning_router.py"
PORT = int(os.environ.get("KOSIKA_ROUTER_PORT", "8791"))


def next_request(lines: list[str]) -> int | None:
    for number, line in enumerate(lines):
        if line.startswith("- [ ] "):
            return number
    return None


def copy_studio(folder: Path) -> None:
    """Kosika as studio has it on GitHub: what others will build on too."""
    subprocess.run(["git", "fetch", "-q", "origin", SOURCE], cwd=REPO, check=False)
    tar = subprocess.run(
        ["git", "archive", "--format=tar", f"origin/{SOURCE}", *COPIED],
        cwd=REPO,
        capture_output=True,
        check=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(tar)) as archive:
        archive.extractall(folder, filter="data")


def keep_answers(since: float) -> None:
    """Tell the router the change passed, so it keeps the answers it gave
    since `since` and gives them again at once if asked the same (see
    models/smarter.py). Not in a swarm: there the other
    coders' answers, which may not pass, came in the same minutes."""
    import urllib.request

    try:
        request = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/kosika/passed",
                                         data=json.dumps({"since": since}).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as answer:
            print("kept", json.load(answer).get("kept", 0), "answers that passed")
    except OSError:
        pass


def router_up() -> bool:
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", PORT)) == 0


def start_router() -> subprocess.Popen | None:
    if router_up():
        return None
    env = dict(os.environ)
    for name in ("GROQ_API_KEY", "OPENROUTER_API_KEY"):
        env.setdefault(name, user_variable(name))
    process = subprocess.Popen(
        [sys.executable, str(ROUTER), "--port", str(PORT)], env=env
    )
    for _ in range(40):
        if router_up():
            return process
        time.sleep(0.25)
    raise SystemExit("the model router did not start")


def user_variable(name: str) -> str:
    """A key kept as a Windows user variable, which a new shell may not have."""
    if os.name != "nt":
        return ""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return str(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return ""


def run_coder(folder: Path, agents: Path, text: str) -> dict:
    shutil.copytree(
        HERE.parent,
        agents / "coder",
        ignore=shutil.ignore_patterns("memory", "runs", "daily"),
    )

    def binder(program):
        return {
            model.name: OpenAICompatibleProvider(
                f"http://127.0.0.1:{PORT}/v1",
                "kosika-virtual",
                api_key="none",
                protocol="openai",
                timeout=600,
            )
            for model in program.models
            if model.provider is None
        }

    app = App(
        agents,
        bind_models=binder,
        model_label="kosika-virtual",
        allow_folder=folder,
        allow_shell=True,
    )
    agent = next(key for key in app.agents() if key.startswith("coder/"))
    started = time.monotonic()
    job = app.start(agent, {"text": text})
    name = os.environ.get("KOSIKA_AGENT") or "coder-1"
    show_overlay()
    while job.state in ("running", "waiting") and time.monotonic() - started < 3600:
        if job.state == "waiting":
            app.decide(job.id, True)
        say_working(name, text, folder, started)
        time.sleep(0.5)
    overlay_stop(name)
    return {
        "state": job.state,
        "seconds": round(time.monotonic() - started),
        "said": getattr(job, "text", "") or "",
        "error": job.error or "",
    }


_overlay = None


def _overlay_module():
    """overlay.py beside the hands, loaded once; None when it is not there."""
    global _overlay
    if _overlay is None and OVERLAY.exists():
        import importlib.util

        spec = importlib.util.spec_from_file_location("kosika_overlay", OVERLAY)
        module = importlib.util.module_from_spec(spec)
        sys.modules["kosika_overlay"] = module
        spec.loader.exec_module(module)
        _overlay = module
    return _overlay


def show_overlay() -> None:
    """Start the overlay if it is not already up (it keeps itself to one)."""
    if os.name != "nt" or not OVERLAY.exists() or os.environ.get("KOSIKA_NO_OVERLAY"):
        return
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen([sys.executable, str(OVERLAY), "--quit-after", "600"], creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def newest_file(folder: Path, since: float) -> Path | None:
    """The file the coder wrote last in its copy, if it wrote one since `since`."""
    newest, at = None, since
    for path in folder.rglob("*"):
        if "__pycache__" in path.parts or ".gocache" in path.parts or not path.is_file():
            continue
        try:
            moment = path.stat().st_mtime
        except OSError:
            continue
        if moment > at and path.suffix not in (".jsonl", ".exe", ".pyc"):
            newest, at = path, moment
    return newest


_last_look = [0.0, None]


def say_working(name: str, text: str, folder: Path, started: float) -> None:
    """Tell the overlay this coder is at work: what it was asked, and the end
    of the line it wrote last, which the overlay types out beside its pointer.
    Only what the coder really wrote is shown; nothing is made up."""
    shown = _overlay_module()
    if shown is None:
        return
    now = time.time()
    if now - _last_look[0] > 3:  # walking the copy is not free; every few seconds is enough
        _last_look[0] = now
        _last_look[1] = newest_file(folder, now - (time.monotonic() - started))
    typing = None
    path = _last_look[1]
    if path is not None:
        try:
            lines = [l for l in path.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
            typing = f"{path.name}: {lines[-1].strip()}" if lines else path.name
        except OSError:
            pass
    doing = text if len(text) <= 60 else text[:57].rstrip() + "..."
    try:
        shown.beat(name, doing, typing)
    except OSError:
        pass


def overlay_stop(name: str) -> None:
    shown = _overlay_module()
    if shown is not None:
        shown.stop(name)


#: How many times a coder may try a request: the second time it is told what
#: the checks found (KOSIKA_CODER_TRIES=1 for a single try).
TRIES = max(1, int(os.environ.get("KOSIKA_CODER_TRIES", "2")))


QUEUE = HERE / "queue.kos"


def queue_kos(workflow: str, name: str, payload: dict) -> dict | None:
    """A workflow of queue.kos, where the to-do list's rules are written in
    Kosika: what it gives, or None when it could not be run, and the caller
    falls back to the same rule in Python so a run is never held up."""
    import ast
    import tempfile

    try:
        with tempfile.TemporaryDirectory() as work:
            ask = Path(work) / "ask.json"
            ask.write_text(json.dumps({name: payload}), encoding="utf-8")
            done = subprocess.run([sys.executable, "-m", "kosika.cli", "run", str(QUEUE), "--workflow", workflow, "--input", str(ask)],
                                  cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                                  env={**os.environ, "PYTHONPATH": str(REPO)})
    except (OSError, subprocess.SubprocessError):
        return None
    for line in reversed(done.stdout.splitlines()):
        if line.strip().startswith("{"):
            try:
                return ast.literal_eval(line.strip())
            except (ValueError, SyntaxError):
                return None
    return None


def second_try(text: str, report: Path) -> str:
    """The request again, with the lines of the checks that failed (queue.kos)."""
    try:
        lines = report.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    told = queue_kos("retry", "r", {"text": text, "report": lines})
    if told and isinstance(told.get("text"), str):
        return told["text"]
    found = [line.strip() for line in lines if line.strip().startswith("FAIL") or line.startswith("Why it did not pass")]
    notes = "\n".join(f"- {line}" for line in found[:12]) or "- the checks did not pass"
    return (f"{text}\n\nYour first try is in this folder already, but it did not pass these checks:\n{notes}\n"
            "Fix the files so every one of these passes. Keep what was right.")


DEVELOP = HERE / "develop.kos"


KEEP = HERE / "keep.kos"


def offer_to_keep(place: Path, text: str, today: str) -> None:
    """Start keep.kos for a run that passed: it makes a branch daily/RUN from
    studio in a worktree of its own, applies the change, commits it and keeps
    it as a training example in WORK/approved, each step waiting for the
    person's yes (WORK/asks, answerable from the phone). It never pushes:
    push stays false until the person decides otherwise. Without keep.kos
    beside this file, nothing happens."""
    if not KEEP.exists():
        return
    asks = WORK / "asks"
    asks.mkdir(exist_ok=True)
    kept = place / "kept.json"
    kept.write_text(json.dumps({"k": {"repo": str(REPO).replace("\\", "/"), "work": str(WORK).replace("\\", "/"),
                                      "run": place.name, "request": text, "passed": True, "date": today,
                                      "push": False}}), encoding="utf-8")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen([sys.executable, "-m", "kosika.cli", "run", str(KEEP), "--input", str(kept),
                      "--allow-folder", str(WORK), "--allow-shell", "--approve-in", str(asks)],
                     cwd=WORK, env={**os.environ, "PYTHONPATH": str(REPO)}, creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("keep.kos is waiting for your yes in", asks)


def judge_in_kosika(place: Path, text: str, files: list[str], ran: dict, today: str) -> dict:
    """Run develop.kos on a finished coder run: the checks, the verdict,
    report.md and a line in kosika-daily/coders.kl. Its steps change files
    and start programs, so it asks before each; this driver, the person's
    stand-in for the day, says yes, as it does for the coder."""
    agents = place / "_judge"
    agents.mkdir(exist_ok=True)
    shutil.copy(DEVELOP, agents / "develop.kos")
    # The design checks develop.kos runs on icons, tokens and Studio plans.
    shutil.copy(HERE / "design_check.py", agents / "design_check.py")
    # develop.kos asks no model; the app is told so by a binder that binds none.
    app = App(agents, bind_models=lambda program: {}, model_label="none", allow_folder=WORK, allow_shell=True)
    agent = next(key for key in app.agents() if key.startswith("develop"))
    done = {"request": text, "run": place.name, "files": files, "state": ran["state"],
            "said": ran["said"] or ran["error"], "seconds": ran["seconds"], "date": today}
    job = app.start(agent, {"inputs": {"d": done}})
    started = time.monotonic()
    while job.state in ("running", "waiting") and time.monotonic() - started < 900:
        if job.state == "waiting":
            app.decide(job.id, True)
        time.sleep(0.3)
    result = getattr(job, "result", None) or {}
    if job.state != "done" or "passed" not in result:
        raise SystemExit(f"develop.kos could not judge {place.name}: {job.state} {job.error}")
    return result


def changed_files(before: Path, after: Path) -> list[str]:
    names = []
    for path in sorted(after.rglob("*")):
        if (
            not path.is_file()
            or "__pycache__" in path.parts
            or path.suffix in (".exe", ".jsonl")
        ):
            continue
        relative = path.relative_to(after)
        old = before / relative
        # A file written back with other line ends, or not changed at all,
        # is not a change.
        if not old.exists() or old.read_bytes().replace(
            b"\r\n", b"\n"
        ) != path.read_bytes().replace(b"\r\n", b"\n"):
            names.append(relative.as_posix())
    return names


def checked(after: Path, files: list[str]) -> list[tuple[str, bool, str]]:
    """The checks a person can trust without reading the model's words."""
    results = []
    tests = [f for f in files if f.startswith("tests/") and f.endswith(".py")]
    if tests:
        ran = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
            cwd=after,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        results.append(
            (
                "pytest " + " ".join(tests),
                ran.returncode == 0,
                (ran.stdout + ran.stderr)[-1500:],
            )
        )
    if any(f.endswith(".go") for f in files) and shutil.which("go"):
        ran = subprocess.run(
            ["go", "-C", "native", "vet", "."],
            cwd=after,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        results.append(
            (
                "go vet (the Go engine compiles)",
                ran.returncode == 0,
                (ran.stdout + ran.stderr)[-1500:],
            )
        )
    return results


#: Every change that passed its checks, as a training example for a Kosika
#: coder model: what was asked, each changed file before and after. Only
#: what passed is kept, so the examples teach what works.
EXAMPLES = WORK / "examples.jsonl"


def save_example(request: str, before: Path, after: Path, files: list[str]) -> None:
    def text_of(path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n") if path.exists() else ""

    example = {
        "request": request,
        "files": [{"path": name, "before": text_of(before / name), "after": text_of(after / name)} for name in files],
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    with EXAMPLES.open("a", encoding="utf-8") as out:
        out.write(json.dumps(example) + "\n")


#: A request a swarm member has taken and is working on.
TAKEN = "- [~] "

#: How long a swarm member may take before it is stopped.
MEMBER_SECONDS = int(os.environ.get("KOSIKA_CODER_SECONDS", "2700"))


def mark_line(text: str, mark: str, note: str = "") -> None:
    """Mark the line holding `text`, open or taken, in todo.md as it is now:
    several swarm members write it, so each reads it again just before, under
    a lock file."""
    lock = TODO.with_suffix(".lock")
    for _ in range(600):
        try:
            lock.mkdir()
            break
        except FileExistsError:
            time.sleep(0.1)
    try:
        lines = TODO.read_text(encoding="utf-8").splitlines()
        moved = queue_kos("mark", "m", {"lines": lines, "text": text, "mark": mark, "note": note})
        if moved and isinstance(moved.get("lines"), list) and len(moved["lines"]) == len(lines):
            lines = [str(line) for line in moved["lines"]]
        else:
            for i, line in enumerate(lines):
                if line[6:].split(" (20")[0].strip() == text and line[:6] in ("- [ ] ", TAKEN):
                    lines[i] = mark + text + note
                    break
        TODO.write_text("\n".join(lines) + "\n", encoding="utf-8")
    finally:
        lock.rmdir()


SPLIT = HERE / "split.kos"
#: A request split into small ones is kept in the list with this mark, so it
#: is not taken again and the list still says where its steps came from.
SPLIT_MARK = "- [>] "


def is_big(text: str) -> bool:
    """As split.kos says: no examples to test against, or very long."""
    return "examples:" not in text or len(text) > 900


def split_big(text: str, most: int = 5) -> list[str]:
    """split.kos's small requests for one big one ("- [ ] ..." lines), or none."""
    import ast
    import tempfile

    with tempfile.TemporaryDirectory() as work:
        ask = Path(work) / "big.json"
        ask.write_text(json.dumps({"b": {"request": text, "most": most}}), encoding="utf-8")
        done = subprocess.run([sys.executable, "-m", "kosika.cli", "run", str(SPLIT), "--workflow", "split", "--input", str(ask),
                               "--model", "kosika-virtual", "--model-url", f"http://127.0.0.1:{PORT}/v1"],
                              cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
                              env={**os.environ, "PYTHONPATH": str(REPO)})
    for line in reversed(done.stdout.splitlines()):
        if line.strip().startswith("{") and "lines" in line:
            return [str(item) for item in ast.literal_eval(line.strip()).get("lines", [])]
    return []


def split_open_requests() -> int:
    """Each open request too big to pass as it is becomes the small ones
    split.kos makes of it, put in its place. Returns how many were split."""
    lines = TODO.read_text(encoding="utf-8").splitlines()
    out, split = [], 0
    for line in lines:
        text = line[6:].strip()
        if line.startswith("- [ ] ") and is_big(text):
            small = split_big(text)
            if small:
                out.append(SPLIT_MARK + text + f" (split into {len(small)} small requests below)")
                out.extend(small)
                split += 1
                continue
        out.append(line)
    if split:
        TODO.write_text("\n".join(out) + "\n", encoding="utf-8")
    return split


GAPS = HERE / "gaps.kos"
#: Kosika AI's memory folder (its --memory FOLDER), where the Brain keeps its
#: timeline as timeline.json. Unset: gaps.kos reads only the coders' log.
BRAIN = os.environ.get("KOSIKA_BRAIN_MEMORY", "")
#: How many new requests gaps.kos may add in one run.
GAPS_MOST = int(os.environ.get("KOSIKA_GAPS_MOST", "4"))
#: A failed request asked again in another shape is kept with this mark.
MOVED_MARK = "- [>] "


def brain_episodes() -> list[dict]:
    """The Brain's timeline, oldest first, or none when it cannot be read."""
    if not BRAIN:
        return []
    try:
        entries = json.loads((Path(BRAIN) / "timeline.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(entries, dict):
        return []
    episodes = [e for e in entries.values() if isinstance(e, dict) and isinstance(e.get("ask"), str)]
    return sorted(episodes, key=lambda e: (str(e.get("at", "")), e.get("seq", 0)))


def gap_requests() -> dict:
    """What gaps.kos (in Kosika) makes of what failed: new request lines, the
    failed requests they replace, and a note for each."""
    import ast
    import tempfile

    WORK.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as work:
        ask = Path(work) / "gaps.json"
        ask.write_text(json.dumps({"a": {"todo": TODO.read_text(encoding="utf-8"), "log": "coders.kl",
                                         "episodes": brain_episodes(), "most": GAPS_MOST}}), encoding="utf-8")
        done = subprocess.run([sys.executable, "-m", "kosika.cli", "run", str(GAPS), "--workflow", "gaps", "--input", str(ask),
                               "--allow-folder", str(WORK)],
                              cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
                              env={**os.environ, "PYTHONPATH": str(REPO)})
    for line in reversed(done.stdout.splitlines()):
        if line.strip().startswith("{") and "lines" in line:
            found = ast.literal_eval(line.strip())
            return {"lines": [str(x) for x in found.get("lines", [])], "moved": [str(x) for x in found.get("moved", [])],
                    "notes": [str(x) for x in found.get("notes", [])]}
    print("gaps.kos found nothing to add:", (done.stderr or done.stdout).strip()[-300:], flush=True)
    return {"lines": [], "moved": [], "notes": []}


def add_gap_requests() -> int:
    """Kosika's own next work: each request gaps.kos found goes into todo.md,
    right after the failed line it replaces (marked [>]) or at the end.
    Returns how many were added."""
    found = gap_requests()
    if not found["lines"]:
        return 0
    today = datetime.date.today().isoformat()
    # gaps.kos gives the replacements of failed requests first, in order.
    moved = dict(zip(found["moved"], found["lines"]))
    rest = found["lines"][len(found["moved"]):]
    lock = TODO.with_suffix(".lock")
    for _ in range(600):
        try:
            lock.mkdir()
            break
        except FileExistsError:
            time.sleep(0.1)
    try:
        out = []
        for line in TODO.read_text(encoding="utf-8").splitlines():
            text = line[6:].split(" (20")[0].strip()
            if line.startswith("- [!] ") and text in moved:
                out.append(MOVED_MARK + text + f" ({today}, asked again below as a function written in Kosika, by gaps.kos)")
                out.append(moved.pop(text))
                continue
            out.append(line)
        out.extend([*moved.values(), *rest])
        TODO.write_text("\n".join(out) + "\n", encoding="utf-8")
    finally:
        lock.rmdir()
    for note in found["notes"]:
        print("gaps.kos:", note, flush=True)
    return len(found["lines"])


def swarm(size: int) -> None:
    """Several coders at once, one request each, sharing the virtual model
    pool. Each works in its own copy and is checked on its own; nothing waits
    for the others but the end of the run."""
    router = start_router()
    # Kosika's own next work first: what failed, asked again in a shape that
    # can pass, and drawings the Brain saw fail (gaps.kos, in Kosika).
    if add_gap_requests():
        print("Added requests Kosika found from what failed (gaps.kos).", flush=True)
    # Big requests are split into small ones first: the shape that passes.
    if split_open_requests():
        print("Split big requests into small ones (split.kos).", flush=True)
    lines = TODO.read_text(encoding="utf-8").splitlines()
    # Which requests this swarm takes, in queue.kos; each is marked as taken.
    taken = queue_kos("take", "a", {"lines": lines, "size": size})
    if taken and isinstance(taken.get("texts"), list):
        open_ones = [str(text) for text in taken["texts"]]
    else:
        open_ones = [line[6:].strip() for line in lines if line.startswith("- [ ] ")][:size]
    if not open_ones:
        print("Nothing is open in todo.md.")
        if router is not None:
            router.terminate()
        return
    for text in open_ones:
        mark_line(text, TAKEN)
    started = time.monotonic()
    alone = dict(os.environ, KOSIKA_SWARM="1") if len(open_ones) > 1 else dict(os.environ)
    # Each member works as one of the kits' agents, so it has its own
    # colour and pointer on the overlay.
    members = [subprocess.Popen([sys.executable, __file__, "--todo", str(TODO), "--take", text],
                                env={**alone, "KOSIKA_AGENT": NAMES[at % len(NAMES)]})
               for at, text in enumerate(open_ones)]
    # A member that hangs (a judge step stuck after a failure) must not hold
    # the whole swarm: after MEMBER_SECONDS it is stopped and its request
    # marked as not passed.
    deadline = time.monotonic() + MEMBER_SECONDS
    for text, member in zip(open_ones, members):
        try:
            member.wait(timeout=max(1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            member.kill()
            mark_line(text, "- [!] ", f" ({datetime.date.today().isoformat()}, stopped after {MEMBER_SECONDS // 60} minutes)")
    if router is not None:
        router.terminate()
    print(f"Swarm of {len(members)}: {round(time.monotonic() - started)} s for all of them together.")



def shared_engine(native: Path) -> Path | None:
    """A kosika-native built from NATIVE, kept under WORK/.engine by the hash
    of its Go sources, so coders building from the same studio share one.
    The first coder builds it behind a lock folder; the others wait for it.
    None when it cannot be built, and the coders build their own as before."""
    import hashlib
    import time
    if not native.is_dir():
        return None
    digest = hashlib.sha256()
    for path in sorted(native.glob("*.go")) + sorted(native.glob("go.*")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    key = digest.hexdigest()[:16]
    folder = WORK / ".engine"
    folder.mkdir(exist_ok=True)
    engine = folder / (f"kosika-native-{key}" + (".exe" if os.name == "nt" else ""))
    lock = folder / f"{key}.lock"
    for _ in range(120):
        if engine.is_file():
            return engine
        try:
            lock.mkdir()
        except FileExistsError:
            time.sleep(5)
            continue
        try:
            building = engine.with_name(engine.name + ".part")
            made = subprocess.run(["go", "-C", str(native), "build", "-o", str(building), "."],
                                  capture_output=True, text=True, timeout=900)
            if made.returncode == 0 and building.is_file():
                building.replace(engine)
                return engine
            return None
        except (OSError, subprocess.SubprocessError):
            return None
        finally:
            shutil.rmtree(lock, ignore_errors=True)
    return None

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--swarm", type=int, default=0, help="run this many coders at once, one request each")
    parser.add_argument("--take", help="(a swarm member) work on this request, already marked as taken")
    parser.add_argument("--todo", help="take requests from this file instead of todo.md (the scorecard uses its own copy)")
    args = parser.parse_args()
    if args.todo:
        global TODO
        TODO = Path(args.todo).resolve()
    if args.swarm:
        swarm(args.swarm)
        return
    lines = TODO.read_text(encoding="utf-8").splitlines()
    if args.take:
        text = args.take
    else:
        number = next_request(lines)
        if number is None:
            print("Nothing is open in todo.md.")
            return
        text = lines[number][len("- [ ] ") :].strip()
    if args.dry_run:
        print("Next:", text)
        return

    today = datetime.date.today().isoformat()
    count = 1
    while True:
        place = WORK / f"{today}-{count}"
        try:
            place.mkdir(parents=True)
            break
        except FileExistsError:
            count += 1
    # What it was asked, for the live board (swarm_watch.py) while it works.
    (place / "request.txt").write_text(text, encoding="utf-8")
    # A Go build folder of its own: coders of a swarm building at once in
    # one shared folder lock each other out ("Access is denied").
    os.environ["GOCACHE"] = str(place / ".gocache")
    # Started warm: a cold build of the Go engine takes minutes when coders
    # build at once, longer than a step may run. A copy of a cache an earlier
    # run left is cheap, and each coder still builds in its own.
    warm = WORK / ".gocache-warm"
    if warm.is_dir():
        shutil.copytree(warm, place / ".gocache", dirs_exist_ok=True)
    # And a temporary folder of its own: Go builds in TEMP, which a coder
    # started from some Pythons may not write ("Access is denied").
    (place / ".tmp").mkdir(exist_ok=True)
    for name in ("GOTMPDIR", "TMP", "TEMP"):
        os.environ[name] = str(place / ".tmp")
    before, after = place / "before", place / "kosika"
    before.mkdir(parents=True)
    copy_studio(before)
    shutil.copytree(before, after)
    # One Go engine for every coder of the swarm, built once from this copy
    # of studio. A std task tests its edited .kos file by path with it, so no
    # coder rebuilds the engine (four at once ran past the 180-second limit
    # on 2026-11-20).
    engine = shared_engine(before / "native")
    if engine is not None:
        os.environ["KOSIKA_STD_ENGINE"] = str(engine)

    router = start_router()
    asked = text
    try:
        for attempt in range(TRIES):
            ran = run_coder(after, place / ("_agents" if attempt == 0 else f"_agents-{attempt + 1}"), asked)
            # What running the tests and the engines left behind is not the change:
            # compiled Python, traces and built engines stay out of the patch.
            for left in [*after.rglob("__pycache__"), *after.rglob(".pytest_cache")]:
                shutil.rmtree(left, ignore_errors=True)
            for left in [*after.rglob("*.jsonl"), *after.rglob("*.exe")]:
                if not (before / left.relative_to(after)).exists():
                    left.unlink(missing_ok=True)
            files = changed_files(before, after)
            diff = subprocess.run(
                ["git", "diff", "--no-index", "--no-prefix", "before", "kosika"],
                cwd=place,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            ).stdout
            diff = diff.replace("--- before/", "--- a/").replace("+++ kosika/", "+++ b/")
            diff = (
                "\n".join(
                    line.replace("diff --git before/", "diff --git a/")
                    .replace("diff --git kosika/", "diff --git a/", 1)
                    .replace(" kosika/", " b/", 1)
                    if line.startswith("diff --git")
                    else line
                    for line in diff.splitlines()
                )
                + "\n"
            )
            (place / "change.patch").write_text(diff, encoding="utf-8", newline="\n")
            # The verdict, the report and the record of the run are Kosika's:
            # develop.kos runs the checks and decides; this only hands it the facts.
            judged = judge_in_kosika(place, text, files, ran, today)
            if judged["passed"] or attempt + 1 == TRIES or ran["state"] != "done":
                break
            # One more try on the same copy, told what the checks found, as a
            # reviewer would send a change back with notes.
            asked = second_try(text, place / "report.md")
            print(f"{place.name}: did not pass; trying once more with the checker's notes", flush=True)
    finally:
        if router is not None:
            router.terminate()
    passed = judged["passed"]
    with (place / "report.md").open("a", encoding="utf-8") as report:
        report.write("\n\nTo bring it into Kosika: `git apply " + str(place / "change.patch") + "`\n")
    (place / "run.json").write_text(
        json.dumps({**ran, "files": files, "passed": passed}, indent=1),
        encoding="utf-8",
    )
    if passed:
        save_example(text, before, after, files)
        if not os.environ.get("KOSIKA_SWARM"):
            keep_answers(time.time() - float(ran["seconds"]) - 5)
        offer_to_keep(place, text, today)
    shutil.rmtree(before, ignore_errors=True)

    mark_line(text, "- [x] " if passed else "- [!] ", f" ({today}, {place.name})")
    print(("PASSED " if passed else "DID NOT PASS ") + text)
    print(place / "report.md")


if __name__ == "__main__":
    main()
