"""The book reader's Python steps: taking the text out of a book, and the
whole run end to end with the stand-in model (no real model needed)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
READER = ROOT / "agents" / "reader"
sys.path.insert(0, str(READER))

from pages import cut_pages, html_text, read_book, tidy  # noqa: E402


class PagesTests(unittest.TestCase):
    def test_words_broken_across_lines_are_joined(self) -> None:
        self.assertEqual(tidy("a hash ta-\nble   stores\n\n\n\nkeys"), "a hash table stores\n\nkeys")

    def test_a_text_without_pages_is_cut_at_paragraph_ends(self) -> None:
        text = "\n\n".join(f"Paragraph {i} " + "word " * 100 for i in range(20))
        pages = cut_pages(text, size=1500)
        self.assertGreater(len(pages), 5)
        self.assertTrue(all(p.startswith("Paragraph") for p in pages))
        self.assertEqual(sum(p.count("Paragraph") for p in pages), 20)

    def test_html_keeps_headings_and_drops_navigation(self) -> None:
        title, text = html_text("<html><head><title>Book</title></head><body><nav>menu</nav>"
                                "<h1>Chapter 1 Trees</h1><p>A tree has a root.</p><script>x()</script></body></html>")
        self.assertEqual(title, "Book")
        self.assertIn("# Chapter 1 Trees", text)
        self.assertIn("A tree has a root.", text)
        self.assertNotIn("menu", text)
        self.assertNotIn("x()", text)


class WholeRunTests(unittest.TestCase):
    def test_a_book_is_read_checked_and_scored_end_to_end(self) -> None:
        chapter = ("A binary search tree keeps smaller keys to the left and larger keys to the right of each node. "
                   "Searching starts at the root and moves left or right at every node it visits on the way down. "
                   "A tree that leans to one side becomes slow, because its height grows with every key added to it. ")
        book = "\n\n".join(f"## Chapter {n}\n\n" + chapter * 4 for n in (1, 2))
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "trees.md"
            source.write_text(book, "utf-8")
            out = Path(folder) / "notes"
            ran = subprocess.run([sys.executable, str(READER / "read.py"), str(source), "--out", str(out),
                                  "--engine", "kosika", "--model-command", sys.executable, str(READER / "stand_in.py")],
                                 capture_output=True, text=True, timeout=600)
            self.assertEqual(ran.returncode, 0, ran.stderr[-1500:])
            score = json.loads((out / "score.json").read_text("utf-8"))
            self.assertEqual(score["chapters"], 2)
            self.assertEqual(score["chapters_with_notes"], 2)
            self.assertEqual(score["ideas_dropped"], 0)
            self.assertTrue(score["passed"], score["why"])
            notes = (out / "notes.md").read_text("utf-8")
            self.assertIn("## 1. Chapter 1", notes)
            self.assertIn("## Scorecard", notes)
            self.assertIn("Mind map of this chapter", (out / "notes.html").read_text("utf-8"))
            shelf = json.loads((out / "memory" / "shelf.json").read_text("utf-8"))
            self.assertGreater(len(shelf), 0)


if __name__ == "__main__":
    unittest.main()
