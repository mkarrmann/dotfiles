"""Isolated desktop launcher checks; no live services or network access."""

import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class OmnigentDesktopTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        # Resolved: on macOS mkdtemp hands back /var/..., and the script under
        # test compares `readlink -f "$unit"` against "$DOTFILES/systemd/...".
        # readlink resolves /var to /private/var while DOTFILES would not, so
        # every managed unit reads as foreign ("is not a managed dotfiles
        # unit"). /var is a real directory on Linux, so this only bites here.
        self.base = Path(temporary.name).resolve()
        self.home = self.base / "home"
        self.dotfiles = self.base / "dotfiles"
        self.bin = self.base / "bin"
        self.calls = self.base / "calls.jsonl"
        self.server_env = self.base / "server-env.json"
        for directory in (self.home / ".local/bin", self.dotfiles / "bin", self.bin):
            directory.mkdir(parents=True)
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "DOTFILES_DIR": str(self.dotfiles),
            "TEST_PROFILE": "desktop",
            "TEST_PLATFORM": "Linux",
            "TEST_CALLS": str(self.calls),
            "TEST_SERVER_ENV": str(self.server_env),
            "OMNIGENT_URL": "https://work-hub.example.net",
        }
        self.script(
            self.dotfiles / "bin/dotfiles-profile",
            '#!/bin/bash\nprintf "%s\\n" "$TEST_PROFILE"\n',
        )
        self.script(self.bin / "uname", '#!/bin/bash\necho "$TEST_PLATFORM"\n')
        self.script(
            self.dotfiles / "bin/omnigent-config-ensure",
            "#!/usr/bin/python3\n"
            "import json, os\n"
            'with open(os.environ["TEST_CALLS"], "a") as output:\n'
            '    output.write(json.dumps(["config-ensure"]) + "\\n")\n',
        )
        self.script(
            self.bin / "launchctl",
            "#!/usr/bin/python3\n"
            "import json, os, sys\n"
            'with open(os.environ["TEST_CALLS"], "a") as output:\n'
            '    output.write(json.dumps(["launchctl", *sys.argv[1:]]) + "\\n")\n'
            'if sys.argv[1] == "print" and not os.environ.get("TEST_LAUNCHCTL_ACTIVE"):\n'
            '    raise SystemExit(1)\n',
        )
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
        self.watcher_unit = self.unit_dir / "omnigent-watcher.service"
        self.watcher_source = self.dotfiles / "systemd/desktop/omnigent-watcher.service"
        self.watcher_plist = self.home / "Library/LaunchAgents/com.mkarrmann.omnigent-watcher.plist"
        # The launcher must never need routing discovery on a desktop.
        self.script(
            self.bin / "omnigent-server-url", "#!/bin/bash\nexit 91\n"
        )
        self.real = self.home / ".local/bin/omnigent"
        self.recording_cli = self.base / "recording-cli"
        self.script(
            self.recording_cli,
            "#!/usr/bin/python3\n"
            "import json, os, pathlib, sys\n"
            'with open(os.environ["TEST_CALLS"], "a") as output:\n'
            '    output.write(json.dumps(["omnigent", *sys.argv[1:]]) + "\\n")\n'
            'with open(os.environ["TEST_SERVER_ENV"], "w") as output:\n'
            '    json.dump({name: os.environ.get(name) for name in ("PYTHONPATH", "OMNIGENT_URL")}, output)\n'
            'if os.environ.get("TEST_MANAGED_SERVER_STATE"):\n'
            '    state = pathlib.Path(os.environ["TEST_MANAGED_SERVER_STATE"])\n'
            '    if sys.argv[1:] == ["server", "stop"]:\n'
            '        state.unlink(missing_ok=True)\n'
            '    elif sys.argv[1] == "start" and not state.exists():\n'
            '        state.write_text(os.environ.get("PYTHONPATH", ""))\n'
            'if sys.argv[1] == "start":\n'
            '    raise SystemExit(int(os.environ.get("TEST_START_EXIT", "0")))\n',
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
        self.assertEqual(self.recorded(), self.mac_activation_calls())
        self.assertEqual(
            json.loads(self.server_env.read_text()),
            {
                "PYTHONPATH": f"{self.dotfiles}/omnigent_config/policy_modules:{self.dotfiles}/services/omnigent-watcher/src",
                "OMNIGENT_URL": "http://127.0.0.1:6767",
            },
        )

    def mac_activation_calls(self, already_active=False):
        domain = f"gui/{os.getuid()}"
        service = f"{domain}/com.mkarrmann.omnigent-watcher"
        return [
            ["config-ensure"],
            ["omnigent", "server", "stop"],
            ["omnigent", "start", "--server", "", "--non-interactive"],
            ["launchctl", "print", service],
            *([["launchctl", "bootout", service]] if already_active else []),
            ["launchctl", "bootstrap", domain, str(self.watcher_plist)],
        ]

    def test_mac_stage_renders_watch_job_without_starting_anything(self):
        self.env["TEST_PLATFORM"] = "Darwin"
        self.real.unlink()
        self.assert_success(self.run_script("omnigent-desktop-ensure", "--stage"))
        payload = plistlib.loads(self.watcher_plist.read_bytes())
        self.assertEqual(
            payload["ProgramArguments"],
            [
                f"{self.dotfiles}/services/omnigent-watcher/.venv/bin/omnigent-watcher",
                "--config", f"{self.dotfiles}/services/omnigent-watcher/config.toml", "run",
            ],
        )
        self.assertTrue(payload["RunAtLoad"])
        self.assertTrue(payload["KeepAlive"])
        self.assertEqual(payload["EnvironmentVariables"]["OMNIGENT_URL"], "http://127.0.0.1:6767")
        self.assertTrue(Path(payload["StandardOutPath"]).parent.is_dir())
        self.assertEqual(self.recorded(), [])
        self.assertFalse(self.real.exists())
        before = self.watcher_plist.stat().st_mtime_ns
        self.assert_success(self.run_script("omnigent-desktop-ensure", "--stage"))
        self.assertEqual(self.watcher_plist.stat().st_mtime_ns, before)

    def test_mac_stage_preserves_foreign_job(self):
        self.env["TEST_PLATFORM"] = "Darwin"
        self.watcher_plist.parent.mkdir(parents=True)
        original = plistlib.dumps({"Label": "com.mkarrmann.omnigent-watcher", "ProgramArguments": ["/custom/watch"]})
        self.watcher_plist.write_bytes(original)
        result = self.run_script("omnigent-desktop-ensure", "--stage")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.watcher_plist.read_bytes(), original)
        self.assertEqual(self.recorded(), [])

    def test_mac_activation_replaces_existing_watch_job(self):
        self.env["TEST_PLATFORM"] = "Darwin"
        self.env["TEST_LAUNCHCTL_ACTIVE"] = "1"
        self.assert_success(self.run_script("omnigent-desktop-ensure"))
        self.assertEqual(self.recorded(), self.mac_activation_calls(already_active=True))

    def test_mac_convergence_replaces_a_server_with_old_router_imports(self):
        self.env["TEST_PLATFORM"] = "Darwin"
        state = self.base / "managed-server"
        state.write_text("old router imports")
        self.env["TEST_MANAGED_SERVER_STATE"] = str(state)
        self.assert_success(self.run_script("omnigent-desktop-ensure"))
        self.assertIn(f"{self.dotfiles}/services/omnigent-watcher/src", state.read_text())
        self.assertEqual(self.recorded(), self.mac_activation_calls())

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
                *self.mac_activation_calls(),
            ],
        )

    def test_mac_ensure_propagates_start_failure(self):
        self.env["TEST_PLATFORM"] = "Darwin"
        self.env["TEST_START_EXIT"] = "23"
        result = self.run_script("omnigent-desktop-ensure")
        self.assertEqual(result.returncode, 23)
        self.assertEqual(self.recorded(), self.mac_activation_calls()[:3])

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
        self.assertEqual(self.watcher_unit.resolve(), self.watcher_source)
        self.assertTrue((self.home / ".local/state/omnigent-watcher").is_dir())
        self.assertFalse(self.real.exists())
        self.assertEqual(self.recorded(), [])

    def test_linux_stage_replaces_old_managed_unit_link(self):
        old_source = self.dotfiles / "systemd/omnigent-host.service"
        old_source.write_text("old work service\n")
        self.unit.symlink_to(old_source)
        old_watcher_source = self.dotfiles / "systemd/omnigent-watcher.service"
        old_watcher_source.write_text("old work watcher\n")
        self.watcher_unit.symlink_to(old_watcher_source)
        self.assert_success(self.run_script("omnigent-desktop-ensure", "--stage"))
        self.assertEqual(self.unit.resolve(), self.unit_source)
        self.assertEqual(self.watcher_unit.resolve(), self.watcher_source)
        self.assertEqual(old_source.read_text(), "old work service\n")
        self.assertEqual(self.recorded(), [])

    def test_linux_stage_preserves_foreign_watcher_before_staging_host(self):
        self.watcher_unit.write_text("custom watcher\n")
        result = self.run_script("omnigent-desktop-ensure", "--stage")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.watcher_unit.read_text(), "custom watcher\n")
        self.assertFalse(self.unit.exists())
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
            ["config-ensure"],
            ["systemctl", "--user", "stop", "omnigent-watcher.service", "omnigent-host.service"],
            ["omnigent", "server", "stop"],
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", "--now", "omnigent-host.service", "omnigent-watcher.service"],
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
                ["config-ensure"],
                ["systemctl", "--user", "disable", "--now", *owned],
                *self.activation_calls()[1:],
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
            self.recorded(), [["config-ensure"], ["systemctl", "--user", "disable", "--now", name]]
        )


if __name__ == "__main__":
    unittest.main()
