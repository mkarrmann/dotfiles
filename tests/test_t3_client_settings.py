"""Checks for bin/t3-client-settings' sync: sources are in-memory stand-ins for
windows and the desktop app, the files are in a temp directory.

Run with: python3 -m unittest discover -s tests -p test_t3_client_settings.py
"""

import importlib.machinery
import importlib.util
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("t3_client_settings", str(ROOT / "bin/t3-client-settings"))
cs = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
sys.modules[loader.name] = cs  # dataclasses look their module up there
loader.exec_module(cs)

DEFAULTS = {"mathRenderingEnabled": False, "fontSizeCode": 13, "wordWrap": True, "onboardingCompletedAt": None}


class Client:
    """A window or desktop app: what it stores, and how often it was rewritten."""

    def __init__(self, root: Path, name: str, stored=None, writable=True):
        self.name, self.stored, self.writes = name, stored, 0
        self.marker = root / name / cs.MARKER
        self.writable = writable

    def source(self):
        def write(settings):
            self.stored, self.writes = dict(settings), self.writes + 1

        return cs.Source(self.name, self.marker, lambda: self.stored, write if self.writable else None, "it is running")

    def change(self, **settings):
        """What T3 Code saves when its settings page changes these: everything."""
        self.stored = {**DEFAULTS, **(self.stored or {}), **settings}


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        cs.SETTINGS_FILE = self.root / "dotfiles/client-settings.json"
        cs.DEFAULTS_FILE = self.root / "dotfiles/client-settings.defaults.json"
        cs.DEFAULTS_FILE.parent.mkdir(parents=True)
        cs.DEFAULTS_FILE.write_text(json.dumps(DEFAULTS))

    def tearDown(self):
        self.tmp.cleanup()

    def sync(self, *clients, prefer=None, dry_run=False):
        with redirect_stdout(StringIO()) as out:
            cs.sync([c.source() for c in clients], dry_run, prefer)
        return out.getvalue()

    def file(self):
        return json.loads(cs.SETTINGS_FILE.read_text()) if cs.SETTINGS_FILE.exists() else None

    def test_first_sync_collects_what_differs_from_the_defaults(self):
        a = Client(self.root, "a", {**DEFAULTS, "mathRenderingEnabled": True, "onboardingCompletedAt": "2026-11-01"})
        b = Client(self.root, "b", {**DEFAULTS, "fontSizeCode": 15, "onboardingCompletedAt": "2026-11-02"})
        fresh = Client(self.root, "fresh")
        self.sync(a, b, fresh)
        self.assertEqual(self.file(), {"fontSizeCode": 15, "mathRenderingEnabled": True})
        for client in (a, b, fresh):
            self.assertEqual((client.stored["mathRenderingEnabled"], client.stored["fontSizeCode"]), (True, 15))
        # Each client keeps its own onboarding.
        self.assertEqual(a.stored["onboardingCompletedAt"], "2026-11-01")
        self.assertNotIn("onboardingCompletedAt", fresh.stored)

    def test_a_change_in_one_window_reaches_the_file_and_the_others(self):
        a, b, c = (Client(self.root, n, dict(DEFAULTS)) for n in "abc")
        self.sync(a, b, c)
        a.change(wordWrap=False)
        self.sync(a, b, c)
        self.assertEqual(self.file(), {"wordWrap": False})
        self.assertEqual([x.stored["wordWrap"] for x in (a, b, c)], [False, False, False])
        writes = [x.writes for x in (a, b, c)]
        self.sync(a, b, c)
        # Nothing changed, so no window is rewritten (and reloaded) again.
        self.assertEqual([x.writes for x in (a, b, c)], writes)

    def test_a_setting_put_back_to_its_default_leaves_the_file(self):
        cs.SETTINGS_FILE.write_text(json.dumps({"mathRenderingEnabled": True, "wordWrap": False}))
        a, b = Client(self.root, "a"), Client(self.root, "b")
        self.sync(a, b)
        b.change(mathRenderingEnabled=False)
        self.sync(a, b)
        self.assertEqual(self.file(), {"wordWrap": False})
        self.assertFalse(a.stored["mathRenderingEnabled"])

    def test_a_new_window_with_default_settings_takes_the_file(self):
        cs.SETTINGS_FILE.write_text(json.dumps({"mathRenderingEnabled": True}))
        old = Client(self.root, "old")
        self.sync(old)
        # A new profile's window saved T3 Code's defaults (finishing onboarding, say) before its first sync.
        new = Client(self.root, "new", {**DEFAULTS, "onboardingCompletedAt": "2026-11-04"})
        self.sync(old, new)
        self.assertEqual(self.file(), {"mathRenderingEnabled": True})
        self.assertTrue(new.stored["mathRenderingEnabled"])

    def test_conflicting_changes_stop_the_sync_unless_one_is_preferred(self):
        a, b = Client(self.root, "a", dict(DEFAULTS)), Client(self.root, "b", dict(DEFAULTS))
        self.sync(a, b)
        a.change(fontSizeCode=14)
        b.change(fontSizeCode=16)
        with self.assertRaisesRegex(cs.SyncError, "fontSizeCode: a=14, b=16"):
            self.sync(a, b)
        self.assertEqual(self.file() or {}, {})
        self.assertEqual((a.stored["fontSizeCode"], b.stored["fontSizeCode"]), (14, 16))
        self.sync(a, b, prefer="b")
        self.assertEqual(self.file(), {"fontSizeCode": 16})
        self.assertEqual(a.stored["fontSizeCode"], 16)

    def test_a_running_desktop_app_is_read_but_not_written(self):
        desktop = Client(self.root, cs.DESKTOP, {**DEFAULTS, "wordWrap": False}, writable=False)
        window = Client(self.root, "w", {**DEFAULTS, "mathRenderingEnabled": True})
        self.sync(window, desktop)
        self.assertEqual(self.file(), {"mathRenderingEnabled": True, "wordWrap": False})
        self.assertEqual(desktop.writes, 0)
        self.assertFalse(desktop.marker.exists())
        self.assertFalse(window.stored["wordWrap"])

    def test_dry_run_changes_nothing(self):
        a = Client(self.root, "a", {**DEFAULTS, "mathRenderingEnabled": True})
        b = Client(self.root, "b", dict(DEFAULTS))
        out = self.sync(a, b, dry_run=True)
        self.assertIn("would write", out)
        self.assertIsNone(self.file())
        self.assertEqual((b.writes, b.stored["mathRenderingEnabled"]), (0, False))
        self.assertFalse(a.marker.exists())


if __name__ == "__main__":
    unittest.main()
