"""Kosi's voice loop: say "Hey Kosi", ask, and hear the answer.

    python agents/voice/kosi_voice.py --engine ..\\kn.exe              listen on the microphone
    python agents/voice/kosi_voice.py --engine ..\\kn.exe --say "hey kosi what time is it"
    python agents/voice/kosi_voice.py --check                          what this computer has for ears and a mouth

One turn goes round like this:

    microphone -> ears (speech to text) -> voice/main.kos (is it for Kosi? where does it go?)
      -> Kosi (agents/assistant) answers at once, or Kosika AI (agents/ai) works on it
      -> voice/main.kos `speak` (short sentences that sound right) -> mouth (text to speech)

The deciding is done by Kosika code (voice/main.kos), exactly and with no
model; this file only moves sound and text between the pieces.

Ears, best first:
    local   faster-whisper on this computer: free, offline, nothing leaves it
            (pip install faster-whisper; KOSI_WHISPER_MODEL, "base.en" by default)
    groq    Groq's free Whisper (GROQ_API_KEY); the recording goes to Groq
Mouth, best first:
    kosika  Kosika Studio's own voice (kosika.designs.speak): Kokoro on this
            computer if installed, else the other voices Studio knows
    windows the voice built into Windows (System.Speech), offline, nothing to install
    print   the words are printed, for a computer with no voice

The microphone needs `pip install sounddevice numpy`. Nothing here is
installed by this program; --check says what is missing.

After Kosi speaks, it listens for a few seconds without the wake words
(KOSI_AWAKE_SECONDS, 8), so a talk can go on: "and tomorrow?".
"""

from __future__ import annotations

import argparse
import io
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
AGENTS = HERE.parent
VOICE = HERE / "main.kos"
RATE = 16000
AWAKE_SECONDS = float(os.environ.get("KOSI_AWAKE_SECONDS", "8"))
ROUTER = os.environ.get("KOSIKA_ROUTER_URL", "http://127.0.0.1:8791/v1")
MODEL = os.environ.get("KOSI_MODEL", "kosika-virtual:soft")
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# The kosika repo beside this one (KOSIKA_HOME elsewhere), for Python's
# engine and Studio's voice.
KOSIKA_HOME = Path(os.environ.get("KOSIKA_HOME") or AGENTS.parent.parent / "kosika").resolve()
if (KOSIKA_HOME / "kosika").is_dir() and str(KOSIKA_HOME) not in sys.path:
    sys.path.insert(0, str(KOSIKA_HOME))


# ------------------------------------------------------------------ engine

def report_of(printed: str) -> dict[str, Any] | None:
    """The record the engine printed: JSON or the Python-shaped one."""
    import ast

    text = "\n".join(line for line in printed.splitlines() if not line.startswith("Trace: ")).strip()
    start = text.find("{")
    if start < 0:
        return None
    for read in (json.loads, ast.literal_eval):
        try:
            value = read(text[start:])
            if isinstance(value, dict):
                return value
        except (ValueError, SyntaxError):
            pass
    return None


class Engine:
    """Runs Kosika programs: the Go engine (kn) when given, else Python's."""

    def __init__(self, command: list[str]) -> None:
        self.command = command

    def run(self, program: Path, given: dict[str, Any], *, workflow: str = "", extra: list[str] | None = None,
            timeout: float = 120) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "given.json"
            path.write_text(json.dumps(given), encoding="utf-8")
            command = self.command + ["run", str(program), "--input", str(path), "--trace", str(Path(work) / "trace.jsonl")]
            if workflow:
                command += ["--workflow", workflow]
            env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(KOSIKA_HOME), os.environ.get("PYTHONPATH", "")]))}
            ran = subprocess.run(command + (extra or []), capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=timeout, cwd=work, creationflags=FLAGS, env=env, check=False)
        found = report_of(ran.stdout)
        if found is None:
            said = (ran.stderr or ran.stdout).strip().splitlines()
            raise RuntimeError(said[-1] if said else f"{program.name} printed nothing")
        return found


def default_engine(given: str = "") -> Engine:
    if given:
        return Engine([given])
    return Engine([sys.executable, "-m", "kosika.cli"])


# ------------------------------------------------------------------ ears

class Ears:
    name = "none"

    def hear(self, wav: bytes) -> str:  # pragma: no cover - replaced by each kind
        raise NotImplementedError


class LocalWhisper(Ears):
    """faster-whisper on this computer: free and offline."""

    name = "local"

    def __init__(self) -> None:
        from faster_whisper import (
            WhisperModel,  # noqa: F401 - checked here, loaded on first use
        )

        self._model = None

    def hear(self, wav: bytes) -> str:
        from faster_whisper import WhisperModel

        if self._model is None:
            self._model = WhisperModel(os.environ.get("KOSI_WHISPER_MODEL", "base.en"), device="cpu", compute_type="int8")
        segments, _ = self._model.transcribe(io.BytesIO(wav), language="en", beam_size=1, vad_filter=True)
        return " ".join(segment.text.strip() for segment in segments).strip()


