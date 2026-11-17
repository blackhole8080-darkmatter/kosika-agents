"""The agents' overlay: every Kosika agent at work, seen on this screen.

    python overlay.py                  # stays up; agents come and go
    python overlay.py --quit-after 120 # ends after two quiet minutes

One process shows everything, and nothing it shows takes a click or a key:

* a pointer for each agent working, in its own colour with its mascot's name,
  gliding to where the agent says it is acting (or wandering in its own band
  of the screen when it has not said);
* the words the agent is typing, in a bubble beside its pointer, appearing
  letter by letter with a caret;
* a soft light along the screen's edges in the colours of the agents
  working. With several at once their colours travel round the edges and
  blend into each other, and when one starts or stops the light changes over
  smoothly rather than jumping. It breathes while anyone works and fades out
  when everyone has stopped.

What it looks like is decided in Kosika (overlay.kos: each agent's colour from
its kit, its tag from its mascot, its arrow from cursor.kos, and how the light
behaves). This file only puts that on the screen.

An agent says it is at work by writing ~/.kosika/glow/NAME.json:

    {"name": "coder-1", "at": 1763380000.0,     # when, in seconds (time.time())
     "doing": "fixing the parser's error lines", # shown under its tag
     "typing": "fn parse(t: Text) -> Tree {",    # the words it is writing now
     "x": 840, "y": 410}                         # where it is acting, in pixels

Only "name" and "at" are needed. A heartbeat older than overlay.kos's
`fresh` seconds means the agent has stopped, so a crash still fades out.
beat() below writes one, and `python overlay.py --beat NAME ...` does it from
a shell or another language.
"""

from __future__ import annotations

import argparse
import ast
import colorsys
import ctypes
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path(os.environ.get("KOSIKA_HOME") or (Path.home() / ".kosika"))
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
FRAME = 1 / 40

Colour = tuple[int, int, int]

# The light as overlay.kos describes it; used only when Kosika cannot be run,
# so the overlay still shows something honest rather than nothing.
DEFAULT_LIGHT = {"edge": 54, "low": 0.38, "high": 0.95, "breath": 3.4, "fade_in": 0.45,
                 "fade_out": 0.9, "change": 0.9, "drift": 14, "fresh": 6, "type_rate": 38}


def beats_folder() -> Path:
    return Path(os.environ.get("KOSIKA_GLOW_HOME") or (HOME / "glow"))


def safe_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "-" for c in name.strip()) or "Kosika"


def beat(name: str, doing: str = "", typing: str | None = None, x: float | None = None,
         y: float | None = None, where: Path | None = None) -> Path:
    """Say this agent is at work now. Call it at least every few seconds."""

    place = where or beats_folder()
    place.mkdir(parents=True, exist_ok=True)
    said: dict[str, object] = {"name": name, "at": time.time()}
    if doing:
        said["doing"] = doing
    if typing is not None:
        said["typing"] = typing
    if x is not None and y is not None:
        said["x"], said["y"] = float(x), float(y)
    path = place / f"{safe_name(name)}.json"
    part = path.with_suffix(".part")
    part.write_text(json.dumps(said), encoding="utf-8")
    for attempt in range(20):
        try:
            os.replace(part, path)  # never half a file for the overlay to read
            return path
        except PermissionError:  # Windows: the overlay is reading it this instant
            time.sleep(0.005 * (attempt + 1))
    path.write_text(json.dumps(said), encoding="utf-8")
    part.unlink(missing_ok=True)
    return path


def stop(name: str, where: Path | None = None) -> None:
    """Say this agent has stopped, rather than waiting for its beat to go stale."""

    try:
        ((where or beats_folder()) / f"{safe_name(name)}.json").unlink()
    except OSError:
        pass


@dataclass
class Beat:
    name: str
    since: float
    doing: str = ""
    typing: str | None = None
    spot: tuple[float, float] | None = None


