"""Checks a coder's design work the way a person would look at it.

    python design_check.py RUN REQUEST_FILE FILE...

develop.kos runs this for a change that touches design work: a .kos file in
Kosika Studio's app assets or one named for an icon, tokens, a theme or a
design, and Markdown plans for Studio. RUN is the run's folder (its copy of
Kosika is RUN/kosika). It prints one line per finding and exits 1 if any
finding is a failure, so "passed" means more than "it ran":

* a design .kos file must be built on Kosika Designs (import one of
  std/designs_*.kos or brand.kos), must carry tests, and its tests must pass
  on the copy's own engine;
* an icon (a function `..icon..(size: Number) -> Text`) is drawn to a
  picture by Chrome; when the request names a letter ("letter K"), the white
  shape on it must look like that letter: it is laid over the letter drawn in
  a bold font, and the two must mostly agree;
* a plan in Markdown must name at least three files that really are in
  Kosika today, so it is about this Studio and not a made-up one.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
DESIGN_NAMES = ("icon", "token", "theme", "design", "brand", "palette")
#: How much the drawn letter must agree with the real one (overlap / union).
LETTER_AGREES = 0.7  # a bold K in another font agrees 0.8; thin arms do not


def is_library(name: str) -> bool:
    """Kosika Designs itself: what design work is built on, not design work."""
    return name.rsplit("/", 1)[-1].startswith("designs_")


def is_design_kos(name: str) -> bool:
    return name.endswith(".kos") and not is_library(name) and ("app_assets/" in name or any(w in name.rsplit("/", 1)[-1] for w in DESIGN_NAMES))


def uses_designs(text: str) -> bool:
    return bool(re.search(r'import\s+"[^"]*(designs_[a-z_]+|brand)\.kos"', text))


def has_tests(text: str) -> bool:
    return bool(re.search(r'^\s*test\s+"', text, re.M))


def run_tests(copy: Path, name: str) -> tuple[bool, str]:
    done = subprocess.run([sys.executable, "-m", "kosika.cli", "test", name], cwd=copy, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=300)
    out = (done.stdout + done.stderr).strip()
    return done.returncode == 0 and " 0 failed" in out, out[-300:]


def icon_function(text: str) -> str | None:
    found = re.search(r"fn\s+(\w*icon\w*)\s*\(\s*\w+\s*:\s*Number\s*\)\s*->\s*Text", text)
    return found.group(1) if found else None


def draw_icon(copy: Path, name: str, function: str, size: int, out: Path) -> str:
    """The icon's SVG, from the coder's own function on the copy's engine."""

    module = Path(name)
    probe = copy / module.parent / f"_probe_{module.stem}.kos"
    # The coder's file with a workflow added, so a function it forgot to
    # export can still be drawn and looked at.
    source = (copy / name).read_text(encoding="utf-8", errors="replace")
    probe.write_text(source + f"\n\nrecord ProbeAsk {{ size: Number }}\nrecord ProbeSvg {{ svg: Text }}\n\n"
                     f"workflow probe_main(a: ProbeAsk) -> ProbeSvg {{\n  drawn = {{svg: {function}(a.size)}}\n}}\n", encoding="utf-8")
    try:
        ask = out.parent / "ask.json"
        ask.write_text('{"a": {"size": %d}}' % size, encoding="utf-8")
        done = subprocess.run([sys.executable, "-m", "kosika.cli", "run", str(probe), "--workflow", "probe_main", "--input", str(ask)],
                              cwd=out.parent, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=120, env={**__import__("os").environ, "PYTHONPATH": str(copy)})
    finally:
        probe.unlink(missing_ok=True)
    text = done.stdout.strip()
    start, end = text.find("<svg"), text.rfind("</svg>")
    if start < 0 or end < 0:
        raise RuntimeError(f"the icon function gave no SVG: {(done.stderr or text)[-200:]}")
    svg = text[start:end + 6].encode().decode("unicode_escape") if "\\\"" in text else text[start:end + 6]
    page = out.parent / "icon.html"
    page.write_text(f'<html><body style="margin:0;background:#000">{svg}</body></html>', encoding="utf-8")
    subprocess.run([str(CHROME), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                    f"--window-size={size},{size}", f"--user-data-dir={out.parent / 'chrome'}", f"--screenshot={out}",
                    page.as_uri()], capture_output=True, timeout=120)
    if not out.exists():
        raise RuntimeError("Chrome could not draw the icon")
    return svg


