"""Checks for bin/metamate-skills-sync's planning and file selection; no meta CLI calls.

Run with: python3 -m unittest discover -s tests -p test_metamate_skills_sync.py
"""

import importlib.machinery
import importlib.util
import sys
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
_loader = importlib.machinery.SourceFileLoader("metamate_skills_sync", str(ROOT / "bin/metamate-skills-sync"))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
sync = importlib.util.module_from_spec(_spec)
sys.modules[_loader.name] = sync
_loader.exec_module(sync)


class PlanTest(unittest.TestCase):
    def test_new_changed_unchanged_and_dropped_skills(self):
        state = {
            "same": sync.Entry("same", "h1", "r1"),
            "changed": sync.Entry("mk-changed", "old", "r2"),
            "dropped": sync.Entry("dropped", "h3", "r3"),
        }
        steps = sync.plan(["same", "changed", "new"], {"same": "h1", "changed": "new-hash", "new": "h4"}, state)
        self.assertEqual(
            steps,
            [
                sync.Step("revise", "changed", "mk-changed"),
                sync.Step("create", "new"),
                sync.Step("uninstall", "dropped", "dropped"),
            ],
        )

    def test_up_to_date_plans_nothing(self):
        self.assertEqual(sync.plan(["a"], {"a": "h"}, {"a": sync.Entry("a", "h", "r")}), [])


class FilesTest(unittest.TestCase):
    def test_skill_files_skip_dot_and_cache_dirs_and_name_follows_alias(self):
        d = Path(tempfile.mkdtemp())
        (d / "SKILL.md").write_text("---\nname: show-in-nvim\ndescription: x\n---\nbody\n")
        (d / "scripts").mkdir()
        (d / "scripts" / "run.sh").write_text("echo hi\n")
        (d / ".rumdl_cache").mkdir()
        (d / ".rumdl_cache" / "CACHEDIR.TAG").write_text("x")
        (d / "__pycache__").mkdir()
        (d / "__pycache__" / "m.pyc").write_text("x")
        files = sync.skill_files(d)
        self.assertEqual([f["path"] for f in files], ["SKILL.md", "scripts/run.sh"])
        renamed = sync.with_name(files, "mk-show-in-nvim")
        self.assertIn("name: mk-show-in-nvim\n", renamed[0]["content"])
        self.assertEqual(renamed[1], files[1])


if __name__ == "__main__":
    unittest.main()
