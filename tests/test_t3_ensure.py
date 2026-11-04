"""Isolated checks for bin/t3-ensure and bin/t3-workspace-serve: temp home, fake
t3/systemctl/curl, no network.

Run with: python3 -m unittest discover -s tests -p test_t3_ensure.py
"""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin/t3-ensure"
SERVE = ROOT / "bin/t3-workspace-serve"


def write_exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
        self.layout = self.root / "layout.json"
        self.profile = "work"
        for d in (self.home / "checkout1", self.home / "checkout2", self.home / "checkoutx", self.dotfiles / "bin", self.bin, self.enabled):
            d.mkdir(parents=True)
        shutil.copy2(ROOT / "bin/dotfiles-profile", self.dotfiles / "bin/dotfiles-profile")
        write_exe(self.dotfiles / "bin/omnigent-acp", f'echo "shim $*" >> {self.calls}\n')
        write_exe(self.dotfiles / "bin-linux/startup-windows", f'echo "startup-windows $*" >> {self.calls}\ncat {self.layout}\n')
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
        env = {
            "HOME": str(self.home),
            "DOTFILES_DIR": str(self.dotfiles),
            "DOTFILES_PROFILE": self.profile,
            "PATH": f"{self.bin}:/usr/bin:/bin",
        }
        return subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True)

    def calls_made(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def settings(self, name):
        return json.loads((self.home / ".t3-workspaces" / name / "userdata/settings.json").read_text())

    def use_desktop_layout(self, servers: dict[str, Path]) -> None:
        """Run as the desktop, with --print-layout declaring one t3 row per (slot, workdir)."""
        self.profile = "desktop"
        rows = [
            {"workspace": str(n), "slot": slot, "workdir": str(workdir), "port": 3780 + n}
            for n, (slot, workdir) in enumerate(servers.items(), start=2)
        ]
        self.layout.write_text(json.dumps({"terminals": [], "t3": rows}))

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
        self.assertFalse([c for c in self.calls_made() if c.startswith("startup-windows")])

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

    def test_desktop_serves_each_t3_row_of_the_layout(self):
        dotfiles, omnigent = self.home / "dotfiles", self.home / "repos/omnigent"
        dotfiles.mkdir()
        omnigent.mkdir(parents=True)
        self.use_desktop_layout({"dotfiles": dotfiles, "omnigent-repo": omnigent, "gatech": self.home / "gatech"})
        result = self.run_ensure()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("startup-windows --print-layout", self.calls_made())
        for slot, workdir in (("dotfiles", dotfiles), ("omnigent-repo", omnigent)):
            providers = self.settings(slot)["providerInstances"]
            self.assertEqual(providers["omnigent-claude"]["config"]["commandArgs"], ["--agent", "claude-native-ui"])
            self.assertEqual(providers["codex"], {"driver": "codex", "enabled": False})
            self.assertIn(f"systemctl --user enable --now t3@{slot}.service", self.calls_made())
            self.assertIn(f"t3 project add {workdir} home={self.home / '.t3-workspaces' / slot}", self.calls_made())
        # A declared workspace that is not cloned here is skipped, not a failure.
        self.assertIn(f"WARNING: skipping t3@gatech: {self.home / 'gatech'} does not exist", result.stdout)
        self.assertFalse((self.home / ".t3-workspaces/gatech").exists())
        self.assertFalse([c for c in self.calls_made() if "t3@gatech" in c])
        # The desktop's servers are the layout's alone; a ~/checkoutN there is not one.
        self.assertFalse((self.home / ".t3-workspaces/checkout1").exists())
        self.assertFalse([c for c in self.calls_made() if "checkout1" in c])

    def test_desktop_layout_failure_fails_clearly(self):
        self.profile = "desktop"
        write_exe(self.dotfiles / "bin-linux/startup-windows", "echo 'ERROR: bad table' >&2; exit 1\n")
        result = self.run_ensure()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ERROR: bad table", result.stderr)
        self.assertIn("--print-layout failed", result.stderr)
        self.assertFalse((self.home / ".t3-workspaces").exists())
        self.assertFalse([c for c in self.calls_made() if c.startswith("systemctl")])


class T3WorkspaceServeTest(unittest.TestCase):
    """Runs against this checkout's own bin-linux/startup-windows table and topology.env."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        write_exe(self.home / ".local/bin/t3", 'echo "t3 $* cwd=$PWD home=$T3CODE_HOME"\n')

    def serve(self, name: str, profile: str, **env: str):
        full_env = {
            "HOME": str(self.home),
            "DOTFILES_DIR": str(ROOT),
            "DOTFILES_PROFILE": profile,
            "PATH": "/usr/bin:/bin",
            **env,
        }
        return subprocess.run([str(SERVE), name], env=full_env, capture_output=True, text=True)

    def expected(self, port: int, workdir: Path, name: str) -> str:
        return f"t3 serve --host 127.0.0.1 --port {port} --no-browser cwd={workdir} home={self.home / '.t3-workspaces' / name}\n"

    def test_desktop_slot_takes_workdir_and_port_from_the_layout(self):
        (self.home / "dotfiles").mkdir()
        result = self.serve("dotfiles", "desktop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, self.expected(3782, self.home / "dotfiles", "dotfiles"))

    def test_desktop_layout_override_reaches_the_server(self):
        (self.home / "somewhere").mkdir()
        override = self.root / "layout.sh"
        override.write_text('WORKSPACES=("7|term|elsewhere|~/somewhere|true" "7|t3")\nT3_BASE_PORT=4000\n')
        result = self.serve("elsewhere", "desktop", SWAY_WINDOWS_LAYOUT=str(override))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, self.expected(4007, self.home / "somewhere", "elsewhere"))

    def test_desktop_slot_without_a_t3_row_fails_clearly(self):
        override = self.root / "layout.sh"
        override.write_text('WORKSPACES=("7|term|elsewhere|~/somewhere|true")\n')
        result = self.serve("elsewhere", "desktop", SWAY_WINDOWS_LAYOUT=str(override))
        self.assertEqual(result.returncode, 2)
        self.assertIn("no t3 row in bin-linux/startup-windows declares slot 'elsewhere'", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_work_checkout_takes_its_port_from_topology(self):
        (self.home / "checkout3").mkdir()
        result = self.serve("checkout3", "work")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, self.expected(3783, self.home / "checkout3", "checkout3"))

    def test_work_rejects_a_slot_name(self):
        result = self.serve("dotfiles", "work")
        self.assertEqual(result.returncode, 2)
        self.assertIn("expected a checkoutN directory name, got 'dotfiles'", result.stderr)


if __name__ == "__main__":
    unittest.main()
