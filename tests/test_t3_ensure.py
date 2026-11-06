"""Isolated checks for bin/t3-ensure, bin/t3-workspace-serve and bin/t3-pairing-tokens:
temp home, fake t3/systemctl/curl, no network.

Run with: python3 -m unittest discover -s tests -p test_t3_ensure.py
"""

import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

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
        write_exe(self.dotfiles / "bin/omnigent-opencode", f'echo "shim $*" >> {self.calls}\n')
        write_exe(self.bin / "uv", f'echo "uv $*" >> {self.calls}\n')
        write_exe(self.dotfiles / "bin-linux/startup-windows", f'echo "startup-windows $*" >> {self.calls}\ncat {self.layout}\n')
        write_exe(self.bin / "t3", f'echo "t3 $* home=$T3CODE_HOME" >> {self.calls}\n')
        write_fake_systemctl(self.bin / "systemctl", self.calls, self.units)
        write_exe(self.bin / "curl", "exit 22\n")
        write_exe(self.bin / "uname", 'echo "$FAKE_UNAME"\n')
        write_exe(self.bin / "hostname", 'echo "${FAKE_FQDN:-devvm1.cco0.facebook.com}"\n')
        self.os = "Linux"

    def run_ensure(self):
        env = {
            "HOME": str(self.home),
            "DOTFILES_DIR": str(self.dotfiles),
            "DOTFILES_PROFILE": self.profile,
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "FAKE_UNAME": self.os,
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
            {"workspace": str(n), "slot": slot, "workdir": str(workdir)}
            for n, (slot, workdir) in enumerate(rows.items(), start=2)
        ]
        self.layout.write_text(json.dumps({"terminals": [], "t3Server": "desktop", "t3Port": 3780, "t3": t3}))

    def test_work_runs_one_server_with_every_checkout_as_a_project(self):
        (self.home / "checkout10").mkdir()
        result = self.run_ensure()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.settings("checkouts")["providerInstances"]["omnigent"],
            {"driver": "opencode", "enabled": True, "displayName": "Omnigent", "config": {"binaryPath": "omnigent-opencode"}},
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
        self.assertIn(f"uv sync --frozen --quiet --project {self.dotfiles / 'services/omnigent-opencode'}", self.calls_made())
        self.assertIn("shim --version", self.calls_made())
        self.assertFalse([c for c in self.calls_made() if c.startswith("startup-windows")])

    def test_work_names_each_checkout_after_its_host(self):
        result = self.run_ensure()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        home = self.home / ".t3-workspaces/checkouts"
        self.assertEqual(
            sorted(c for c in self.calls_made() if c.startswith("t3 project rename")),
            sorted(f"t3 project rename {self.home / n} {n} \u00b7 cco home={home}" for n in ("checkout1", "checkout2")),
        )

    def test_a_project_that_cannot_be_named_is_reported(self):
        write_exe(
            self.bin / "t3",
            f'echo "t3 $* home=$T3CODE_HOME" >> {self.calls}\n'
            '[ "$2" = rename ] && exit 1\n'
            "exit 0\n",
        )
        result = self.run_ensure()
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"WARNING: could not name t3@checkouts.service's project {self.home / 'checkout1'}", result.stdout)

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
        self.assertIn("omnigent", settings["providerInstances"])

    def test_the_retired_acp_providers_are_removed(self):
        path = self.home / ".t3-workspaces/checkouts/userdata/settings.json"
        path.parent.mkdir(parents=True)
        acp = {"driver": "acpRegistry", "config": {"source": "local", "commandPath": "omnigent-acp"}}
        path.write_text(json.dumps({"providerInstances": {"omnigent-claude": acp, "omnigent-codex": acp}}))
        result = self.run_ensure()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        providers = self.settings("checkouts")["providerInstances"]
        self.assertNotIn("omnigent-claude", providers)
        self.assertNotIn("omnigent-codex", providers)
        self.assertEqual(providers["omnigent"]["driver"], "opencode")

    def test_a_runtime_that_cannot_be_installed_is_reported(self):
        write_exe(self.bin / "uv", "exit 1\n")
        result = self.run_ensure()
        self.assertIn("WARNING: could not install omnigent-opencode's runtime", result.stdout)

    def test_server_settings_are_the_dotfiles_settings(self):
        shared = self.dotfiles / "t3code/settings.json"
        shared.parent.mkdir(parents=True)
        shared.write_text(json.dumps({"idleCompaction": "onReturn", "defaultThreadEnvMode": "local"}))
        path = self.home / ".t3-workspaces/checkouts/userdata/settings.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"defaultThreadEnvMode": "worktree", "projectSettingsFolded": True}))
        result = self.run_ensure()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(path.resolve(), shared.resolve())
        settings = json.loads(shared.read_text())
        # The server's own settings join the dotfiles', which win where both have one.
        self.assertEqual(
            {k: settings[k] for k in ("idleCompaction", "defaultThreadEnvMode", "projectSettingsFolded")},
            {"idleCompaction": "onReturn", "defaultThreadEnvMode": "local", "projectSettingsFolded": True},
        )
        self.assertIn("omnigent", settings["providerInstances"])
        self.assertEqual(
            json.loads(path.with_name("settings.json.pre-dotfiles").read_text()),
            {"defaultThreadEnvMode": "worktree", "projectSettingsFolded": True},
        )

    def test_a_server_without_settings_starts_the_dotfiles_settings(self):
        result = self.run_ensure()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        shared = self.dotfiles / "t3code/settings.json"
        path = self.home / ".t3-workspaces/checkouts/userdata/settings.json"
        self.assertEqual(path.resolve(), shared.resolve())
        self.assertIn("omnigent", json.loads(shared.read_text())["providerInstances"])
        self.assertFalse(path.with_name("settings.json.pre-dotfiles").exists())

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
            '[ "$2" = add ] || exit 0\n'
            "echo \"ProjectAlreadyExistsError: An active project already exists for '$3'.\" >&2; exit 1\n",
        )
        result = self.run_ensure()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len([c for c in self.calls_made() if c.startswith("t3 project add")]), 2)

    def test_missing_t3_that_cannot_be_installed_fails_clearly(self):
        (self.bin / "t3").unlink()
        result = self.run_ensure()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("[t3-ensure] ERROR:", result.stderr)
        self.assertFalse((self.home / ".local/bin/t3").exists())
        self.assertFalse((self.home / ".t3-workspaces").exists())

    def test_desktop_runs_one_server_with_every_t3_row_as_a_project(self):
        dotfiles, omnigent = self.home / "dotfiles", self.home / "repos/omnigent"
        dotfiles.mkdir()
        omnigent.mkdir(parents=True)
        self.use_desktop_layout({"dotfiles": dotfiles, "omnigent-repo": omnigent, "gatech": self.home / "gatech"})
        result = self.run_ensure()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("startup-windows --print-layout", self.calls_made())
        providers = self.settings("desktop")["providerInstances"]
        self.assertEqual(providers["omnigent"]["config"]["binaryPath"], "omnigent-opencode")
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
        # The desktop's server is the layout's alone; a ~/checkoutN there is no project of it.
        self.assertFalse([c for c in self.calls_made() if "checkout1" in c])
        self.assertEqual(self.units_in("active"), ["t3@desktop.service"])

    def test_desktop_retires_every_other_server(self):
        (self.home / "dotfiles").mkdir()
        self.use_desktop_layout({"dotfiles": self.home / "dotfiles"})
        self.run_unit("t3@desktop.service")
        # A server per t3 row, as before t3@desktop: found by the state t3-ensure
        # wrote for it, or by the link `systemctl --user enable` left.
        old_state = self.home / ".t3-workspaces/dotfiles/userdata/settings.json"
        old_state.parent.mkdir(parents=True)
        old_state.write_text('{"theme": "dark"}')
        self.run_unit("t3@dotfiles.service")
        wants = self.home / ".config/systemd/user/default.target.wants"
        wants.mkdir(parents=True)
        (wants / "t3@orchest.service").symlink_to("/nonexistent/t3@.service")
        self.run_unit("t3@orchest.service")
        # State of a server that is no longer enabled is not a server to stop.
        (self.home / ".t3-workspaces/presto").mkdir()
        result = self.run_ensure()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.units_in("enabled"), ["t3@desktop.service"])
        self.assertEqual(self.units_in("active"), ["t3@desktop.service"])
        self.assertIn("retired t3@dotfiles.service", result.stdout)
        self.assertIn("retired t3@orchest.service", result.stdout)
        self.assertEqual(old_state.read_text(), '{"theme": "dark"}')
        self.assertFalse([c for c in self.calls_made() if "presto" in c and "disable" in c])
        self.calls.unlink()
        rerun = self.run_ensure()
        self.assertEqual((rerun.returncode, rerun.stdout), (0, ""))

    def test_desktop_reports_a_server_it_could_not_retire(self):
        (self.home / "dotfiles").mkdir()
        self.use_desktop_layout({"dotfiles": self.home / "dotfiles"})
        (self.home / ".t3-workspaces/gatech").mkdir(parents=True)
        self.run_unit("t3@gatech.service")
        (self.units / "disable-fails").touch()
        result = self.run_ensure()
        self.assertEqual(result.returncode, 1)
        self.assertIn("WARNING: could not disable t3@gatech.service", result.stdout)
        self.assertEqual(self.units_in("active"), ["t3@desktop.service", "t3@gatech.service"])

    def test_desktop_gives_the_app_its_own_omnigent_providers(self):
        app_settings = self.home / ".t3/userdata/settings.json"
        app_settings.parent.mkdir(parents=True)
        app_settings.write_text(json.dumps({"theme": "dark", "providerInstances": {"codex": {"driver": "codex", "enabled": True}}}))
        self.use_desktop_layout({})
        result = self.run_ensure()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        settings = json.loads(app_settings.read_text())
        providers = settings["providerInstances"]
        self.assertEqual(providers["omnigent"]["config"]["binaryPath"], str(self.dotfiles / "bin/omnigent-opencode"))
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

    def test_desktop_layout_failure_fails_clearly(self):
        self.profile = "desktop"
        write_exe(self.dotfiles / "bin-linux/startup-windows", "echo 'ERROR: bad table' >&2; exit 1\n")
        result = self.run_ensure()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ERROR: bad table", result.stderr)
        self.assertIn("--print-layout failed", result.stderr)
        self.assertFalse((self.home / ".t3-workspaces").exists())
        self.assertFalse([c for c in self.calls_made() if c.startswith("systemctl")])


