"""Every agent, the coder swarm and the model router keep checking and
passing their own tests on the Kosika engine beside this repo."""

from __future__ import annotations

import unittest
from pathlib import Path

from kosika.modules import load_program
from kosika.testing import run_tests

ROOT = Path(__file__).resolve().parents[1]
SKIPPED = ("runs", "memory", "traces")


def programs() -> list[Path]:
    found = [*ROOT.glob("agents/**/*.kos"), *ROOT.glob("swarm/*.kos"), *ROOT.glob("models/*.kos")]
    return sorted(p for p in found if not set(p.relative_to(ROOT).parts) & set(SKIPPED))


class AgentTests(unittest.TestCase):
    def test_every_agent_has_a_main_program(self) -> None:
        folders = sorted(p for p in (ROOT / "agents").iterdir() if p.is_dir())
        self.assertGreaterEqual(len(folders), 20)
        for folder in folders:
            with self.subTest(agent=folder.name):
                self.assertTrue((folder / "main.kos").is_file())

    def test_every_program_checks_and_passes_its_own_tests(self) -> None:
        found = programs()
        self.assertGreaterEqual(len(found), 40)
        for path in found:
            with self.subTest(program=path.relative_to(ROOT).as_posix()):
                results = run_tests(load_program(path))
                self.assertGreater(len(results), 0)
                failed = [f"{r.name}: {r.message}" for r in results if not r.passed]
                self.assertEqual(failed, [])


if __name__ == "__main__":
    unittest.main()
