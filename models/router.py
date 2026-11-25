"""Kosika virtual model: one model name in front of several free ones.

    python router.py                      # serves http://127.0.0.1:8791/v1
    python router.py --config router.json --port 8791

Any Kosika program, agent or benchmark that speaks the OpenAI protocol can
use it in place of a single provider:

    kosika run main.kos --model kosika-virtual --model-url http://127.0.0.1:8791/v1 --model-protocol openai

What it adds to a single free model, and nothing else:

- routing: each request goes to the first model on its route that is not
  cooling down after a rate limit (HTTP 429) or failing
- adaptive compute: a short request with no tools and no sign of an earlier
  failure starts on a small fast model; anything else starts on the strong
  one; a reply that fails the checks below moves up the route
- verification: a reply must say something or call a tool; tool calls
  must name a tool that was offered and carry JSON arguments; a reply asked
  for as JSON must parse. A reply that fails is never passed on.
- patience: when every model refuses at once, it waits a moment and goes
  round once more
- a log: every request, the route it took and why, one JSON line each, so a
  change to the routing can be judged on what really happened (improve.py)

Keys are read from the user's environment (GROQ_API_KEY, OPENROUTER_API_KEY)
and never written anywhere. Only models that cost nothing are configured.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT = "Kosika/0.1 (virtual model)"
NAME = "kosika-virtual"
#: What each free cloud model has used of its day, kept between restarts.
ALLOWANCE = Path.home() / ".kosika" / "router-allowance.json"


def json_in(text: str) -> str | None:
    """The first whole JSON object in text, as text, or None."""
    start = text.find("{")
    while start != -1:
        depth = 0
        quoted = escaped = False
        for at in range(start, len(text)):
            ch = text[at]
            if quoted:
                escaped = ch == "\\" and not escaped
                if ch == '"' and not escaped:
                    quoted = False
                continue
            if ch == '"':
                quoted = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    piece = text[start:at + 1]
                    try:
                        json.loads(piece)
                        return piece
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None


def is_refused(error: Exception) -> bool:
    """Whether a call failed because nothing was listening at all."""
    reason = getattr(error, "reason", error)
    return isinstance(reason, ConnectionRefusedError) or "refused" in str(reason).lower() or "10061" in str(reason)


def reply_of(text: str) -> dict:
    """A chat reply carrying text, as an OpenAI-shaped service gives one."""
    return {"choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}


def load(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    for model in config["models"].values():
        # A model on this computer, such as one Ollama serves, needs no key.
        if model.get("key") and not os.environ.get(model["key"]):
            raise SystemExit(f"{model['key']} is not set; the router reads keys from the environment only.")
    return config


AGAIN_IN = re.compile(r"try again in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", re.IGNORECASE)


def asks_plain_text(body: dict) -> bool:
    """Whether the request asks for plain text, as Kosika does for a model `as text`."""
    for message in body.get("messages") or []:
        if message.get("role") == "system" and str(message.get("content") or "").startswith("Answer with only the text asked for, in plain text"):
            return True
    return False


def hollow(text: str) -> bool:
    """Whether a reply is JSON with nothing in it: {}, [], or only blank fields."""
    try:
        value = json.loads(text.strip())
    except ValueError:
        return False

    def blank(v) -> bool:
        if isinstance(v, dict):
            return all(blank(x) for x in v.values())
        if isinstance(v, list):
            return all(blank(x) for x in v)
        return v is None or (isinstance(v, str) and not v.strip())

    if isinstance(value, dict) and any(isinstance(v, list) and not v for v in value.values()):
        # {"ideas": []} is a deliberate answer, "nothing here", which the
        # program's own check accepts or refuses; asking every other model
        # the same question only wastes time and free quota.
        return False
    return isinstance(value, (dict, list)) and blank(value)


def used_up_for(error: urllib.error.HTTPError) -> float:
    """Seconds until a daily allowance comes back, or 0 for a short limit.

    Groq says it in words, "tokens per day ... Please try again in 28m11s",
    and gives no Retry-After for it.
    """
    try:
        said = error.read().decode("utf-8", "replace")
    except (OSError, ValueError):
        return 0.0
    if "per day" not in said.lower():
        return 0.0
    found = AGAIN_IN.search(said)
    if not found or not any(found.groups()):
        return 3600.0
    hours, minutes, seconds = (float(part or 0) for part in found.groups())
    return hours * 3600 + minutes * 60 + seconds


def tokens_of(body: dict, reply: dict) -> int:
    """Tokens a call spent: the service's own count, or about four characters
    a token when it gives none."""
    usage = reply.get("usage") or {}
    told = usage.get("total_tokens") or (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0)
    if told:
        return int(told)
    asked = sum(len(str(m.get("content") or "")) for m in body.get("messages") or [])
    try:
        said = len(reply["choices"][0]["message"].get("content") or "")
    except (KeyError, IndexError, TypeError):
        said = 0
    return (asked + said) // 4 + 1


class Allowance:
    """Each free cloud model's daily allowance, and how much of it is used
    today (UTC, as the services count). router.json gives a model its
    `per_day_tokens`; one without it is not counted. Kept on disk, so a
    restarted router still knows what the day has used."""

    def __init__(self, config: dict, path: Path | None = None, today=None) -> None:
        self.limits = {name: int(m["per_day_tokens"]) for name, m in config["models"].items() if m.get("per_day_tokens")}
        self.path = path
        self.today = today or (lambda: time.strftime("%Y-%m-%d", time.gmtime()))
        self.lock = threading.Lock()
        self.day, self.used = self.today(), {}
        if path is not None:
            try:
                kept = json.loads(path.read_text(encoding="utf-8"))
                if kept.get("day") == self.day:
                    self.used = {k: int(v) for k, v in kept.get("used", {}).items()}
            except (OSError, ValueError, AttributeError):
                pass

    def _fresh(self) -> None:
        if self.today() != self.day:
            self.day, self.used = self.today(), {}

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"day": self.day, "used": self.used}, indent=1), encoding="utf-8")
        except OSError:
            pass

    def spend(self, name: str, tokens: int) -> None:
        if name not in self.limits:
            return
        with self.lock:
            self._fresh()
            self.used[name] = self.used.get(name, 0) + max(0, int(tokens))
            self._save()

    def used_up(self, name: str) -> None:
        """The service said the day's allowance is gone: count it all used."""
        if name not in self.limits:
            return
        with self.lock:
            self._fresh()
            self.used[name] = max(self.used.get(name, 0), self.limits[name])
            self._save()

    def left(self, name: str) -> float:
        """The share of today's allowance left, 1.0 for a model not counted."""
        if name not in self.limits:
            return 1.0
        with self.lock:
            self._fresh()
            return max(0.0, 1.0 - self.used.get(name, 0) / self.limits[name])

    def report(self) -> dict:
        with self.lock:
            self._fresh()
            return {name: {"used": self.used.get(name, 0), "per_day": limit} for name, limit in self.limits.items()}