def load_t3_ensure():
    loader = importlib.machinery.SourceFileLoader("t3_ensure", str(SCRIPT))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


class T3CliInstallTest(unittest.TestCase):
    """Step 1 against a release archive built here and a fake curl serving it."""

    VERSION = "0.1.6"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.t3_home = self.root / "home/.t3"
        self.bin_dir = self.root / "home/.local/bin"
        self.fetched = self.root / "fetched.log"
        self.module = load_t3_ensure()
        self.platform = self.module.cli_platform()
        self.archive = self.root / "release" / f"t3-{self.VERSION}-{self.platform}.tar.gz"
        self.archive.parent.mkdir()
        script = f'#!/bin/sh\n[ "$1" = --version ] && echo {self.VERSION}\n'.encode()
        with tarfile.open(self.archive, "w:gz") as tar:
            info = tarfile.TarInfo(f"t3-{self.VERSION}-{self.platform}/t3")
            info.size, info.mode = len(script), 0o755
            tar.addfile(info, io.BytesIO(script))
        self.checksums = {self.platform: hashlib.sha256(self.archive.read_bytes()).hexdigest()}
        fake_bin = self.root / "bin"
        write_exe(
            fake_bin / "curl",
            f'out=""; url=""; while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift ;; -*) ;; *) url="$1" ;; esac; shift; done\n'
            f'echo "$url" >> {self.fetched}\n'
            f'[ -n "$FAKE_RELEASE_ARCHIVE" ] || exit 22\n'
            f'cp "$FAKE_RELEASE_ARCHIVE" "$out"\n',
        )
        patcher = mock.patch.dict(os.environ, {"PATH": f"{fake_bin}:/usr/bin:/bin", "FAKE_RELEASE_ARCHIVE": str(self.archive)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def install(self, checksums=None):
        return self.module.install_t3(self.VERSION, self.checksums if checksums is None else checksums, self.t3_home, self.bin_dir)

    def fetches(self):
        return self.fetched.read_text().splitlines() if self.fetched.exists() else []

    def versions(self):
        directory = self.t3_home / "runtime/versions"
        return sorted(p.name for p in directory.iterdir()) if directory.exists() else []

    def test_installs_the_pinned_release_where_install_sh_would(self):
        link = self.install()
        self.assertEqual(link, self.bin_dir / "t3")
        version_dir = self.t3_home / "runtime/versions" / self.VERSION
        self.assertEqual(link.resolve(), (version_dir / "t3").resolve())
        self.assertEqual(subprocess.run([str(link), "--version"], capture_output=True, text=True).stdout, f"{self.VERSION}\n")
        self.assertEqual((version_dir / ".install-complete").read_text().strip(), self.VERSION)
        self.assertEqual((self.t3_home / "runtime/launchers").read_text().splitlines(), [str(self.bin_dir.resolve() / "t3")])
        self.assertEqual(self.versions(), [self.VERSION])
        self.assertEqual(
            self.fetches(),
            [f"https://github.com/pingdotgg/t3code/releases/download/v{self.VERSION}/t3-{self.VERSION}-{self.platform}.tar.gz"],
        )
        # A second run reuses the download and records the launcher once.
        self.install()
        self.assertEqual(len(self.fetches()), 1)
        self.assertEqual(len((self.t3_home / "runtime/launchers").read_text().splitlines()), 1)

    def test_an_archive_that_does_not_match_the_pin_is_not_installed(self):
        with self.assertRaises(SystemExit) as raised:
            self.install({self.platform: "0" * 64})
        self.assertIn("checksum mismatch", str(raised.exception))
        self.assertFalse((self.bin_dir / "t3").exists())
        self.assertEqual(self.versions(), [])

    def test_a_failed_download_installs_nothing(self):
        os.environ["FAKE_RELEASE_ARCHIVE"] = ""
        with self.assertRaises(SystemExit) as raised:
            self.install()
        self.assertIn("could not download", str(raised.exception))
        self.assertFalse((self.bin_dir / "t3").exists())
        self.assertEqual(self.versions(), [])

    def test_a_host_without_a_pin_downloads_nothing(self):
        with self.assertRaises(SystemExit) as raised:
            self.install({})
        self.assertIn(f"no pinned t3 CLI for {self.platform}", str(raised.exception))
        self.assertEqual(self.fetches(), [])

    def test_a_version_already_unpacked_is_linked_without_a_download(self):
        version_dir = self.t3_home / "runtime/versions" / self.VERSION
        write_exe(version_dir / "t3", "exit 0\n")
        (version_dir / ".install-complete").write_text(f"{self.VERSION}\n")
        os.environ["FAKE_RELEASE_ARCHIVE"] = ""
        link = self.install()
        self.assertEqual(link.resolve(), (version_dir / "t3").resolve())
        self.assertEqual(self.fetches(), [])


class T3EnsureMacTest(unittest.TestCase):
    """The Mac's own server: launchd runs it, so t3-ensure only configures it."""

    run_ensure = T3EnsureTest.run_ensure
    calls_made = T3EnsureTest.calls_made
    settings = T3EnsureTest.settings
    run_unit = T3EnsureTest.run_unit

    def setUp(self):
        T3EnsureTest.setUp(self)
        self.os = "Darwin"
        (self.home / "dotfiles").mkdir()

    def test_mac_configures_its_local_server_with_dotfiles_as_its_project(self):
        self.run_unit("t3@checkout1.service")
        result = self.run_ensure()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        providers = self.settings("local")["providerInstances"]
        self.assertEqual(providers["omnigent"]["config"]["binaryPath"], "omnigent-opencode")
        self.assertEqual(providers["codex"], {"driver": "codex", "enabled": False})
        self.assertIn(f"t3 project add {self.home / 'dotfiles'} home={self.home / '.t3-workspaces/local'}", self.calls_made())
        # No systemd here: nothing enabled, started or retired, and no work server.
        self.assertFalse([c for c in self.calls_made() if c.startswith("systemctl")])
        self.assertFalse((self.home / ".t3-workspaces/checkouts").exists())

    def test_mac_without_dotfiles_has_no_server_to_configure(self):
        (self.home / "dotfiles").rmdir()
        result = self.run_ensure()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.home / ".t3-workspaces/local").exists())