def letter_agreement(picture: Path, letter: str) -> float:
    """How much the white shape on the icon agrees with `letter` in a bold
    font, once both are fitted to the same box: overlap over union."""

    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    rgb = np.asarray(Image.open(picture).convert("RGB")).astype(int)
    white = (rgb.min(axis=2) > 215) & (rgb.max(axis=2) - rgb.min(axis=2) < 40)
    ys, xs = np.nonzero(white)
    if len(xs) < 20:
        return 0.0
    drawn = white[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    size = 200
    font = None
    for name in ("seguibl.ttf", "arialbd.ttf", "segoeuib.ttf"):
        try:
            font = ImageFont.truetype(str(Path(r"C:\Windows\Fonts") / name), size)
            break
        except OSError:
            continue
    canvas = Image.new("L", (size * 2, size * 2), 0)
    ImageDraw.Draw(canvas).text((size // 4, size // 4), letter, fill=255, font=font or ImageFont.load_default())
    real = np.asarray(canvas) > 127
    ys, xs = np.nonzero(real)
    real = real[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    shape = (64, 64)
    a = np.asarray(Image.fromarray(drawn.astype(np.uint8) * 255).resize(shape)) > 127
    b = np.asarray(Image.fromarray(real.astype(np.uint8) * 255).resize(shape)) > 127
    union = (a | b).sum()
    return float((a & b).sum() / union) if union else 0.0


def tile_cover(picture: Path) -> float:
    """How much of the icon is painted: it is drawn on black, so an icon
    whose tile did not fill (a broken gradient, say) is mostly black."""

    import numpy as np
    from PIL import Image

    rgb = np.asarray(Image.open(picture).convert("RGB")).astype(int)
    return float((rgb.max(axis=2) > 24).mean())


def plan_names_real_files(copy: Path, text: str) -> tuple[int, list[str]]:
    named = set(re.findall(r"`([A-Za-z0-9_./-]+\.[a-z]{1,5})`", text))
    real = sorted(n for n in named if (copy / n).is_file() or (copy / "kosika" / n).is_file())
    return len(real), real


def design_names(copy: Path) -> set[str]:
    """Every name Kosika Designs exports."""

    names: set[str] = set()
    for library in (copy / "kosika" / "std").glob("designs*.kos"):
        found = re.search(r"export\s*\[([^\]]*)\]", library.read_text(encoding="utf-8", errors="replace"))
        if found:
            names.update(n.strip() for n in found.group(1).split(",") if n.strip())
    return names


def plan_design_names(text: str) -> list[str]:
    """The functions a plan says to build with: names in backticks on its
    lines about Kosika Designs."""

    named: list[str] = []
    for line in text.splitlines():
        if "design" in line.lower():
            named += [n for n in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)(?:\(\))?`", line) if not n.startswith("designs")]
    return named


def main(run: Path, request: str, files: list[str]) -> int:
    run = run.resolve()
    copy = run / "kosika"
    failed = False

    def say(ok: bool, words: str) -> None:
        nonlocal failed
        failed = failed or not ok
        print(("PASS " if ok else "FAIL ") + words)

    letter = re.search(r"\bletter\s+([A-Z])\b", request)
    for name in files:
        path = copy / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if is_library(name) and "designs_" not in request.split(" with ")[0]:
            say(False, f"{name}: the Kosika Designs library was changed or copied, but the request only asked to use it")
            continue
        if is_design_kos(name):
            say(uses_designs(text), f"{name} is built on Kosika Designs (imports std/designs_*.kos or brand.kos)")
            if has_tests(text):
                ok, out = run_tests(copy, name)
                say(ok, f"{name}'s own tests pass" + ("" if ok else f": {out}"))
            else:
                say(False, f"{name} has tests")
            function = icon_function(text)
            if function:
                say(bool(re.search(r"export\s*\[[^\]]*\b" + function + r"\b", text)), f"{function} is exported")
                with tempfile.TemporaryDirectory() as work:
                    picture = Path(work) / "icon.png"
                    try:
                        draw_icon(copy, name, function, 256, picture)
                        kept = run / f"icon-{Path(name).stem}.png"
                        kept.write_bytes(picture.read_bytes())
                        say(True, f"{function} draws an icon ({kept.name})")
                        cover = tile_cover(picture)
                        say(cover >= 0.6, f"the icon's tile is painted ({cover:.0%} of it, 60% needed)")
                        if letter:
                            wanted = letter.group(1)
                            scores = {c: letter_agreement(picture, c) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}
                            closest = max(scores, key=scores.get)
                            # It must look like the letter asked for more than like any other.
                            say(closest == wanted and scores[wanted] >= LETTER_AGREES,
                                f"the icon's letter looks like {wanted} ({scores[wanted]:.2f}; it looks most like {closest}, {scores[closest]:.2f})")
                    except Exception as error:
                        say(False, f"{function} draws an icon: {error}")
        elif name.endswith(".md") and "STUDIO" in name.upper():
            count, real = plan_names_real_files(copy, text)
            say(count >= 3, f"{name} names {count} files that are in Kosika today ({', '.join(real[:6])})")
            named = plan_design_names(text)
            if named:
                known = design_names(copy)
                made_up = sorted({n for n in named if n not in known})
                say(len(made_up) <= len(set(named)) // 2,
                    f"{name} builds with Kosika Designs functions that exist ({len(set(named)) - len(made_up)} of {len(set(named))}; not in Kosika Designs: {', '.join(made_up[:8])})")
    if not failed:
        print("design checks: nothing failed")
    return 1 if failed else 0


if __name__ == "__main__":
    asked = Path(sys.argv[2])
    sys.exit(main(Path(sys.argv[1]), asked.read_text(encoding="utf-8") if asked.exists() else sys.argv[2], sys.argv[3:]))