class Router:
    def __init__(self, config: dict, log: Path, allowance: Allowance | None = None) -> None:
        self.config = config
        self.log = log
        self.cool: dict[str, float] = {}
        self.lock = threading.Lock()
        self.allowance = allowance or Allowance(config)

    # ------------------------------------------------------------ deciding

    def difficulty(self, body: dict) -> tuple[str, str]:
        """Which route a request starts on, and why, from the request alone."""
        messages = body.get("messages") or []
        size = sum(len(json.dumps(m.get("content") or "")) for m in messages)
        last = json.dumps(messages[-1].get("content") or "") if messages else ""
        signs = [w for w in self.config["failure_words"] if w.lower() in last.lower()]
        rule = self.config["quick_when"]
        if body.get("tools"):
            return "strong", "tools offered"
        if signs:
            return "strong", "an earlier step failed: " + ", ".join(signs[:3])
        if size > rule["max_chars"]:
            return "strong", f"{size} characters"
        easy = self.config.get("easy_when")
        wants = body.get("max_tokens") or body.get("max_completion_tokens") or 0
        if easy and "easy" in self.config["routes"] and size <= easy["max_chars"] and 0 < wants <= easy["max_tokens"]:
            return "easy", f"{size} characters, {wants} tokens wanted, no tools"
        return "quick", f"{size} characters, no tools"

    def route(self, start: str) -> list[str]:
        return self.plan(start, self.config["routes"][start])

    def plan(self, start: str, route: list[str]) -> list[str]:
        """The route with the day's allowances in mind. An easy step goes to
        the models on this computer first, which cost no allowance. On easy
        and quick routes, a cloud model with less of its day left than
        `reserve` (router.json) moves to the back, so what it has left goes
        to the strong steps: planning, review, long or failed work."""
        if start == "easy":
            local = [m for m in route if not self.config["models"][m].get("key")]
            route = local + [m for m in route if m not in local]
        if start in ("easy", "quick"):
            reserve = float(self.config.get("reserve", 0))
            low = [m for m in route if self.allowance.left(m) < reserve]
            route = [m for m in route if m not in low] + low
        return route

    # ------------------------------------------------------------ checking

    @staticmethod
    def problem(body: dict, reply: dict) -> str | None:
        """What is wrong with a reply, or None when it may be passed on."""
        try:
            choice = reply["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError):
            return "no choices in the reply"
        text = message.get("content") or ""
        calls = message.get("tool_calls") or []
        if not text.strip() and not calls:
            return "empty reply" + (" (ran out of tokens)" if choice.get("finish_reason") == "length" else "")
        if not calls and hollow(text):
            # Ollama in JSON mode answers {} when it has nothing to say; a
            # record with every field blank is no answer either.
            return "empty JSON reply"
        offered = {t.get("function", {}).get("name") for t in body.get("tools") or []}
        for call in calls:
            name = call.get("function", {}).get("name")
            if name not in offered:
                return f"called a tool that was not offered: {name}"
            try:
                json.loads(call["function"].get("arguments") or "{}")
            except (ValueError, TypeError):
                return f"tool {name} got arguments that are not JSON"
        if (body.get("response_format") or {}).get("type") in ("json_object", "json_schema") and not calls and not asks_plain_text(body):
            try:
                json.loads(text[text.index("{"): text.rindex("}") + 1] if "{" in text else text)
            except ValueError:
                return "asked for JSON, and the reply is not JSON"
        return None

    # ------------------------------------------------------------ asking

    def ask(self, name: str, body: dict) -> dict:
        model = self.config["models"][name]
        sent = {k: v for k, v in body.items() if k not in ("model", "stream", "stream_options")}
        sent["model"] = model["id"]
        # Settings a model needs from this router, such as a lower reasoning
        # effort, as the config gives them for that model.
        sent.update(model.get("extra", {}))
        if "response_format" in sent and asks_plain_text(sent):
            # Older Kosika engines put a model `as text` in JSON mode too;
            # a server then wraps the text in an object of its own.
            del sent["response_format"]
        request = urllib.request.Request(
            model["url"], data=json.dumps(sent).encode(), method="POST",
            headers={**({"Authorization": "Bearer " + os.environ[model["key"]]} if model.get("key") else {}),
                     "Content-Type": "application/json", "User-Agent": AGENT})
        try:
            with urllib.request.urlopen(request, timeout=model.get("timeout_seconds", self.config["timeout_seconds"])) as answer:
                return json.load(answer)
        except urllib.error.HTTPError as error:
            said = error.read().decode("utf-8", "replace")
            if error.code == 400 and "Failed to generate JSON" in said and "response_format" in sent:
                return self.json_again(name, sent, said, error)
            # Read once above, so the caller can still say why.
            error.read = lambda said=said: said.encode()
            raise

    def json_again(self, name: str, sent: dict, said: str, error: urllib.error.HTTPError) -> dict:
        """Groq refuses a reply it asked for as JSON when its own check of
        the JSON fails, though the model's words are often good JSON with a
        little around them: Groq gives them back as `failed_generation`.
        Take the JSON out of them; failing that, ask the same model once
        without JSON mode, telling it to answer with only the object."""
        try:
            failed = json.loads(said).get("error", {}).get("failed_generation") or ""
        except ValueError:
            failed = ""
        found = json_in(failed)
        if found is not None:
            return reply_of(found)
        plain = {k: v for k, v in sent.items() if k != "response_format"}
        messages = list(plain.get("messages") or [])
        messages.append({"role": "user", "content": "Reply with only one JSON object, and nothing before or after it."})
        plain["messages"] = messages
        model = self.config["models"][name]
        request = urllib.request.Request(
            model["url"], data=json.dumps(plain).encode(), method="POST",
            headers={**({"Authorization": "Bearer " + os.environ[model["key"]]} if model.get("key") else {}),
                     "Content-Type": "application/json", "User-Agent": AGENT})
        with urllib.request.urlopen(request, timeout=model.get("timeout_seconds", self.config["timeout_seconds"])) as answer:
            reply = json.load(answer)
        try:
            text = reply["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, TypeError):
            return reply
        found = json_in(text)
        return reply_of(found) if found is not None else reply

    def answer(self, body: dict) -> tuple[int, dict]:
        start, why = self.difficulty(body)
        tried: list = []
        began = time.time()
        reply = self.attempt(body, start, tried)
        if reply is None and self.config.get("patience_seconds"):
            # Every free model refused at once, usually a burst of rate
            # limits: wait a moment, as a person would, and go round once more.
            time.sleep(self.config["patience_seconds"])
            with self.lock:
                self.cool.clear()
            reply = self.attempt(body, start, tried)
        self.write(start, why, tried, began, reply is not None)
        if reply is None:
            return 502, {"error": {"message": "No free model gave a reply that passed the checks.", "tried": tried}}
        return 200, reply

    def attempt(self, body: dict, start: str, tried: list) -> dict | None:
        """Ask down the route; the first reply that passes the checks, or None."""
        for name in self.route(start):
            with self.lock:
                if self.cool.get(name, 0) > time.time():
                    tried.append({"model": name, "skipped": "cooling down after a rate limit"})
                    continue
            t0 = time.time()
            try:
                reply = self.ask(name, body)
            except urllib.error.HTTPError as error:
                wait = float(error.headers.get("retry-after") or self.config["cool_seconds"])
                if error.code == 429:
                    # A day's allowance used up is not a burst: the service
                    # says when it comes back, and asking sooner only wastes
                    # every later request a round trip.
                    daily = used_up_for(error)
                    with self.lock:
                        self.cool[name] = time.time() + (daily if daily else min(wait, 120))
                    if daily:
                        self.allowance.used_up(name)
                step = {"model": name, "status": error.code, "ms": int((time.time() - t0) * 1000)}
                if error.code == 429 and daily:
                    step["why"] = f"daily allowance used up; resting {daily / 60:.0f} min"
                tried.append(step)
                continue
            except (urllib.error.URLError, TimeoutError, ValueError) as error:
                tried.append({"model": name, "error": str(error)[:200], "ms": int((time.time() - t0) * 1000)})
                if is_refused(error):
                    # Nothing listens there (Ollama or a model server not
                    # started): leave it out for a minute, not a round trip
                    # on every request.
                    with self.lock:
                        self.cool[name] = time.time() + 60
                continue
            self.allowance.spend(name, tokens_of(body, reply))
            wrong = self.problem(body, reply)
            tried.append({"model": name, "status": 200, "ms": int((time.time() - t0) * 1000), "problem": wrong})
            if wrong is None:
                reply["model"] = NAME
                return reply
        return None

    def write(self, start: str, why: str, tried: list, began: float, ok: bool) -> None:
        line = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "start": start, "why": why, "ok": ok,
                "ms": int((time.time() - began) * 1000), "tried": tried}
        with self.lock, self.log.open("a", encoding="utf-8") as out:
            out.write(json.dumps(line) + "\n")


def handler(router: Router):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code: int, value: dict) -> None:
            data = json.dumps(value).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.rstrip("/").endswith("/models"):
                return self.send(200, {"object": "list", "data": [{"id": NAME, "object": "model"}]})
            if self.path.rstrip("/").endswith("/kosika/allowance"):
                return self.send(200, router.allowance.report())
            self.send(404, {"error": {"message": "not found"}})

        def do_POST(self):
            if not self.path.rstrip("/").endswith("/chat/completions"):
                return self.send(404, {"error": {"message": "not found"}})
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            code, reply = router.answer(body)
            if code == 502 and str(body.get("model") or "").endswith(":soft"):
                # A program asking for "kosika-virtual:soft" would rather
                # have an empty answer, which its own checks refuse and its
                # `otherwise` handles, than a failure that stops the whole
                # run and is asked again for minutes first.
                code, reply = 200, {**reply_of(""), "model": NAME, "kosika_gave_up": True}
            if not body.get("stream") or code != 200:
                return self.send(code, reply)
            # A streamed request gets the checked reply as one piece: nothing
            # is shown before it has passed the checks.
            message = reply["choices"][0]["message"]
            chunk = {"id": reply.get("id", "kv"), "object": "chat.completion.chunk", "model": NAME,
                     "choices": [{"index": 0, "delta": {"role": "assistant", "content": message.get("content") or ""},
                                  "finish_reason": reply["choices"][0].get("finish_reason")}],
                     "usage": reply.get("usage")}
            data = ("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(HERE / "router.json"))
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--log", default=str(Path.home() / ".kosika" / "virtual-router.jsonl"))
    parser.add_argument("--allowance", default=str(ALLOWANCE))
    args = parser.parse_args()
    log = Path(args.log)
    log.parent.mkdir(parents=True, exist_ok=True)
    config = load(Path(args.config))
    router = Router(config, log, Allowance(config, Path(args.allowance)))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(router))
    print(f"{NAME} on http://127.0.0.1:{args.port}/v1, log {log}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
