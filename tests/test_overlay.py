"""The agents' overlay decides its light, its pointers and its typing without
a screen, so all of that is checked here; only putting pixels on Windows is
left to the demo (agents/hands/demo-overlay.ps1)."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("overlay", ROOT / "agents" / "hands" / "overlay.py")
overlay = importlib.util.module_from_spec(spec)
sys.modules["overlay"] = overlay
spec.loader.exec_module(overlay)


class Beats(unittest.TestCase):
    def test_a_beat_is_read_back_and_goes_stale(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            where = Path(folder)
            overlay.beat("coder-1", "fixing the parser", "fn parse(", 300, 200, where=where)
            overlay.beat("scout", where=where)
            now = time.time()
            found = overlay.read_beats(now, 6, where)
            self.assertEqual([b.name for b in found], ["coder-1", "scout"])
            self.assertEqual(found[0].spot, (300.0, 200.0))
            self.assertEqual(found[0].typing, "fn parse(")
            self.assertIsNone(found[1].typing)
            self.assertEqual(overlay.read_beats(now + 7, 6, where), [])

    def test_a_broken_beat_is_skipped_and_a_stop_is_at_once(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            where = Path(folder)
            (where / "bad.json").write_text("{not json", encoding="utf-8")
            overlay.beat("coder-2", where=where)
            self.assertEqual([b.name for b in overlay.read_beats(time.time(), 6, where)], ["coder-2"])
            overlay.stop("coder-2", where=where)
            self.assertEqual(overlay.read_beats(time.time(), 6, where), [])
            self.assertFalse(list(where.glob("*.part")))

    def test_names_are_safe_file_names(self) -> None:
        self.assertEqual(overlay.safe_name("../a b"), "..-a-b")
        with tempfile.TemporaryDirectory() as folder:
            path = overlay.beat("a/b", where=Path(folder))
            self.assertEqual(path.parent, Path(folder))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["name"], "a/b")


class Light(unittest.TestCase):
    def test_full_at_the_edge_and_nothing_past_the_band(self) -> None:
        strength = overlay.falloff([0, 10, 30, 54, 80], 54)
        self.assertAlmostEqual(float(strength[0]), 1.0)
        self.assertTrue(all(strength[i] > strength[i + 1] for i in range(3)))
        self.assertEqual(float(strength[3]), 0.0)
        self.assertEqual(float(strength[4]), 0.0)

    def test_the_four_sides_cover_the_edges_once(self) -> None:
        sides = overlay.strips(400, 300, 20)
        lit = np.zeros((300, 400))
        for s in sides:
            lit[s.top:s.top + s.height, s.left:s.left + s.width] += 1
        self.assertEqual(lit.max(), 1)
        self.assertEqual(lit[150, 200], 0)  # the middle is never covered
        for s in sides:
            self.assertEqual(s.strength.shape, (s.height, s.width))
            places = np.broadcast_to(s.places, s.strength.shape)
            self.assertTrue(((places >= 0) & (places < 1)).all())

    def test_one_agent_is_one_colour_all_round(self) -> None:
        got = overlay.colour_round([(255, 0, 0)], np.linspace(0, 0.99, 50), 0.3)
        self.assertTrue((got == [255, 0, 0]).all())

    def test_several_agents_blend_into_each_other_with_no_jump(self) -> None:
        colours = [(255, 0, 0), (0, 0, 255), (0, 255, 0)]
        places = np.linspace(0, 1, 2000, endpoint=False)
        got = overlay.colour_round(colours, places, 0.0)
        self.assertLess(np.abs(np.diff(got, axis=0)).max(), 6)  # smooth all round
        # each agent's own colour is seen somewhere on the edges
        for c in colours:
            self.assertLess(np.abs(got - c).sum(axis=1).min(), 3)
        # and the colours travel: a turn moves them round
        moved = overlay.colour_round(colours, places, 0.25)
        self.assertGreater(np.abs(moved - got).max(), 50)

    def test_colours_still_travel_late_in_a_long_run(self) -> None:
        # The clock is seconds since 1970: the turn must not drown the places.
        places = np.linspace(0, 1, 400, endpoint=False, dtype=np.float32)
        got = overlay.colour_round([(255, 0, 0), (0, 0, 255)], places, 1.763e9 / 14)
        self.assertGreater(np.ptp(got[:, 0]), 200)

    def test_a_change_of_agents_is_eased_not_jumped(self) -> None:
        blend = overlay.Blend(1.0)
        places = np.array([0.1, 0.6])
        blend.set([(255, 0, 0)], 0.0)
        blend.set([(0, 0, 255)], 10.0)
        start = blend.at_places(places, 0, 10.0)
        middle = blend.at_places(places, 0, 10.5)
        end = blend.at_places(places, 0, 11.5)
        self.assertTrue((start == [255, 0, 0]).all())
        self.assertTrue(0 < middle[0][0] < 255 and 0 < middle[0][2] < 255)
        self.assertTrue((end == [0, 0, 255]).all())

    def test_the_light_breathes_between_its_low_and_high(self) -> None:
        values = [overlay.breath(t / 10, 0.38, 0.95, 3.4) for t in range(100)]
        self.assertAlmostEqual(min(values), 0.38, places=2)
        self.assertAlmostEqual(max(values), 0.95, places=2)

    def test_pixels_are_premultiplied_for_windows(self) -> None:
        px = overlay.premultiplied(np.array([[[200.0, 100.0, 50.0]]]), np.array([[0.5]]))
        self.assertEqual(px[0, 0].tolist(), [25, 50, 100, 127])


class Typing(unittest.TestCase):
    def test_words_appear_letter_by_letter_and_catch_up(self) -> None:
        text = "fn parse(t: Text) -> Tree {"
        shown = overlay.shown_typing(text, 0, 38, 0.1)
        self.assertTrue(0 < shown < len(text))
        self.assertEqual(overlay.shown_typing(text, shown, 38, 10), len(text))
        far = overlay.shown_typing("x" * 400, 0, 38, 0.1)
        near = overlay.shown_typing("x" * 10, 0, 38, 0.1)
        self.assertGreater(far, near)  # far behind, it hurries
        self.assertEqual(overlay.shown_typing("ab", 5, 38, 0.1), 2)

    def test_only_the_end_of_long_words_is_shown(self) -> None:
        self.assertEqual(overlay.last_words("short", 46), "short")
        long = overlay.last_words("a" * 80 + "END", 46)
        self.assertEqual(len(long), 46)
        self.assertTrue(long.startswith("…") and long.endswith("END"))
        self.assertEqual(overlay.last_words("first\nsecond", 46), "second")

    def test_wandering_agents_keep_to_their_own_band(self) -> None:
        for t in range(0, 300, 7):
            x0, _ = overlay.wander("coder-1", t, 1920, 1080, 0, 3)
            x2, _ = overlay.wander("coder-3", t, 1920, 1080, 2, 3)
            self.assertLess(x0, x2)


class Kosika(unittest.TestCase):
    @unittest.skipUnless(overlay.engine_folder(), "no Kosika engine with Kosika Designs beside this repo")
    def test_kosika_gives_each_agent_its_kit_colour_and_a_pointer(self) -> None:
        scene = overlay.ask_kosika(["coder-1", "scout"], 1.25)
        coder, scout = scene["agents"]
        self.assertEqual(coder["colour"], "#ff3ea5")
        self.assertNotEqual(coder["colour"], scout["colour"])
        self.assertTrue(coder["idle"].startswith("<svg") and "typing" in coder["typing"])
        self.assertGreater(scene["light"]["fresh"], 0)


if __name__ == "__main__":
    unittest.main()
