"""The design checks look at a coder's design work as a person would."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("design_check", ROOT / "agents" / "coder" / "daily" / "design_check.py")
check = importlib.util.module_from_spec(spec)
sys.modules["design_check"] = check
spec.loader.exec_module(check)


def icon(letter: str, folder: Path, tile: bool = True) -> Path:
    image = Image.new("RGB", (256, 256), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    if tile:
        draw.rounded_rectangle([0, 0, 255, 255], radius=56, fill=(255, 122, 0))
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\segoeuib.ttf", 160)
    except OSError:
        font = ImageFont.load_default()
    draw.text((70, 40), letter, fill=(255, 255, 255), font=font)
    path = folder / f"{letter}-{tile}.png"
    image.save(path)
    return path


class Looks(unittest.TestCase):
    @unittest.skipUnless(Path(r"C:\Windows\Fonts\segoeuib.ttf").exists(), "needs Windows' fonts")
    def test_a_k_reads_as_k_and_an_h_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            for letter in "KH":
                path = icon(letter, Path(work))
                scores = {c: check.letter_agreement(path, c) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}
                self.assertEqual(max(scores, key=scores.get), letter)
                self.assertGreaterEqual(scores[letter], check.LETTER_AGREES)

    def test_an_unpainted_tile_is_seen(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            self.assertGreater(check.tile_cover(icon("K", Path(work))), 0.6)
            self.assertLess(check.tile_cover(icon("K", Path(work), tile=False)), 0.6)


class Rules(unittest.TestCase):
    def test_what_counts_as_design_work(self) -> None:
        self.assertTrue(check.is_design_kos("kosika/app_assets/studio_icon.kos"))
        self.assertTrue(check.is_design_kos("kosika/std/my_theme.kos"))
        self.assertFalse(check.is_design_kos("kosika/std/designs_palette.kos"))  # the library itself
        self.assertTrue(check.is_library("std/designs_palette.kos"))
        self.assertFalse(check.is_design_kos("kosika/std/text.kos"))

    def test_built_on_kosika_designs_and_tested(self) -> None:
        self.assertTrue(check.uses_designs('import "std/designs_palette.kos"\n'))
        self.assertTrue(check.uses_designs('import "brand.kos"'))
        self.assertFalse(check.uses_designs('import "std/text.kos"'))
        self.assertTrue(check.has_tests('fn a() -> Number { return 1 }\ntest "a" { expect a() == 1 }'))
        self.assertFalse(check.has_tests("fn a() -> Number { return 1 }"))
        self.assertEqual(check.icon_function("fn studio_icon(size: Number) -> Text {"), "studio_icon")

    def test_a_plan_must_name_real_files(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            copy = Path(work)
            (copy / "desktop").mkdir()
            (copy / "desktop" / "README.md").write_text("x", encoding="utf-8")
            count, real = check.plan_names_real_files(copy, "Change `desktop/README.md` and `app/made_up.js`.")
            self.assertEqual((count, real), (1, ["desktop/README.md"]))


    def test_a_plan_must_build_with_functions_kosika_designs_has(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            std = Path(work) / "kosika" / "std"
            std.mkdir(parents=True)
            (std / "designs_motion.kos").write_text("export [motion_reveal, motion_spring]\n", encoding="utf-8")
            self.assertEqual(check.design_names(Path(work)), {"motion_reveal", "motion_spring"})
        named = check.plan_design_names("**Design Functions:** `designs_motion.kos` - `motion_reveal`, `fadeIn()`\nOther: `x`")
        self.assertEqual(named, ["motion_reveal", "fadeIn"])


if __name__ == "__main__":
    unittest.main()