class GroqWhisper(Ears):
    """Groq's free Whisper; the recording goes to Groq."""

    name = "groq"

    def __init__(self) -> None:
        self.key = os.environ.get("GROQ_API_KEY", "")
        if not self.key:
            raise RuntimeError("no GROQ_API_KEY")

    def hear(self, wav: bytes) -> str:
        import urllib.request
        import uuid

        edge = uuid.uuid4().hex
        body = (f'--{edge}\r\nContent-Disposition: form-data; name="model"\r\n\r\nwhisper-large-v3-turbo\r\n'
                f'--{edge}\r\nContent-Disposition: form-data; name="language"\r\n\r\nen\r\n'
                f'--{edge}\r\nContent-Disposition: form-data; name="file"; filename="said.wav"\r\n'
                f'Content-Type: audio/wav\r\n\r\n').encode() + wav + f"\r\n--{edge}--\r\n".encode()
        asked = urllib.request.Request("https://api.groq.com/openai/v1/audio/transcriptions", data=body, headers={
            "Authorization": f"Bearer {self.key}", "Content-Type": f"multipart/form-data; boundary={edge}", "User-Agent": "Kosika"})
        with urllib.request.urlopen(asked, timeout=60) as reply:
            return str(json.loads(reply.read()).get("text", "")).strip()


def best_ears() -> Ears | None:
    for kind in (LocalWhisper, GroqWhisper):
        try:
            return kind()
        except Exception:  # noqa: BLE001, S112 - a missing package or key just means the next kind
            continue
    return None


# ------------------------------------------------------------------ mouth

class Mouth:
    name = "print"

    def say(self, sentence: str) -> None:
        print(f"Kosi: {sentence}", flush=True)


class KosikaVoice(Mouth):
    """Studio's own voice (Kokoro on this computer first), played as WAV."""

    name = "kosika"

    def __init__(self) -> None:
        from kosika.designs import speak

        self._speak = speak
        if not self._speak("Ready."):
            raise RuntimeError("Studio has no voice on this computer")

    def say(self, sentence: str) -> None:
        print(f"Kosi: {sentence}", flush=True)
        play_wav(self._speak(sentence))


class WindowsVoice(Mouth):
    """The voice built into Windows: offline, nothing to install."""

    name = "windows"

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("not Windows")

    def say(self, sentence: str) -> None:
        print(f"Kosi: {sentence}", flush=True)
        words = sentence.replace("'", "''")
        script = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  f"$s.Rate = 0; $s.Speak('{words}')")
        subprocess.run(["powershell", "-NoProfile", "-Command", script], check=False, timeout=120, creationflags=FLAGS)


def best_mouth() -> Mouth:
    for kind in (KosikaVoice, WindowsVoice):
        try:
            return kind()
        except Exception:  # noqa: BLE001, S112 - try the next voice
            continue
    return Mouth()


def play_wav(wav: bytes) -> None:
    if not wav:
        return
    if os.name == "nt":
        import winsound

        winsound.PlaySound(wav, winsound.SND_MEMORY)
        return
    try:
        import sounddevice
        import soundfile

        samples, rate = soundfile.read(io.BytesIO(wav))
        sounddevice.play(samples, rate)
        sounddevice.wait()
    except ImportError:
        pass


# ------------------------------------------------------------------ microphone