def read_beats(now: float, fresh: float, where: Path | None = None) -> list[Beat]:
    """Every agent still at work, in a steady order (by name)."""

    found: list[Beat] = []
    folder = where or beats_folder()
    for path in sorted(folder.glob("*.json")) if folder.exists() else []:
        try:
            said = json.loads(path.read_text(encoding="utf-8"))
            at = float(said.get("at", path.stat().st_mtime))
        except (OSError, ValueError, TypeError):
            continue
        if now - at > fresh:
            continue
        spot = None
        if isinstance(said.get("x"), (int, float)) and isinstance(said.get("y"), (int, float)):
            spot = (float(said["x"]), float(said["y"]))
        typing = said.get("typing")
        found.append(Beat(str(said.get("name") or path.stem), now - at, str(said.get("doing") or ""),
                          typing if isinstance(typing, str) else None, spot))
    found.sort(key=lambda b: b.name.lower())
    return found


# ---------------------------------------------------------------- the light

def hex_colour(text: str) -> Colour:
    text = text.strip().lstrip("#")
    if len(text) == 6:
        try:
            return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
        except ValueError:
            pass
    return (255, 122, 0)


def name_colour(name: str) -> Colour:
    """A colour from the name alone, for when Kosika could not be asked."""

    seed = int.from_bytes(hashlib.sha256(name.strip().lower().encode()).digest()[:4], "big")
    r, g, b = colorsys.hsv_to_rgb((seed % 360) / 360, 0.75, 1.0)
    return (round(r * 255), round(g * 255), round(b * 255))


def breath(seconds: float, low: float, high: float, period: float) -> float:
    wave = (math.sin(2 * math.pi * seconds / period - math.pi / 2) + 1) / 2
    return low + (high - low) * wave


def falloff(distance, edge: float):
    """How strong the light is this far in from the screen's edge: full at the
    edge, nothing past `edge`, and softly in between (works on arrays too)."""

    import numpy as np

    reach = np.clip(1.0 - np.asarray(distance, dtype=np.float32) / float(edge), 0.0, 1.0)
    return reach * reach * (3 - 2 * reach) * reach  # smooth, and quick to fade


def colour_round(colours: list[Colour], places, turn: float):
    """The colour at each place round the screen (0 to 1, clockwise), with
    the agents' colours spread round it and moved on by `turn`, each blending
    smoothly into the next. One colour is that colour everywhere."""

    import numpy as np

    places = np.asarray(places, dtype=np.float32)
    table = np.asarray(colours or [(255, 122, 0)], dtype=np.float32)
    count = len(table)
    if count == 1:
        return np.broadcast_to(table[0], places.shape + (3,)).astype(np.float32)
    where = ((places + (float(turn) % 1.0)) % 1.0) * count
    first = np.floor(where).astype(np.int32) % count
    second = (first + 1) % count
    part = where - np.floor(where)
    part = part * part * (3 - 2 * part)
    return table[first] * (1 - part)[..., None] + table[second] * part[..., None]


@dataclass
class Strip:
    """One side of the light: where it is, and for each of its pixels how
    strong the light is and how far round the screen the pixel lies."""

    left: int
    top: int
    width: int
    height: int
    strength: object
    places: object


