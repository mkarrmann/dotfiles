"""Server extension selection and stale-config detection with a temporary HOME."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class OmnigentServerConfigTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.home = self.base / "home"
        self.dotfiles = self.base / "dotfiles"
        self.bin = self.base / "bin"
        for directory in (self.home / ".omnigent", self.dotfiles / "bin", self.bin):
            directory.mkdir(parents=True)
        shutil.copytree(ROOT / "omnigent_config", self.dotfiles / "omnigent_config")
        self.calls = self.base / "routing-calls"
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "DOTFILES_DIR": str(self.dotfiles),
            "OMNIGENT_PY": sys.executable,
            "TEST_PROFILE": "desktop",
            "TEST_CANDIDATE": "1",
            "TEST_CALLS": str(self.calls),
        }
        self.script(self.dotfiles / "bin/dotfiles-profile", 'echo "$TEST_PROFILE"')
        self.script(
            self.dotfiles / "bin/omnigent-server-url",
            'echo "$*" >> "$TEST_CALLS"\nexit "$TEST_CANDIDATE"',
        )
        self.script(self.bin / "uv", "exit 1")
        self.config = self.home / ".omnigent/config.yaml"
        self.config.write_text("host: {host_id: keep}\nrunner: {custom_option: keep}\n")

    @staticmethod
    def script(path, body):
        path.write_text("#!/bin/bash\nset -eu\n" + body + "\n")
        path.chmod(0o755)

    def run_helper(self, name):
        return subprocess.run(
            [str(ROOT / "bin" / name)],
            env=self.env,
            cwd=self.dotfiles,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_desktop_and_hub_install_router_but_work_clients_do_not(self):
        for profile, candidate, router in (
            ("desktop", False, True),
            ("work", True, True),
            ("work", False, False),
        ):
            with self.subTest(profile=profile, candidate=candidate):
                self.env["TEST_PROFILE"] = profile
                self.env["TEST_CANDIDATE"] = "0" if candidate else "1"
                self.config.write_text("host: {host_id: keep}\nrunner: {custom_option: keep}\n")
                result = self.run_helper("omnigent-config-ensure")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                config = yaml.safe_load(self.config.read_text())
                self.assertEqual(config.get("debug_router_modules", []), ["omnigent_watcher.http_api"] if router else [])
                self.assertEqual(config["host"], {"host_id": "keep"})
                self.assertEqual(config["runner"]["custom_option"], "keep")
                self.assertIn("no_foreground_wait", config["policy_modules"])
                if profile == "desktop":
                    self.assertFalse(self.calls.exists())
                before = self.config.stat().st_mtime_ns
                self.assertEqual(self.run_helper("omnigent-config-ensure").returncode, 0)
                self.assertEqual(self.config.stat().st_mtime_ns, before)

    def test_router_source_edits_are_reported_as_stale_without_routing_queries(self):
        self.script(
            self.bin / "systemctl",
            'if [[ "$2" == show ]]; then echo "2026-01-02 00:00:00 UTC"; fi',
        )
        sources = self.dotfiles / "services/omnigent-watcher/src/omnigent_watcher"
        sources.mkdir(parents=True)
        router = sources / "http_api.py"
        router.write_text("# router source\n")
        for path in self.dotfiles.rglob("*"):
            if path.is_file():
                os.utime(path, (1, 1))
        self.assertEqual(self.run_helper("omnigent-server-config-stale").returncode, 1)
        os.utime(router, (2_000_000_000, 2_000_000_000))
        result = self.run_helper("omnigent-server-config-stale")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("omnigent_watcher/http_api.py", result.stdout)
        self.assertFalse(self.calls.exists())


if __name__ == "__main__":
    unittest.main()