def wav_of(samples: Any, rate: int = RATE) -> bytes:
    """16-bit mono WAV from float samples in -1..1."""
    import numpy

    out = io.BytesIO()
    with wave.open(out, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes((numpy.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return out.getvalue()


def utterances(threshold: float = 0.015, quiet_seconds: float = 0.8, most_seconds: float = 15) -> Any:
    """Each thing said into the microphone, as WAV: it starts when the sound
    is louder than `threshold` and ends after `quiet_seconds` of quiet."""
    import numpy
    import sounddevice

    blocks: queue.Queue = queue.Queue()
    block = int(RATE * 0.05)
    with sounddevice.InputStream(samplerate=RATE, channels=1, blocksize=block, dtype="float32",
                                 callback=lambda data, *_: blocks.put(data[:, 0].copy())):
        heard: list[Any] = []
        quiet = 0.0
        before: list[Any] = []
        while True:
            chunk = blocks.get()
            loud = float(numpy.sqrt(numpy.mean(chunk ** 2))) > threshold
            if not heard:
                before = (before + [chunk])[-6:]  # a little sound from before it got loud
                if loud:
                    heard = list(before)
                    quiet = 0.0
                continue
            heard.append(chunk)
            quiet = 0.0 if loud else quiet + 0.05
            if quiet >= quiet_seconds or len(heard) * 0.05 >= most_seconds:
                yield wav_of(numpy.concatenate(heard))
                heard, before = [], []


# ------------------------------------------------------------------ Kosi

class Kosi:
    """One voice talk: what was heard in, what Kosi says out."""

    def __init__(self, engine: Engine, mouth: Mouth, *, agents: Path = AGENTS, memory: Path | None = None,
                 model: list[str] | None = None, clock: Callable[[], float] = time.time) -> None:
        self.engine = engine
        self.mouth = mouth
        self.agents = agents
        self.memory = memory or agents / "assistant" / "memory"
        self.model = model if model is not None else ["--model", MODEL, "--model-url", ROUTER, "--model-timeout", "120"]
        self.clock = clock
        self.awake_until = 0.0
        self.working: list[threading.Thread] = []

    def awake(self) -> bool:
        return self.clock() < self.awake_until

    def say(self, text: str) -> None:
        if not text.strip():
            return
        spoken = self.engine.run(VOICE, {"answer": {"text": text}}, workflow="speak")
        for sentence in spoken.get("sentences", []):
            self.mouth.say(sentence)
        self.awake_until = self.clock() + AWAKE_SECONDS

    def turn(self, heard: str) -> dict[str, Any]:
        """Deal with one thing heard; gives the turn voice/main.kos decided."""
        turn = self.engine.run(VOICE, {"heard": {"text": heard, "awake": self.awake()}})
        if not turn.get("to_kosi"):
            return turn
        route = turn.get("route")
        if route in ("wake", "sleep", "stop"):
            self.say(turn.get("reply", ""))
            self.awake_until = self.clock() + AWAKE_SECONDS if turn.get("listen_again") else 0.0
            return turn
        if route == "ai":
            self.say(turn.get("reply", ""))
            job = threading.Thread(target=self._work, args=(turn["ask"],), daemon=True)
            self.working.append(job)
            job.start()
            return turn
        self.say(self.answer(turn["ask"]))
        return turn

    def answer(self, ask: str) -> str:
        """Kosi, the assistant, answers at once."""
        self.memory.mkdir(parents=True, exist_ok=True)
        try:
            said = self.engine.run(self.agents / "assistant" / "main.kos", {"msg": {"text": ask}},
                                   extra=["--memory", str(self.memory)] + self.model, timeout=900)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            return f"Sorry, I couldn't answer that: {exc}"
        return str(said.get("text") or "I have nothing to say to that.")

    def _work(self, ask: str) -> None:
        """Kosika AI works on it, and Kosi says when it is done."""
        lessons = Path.home() / ".kosika" / "ai" / "lessons"
        lessons.mkdir(parents=True, exist_ok=True)
        try:
            report = self.engine.run(self.agents / "ai" / "main.kos", {"ask": {"text": ask, "most": 3}}, timeout=1800,
                                     extra=["--memory", str(lessons), "--allow-site", "en.wikipedia.org",
                                            "--max-output-tokens", "2000", "--max-function-calls", "400000"] + self.model)
            steps = report.get("steps", [])
            checked = sum(1 for step in steps if step.get("ok"))
            self.say(f"About {ask}: {report.get('answer', '')} {checked} of {len(steps)} steps passed their checks.")
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            self.say(f"I couldn't finish {ask}: {exc}")


# ------------------------------------------------------------------ main

def check() -> int:
    ears, mouth = best_ears(), best_mouth()
    try:
        import sounddevice  # noqa: F401

        mic = "ready"
    except ImportError:
        mic = "missing: pip install sounddevice numpy"
    print(f"ears: {ears.name if ears else 'none: pip install faster-whisper, or set GROQ_API_KEY'}")
    print(f"mouth: {mouth.name}")
    print(f"microphone: {mic}")
    return 0 if ears and mic == "ready" else 1


def main(argv: list[str] | None = None) -> int:
    given = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    given.add_argument("--engine", default="", help="the Go engine (kn); Python's is used without it")
    given.add_argument("--say", default="", help="one turn from these words instead of the microphone")
    given.add_argument("--check", action="store_true", help="say what this computer has for ears, mouth and microphone")
    given.add_argument("--memory", default="", help="Kosi's memory folder (agents/assistant/memory)")
    args = given.parse_args(argv)
    if args.check:
        return check()
    kosi = Kosi(default_engine(args.engine), best_mouth(), memory=Path(args.memory) if args.memory else None)
    if args.say:
        kosi.turn(args.say)
        for job in kosi.working:
            job.join()
        return 0
    ears = best_ears()
    if ears is None:
        print("Kosi has no ears: pip install faster-whisper, or set GROQ_API_KEY.", file=sys.stderr)
        return 1
    print(f"Listening (ears: {ears.name}, mouth: {kosi.mouth.name}). Say \"Hey Kosi\". Ctrl+C stops.", flush=True)
    try:
        for wav in utterances():
            text = ears.hear(wav)
            if text:
                print(f"heard: {text}", flush=True)
                kosi.turn(text)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
