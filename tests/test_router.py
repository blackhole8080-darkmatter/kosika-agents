"""The model router keeps each free model's day in mind: easy steps go to
the models on this computer first, and a cloud model running low on its
daily allowance is kept for the strong steps."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "models"))

import router  # noqa: E402

CONFIG = {
    "models": {
        "quick": {"id": "q", "url": "u", "key": "K", "per_day_tokens": 1000},
        "strong": {"id": "s", "url": "u", "key": "K", "per_day_tokens": 1000},
        "other": {"id": "o", "url": "u", "key": "K"},
        "local": {"id": "l", "url": "u", "key": ""},
    },
    "routes": {
        "easy": ["quick", "local", "other", "strong"],
        "quick": ["quick", "strong", "other", "local"],
        "strong": ["strong", "other", "local"],
    },
    "quick_when": {"max_chars": 2500},
    "easy_when": {"max_chars": 1500, "max_tokens": 400},
    "reserve": 0.25,
    "failure_words": ["Traceback"],
    "cool_seconds": 20,
    "timeout_seconds": 5,
}


def body(text: str, max_tokens: int | None = None, **more) -> dict:
    found = {"messages": [{"role": "user", "content": text}], **more}
    if max_tokens is not None:
        found["max_tokens"] = max_tokens
    return found


def answer(text: str, tokens: int | None = None) -> dict:
    reply = router.reply_of(text)
    if tokens is not None:
        reply["usage"] = {"total_tokens": tokens}
    return reply


class Scripted(router.Router):
    """A router whose models answer from a script, never the network."""

    def __init__(self, script: dict, allowance: router.Allowance | None = None) -> None:
        super().__init__(json.loads(json.dumps(CONFIG)), Path(tempfile.mkdtemp()) / "log.jsonl", allowance)
        self.script = script
        self.asked: list[str] = []

    def ask(self, name: str, body: dict) -> dict:
        self.asked.append(name)
        given = self.script[name]
        if isinstance(given, Exception):
            raise given
        return given


def daily_429() -> urllib.error.HTTPError:
    said = json.dumps({"error": {"message": "Rate limit reached on tokens per day (TPD): Limit 1000. Please try again in 8m7s."}})
    return urllib.error.HTTPError("u", 429, "Too Many Requests", {}, io.BytesIO(said.encode()))


class AllowanceTests(unittest.TestCase):
    def test_counts_only_models_with_an_allowance(self) -> None:
        a = router.Allowance(CONFIG)
        a.spend("quick", 300)
        a.spend("other", 999)
        self.assertAlmostEqual(a.left("quick"), 0.7)
        self.assertEqual(a.left("other"), 1.0)
        self.assertEqual(a.report(), {"quick": {"used": 300, "per_day": 1000}, "strong": {"used": 0, "per_day": 1000}})

    def test_a_used_up_day_is_counted_whole(self) -> None:
        a = router.Allowance(CONFIG)
        a.spend("strong", 10)
        a.used_up("strong")
        self.assertEqual(a.left("strong"), 0.0)

    def test_kept_on_disk_for_the_same_day_and_forgotten_the_next(self) -> None:
        path = Path(tempfile.mkdtemp()) / "allowance.json"
        day = ["2026-11-18"]
        first = router.Allowance(CONFIG, path, today=lambda: day[0])
        first.spend("quick", 500)
        again = router.Allowance(CONFIG, path, today=lambda: day[0])
        self.assertAlmostEqual(again.left("quick"), 0.5)
        day[0] = "2026-11-19"
        self.assertEqual(again.left("quick"), 1.0)
        self.assertEqual(router.Allowance(CONFIG, path, today=lambda: day[0]).left("quick"), 1.0)

    def test_tokens_come_from_the_service_or_are_estimated(self) -> None:
        self.assertEqual(router.tokens_of(body("hi"), answer("ok", 42)), 42)
        self.assertEqual(router.tokens_of(body("x" * 400), answer("y" * 400)), 201)


class PlanTests(unittest.TestCase):
    def test_a_short_ask_wanting_few_tokens_is_easy(self) -> None:
        r = Scripted({})
        self.assertEqual(r.difficulty(body("What is 2+2?", 200))[0], "easy")
        self.assertEqual(r.difficulty(body("What is 2+2?", 2000))[0], "quick")
        self.assertEqual(r.difficulty(body("What is 2+2?"))[0], "quick")
        self.assertEqual(r.difficulty(body("What is 2+2?", 200, tools=[{"function": {"name": "t"}}]))[0], "strong")
        self.assertEqual(r.difficulty(body("x" * 2000, 200))[0], "quick")

    def test_an_easy_step_goes_to_this_computer_first(self) -> None:
        self.assertEqual(Scripted({}).route("easy"), ["local", "quick", "other", "strong"])

    def test_a_cloud_model_running_low_is_kept_for_strong_steps(self) -> None:
        r = Scripted({})
        r.allowance.spend("quick", 800)
        self.assertEqual(r.route("quick"), ["strong", "other", "local", "quick"])
        self.assertEqual(r.route("easy"), ["local", "other", "strong", "quick"])
        r.allowance.spend("strong", 900)
        self.assertEqual(r.route("strong"), ["strong", "other", "local"])

    def test_an_answer_is_counted_against_the_day(self) -> None:
        r = Scripted({"quick": answer("4", 120)})
        code, _ = r.answer(body("What is 2+2?", 2000))
        self.assertEqual(code, 200)
        self.assertEqual(r.allowance.report()["quick"]["used"], 120)

    def test_a_daily_refusal_marks_the_day_used_up(self) -> None:
        r = Scripted({"quick": daily_429(), "strong": answer("4", 50)})
        code, _ = r.answer(body("What is 2+2?", 2000))
        self.assertEqual(code, 200)
        self.assertEqual(r.asked, ["quick", "strong"])
        self.assertEqual(r.allowance.left("quick"), 0.0)
        self.assertEqual(r.route("quick")[-1], "quick")


class LearningPlanTests(unittest.TestCase):
    def test_speed_ranking_does_not_undo_the_days_plan(self) -> None:
        import learning_router

        folder = Path(tempfile.mkdtemp())
        ledger = learning_router.Ledger(folder / "ledger.json")
        for _ in range(5):
            # quick has been the fastest by far: the ledger alone puts it first.
            ledger.note("plain", "quick", True, 100)
            ledger.note("plain", "local", True, 60000)
        r = learning_router.LearningRouter(json.loads(json.dumps(CONFIG)), folder / "log.jsonl", ledger)
        r.local.body = body("What is 2+2?", 200)
        r.local.kind = "plain"
        saved = learning_router.kosika_order
        learning_router.kosika_order = lambda *a: None
        try:
            self.assertEqual(r.route("easy")[0], "local")
            r.allowance.spend("quick", 900)
            self.assertEqual(r.route("quick")[-1], "quick")
        finally:
            learning_router.kosika_order = saved


if __name__ == "__main__":
    unittest.main()
