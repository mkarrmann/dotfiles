"""Isolated checks for bin/t3-ensure, bin/t3-workspace-serve and bin/t3-pairing-tokens:
temp home, fake t3/systemctl/curl, no network.

Run with: python3 -m unittest discover -s tests -p test_t3_ensure.py
"""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin/t3-ensure"
SERVE = ROOT / "bin/t3-workspace-serve"
PAIRING_TOKENS = ROOT / "bin/t3-pairing-tokens"


def write_exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def write_fake_systemctl(path: Path, calls: Path, state: Path) -> None:
    """A user manager whose units are enabled or active while STATE/enabled/UNIT
    or STATE/active/UNIT exists; `disable` fails while STATE/disable-fails does."""
    for d in ("enabled", "active"):
        (state / d).mkdir(parents=True, exist_ok=True)
    write_exe(
        path,
        f'echo "systemctl $*" >> {calls}\n'
        f'unit=""; for a in "$@"; do unit="$a"; done\n'
        f'now=false; case " $* " in *" --now "*) now=true ;; esac\n'
        f'case "$2" in\n'
        f'  is-enabled) [ -f "{state}/enabled/$unit" ]; exit ;;\n'
        f'  is-active) [ -f "{state}/active/$unit" ]; exit ;;\n'
        f'  enable) touch "{state}/enabled/$unit"; if $now; then touch "{state}/active/$unit"; fi ;;\n'
        f'  disable) [ ! -e "{state}/disable-fails" ] || exit 1\n'
        f'    rm -f "{state}/enabled/$unit"; if $now; then rm -f "{state}/active/$unit"; fi ;;\n'
        f'esac\n',
    )


