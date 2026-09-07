"""Isolated desktop launcher checks; no live services or network access."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class OmnigentDesktopTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.home = self.base / "home"
        self.dotfiles = self.base / "dotfiles"
        self.bin = self.base / "bin"
        self.calls = self.base / "calls.jsonl"
        for directory in (self.home / ".local/bin", self.dotfiles / "bin", self.bin):
            directory.mkdir(parents=True)
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "DOTFILES_DIR": str(self.dotfiles),
            "TEST_PROFILE": "desktop",
            "TEST_PLATFORM": "Linux",
            "TEST_CALLS": str(self.calls),
            "OMNIGENT_URL": "https://work-hub.example.net",
        }
        self.script(
            self.dotfiles / "bin/dotfiles-profile",
            '#!/bin/bash\nprintf "%s\\n" "$TEST_PROFILE"\n',
        )
        self.script(self.bin / "uname", '#!/bin/bash\necho "$TEST_PLATFORM"\n')
        self.script(
            self.bin / "systemctl",
            "#!/usr/bin/python3\n"
            "import json, os, sys\n"
            'with open(os.environ["TEST_CALLS"], "a") as output:\n'
            '    output.write(json.dumps(["systemctl", *sys.argv[1:]]) + "\\n")\n'
            'if os.environ.get("TEST_SYSTEMCTL_FAIL") in sys.argv[1:]:\n'
            '    raise SystemExit(25)\n',
        )
        shutil.copytree(ROOT / "systemd/desktop", self.dotfiles / "systemd/desktop")
        self.unit_dir = self.home / ".config/systemd/user"
        self.unit_dir.mkdir(parents=True)
        self.unit = self.unit_dir / "omnigent-host.service"
        self.unit_source = self.dotfiles / "systemd/desktop/omnigent-host.service"
        # The launcher must never need routing discovery on a desktop.
        self.script(
            self.bin / "omnigent-server-url", "#!/bin/bash\nexit 91\n"
        )
        self.real = self.home / ".local/bin/omnigent"
        self.recording_cli = self.base / "recording-cli"
        self.script(
            self.recording_cli,
            "#!/usr/bin/python3\n"
            "import json, os, sys\n"
            'with open(os.environ["TEST_CALLS"], "a") as output:\n'
            '    output.write(json.dumps(["omnigent", *sys.argv[1:]]) + "\\n")\n'
            'raise SystemExit(int(os.environ.get("TEST_START_EXIT", "0")))\n',
        )
        shutil.copy2(self.recording_cli, self.real)
        self.env["TEST_RECORDING_CLI"] = str(self.recording_cli)
        self.script(
            self.bin / "uv",
            "#!/usr/bin/python3\n"
            "import json, os, pathlib, shutil, sys\n"
            'with open(os.environ["TEST_CALLS"], "a") as output:\n'
            '    output.write(json.dumps(["uv", *sys.argv[1:]]) + "\\n")\n'
            'if os.environ.get("TEST_INSTALL_EXIT"):\n'
            '    raise SystemExit(int(os.environ["TEST_INSTALL_EXIT"]))\n'
            'shutil.copy2(os.environ["TEST_RECORDING_CLI"], '
            'pathlib.Path(os.environ["HOME"]) / ".local/bin/omnigent")\n',
        )

    @staticmethod
    def script(path, contents):
        path.write_text(contents)
        path.chmod(0o755)

    def run_script(self, name, *args):
        path = self.dotfiles / "bin" / name
        shutil.copy2(ROOT / "bin" / name, path)
        return subprocess.run(
            [str(path), *args],
            cwd=self.dotfiles,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def recorded(self):
        if not self.calls.exists():
            return []
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_desktop_ignores_inherited_work_url(self):
        result = self.run_script("omnigent", "claude", "--model", "opus")
        self.assert_success(result)
        self.assertEqual(self.recorded(), [["omnigent", "claude", "--model", "opus"]])

    def test_desktop_preserves_explicit_remote_and_local_choices(self):
        for args in (
            ("claude", "--server", "https://chosen.example.net"),
            ("run", "--server=https://chosen.example.net", "agent.yaml"),
            ("run", "--local", "agent.yaml"),
        ):
            with self.subTest(args=args):
                self.assert_success(self.run_script("omnigent", *args))
                self.assertEqual(self.recorded()[-1], ["omnigent", *args])

    def test_work_still_injects_hub_url(self):
        self.env["TEST_PROFILE"] = "work"
        self.assert_success(self.run_script("omnigent", "claude", "--model", "opus"))
        self.assertEqual(
            self.recorded(),
            [["omnigent", "claude", "--server", self.env["OMNIGENT_URL"], "--model", "opus"]],
        )

    def test_work_preserves_explicit_server_and_local_choices(self):
        self.env["TEST_PROFILE"] = "work"
        for args in (
            ("claude", "--server", "https://chosen.example.net"),
            ("claude", "--server=https://chosen.example.net"),
            ("run", "--local", "agent.yaml"),
            ("start",),
        ):
            with self.subTest(args=args):
                self.assert_success(self.run_script("omnigent", *args))
                self.assertEqual(self.recorded()[-1], ["omnigent", *args])

    def test_mac_ensure_starts_local_without_reinstalling(self):
        self.env["TEST_PLATFORM"] = "Darwin"
        self.assert_success(self.run_script("omnigent-desktop-ensure"))
        self.assertEqual(
            self.recorded(), [["omnigent", "start", "--server", "", "--non-interactive"]]
        )

    def test_work_ensure_refuses_before_install_or_start(self):
        self.env["TEST_PROFILE"] = "work"
        self.real.unlink()
        result = self.run_script("omnigent-desktop-ensure")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.recorded(), [])
        self.assertFalse(self.real.exists())

    def test_mac_ensure_installs_missing_tool_before_starting(self):
        self.env["TEST_PLATFORM"] = "Darwin"
        self.real.unlink()
        self.assert_success(self.run_script("omnigent-desktop-ensure"))
        self.assertEqual(
            self.recorded(),
            [
                ["uv", "tool", "install", "omnigent>=0.12.0"],
                ["omnigent", "start", "--server", "", "--non-interactive"],
            ],
        )

    def test_mac_ensure_propagates_start_failure(self):
        self.env["TEST_PLATFORM"] = "Darwin"
        self.env["TEST_START_EXIT"] = "23"
        result = self.run_script("omnigent-desktop-ensure")
        self.assertEqual(result.returncode, 23)
        self.assertEqual(len(self.recorded()), 1)

    def test_desktop_ensure_stops_after_install_failure(self):
        self.real.unlink()
        self.env["TEST_INSTALL_EXIT"] = "24"
        result = self.run_script("omnigent-desktop-ensure")
        self.assertEqual(result.returncode, 24)
        self.assertEqual(self.recorded(), [["uv", "tool", "install", "omnigent>=0.12.0"]])

    def test_linux_stage_links_unit_without_installing_or_activating(self):
        self.real.unlink()
        self.assert_success(self.run_script("omnigent-desktop-ensure", "--stage"))
        self.assertTrue(self.unit.is_symlink())
        self.assertEqual(self.unit.resolve(), self.unit_source)
        self.assertFalse(self.real.exists())
        self.assertEqual(self.recorded(), [])

    def test_linux_stage_replaces_old_managed_unit_link(self):
        old_source = self.dotfiles / "systemd/omnigent-host.service"
        old_source.write_text("old work service\n")
        self.unit.symlink_to(old_source)
        self.assert_success(self.run_script("omnigent-desktop-ensure", "--stage"))
        self.assertEqual(self.unit.resolve(), self.unit_source)
        self.assertEqual(old_source.read_text(), "old work service\n")
        self.assertEqual(self.recorded(), [])

    def test_linux_stage_is_repeatable(self):
        for _ in range(2):
            self.assert_success(self.run_script("omnigent-desktop-ensure", "--stage"))
        self.assertEqual(self.unit.resolve(), self.unit_source)
        self.assertEqual(self.recorded(), [])

    def test_linux_stage_rejects_custom_unit_and_preserves_it(self):
        self.unit.write_text("my custom service\n")
        result = self.run_script("omnigent-desktop-ensure", "--stage")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.unit.is_symlink())
        self.assertEqual(self.unit.read_text(), "my custom service\n")
        self.assertEqual(self.recorded(), [])

    def test_linux_stage_rejects_foreign_unit_link(self):
        foreign = self.base / "foreign-host.service"
        foreign.write_text("foreign service\n")
        self.unit.symlink_to(foreign)
        result = self.run_script("omnigent-desktop-ensure", "--stage")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.unit.resolve(), foreign)
        self.assertEqual(self.recorded(), [])

    def test_work_stage_refuses_without_changing_units(self):
        self.env["TEST_PROFILE"] = "work"
        result = self.run_script("omnigent-desktop-ensure", "--stage")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.unit.exists())
        self.assertEqual(self.recorded(), [])

    def test_linux_activation_uses_managed_systemd_unit(self):
        self.assert_success(self.run_script("omnigent-desktop-ensure"))
        self.assertEqual(self.unit.resolve(), self.unit_source)
        self.assertEqual(self.recorded(), self.activation_calls())

    def test_linux_activation_installs_tool_before_service_commands(self):
        self.real.unlink()
        self.assert_success(self.run_script("omnigent-desktop-ensure"))
        self.assertEqual(
            self.recorded(),
            [["uv", "tool", "install", "omnigent>=0.12.0"], *self.activation_calls()],
        )

    @staticmethod
    def activation_calls():
        return [
            ["systemctl", "--user", "stop", "omnigent-host.service"],
            ["omnigent", "host", "stop", "--server", "", "--daemon-only"],
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", "--now", "omnigent-host.service"],
        ]

    def disabled_units(self):
        path = self.dotfiles / "systemd/desktop/disabled-units.list"
        return [
            line.strip()
            for line in path.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

    def test_linux_retires_only_owned_units_before_host_activation(self):
        names = self.disabled_units()
        self.assertGreaterEqual(len(names), 2)
        owned = names[:-1]
        for name in owned:
            source = self.dotfiles / "systemd" / name
            source.write_text("work unit\n")
            (self.unit_dir / name).symlink_to(source)
        foreign = self.base / names[-1]
        foreign.write_text("foreign unit\n")
        foreign_link = self.unit_dir / names[-1]
        foreign_link.symlink_to(foreign)
        self.assert_success(self.run_script("omnigent-desktop-ensure"))
        self.assertEqual(
            self.recorded(),
            [
                ["systemctl", "--user", "disable", "--now", *owned],
                *self.activation_calls(),
            ],
        )
        self.assertEqual(foreign_link.resolve(), foreign)

    def test_linux_activation_failure_is_reported(self):
        self.env["TEST_SYSTEMCTL_FAIL"] = "enable"
        result = self.run_script("omnigent-desktop-ensure")
        self.assertEqual(result.returncode, 25)
        self.assertEqual(self.recorded(), self.activation_calls())

    def test_linux_retirement_failure_prevents_host_replacement(self):
        name = self.disabled_units()[0]
        source = self.dotfiles / "systemd" / name
        source.write_text("work timer\n")
        (self.unit_dir / name).symlink_to(source)
        self.env["TEST_SYSTEMCTL_FAIL"] = "disable"
        result = self.run_script("omnigent-desktop-ensure")
        self.assertEqual(result.returncode, 25)
        self.assertEqual(
            self.recorded(), [["systemctl", "--user", "disable", "--now", name]]
        )


if __name__ == "__main__":
    unittest.main()
