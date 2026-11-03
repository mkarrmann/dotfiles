"""Isolated checks for bin/t3-ensure: temp home, fake t3/systemctl/curl, no network.

Run with: python3 -m unittest discover -s tests -p test_t3_ensure.py
"""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin/t3-ensure"


def write_exe(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


class T3EnsureTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / "home"
        self.dotfiles = self.root / "dotfiles"
        self.bin = self.root / "bin"
        self.calls = self.root / "calls.log"
        self.enabled = self.root / "enabled"
        for d in (self.home / "checkout1", self.home / "checkout2", self.home / "checkoutx", self.dotfiles / "bin", self.bin, self.enabled):
            d.mkdir(parents=True)
        write_exe(self.dotfiles / "bin/omnigent-acp", f'echo "shim $*" >> {self.calls}\n')
        write_exe(self.bin / "t3", f'echo "t3 $* home=$T3CODE_HOME" >> {self.calls}\n')
        # Fake systemctl: `is-enabled` succeeds for units marked in $enabled; `enable` marks them.
        write_exe(
            self.bin / "systemctl",
            f'echo "systemctl $*" >> {self.calls}\n'
            f'unit=""; for a in "$@"; do unit="$a"; done\n'
            f'case "$2" in is-enabled) [ -f "{self.enabled}/$unit" ] ;; enable) touch "{self.enabled}/$unit" ;; esac\n',
        )
        write_exe(self.bin / "curl", "exit 22\n")

    def run_ensure(self):
        env = {"HOME": str(self.home), "DOTFILES_DIR": str(self.dotfiles), "PATH": f"{self.bin}:/usr/bin:/bin"}
        return subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True)

    def calls_made(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def settings(self, checkout):
        return json.loads((self.home / ".t3-workspaces" / checkout / "userdata/settings.json").read_text())

    def test_each_checkout_gets_providers_and_a_started_server(self):
        result = self.run_ensure()
        self.assertEqual(result.returncode, 0, result.stderr)
        for checkout in ("checkout1", "checkout2"):
            self.assertEqual(
                self.settings(checkout)["providerInstances"]["omnigent-codex"],
                {
                    "driver": "acpRegistry",
                    "displayName": "Omnigent · Codex",
                    "config": {
                        "source": "local",
                        "commandPath": str(self.dotfiles / "bin/omnigent-acp"),
                        "commandArgs": ["--agent", "codex-native-ui"],
                    },
                },
            )
            self.assertEqual(self.settings(checkout)["providerInstances"]["claudeAgent"], {"driver": "claudeAgent", "enabled": False})
            self.assertIn(f"systemctl --user enable --now t3@{checkout}.service", self.calls_made())
            # Added through the CLI, not bootstrapped, so new windows skip T3 Code's first-run wizard.
            self.assertIn(
                f"t3 project add {self.home / checkout} home={self.home / '.t3-workspaces' / checkout}", self.calls_made()
            )
        self.assertFalse((self.home / ".t3-workspaces/checkoutx").exists())
        self.assertIn("shim --help", self.calls_made())

    def test_existing_settings_are_kept(self):
        path = self.home / ".t3-workspaces/checkout1/userdata/settings.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"theme": "dark", "providerInstances": {"codex": {"driver": "codex"}}}))
        self.run_ensure()
        settings = self.settings("checkout1")
        self.assertEqual(settings["theme"], "dark")
        self.assertEqual(settings["providerInstances"]["codex"], {"driver": "codex"})
        self.assertIn("omnigent-claude", settings["providerInstances"])

    def test_rerun_leaves_settings_and_enabled_servers_alone(self):
        self.run_ensure()
        settings_path = self.home / ".t3-workspaces/checkout1/userdata/settings.json"
        before = settings_path.stat().st_mtime_ns
        self.calls.unlink()
        result = self.run_ensure()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(settings_path.stat().st_mtime_ns, before)
        self.assertEqual(result.stdout, "")
        self.assertFalse([c for c in self.calls_made() if " enable " in c])

    def test_existing_project_counts_as_added(self):
        write_exe(
            self.bin / "t3",
            f'echo "t3 $* home=$T3CODE_HOME" >> {self.calls}\n'
            "echo \"ProjectAlreadyExistsError: An active project already exists for '$3'.\" >&2; exit 1\n",
        )
        result = self.run_ensure()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len([c for c in self.calls_made() if c.startswith("t3 project add")]), 2)

    def test_missing_t3_with_unreachable_installer_fails_clearly(self):
        (self.bin / "t3").unlink()
        result = self.run_ensure()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not download T3 Code's installer", result.stderr)
        self.assertFalse((self.home / ".t3-workspaces").exists())


if __name__ == "__main__":
    unittest.main()