def strips(width: int, height: int, band: int) -> list[Strip]:
    """The four sides of the light. The top and bottom take the corners, the
    sides only what lies between, so no pixel is lit twice."""

    import numpy as np

    band = max(4, min(band, width // 3, height // 3))
    perimeter = 2.0 * (width + height)
    xs = np.arange(width, dtype=np.float32)
    ys = np.arange(band, dtype=np.float32)
    side_ys = np.arange(band, height - band, dtype=np.float32)
    bs = np.arange(band, dtype=np.float32)
    out: list[Strip] = []
    # top: y from the top edge; clockwise from the top-left corner
    gx, gy = np.meshgrid(xs, ys)
    near = np.minimum(np.minimum(gy, gx), width - 1 - gx)
    out.append(Strip(0, 0, width, band, falloff(near, band), xs[None, :] / perimeter))
    # bottom: right to left
    gyb = band - 1 - gy
    near = np.minimum(np.minimum(gyb, gx), width - 1 - gx)
    out.append(Strip(0, height - band, width, band, falloff(near, band),
                     (width + height + (width - 1 - xs[None, :])) / perimeter))
    # right: top to bottom
    gx2, gy2 = np.meshgrid(bs, side_ys)
    out.append(Strip(width - band, band, band, len(side_ys), falloff(band - 1 - gx2, band),
                     (width + side_ys[:, None]) / perimeter))
    # left: bottom to top
    out.append(Strip(0, band, band, len(side_ys), falloff(gx2, band),
                     (2 * width + height + (height - 1 - side_ys[:, None])) / perimeter))
    return out


def premultiplied(colours, alpha):
    """Blue, green, red, alpha bytes, colours multiplied by alpha, as a
    layered window wants them."""

    import numpy as np

    a = np.clip(alpha, 0.0, 1.0).astype(np.float32)
    out = np.empty(a.shape + (4,), dtype=np.uint8)
    out[..., 0] = (colours[..., 2] * a).astype(np.uint8)
    out[..., 1] = (colours[..., 1] * a).astype(np.uint8)
    out[..., 2] = (colours[..., 0] * a).astype(np.uint8)
    out[..., 3] = (a * 255).astype(np.uint8)
    return out


class Blend:
    """The edges' colours, changing over smoothly when agents come and go."""

    def __init__(self, change: float) -> None:
        self.change = max(0.05, change)
        self.now: list[Colour] = []
        self.was: list[Colour] = []
        self.since = -1e9

    def set(self, colours: list[Colour], at: float) -> None:
        if colours and colours != self.now:
            self.was, self.now, self.since = (self.now or colours), colours, at

    def at_places(self, places, turn: float, at: float):
        done = min(1.0, (at - self.since) / self.change)
        done = done * done * (3 - 2 * done)
        new = colour_round(self.now, places, turn)
        if done >= 1.0:
            return new
        return colour_round(self.was, places, turn) * (1 - done) + new * done


# ------------------------------------------------------------ the typing

def shown_typing(text: str, shown: float, rate: float, seconds: float) -> float:
    """How many letters of `text` are on screen after `seconds` more: they
    appear at `rate` a second, and all at once if the agent is far ahead."""

    goal = len(text)
    if shown > goal:
        return float(goal)
    behind = goal - shown
    step = rate * seconds * (1 + behind / 40.0)  # catch up when far behind
    return min(float(goal), shown + step)


def last_words(text: str, most: int) -> str:
    """The end of what is being typed, on one line, as much as fits."""

    one = " ".join(text.replace("\t", "  ").splitlines()[-1:]) if text else ""
    return one if len(one) <= most else "…" + one[-(most - 1):]


def wander(name: str, now: float, width: int, height: int, lane: int, lanes: int, margin: int = 110):
    """Where an agent that has not said where it acts drifts to: a slow
    figure of eight in its own band of the screen, at its own pace."""

    seed = sum(ord(c) for c in name)
    turn = 26.0 + seed % 13
    step = now / turn * 2 * math.pi + (seed % 360) * math.pi / 180
    band = max(1, width - 2 * margin) / max(1, lanes)
    x = margin + lane * band + band * (0.5 + 0.40 * math.sin(step))
    y = margin + max(1, height - 2 * margin) * (0.5 + 0.32 * math.sin(2 * step + 0.7))
    return x, y


# ------------------------------------------------------- asking Kosika

def engine_folder() -> Path | None:
    """A Kosika engine that has Kosika Designs (the pointer needs its palette)."""

    asked = os.environ.get("KOSIKA_ENGINE")
    roots = [Path(asked)] if asked else []
    projects = HERE.parents[2] if len(HERE.parents) > 2 else HERE
    roots += [projects / "kosika-studio-app", projects / "kosika-desktop", projects / "kosika"]
    for root in roots:
        if (root / "kosika" / "std" / "designs_motion.kos").exists():
            return root
    return None


def ask_kosika(names: list[str], scale: float) -> dict:
    """overlay.kos's scene for these agents: their colours, tags, arrows and
    how the light behaves."""

    engine = engine_folder()
    env = dict(os.environ)
    if engine:
        env["PYTHONPATH"] = str(engine) + os.pathsep + env.get("PYTHONPATH", "")
    with tempfile.TemporaryDirectory() as work:
        ask = Path(work) / "ask.json"
        ask.write_text(json.dumps({"a": {"names": names, "scale": scale}}), encoding="utf-8")
        done = subprocess.run([sys.executable, "-m", "kosika.cli", "run", str(HERE / "overlay.kos"), "--workflow", "scene",
                               "--input", str(ask)], cwd=work, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=env, timeout=180,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for line in reversed(done.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{") and "agents" in line:
            said = ast.literal_eval(line)
            return said.get("shown", said)
    raise RuntimeError(f"overlay.kos gave no scene: {(done.stderr or done.stdout)[-400:]}")


def rendered(svg: str, width: int, height: int) -> Path | None:
    """The SVG as a picture with soft edges, rendered once by Chrome and kept."""

    folder = HOME / "pointers"
    folder.mkdir(parents=True, exist_ok=True)
    png = folder / f"overlay-{hashlib.sha256(svg.encode()).hexdigest()[:16]}.png"
    if png.exists():
        return png
    if not CHROME.exists():
        return None
    with tempfile.TemporaryDirectory() as work:
        page = Path(work) / "p.html"
        page.write_text('<!doctype html><html><body style="margin:0;background:transparent">' + svg + "</body></html>",
                        encoding="utf-8")
        subprocess.run([str(CHROME), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                        "--default-background-color=00000000", f"--window-size={int(width)},{int(height)}",
                        f"--user-data-dir={Path(work) / 'chrome'}", f"--screenshot={png}", page.as_uri()],
                       capture_output=True, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return png if png.exists() else None


# ------------------------------------------------------------- the screen

def _windows():
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()

    class BLEND(ctypes.Structure):
        _fields_ = [("op", ctypes.c_byte), ("flags", ctypes.c_byte), ("alpha", ctypes.c_ubyte), ("format", ctypes.c_byte)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("width", wintypes.LONG), ("height", wintypes.LONG), ("planes", wintypes.WORD),
                    ("bits", wintypes.WORD), ("compression", wintypes.DWORD), ("image_size", wintypes.DWORD),
                    ("xppm", wintypes.LONG), ("yppm", wintypes.LONG), ("used", wintypes.DWORD), ("important", wintypes.DWORD)]

    WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    class WNDCLASS(ctypes.Structure):
        _fields_ = [("style", wintypes.UINT), ("proc", WNDPROC), ("cls_extra", ctypes.c_int), ("wnd_extra", ctypes.c_int),
                    ("instance", wintypes.HINSTANCE), ("icon", wintypes.HICON), ("cursor", wintypes.HANDLE),
                    ("background", wintypes.HBRUSH), ("menu", wintypes.LPCWSTR), ("name", wintypes.LPCWSTR)]

    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                       wintypes.HINSTANCE, wintypes.LPVOID]
    user32.GetDC.restype = wintypes.HDC
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    wintypes.UINT]
    user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                           ctypes.POINTER(wintypes.SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                           wintypes.DWORD, ctypes.POINTER(BLEND), wintypes.DWORD]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
                                       wintypes.HANDLE, wintypes.DWORD]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    return user32, gdi32, kernel32, BLEND, BITMAPINFOHEADER, WNDPROC, WNDCLASS, wintypes


class Screen:
    """Click-through, see-through, topmost windows that never take the keyboard."""

    EX = 0x80000 | 0x20 | 0x8 | 0x80 | 0x08000000  # layered, transparent, topmost, tool window, no activate

    def __init__(self) -> None:
        (self.user32, self.gdi32, self.kernel32, self.BLEND, self.HEADER, WNDPROC, WNDCLASS,
         self.wintypes) = _windows()
        self.proc = WNDPROC(lambda h, m, w, l: self.user32.DefWindowProcW(h, m, w, l))
        self.instance = self.kernel32.GetModuleHandleW(None)
        self.user32.RegisterClassW(ctypes.byref(WNDCLASS(0, self.proc, 0, 0, self.instance, None, None, None, None,
                                                         "KosikaOverlay")))
        self.dc = self.user32.GetDC(None)
        self.left = self.user32.GetSystemMetrics(76)
        self.top = self.user32.GetSystemMetrics(77)
        self.width = self.user32.GetSystemMetrics(78) or 1920
        self.height = self.user32.GetSystemMetrics(79) or 1080
        try:
            self.scale = max(1.0, self.user32.GetDpiForSystem() / 96)
        except (AttributeError, OSError):
            self.scale = 1.0

    def window(self) -> "Layer":
        handle = self.user32.CreateWindowExW(self.EX, "KosikaOverlay", "Kosika agents", 0x80000000, 0, 0, 1, 1,
                                             None, None, self.instance, None)
        if not handle:
            raise OSError(f"Windows gave no overlay window (error {ctypes.get_last_error()})")
        return Layer(self, handle)

    def pump(self) -> None:
        message = self.wintypes.MSG()
        while self.user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
            self.user32.TranslateMessage(ctypes.byref(message))
            self.user32.DispatchMessageW(ctypes.byref(message))


class Layer:
    """One layered window and the bitmap behind it."""

    def __init__(self, screen: Screen, handle) -> None:
        self.screen, self.handle = screen, handle
        self.memory = None
        self.bitmap = None
        self.bits = ctypes.c_void_p()
        self.size = (0, 0)
        self.shown = False

    def _surface(self, width: int, height: int) -> None:
        if self.size == (width, height):
            return
        g = self.screen.gdi32
        if self.memory:
            g.DeleteDC(self.memory)
            g.DeleteObject(self.bitmap)
        self.memory = g.CreateCompatibleDC(self.screen.dc)
        header = self.screen.HEADER(ctypes.sizeof(self.screen.HEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        self.bitmap = g.CreateDIBSection(self.screen.dc, ctypes.byref(header), 0, ctypes.byref(self.bits), None, 0)
        g.SelectObject(self.memory, self.bitmap)
        self.size = (width, height)

    def put(self, pixels, x: int, y: int, alpha: float = 1.0) -> None:
        """Show these BGRA pixels (premultiplied) with their top-left at x, y."""

        height, width = pixels.shape[:2]
        self._surface(width, height)
        data = pixels.tobytes()
        ctypes.memmove(self.bits, data, len(data))
        self.move(x, y, alpha)

    def move(self, x: int, y: int, alpha: float = 1.0) -> None:
        w = self.screen.wintypes
        size, origin, place = w.SIZE(*self.size), w.POINT(0, 0), w.POINT(int(x), int(y))
        blend = self.screen.BLEND(0, 0, max(0, min(255, int(alpha * 255))), 1)
        self.screen.user32.UpdateLayeredWindow(self.handle, self.screen.dc, ctypes.byref(place), ctypes.byref(size),
                                               self.memory, ctypes.byref(origin), 0, ctypes.byref(blend), 2)
        if not self.shown:
            self.screen.user32.ShowWindow(self.handle, 4)
            self.shown = True
        # Stay above windows that were brought forward since.
        self.screen.user32.SetWindowPos(self.handle, -1, 0, 0, 0, 0, 0x1 | 0x2 | 0x10)

    def hide(self) -> None:
        if self.shown:
            self.screen.user32.ShowWindow(self.handle, 0)
            self.shown = False

    def close(self) -> None:
        self.screen.user32.DestroyWindow(self.handle)


# --------------------------------------------------------- the pointers

def font(size: int, bold: bool = False):
    from PIL import ImageFont

    for name in (["seguisb.ttf", "segoeuib.ttf"] if bold else ["segoeui.ttf"]) + ["arial.ttf"]:
        try:
            return ImageFont.truetype(str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / name), size)
        except OSError:
            continue
    return ImageFont.load_default()


def mono(size: int):
    from PIL import ImageFont

    for name in ["CascadiaMono.ttf", "consola.ttf"]:
        try:
            return ImageFont.truetype(str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / name), size)
        except OSError:
            continue
    return font(size)


def plain_arrow(colour: Colour, tag: str, scale: float):
    """An arrow drawn here, for when Kosika's own could not be rendered."""

    from PIL import Image, ImageDraw

    s = scale
    label = font(int(12 * s), bold=True)
    tw = int(ImageDraw.Draw(Image.new("RGBA", (1, 1))).textlength(tag, font=label))
    image = Image.new("RGBA", (int(40 * s) + tw + int(20 * s), int(64 * s)), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    tip = int(4 * s)
    arrow = [(tip, tip), (tip, tip + 30 * s), (tip + 8 * s, tip + 23 * s), (tip + 14 * s, tip + 36 * s),
             (tip + 19 * s, tip + 34 * s), (tip + 13 * s, tip + 21 * s), (tip + 23 * s, tip + 21 * s)]
    d.polygon(arrow, fill=colour + (255,), outline=(255, 255, 255, 255))
    d.rounded_rectangle([tip + 22 * s, tip + 30 * s, tip + 22 * s + tw + 16 * s, tip + 50 * s], radius=int(9 * s),
                        fill=colour + (255,))
    d.text((tip + 30 * s, tip + 32 * s), tag, font=label, fill=(7, 8, 13, 255))
    return image, tip


@dataclass
class Agent:
    """An agent on the screen: its look, where its pointer is, its words."""

    name: str
    colour: Colour
    tag: str
    idle: object = None      # PIL images of the arrow with its tag
    typing_look: object = None
    tip: int = 4
    layer: Layer | None = None
    x: float = -1.0
    y: float = -1.0
    seen: float = 0.0        # how visible, 0 to 1
    shown_letters: float = 0.0
    words: str = ""
    doing: str = ""
    picture_key: tuple = field(default_factory=tuple)
    picture: object = None


def bubble(text: str, colour: Colour, scale: float, caret: bool, doing: str):
    """The words an agent is typing, in a dark bubble edged in its colour."""

    from PIL import Image, ImageDraw

    s = scale
    words_font, small = mono(int(13 * s)), font(int(11 * s))
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    line = last_words(text, 46)
    shown = line + ("▍" if caret else " ")
    tw = int(probe.textlength(shown, font=words_font)) if text is not None else 0
    dw = int(probe.textlength(doing, font=small)) if doing else 0
    pad = int(10 * s)
    width = max(tw, dw) + 2 * pad
    height = (int(22 * s) if text is not None else 0) + (int(17 * s) if doing else 0) + int(10 * s)
    image = Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    d.rounded_rectangle([0, 0, width - 1, height - 1], radius=int(9 * s), fill=(14, 16, 24, 232),
                        outline=colour + (235,), width=max(1, int(1.5 * s)))
    y = int(5 * s)
    if doing:
        d.text((pad, y), doing, font=small, fill=(196, 201, 214, 255))
        y += int(17 * s)
    if text is not None:
        d.text((pad, y), shown, font=words_font, fill=(244, 246, 250, 255))
    return image


def tag_corner(arrow) -> tuple[int, int]:
    """The bottom-left corner of the pointer's tag: the lowest solid row of the
    picture, and where it starts. Read from the picture, so a change to
    cursor.kos's look never puts the words over the name."""

    import numpy as np

    solid = np.asarray(arrow)[..., 3] >= 200
    rows = np.flatnonzero(solid.any(axis=1))
    if not len(rows):
        return 0, arrow.height
    bottom = int(rows[-1])
    band = solid[max(0, bottom - 3): bottom + 1].any(axis=0)
    return int(np.flatnonzero(band)[0]), bottom


def compose(agent: Agent, scale: float, caret: bool):
    """The arrow, and under it the bubble, as one picture."""

    import numpy as np
    from PIL import Image

    typing = agent.words != "" or agent.shown_letters > 0
    arrow = agent.typing_look if typing and agent.typing_look is not None else agent.idle
    text = agent.words[: int(agent.shown_letters)] if typing else None
    if text is None and not agent.doing:
        image = arrow
    else:
        note = bubble(text if text is not None else None, agent.colour, scale, caret and typing, agent.doing) \
            if (text is not None or agent.doing) else None
        left, bottom = tag_corner(arrow)
        top = bottom + int(5 * scale)  # under the tag, never over it
        width = max(arrow.width, left + note.width)
        image = Image.new("RGBA", (width, max(arrow.height, top + note.height)), (0, 0, 0, 0))
        image.alpha_composite(arrow, (0, 0))
        image.alpha_composite(note, (left, top))
    rgba = np.asarray(image, dtype=np.float32)
    return premultiplied(rgba[..., :3], rgba[..., 3] / 255.0)


# ----------------------------------------------------------------- looks

def look_key(scale: float) -> str:
    """Changes whenever anything that decides a look changes."""

    digest = hashlib.sha256(f"{scale:.2f}".encode())
    for name in ["overlay.kos", "kits.kos", "kit.kos", "mascots.kos", "pointer_look.kos", "cursor.kos"]:
        path = HERE / name
        digest.update(path.read_bytes() if path.exists() else b"-")
    return digest.hexdigest()[:16]


class Looks:
    """Every agent's look, asked of Kosika away from the screen's own thread,
    so drawing never stops while a new agent is being drawn. The looks are
    kept on disk, so the next start shows every known agent at once."""

    def __init__(self, scale: float) -> None:
        import queue
        import threading

        self.scale = scale
        self.kept = HOME / "pointers" / f"overlay-scene-{look_key(scale)}.json"
        self.ready: dict[str, dict] = {}
        self.light: dict = {}
        self.asked: set[str] = set()
        self.lock = threading.Lock()
        self.wanted: "queue.Queue[list[str]]" = queue.Queue()
        try:
            kept = json.loads(self.kept.read_text(encoding="utf-8"))
            self.light = kept.get("light") or {}
            self.ready = {name: look for name, look in kept.get("agents", {}).items()
                          if Path(look.get("idle_png", "")).exists()}
        except (OSError, ValueError):
            pass
        threading.Thread(target=self._draw, daemon=True).start()
        if not self.ready:
            self.wanted.put([])  # every agent with a kit, in one go

    def get(self, name: str) -> dict | None:
        with self.lock:
            look = self.ready.get(name)
            if look is None and name not in self.asked:
                self.asked.add(name)
                self.wanted.put([name])
        if look is not None and self.light:
            look = {**look, "light": self.light}
        return look

    def _draw(self) -> None:
        while True:
            names = self.wanted.get()
            try:
                scene = ask_kosika(names, self.scale)
                found = {}
                for look in scene["agents"]:
                    idle = rendered(look["idle"], look["idle_width"], look["height"])
                    typing = rendered(look["typing"], look["typing_width"], look["height"])
                    found[look["name"]] = {
                        "colour": look["colour"], "tag": look["tag"], "tip": look["tip"],
                        "idle_png": str(idle) if idle else "", "typing_png": str(typing or idle or ""),
                        "problem": "" if idle else "Chrome could not draw the pointer"}
                with self.lock:
                    self.light = scene.get("light") or self.light
                    self.ready.update(found)
                    for name in names:  # asked for, but Kosika drew no one by that name
                        self.ready.setdefault(name, self._plain(name, "overlay.kos drew no look for it"))
                    kept = {"light": self.light, "agents": self.ready}
                part = self.kept.with_suffix(".part")
                part.write_text(json.dumps(kept), encoding="utf-8")
                os.replace(part, self.kept)
            except Exception as error:  # an agent must still be seen
                print(f"overlay: Kosika could not draw {names or 'the agents'}: {error}", file=sys.stderr, flush=True)
                with self.lock:
                    for name in names:
                        self.ready[name] = self._plain(name, str(error))

    @staticmethod
    def _plain(name: str, problem: str) -> dict:
        r, g, b = name_colour(name)
        return {"colour": f"#{r:02x}{g:02x}{b:02x}", "tag": name, "tip": 4, "problem": problem}


# ------------------------------------------------------------- the overlay

class Overlay:
    def __init__(self, quit_after: float | None) -> None:
        self.screen = Screen()
        self.quit_after = quit_after
        self.light = dict(DEFAULT_LIGHT)
        self.agents: dict[str, Agent] = {}
        band = int(self.light["edge"] * self.screen.scale)
        self.sides = strips(self.screen.width, self.screen.height, band)
        self.side_layers = [self.screen.window() for _ in self.sides]
        self.blend = Blend(self.light["change"])
        self.level = 0.0
        self.quiet_since = time.time()
        self.problems: list[str] = []
        self.looks = Looks(self.screen.scale)
        if self.looks.light:
            self.light.update(self.looks.light)
            self.blend.change = max(0.05, float(self.light["change"]))

    def meet(self, name: str) -> Agent | None:
        """The agent on the screen, once Kosika has said how it looks."""

        from PIL import Image

        look = self.looks.get(name)
        if look is None:
            return None  # still being drawn; it appears the moment it is
        if look.get("light"):
            self.light.update(look["light"])
            self.blend.change = max(0.05, float(self.light["change"]))
        agent = Agent(name, hex_colour(look["colour"]), look["tag"], tip=int(look["tip"]))
        try:
            agent.idle = Image.open(look["idle_png"]).convert("RGBA")
            agent.typing_look = Image.open(look["typing_png"]).convert("RGBA")
        except (OSError, KeyError, TypeError) as error:
            self.problems.append(f"{name}: drawn here, not by Kosika ({look.get('problem') or error})")
            print(f"overlay: {self.problems[-1]}", file=sys.stderr, flush=True)
            agent.idle, agent.tip = plain_arrow(agent.colour, agent.tag, self.screen.scale)
            agent.typing_look = agent.idle
        agent.layer = self.screen.window()
        return agent

    def frame(self, now: float, seconds: float) -> bool:
        self.screen.pump()
        beats = read_beats(now, float(self.light["fresh"]))
        for b in beats:
            if b.name not in self.agents:
                met = self.meet(b.name)
                if met:
                    self.agents[b.name] = met
        beats = [b for b in beats if b.name in self.agents]
        working = {b.name: b for b in beats}
        names = sorted(self.agents)
        lanes = max(1, len(beats))
        lane_of = {b.name: i for i, b in enumerate(beats)}
        caret = int(now * 2) % 2 == 0
        for name in names:
            agent = self.agents[name]
            b = working.get(name)
            goal_seen = 1.0 if b else 0.0
            rate = seconds / (self.light["fade_in"] if b else self.light["fade_out"])
            agent.seen = min(goal_seen, agent.seen + rate) if goal_seen > agent.seen else max(goal_seen, agent.seen - rate)
            if b:
                agent.doing = b.doing
                words = b.typing or ""
                if not words.startswith(agent.words[: int(agent.shown_letters)]):
                    agent.shown_letters = 0.0
                agent.words = words
                agent.shown_letters = shown_typing(words, agent.shown_letters, float(self.light["type_rate"]), seconds)
                gx, gy = b.spot if b.spot else wander(name, now, self.screen.width, self.screen.height,
                                                       lane_of[name], lanes)
                if agent.x < 0:
                    agent.x, agent.y = gx, gy
                agent.x += (gx - agent.x) * 0.16
                agent.y += (gy - agent.y) * 0.16
            if agent.seen <= 0.0:
                agent.layer.hide()
                continue
            key = (agent.words[: int(agent.shown_letters)], agent.doing, caret and bool(agent.words))
            if key != agent.picture_key:
                agent.picture = compose(agent, self.screen.scale, caret)
                agent.picture_key = key
                agent.layer.put(agent.picture, agent.x - agent.tip, agent.y - agent.tip, agent.seen)
            else:
                agent.layer.move(agent.x - agent.tip, agent.y - agent.tip, agent.seen)
        self.draw_light(now, seconds, [self.agents[b.name].colour for b in beats])
        if beats:
            self.quiet_since = now
        return not (self.quit_after and now - self.quiet_since > self.quit_after)

    def draw_light(self, now: float, seconds: float, colours: list[Colour]) -> None:
        distinct = list(dict.fromkeys(colours))
        self.blend.set(distinct, now)
        goal = 1.0 if distinct else 0.0
        rate = seconds / (self.light["fade_in"] if goal else self.light["fade_out"])
        self.level = min(goal, self.level + rate) if goal > self.level else max(goal, self.level - rate)
        if self.level <= 0.0:
            for layer in self.side_layers:
                layer.hide()
            return
        glow = self.level * breath(now, self.light["low"], self.light["high"], self.light["breath"])
        turn = (now / max(1.0, float(self.light["drift"]))) % 1.0  # small, or float32 loses the places
        for side, layer in zip(self.sides, self.side_layers):
            colours_here = self.blend.at_places(side.places, turn, now)
            layer.put(premultiplied(colours_here, side.strength * glow), side.left + self.screen.left,
                      side.top + self.screen.top)

    def run(self) -> None:
        last = time.time()
        while True:
            now = time.time()
            if not self.frame(now, now - last):
                break
            last = now
            time.sleep(max(0.0, FRAME - (time.time() - now)))


def already_running(kernel32) -> bool:
    kernel32.CreateMutexW(None, False, "Local\\KosikaAgentsOverlay")
    return ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS


def main() -> None:
    parser = argparse.ArgumentParser(description="Show every Kosika agent at work on this screen.")
    parser.add_argument("--quit-after", type=float, default=0, help="end after this many quiet seconds (0: stay)")
    parser.add_argument("--beat", metavar="NAME", help="say NAME is at work now, and end")
    parser.add_argument("--doing", default="")
    parser.add_argument("--typing")
    parser.add_argument("--at", nargs=2, type=float, metavar=("X", "Y"))
    parser.add_argument("--stop", metavar="NAME", help="say NAME has stopped, and end")
    args = parser.parse_args()
    if args.beat:
        beat(args.beat, args.doing, args.typing, *(args.at or (None, None)))
        return
    if args.stop:
        stop(args.stop)
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if already_running(kernel32):
        print("overlay: already showing on this screen", flush=True)
        return
    Overlay(args.quit_after or None).run()


if __name__ == "__main__":
    main()