class T3EnsureTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / "home"
        self.dotfiles = self.root / "dotfiles"
        self.bin = self.root / "bin"
        self.calls = self.root / "calls.log"
        self.units = self.root / "units"
        self.layout = self.root / "layout.json"
        self.profile = "work"
        for d in (self.home / "checkout1", self.home / "checkout2", self.home / "checkoutx", self.dotfiles / "bin", self.bin):
            d.mkdir(parents=True)
        shutil.copy2(ROOT / "bin/dotfiles-profile", self.dotfiles / "bin/dotfiles-profile")
        write_exe(self.dotfiles / "bin/omnigent-acp", f'echo "shim $*" >> {self.calls}\n')
        write_exe(self.dotfiles / "bin-linux/startup-windows", f'echo "startup-windows $*" >> {self.calls}\ncat {self.layout}\n')
        write_exe(self.bin / "t3", f'echo "t3 $* home=$T3CODE_HOME" >> {self.calls}\n')
        write_fake_systemctl(self.bin / "systemctl", self.calls, self.units)
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

    def run_unit(self, unit: str) -> None:
        """A unit enabled and started, as the old t3-ensure left each t3@checkoutN."""
        (self.units / "enabled" / unit).touch()
        (self.units / "active" / unit).touch()

    def units_in(self, state: str) -> list[str]:
        return sorted(p.name for p in (self.units / state).iterdir())

    def use_desktop_layout(self, rows: dict[str, Path]) -> None:
        """Run as the desktop, with --print-layout declaring server "desktop" and one t3 row per (slot, workdir)."""
        self.profile = "desktop"
        t3 = [
            {"workspace": str(n), "slot": slot, "workdir": str(workdir), "port": 3780}
            for n, (slot, workdir) in enumerate(rows.items(), start=2)
        ]
        self.layout.write_text(json.dumps({"terminals": [], "t3Server": "desktop", "t3Port": 3780, "t3": t3}))

    def test_work_runs_one_server_with_every_checkout_as_a_project(self):
        (self.home / "checkout10").mkdir()
        result = self.run_ensure()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.settings("checkouts")["providerInstances"]["omnigent-codex"],
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
        self.assertEqual(self.settings("checkouts")["providerInstances"]["claudeAgent"], {"driver": "claudeAgent", "enabled": False})
        self.assertEqual(self.units_in("enabled"), ["t3@checkouts.service"])
        self.assertEqual(self.units_in("active"), ["t3@checkouts.service"])
        # Added through the CLI, not bootstrapped, so new windows skip T3 Code's first-run wizard.
        home = self.home / ".t3-workspaces/checkouts"
        self.assertEqual(
            sorted(c for c in self.calls_made() if c.startswith("t3 project add")),
            sorted(f"t3 project add {self.home / name} home={home}" for name in ("checkout1", "checkout2", "checkout10")),
        )
        self.assertEqual([p.name for p in (self.home / ".t3-workspaces").iterdir()], ["checkouts"])
        self.assertIn("shim --help", self.calls_made())
        self.assertFalse([c for c in self.calls_made() if c.startswith("startup-windows")])

    def test_work_retires_the_server_per_checkout(self):
        self.run_unit("t3@checkout1.service")
        # A server whose checkout is gone is found by the state the old t3-ensure wrote for it.
        old_state = self.home / ".t3-workspaces/checkout7/userdata/settings.json"
        old_state.parent.mkdir(parents=True)
        old_state.write_text('{"theme": "dark"}')
        self.run_unit("t3@checkout7.service")
        result = self.run_ensure()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.units_in("enabled"), ["t3@checkouts.service"])
        self.assertEqual(self.units_in("active"), ["t3@checkouts.service"])
        self.assertIn("retired t3@checkout1.service", result.stdout)
        self.assertIn("retired t3@checkout7.service", result.stdout)
        # Their state stays.
        self.assertEqual(old_state.read_text(), '{"theme": "dark"}')
        self.calls.unlink()
        rerun = self.run_ensure()
        self.assertEqual((rerun.returncode, rerun.stdout), (0, ""))

    def test_work_reports_a_server_per_checkout_it_could_not_retire(self):
        self.run_unit("t3@checkout2.service")
        (self.units / "disable-fails").touch()
        result = self.run_ensure()
        self.assertEqual(result.returncode, 1)
        self.assertIn("WARNING: could not disable t3@checkout2.service", result.stdout)
        self.assertEqual(self.units_in("active"), ["t3@checkout2.service", "t3@checkouts.service"])

    def test_existing_settings_are_kept(self):
        path = self.home / ".t3-workspaces/checkouts/userdata/settings.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"theme": "dark", "providerInstances": {"codex": {"driver": "codex"}}}))
        self.run_ensure()
        settings = self.settings("checkouts")
        self.assertEqual(settings["theme"], "dark")
        self.assertEqual(settings["providerInstances"]["codex"], {"driver": "codex"})
        self.assertIn("omnigent-claude", settings["providerInstances"])

    def test_rerun_leaves_settings_and_enabled_servers_alone(self):
        self.run_ensure()
        settings_path = self.home / ".t3-workspaces/checkouts/userdata/settings.json"
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

    @unittest.skipUnless(sys.platform.startswith("linux"), "t3-ensure takes the desktop path only on Linux")
    def test_desktop_runs_one_server_with_every_t3_row_as_a_project(self):
        dotfiles, omnigent = self.home / "dotfiles", self.home / "repos/omnigent"
        dotfiles.mkdir()
        omnigent.mkdir(parents=True)
        self.run_unit("t3@checkout1.service")
        self.use_desktop_layout({"dotfiles": dotfiles, "omnigent-repo": omnigent, "gatech": self.home / "gatech"})
        result = self.run_ensure()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("startup-windows --print-layout", self.calls_made())
        providers = self.settings("desktop")["providerInstances"]
        self.assertEqual(providers["omnigent-claude"]["config"]["commandArgs"], ["--agent", "claude-native-ui"])
        self.assertEqual(providers["codex"], {"driver": "codex", "enabled": False})
        self.assertEqual([c for c in self.calls_made() if " enable " in c], ["systemctl --user enable --now t3@desktop.service"])
        home = self.home / ".t3-workspaces/desktop"
        self.assertEqual(
            [c for c in self.calls_made() if c.startswith("t3 project add")],
            [f"t3 project add {dotfiles} home={home}", f"t3 project add {omnigent} home={home}"],
        )
        # A declared workspace that is not cloned here is skipped, not a failure.
        self.assertIn(f"WARNING: skipping t3@desktop's project {self.home / 'gatech'} (workspace 4): it does not exist", result.stdout)
        self.assertEqual(sorted(p.name for p in (self.home / ".t3-workspaces").iterdir()), ["desktop"])
        # The desktop's server is the layout's alone; a ~/checkoutN there is not one,
        # and a t3@checkoutN there is not the work hubs' to retire.
        self.assertFalse([c for c in self.calls_made() if "checkout1" in c])
        self.assertEqual(self.units_in("active"), ["t3@checkout1.service", "t3@desktop.service"])

    @unittest.skipUnless(sys.platform.startswith("linux"), "t3-ensure takes the desktop path only on Linux")
    def test_desktop_gives_the_app_its_own_omnigent_providers(self):
        app_settings = self.home / ".t3/userdata/settings.json"
        app_settings.parent.mkdir(parents=True)
        app_settings.write_text(json.dumps({"theme": "dark", "providerInstances": {"codex": {"driver": "codex", "enabled": True}}}))
        self.use_desktop_layout({})
        result = self.run_ensure()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        settings = json.loads(app_settings.read_text())
        providers = settings["providerInstances"]
        self.assertEqual(providers["omnigent-codex"]["config"]["commandArgs"], ["--agent", "codex-native-ui"])
        self.assertEqual(providers["claudeAgent"], {"driver": "claudeAgent", "enabled": False})
        # A built-in the user configured, and every other setting, are kept.
        self.assertEqual(providers["codex"], {"driver": "codex", "enabled": True})
        self.assertEqual(settings["theme"], "dark")
        # The app runs its own server: no unit and no project for it.
        self.assertFalse([c for c in self.calls_made() if c.startswith(("systemctl", "t3 project"))])
        before = app_settings.read_text()
        self.assertEqual(self.run_ensure().returncode, 0)
        self.assertEqual(app_settings.read_text(), before)

    def test_work_leaves_the_app_environment_alone(self):
        self.assertEqual(self.run_ensure().returncode, 0)
        self.assertFalse((self.home / ".t3").exists())

    @unittest.skipUnless(sys.platform.startswith("linux"), "t3-ensure takes the desktop path only on Linux")
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

    def test_desktop_server_takes_its_port_from_the_layout(self):
        result = self.serve("desktop", "desktop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, self.expected(3780, self.home, "desktop"))

    def test_desktop_layout_override_reaches_the_server(self):
        override = self.root / "layout.sh"
        override.write_text('T3_SERVER=elsewhere\nT3_PORT=4007\n')
        result = self.serve("elsewhere", "desktop", SWAY_WINDOWS_LAYOUT=str(override))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, self.expected(4007, self.home, "elsewhere"))

    def test_desktop_rejects_a_name_other_than_the_layouts_server(self):
        result = self.serve("dotfiles", "desktop")
        self.assertEqual(result.returncode, 2)
        self.assertIn("bin-linux/startup-windows' t3 server is 'desktop', not 'dotfiles'", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_work_server_serves_every_checkout_from_home_on_the_topologys_port(self):
        topology = self.root / "topology.env"
        topology.write_text("T3_PORT=4100\n")
        result = self.serve("checkouts", "work", OMNIGENT_TOPOLOGY_FILE=str(topology))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, self.expected(4100, self.home, "checkouts"))

    def test_work_rejects_a_server_per_checkout(self):
        (self.home / "checkout3").mkdir()
        result = self.serve("checkout3", "work")
        self.assertEqual(result.returncode, 2)
        self.assertIn("the work hubs' t3 server is 'checkouts', not 'checkout3'", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_work_rejects_a_slot_name(self):
        result = self.serve("dotfiles", "work")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")


class T3PairingTokensTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / "home"
        self.bin = self.root / "bin"
        self.units = self.root / "units"
        self.mints = self.root / "mints.log"
        for d in (self.home / "checkout1", self.home / "checkout2", self.home / "checkoutx", self.home / "dotfiles"):
            d.mkdir(parents=True)
        (self.home / "checkout9").write_text("not a checkout")
        # Each `t3 pair` mints a new token, as the real one does.
        write_exe(
            self.home / ".local/bin/t3",
            f'echo "$T3CODE_HOME $*" >> {self.mints}\n'
            f'n=$(( $(wc -l < {self.mints}) ))\n'
            'printf "Pairing with the running server.\\n\\nToken: TOKEN%s\\nExpires: in 12h\\n" "$n"\n',
        )
        write_fake_systemctl(self.bin / "systemctl", self.root / "calls.log", self.units)

    def tokens(self, *args):
        env = {"HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin"}
        return subprocess.run([str(PAIRING_TOKENS), *args], env=env, capture_output=True, text=True)

    def test_as_many_distinct_tokens_as_asked_from_the_one_server(self):
        (self.units / "active/t3@checkouts.service").touch()
        result = self.tokens("3")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["T3_PAIR TOKEN1", "T3_PAIR TOKEN2", "T3_PAIR TOKEN3"])
        servers = {line.split()[0] for line in self.mints.read_text().splitlines()}
        self.assertEqual(servers, {str(self.home / ".t3-workspaces/checkouts")})

    def test_a_count_that_is_no_number_is_misuse(self):
        (self.units / "active/t3@checkouts.service").touch()
        self.assertEqual(self.tokens("all").returncode, 2)
        self.assertFalse(self.mints.exists())

    def test_nothing_while_the_server_is_not_running(self):
        # A server per checkout, as before t3@checkouts, mints nothing either.
        (self.home / ".t3-workspaces/checkout1").mkdir(parents=True)
        (self.units / "active/t3@checkout1.service").touch()
        result = self.tokens()
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        self.assertFalse(self.mints.exists())

    def test_nothing_without_t3(self):
        (self.units / "active/t3@checkouts.service").touch()
        (self.home / ".local/bin/t3").unlink()
        result = self.tokens()
        self.assertEqual((result.returncode, result.stdout), (0, ""))

if __name__ == "__main__":
    unittest.main()
