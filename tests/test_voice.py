"""Kosi's voice loop: the right thing heard goes to the right place, and
answers are spoken in short sentences. The engine is real; the ears,
mouth and other agents are stand-ins."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agents" / "voice"))

import kosi_voice


class Said(kosi_voice.Mouth):
    def __init__(self) -> None:
        self.lines: list[str] = []

    def say(self, sentence: str) -> None:
        self.lines.append(sentence)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Stand(kosi_voice.Engine):
    """The real engine for voice/main.kos; the assistant and Kosika AI answer as told."""

    def __init__(self, answers: dict[str, dict]) -> None:
        super().__init__(kosi_voice.default_engine().command)
        self.answers = answers
        self.asked: list[tuple[str, dict]] = []

    def run(self, program, given, **kwargs):
        if program == kosi_voice.VOICE:
            return super().run(program, given, **kwargs)
        self.asked.append((program.parent.name, given))
        return self.answers[program.parent.name]


def kosi(answers: dict[str, dict] | None = None):
    engine = Stand(answers or {"assistant": {"text": "It is 6 pm. You have **two** reminders.", "suggest": []}})
    mouth, clock = Said(), Clock()
    return kosi_voice.Kosi(engine, mouth, clock=clock, memory=Path("/tmp/kosi-test-memory")), engine, mouth, clock


def test_only_what_is_said_to_kosi_is_answered() -> None:
    k, engine, mouth, _ = kosi()
    assert k.turn("the cosy cafe on the corner")["to_kosi"] is False
    assert engine.asked == [] and mouth.lines == []
    k.turn("Hey Kosi, what time is it?")
    assert engine.asked == [("assistant", {"msg": {"text": "what time is it"}})]
    assert mouth.lines == ["It is 6 pm. You have two reminders."]


def test_a_talk_goes_on_without_the_name_for_a_few_seconds() -> None:
    k, engine, _, clock = kosi()
    k.turn("hey kosi what time is it")
    clock.now += 3
    assert k.turn("and tomorrow?")["to_kosi"] is True
    clock.now += 60
    assert k.turn("and the day after?")["to_kosi"] is False
    assert len(engine.asked) == 2


def test_the_name_alone_and_goodbye() -> None:
    k, _, mouth, _ = kosi()
    k.turn("Hey Kosi.")
    assert mouth.lines == ["Yes?"] and k.awake()
    k.turn("good night")
    assert k.awake() is False


def test_work_goes_to_kosika_ai_and_kosi_says_when_it_is_done() -> None:
    report = {"answer": "Study maths on Monday and physics on Tuesday.",
              "steps": [{"ok": True}, {"ok": True}, {"ok": False}]}
    k, engine, mouth, _ = kosi({"ai": report})
    turn = k.turn("hey kosi plan my week around the exams")
    assert turn["route"] == "ai"
    for job in k.working:
        job.join()
    assert engine.asked == [("ai", {"ask": {"text": "plan my week around the exams", "most": 3}})]
    assert mouth.lines[0].startswith("On it.")
    assert any("2 of 3 steps passed" in line for line in mouth.lines)
