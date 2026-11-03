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
        self.t3_home = self.home / ".t3"
        self.dotfiles = self.root / "dotfiles"
        self.bin = self.root / "bin"
        self.calls = self.root / "calls.log"
        for d in (self.home, self.dotfiles / "bin", self.dotfiles / "omnigent_config", self.bin):
            d.mkdir(parents=True)
        (self.dotfiles / "omnigent_config/topology.env").write_text("T3_SERVER_PORT=4999\n")
        write_exe(self.dotfiles / "bin/omnigent-acp", f'echo "shim $*" >> {self.calls}\n')
        write_exe(self.bin / "t3", f'echo "t3 $*" >> {self.calls}\n')
        write_exe(self.bin / "systemctl", f'echo "systemctl $*" >> {self.calls}\n[ -f {self.root}/unit-present ]\n')
        write_exe(self.bin / "curl", "exit 22\n")

    def run_ensure(self):
        env = {
            "HOME": str(self.home),
            "T3CODE_HOME": str(self.t3_home),
            "DOTFILES_DIR": str(self.dotfiles),
            "PATH": f"{self.bin}:/usr/bin:/bin",
        }
        return subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True)

    def calls_made(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def settings(self):
        return json.loads((self.t3_home / "userdata/settings.json").read_text())

    def test_fresh_host_gets_providers_loopback_env_and_service(self):
        result = self.run_ensure()
        self.assertEqual(result.returncode, 0, result.stderr)
        instances = self.settings()["providerInstances"]
        self.assertEqual(
            instances["omnigent-codex"],
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
        self.assertEqual(instances["omnigent-claude"]["config"]["commandArgs"], ["--agent", "claude-native-ui"])
        self.assertEqual(
            (self.t3_home / "service.env").read_text(), "T3CODE_HOST=127.0.0.1\nT3CODE_PORT=4999\n"
        )
        self.assertIn("shim --help", self.calls_made())
        self.assertIn("t3 service install", self.calls_made())

    def test_existing_settings_and_env_entries_are_kept(self):
        (self.t3_home / "userdata").mkdir(parents=True)
        (self.t3_home / "userdata/settings.json").write_text(
            json.dumps({"theme": "dark", "providerInstances": {"codex": {"driver": "codex"}}})
        )
        (self.t3_home / "service.env").write_text("T3CODE_PORT=1\nOTHER=x\n")
        self.run_ensure()
        settings = self.settings()
        self.assertEqual(settings["theme"], "dark")
        self.assertEqual(settings["providerInstances"]["codex"], {"driver": "codex"})
        self.assertIn("omnigent-claude", settings["providerInstances"])
        self.assertEqual(
            (self.t3_home / "service.env").read_text(), "T3CODE_PORT=4999\nOTHER=x\nT3CODE_HOST=127.0.0.1\n"
        )

    def test_rerun_with_installed_unit_changes_nothing(self):
        self.run_ensure()
        (self.root / "unit-present").touch()
        settings_path = self.t3_home / "userdata/settings.json"
        before = settings_path.stat().st_mtime_ns
        self.calls.unlink()
        result = self.run_ensure()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(settings_path.stat().st_mtime_ns, before)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("t3 service install", self.calls_made())

    def test_missing_t3_with_unreachable_installer_fails_clearly(self):
        (self.bin / "t3").unlink()
        result = self.run_ensure()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not download T3 Code's installer", result.stderr)
        self.assertFalse((self.t3_home / "userdata/settings.json").exists())


if __name__ == "__main__":
    unittest.main()