class T3WorkspaceServeTest(unittest.TestCase):
    """Runs against this checkout's own bin-linux/startup-windows table and topology.env."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        write_exe(self.home / ".local/bin/t3", 'echo "t3 $* cwd=$PWD home=$T3CODE_HOME"\n')
        self.bin = self.root / "bin"
        write_exe(self.bin / "uname", 'echo "${FAKE_UNAME:-Linux}"\n')
        write_exe(self.bin / "hostname", 'echo "${FAKE_FQDN:-devvm1.cco0.facebook.com}"\n')

    def serve(self, name: str, profile: str, **env: str):
        full_env = {
            "HOME": str(self.home),
            "DOTFILES_DIR": str(ROOT),
            "DOTFILES_PROFILE": profile,
            "PATH": f"{self.bin}:/usr/bin:/bin",
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
        self.assertIn("this host's t3 server is 'checkouts', not 'checkout3'", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_mac_serves_its_local_server_from_home_on_the_topologys_port(self):
        topology = self.root / "topology.env"
        topology.write_text("T3_PORT=4100\n")
        result = self.serve("local", "work", FAKE_UNAME="Darwin", OMNIGENT_TOPOLOGY_FILE=str(topology))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, self.expected(4100, self.home, "local"))

    def test_mac_rejects_the_work_hubs_server(self):
        result = self.serve("checkouts", "work", FAKE_UNAME="Darwin")
        self.assertEqual(result.returncode, 2)
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

    def tokens(self, *args, wait="0"):
        env = {"HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin", "T3_PAIRING_SERVER_WAIT": wait}
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

    def test_a_server_that_starts_after_the_session_still_mints(self):
        # The fake systemctl reports the server active once this file exists.
        starter = subprocess.Popen(["sh", "-c", f"sleep 2; touch {self.units / 'active/t3@checkouts.service'}"])
        self.addCleanup(starter.wait)
        result = self.tokens("2", wait="30")
        self.assertEqual(result.stdout.splitlines(), ["T3_PAIR TOKEN1", "T3_PAIR TOKEN2"])

    def test_nothing_without_t3(self):
        (self.units / "active/t3@checkouts.service").touch()
        (self.home / ".local/bin/t3").unlink()
        result = self.tokens()
        self.assertEqual((result.returncode, result.stdout), (0, ""))

if __name__ == "__main__":
    unittest.main()
