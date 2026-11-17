"""Watch the overlay with three agents coming and going (about 40 seconds).

    python demo_overlay.py                 # starts the overlay if it is not up
    python demo_overlay.py --shots FOLDER  # also keeps screenshots as it goes

coder-1 types a few lines of Kosika at spots it names, coder-2 runs tests and
wanders, the scout reads the web. They start one after another, so the edge
light goes from one colour to a blend of three, and back down to nothing.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import overlay  # noqa: E402

LINES = [
    "fn greet(name: Text) -> Text {",
    '  return f"Hello, {name}!"',
    "}",
    'test "greets by name" { expect greet("Asha") == "Hello, Asha!" }',
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=Path)
    parser.add_argument("--seconds", type=float, default=40)
    args = parser.parse_args()
    shown = subprocess.Popen([sys.executable, str(HERE / "overlay.py"), "--quit-after", "20"])
    start = time.time()
    shots = {4.0: "one-agent", 12.0: "three-agents", 20.0: "three-agents-later", 31.0: "two-left"}
    taken: set[float] = set()
    try:
        while (t := time.time() - start) < args.seconds:
            if t < 30:
                line = LINES[int(t / 6) % len(LINES)]
                typed = line[: int((t % 6) * 9)]
                spot = (520 + (int(t / 6) % 3) * 260, 330 + (int(t / 6) % 2) * 180)
                overlay.beat("coder-1", "writing greet.kos", typed, *spot)
            elif t < 31:
                overlay.stop("coder-1")
            if 3 < t < 24:
                overlay.beat("coder-2", "running greet.kos's tests")
            elif t >= 24:
                overlay.stop("coder-2")
            if 6 < t < 34:
                overlay.beat("scout", "reading the Kosika docs")
            elif t >= 34:
                overlay.stop("scout")
            for at, name in shots.items():
                if args.shots and t >= at and at not in taken:
                    from PIL import ImageGrab

                    args.shots.mkdir(parents=True, exist_ok=True)
                    ImageGrab.grab(all_screens=True).save(args.shots / f"{int(at):02d}-{name}.png")
                    taken.add(at)
            time.sleep(0.25)
    finally:
        for name in ("coder-1", "coder-2", "scout"):
            overlay.stop(name)
    print(f"done; the overlay ends itself after 20 quiet seconds (pid {shown.pid})")


if __name__ == "__main__":
    main()
