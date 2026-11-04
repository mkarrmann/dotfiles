"""omnigent-version-ensure checks using fake commands and temporary homes."""

import json
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FLOOR = "0.20.0"
UPGRADE = ["uv", "tool", "install", "--force", f"omnigent>={FLOOR}", "--with", "litellm"]
RESTART = ["systemctl", "--user", "try-restart", "omnigent-host.service"]
BACKUP = re.compile(r"chat\.db\.pre-upgrade-\d{8}-\d{6}")

# One stub for every fake command, dispatched on its name. The installed
# omnigent and its uv tool python are copies of it too, reading what is
# "installed" from TEST_STATE, which the uv stub rewrites the way a real
# `uv tool install` would.
COMMAND_STUB = r'''#!{python}
import json, os, pathlib, re, shutil, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
home = pathlib.Path(os.environ["HOME"])
state_file = pathlib.Path(os.environ["TEST_STATE"])
with open(os.environ["TEST_CALLS"], "a") as output:
    output.write(json.dumps([name, *args]) + "\n")
    if name == "uv":
        backups = sorted(p.name for p in (home / ".omnigent").glob("chat.db.pre-upgrade-*"))
        output.write(json.dumps(["uv saw backups", *backups]) + "\n")
if name == "uname":
    print("Linux")
elif name == "systemctl":
    if "is-active" in args:
        raise SystemExit(3)
elif name == "omnigent-hub":
    if args[:1] == ["snapshot"]:
        raise SystemExit(int(os.environ.get("TEST_SNAPSHOT_EXIT", "0")))
elif name == "uv":
    spec = next(arg for arg in args if arg.startswith("omnigent"))
    version = re.split("[<>=]=", spec, maxsplit=1)[1]
    state_file.write_text(json.dumps({"version": version, "litellm": "litellm" in args}))
    for target in (home / ".local/bin/omnigent", home / ".local/share/uv/tools/omnigent/bin/python"):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sys.argv[0], target)
elif name == "omnigent":
    print("omnigent " + json.loads(state_file.read_text())["version"])
elif name == "python":
    litellm = json.loads(state_file.read_text())["litellm"]
    raise SystemExit(0 if args == ["-c", "import litellm"] and litellm else 1)
'''


class OmnigentVersionEnsureTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.home = self.base / "home"
        self.dotfiles = self.base / "dotfiles"
        self.bin = self.base / "bin"
        self.calls = self.base / "calls.jsonl"
        self.state = self.base / "installed.json"
        self.data = self.home / ".omnigent"
        for directory in (self.home, self.dotfiles / "bin", self.bin, self.data):
            directory.mkdir(parents=True)
        self.script = self.dotfiles / "bin/omnigent-version-ensure"
        for name in ("omnigent-version-ensure", "dotfiles-profile"):
            shutil.copy2(ROOT / "bin" / name, self.dotfiles / "bin" / name)
        topology = self.base / "topology.env"
        topology.write_text(f"OMNIGENT_MIN_VERSION={FLOOR}\n")
        self.stub = self.base / "stub"
        self.stub.write_text(COMMAND_STUB.replace("{python}", sys.executable, 1))
        self.stub.chmod(0o755)
        for command in ("bash", "sed", "sort", "head", "tail", "awk", "cat", "date", "python3"):
            executable = shutil.which(command)
            self.assertIsNotNone(executable, command)
            (self.bin / command).symlink_to(executable)
        for command in ("uname", "systemctl", "uv"):
            shutil.copy2(self.stub, self.bin / command)
        # Present on every profile, so the desktop cases prove they never ask it.
        hub = self.dotfiles / "services/omnigent-hub/.venv/bin/omnigent-hub"
        hub.parent.mkdir(parents=True)
        shutil.copy2(self.stub, hub)
        # Built from scratch rather than copied from os.environ: nothing here may
        # resolve to the real HOME, uv, or systemctl, or inherit the
        # OMNIGENT_RUNNER_ID of an Omnigent session running these tests.
        self.env = {
            "HOME": str(self.home),
            "PATH": str(self.bin),
            "DOTFILES_DIR": str(self.dotfiles),
            "DOTFILES_PROFILE": "desktop",
            "OMNIGENT_TOPOLOGY_FILE": str(topology),
            "TEST_CALLS": str(self.calls),
            "TEST_STATE": str(self.state),
        }
        for command in ("uv", "systemctl"):
            self.assertEqual(shutil.which(command, path=self.env["PATH"]), str(self.bin / command))

    def install(self, version, litellm=True):
        self.state.write_text(json.dumps({"version": version, "litellm": litellm}))
        for target in (
            self.home / ".local/bin/omnigent",
            self.home / ".local/share/uv/tools/omnigent/bin/python",
        ):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.stub, target)

    def chat_db(self):
        """A real WAL database whose newest rows exist only in its WAL, held open
        by a writer for the whole test the way the running server holds it."""
        writer = sqlite3.connect(self.data / "chat.db")
        self.addCleanup(writer.close)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY, title TEXT)")
        writer.executemany(
            "INSERT INTO conversations (title) VALUES (?)", [(f"session {n}",) for n in range(500)]
        )
        writer.commit()
        self.assertTrue((self.data / "chat.db-wal").stat().st_size > 0)
        return 500

    def run_script(self, *args, **overrides):
        return subprocess.run(
            [str(self.script), *args],
            cwd=self.base,
            env={**self.env, **overrides},
            capture_output=True,
            text=True,
            timeout=15,
        )

    def recorded(self, command=None):
        entries = (
            [json.loads(line) for line in self.calls.read_text().splitlines()]
            if self.calls.exists()
            else []
        )
        return [entry for entry in entries if command is None or entry[0] == command]

    def backups(self):
        return sorted(path for path in self.data.iterdir() if path.name.startswith("chat.db.pre-upgrade-"))

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_untouched(self):
        for command in ("uv", "systemctl", "omnigent-hub"):
            self.assertEqual(self.recorded(command), [], command)
        self.assertEqual(self.backups(), [])

    def test_satisfied_floor_changes_nothing(self):
        self.install(FLOOR)
        self.chat_db()
        for args in ((), ("--check",)):
            with self.subTest(args=args):
                result = self.run_script(*args)
                self.assert_success(result)
                self.assertIn(f"omnigent {FLOOR} satisfies the >= {FLOOR} floor", result.stdout)
                self.assert_untouched()

    def test_check_reports_the_problem_and_the_fix_without_changing_anything(self):
        self.chat_db()
        fix = f"\n  {self.dotfiles}/bin/omnigent-version-ensure\n"
        for installed, extra, problem in (
            (None, {}, "omnigent is not installed"),
            (("0.15.0", True), {}, f"omnigent 0.15.0 is below the >= {FLOOR} floor"),
            ((FLOOR, False), {}, f"omnigent {FLOOR} is missing the litellm extra"),
            ((FLOOR, True), {"OMNIGENT_MIN_VERSION": "0.21.0"}, f"omnigent {FLOOR} is below the >= 0.21.0 floor"),
            # Reporting is safe inside a session; only installing is refused there.
            (("0.15.0", True), {"OMNIGENT_RUNNER_ID": "runner_test"}, "below the"),
        ):
            with self.subTest(installed=installed, extra=extra):
                self.state.unlink(missing_ok=True)
                shutil.rmtree(self.home / ".local", ignore_errors=True)
                if installed:
                    self.install(*installed)
                result = self.run_script("--check", **extra)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(problem, result.stdout)
                self.assertIn("outside Omnigent", result.stdout)
                self.assertTrue(result.stdout.endswith(fix), result.stdout)
                self.assert_untouched()

    def test_desktop_upgrade_backs_up_chat_db_then_installs_and_restarts(self):
        self.install("0.15.0")
        rows = self.chat_db()
        result = self.run_script()
        self.assert_success(result)
        backups = self.backups()
        self.assertEqual(len(backups), 1, backups)
        backup = backups[0]
        self.assertRegex(backup.name, BACKUP)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.assertIn(f"backed up chat.db to {backup}", result.stdout)
        with sqlite3.connect(f"file:{backup}?mode=ro", uri=True) as check:
            self.assertEqual(check.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
            self.assertEqual(check.execute("SELECT count(*) FROM conversations").fetchone(), (rows,))
            self.assertEqual(check.execute("PRAGMA journal_mode").fetchone(), ("delete",))
        self.assertEqual(
            sorted(path.name for path in self.data.iterdir() if path.name.startswith(backup.name)),
            [backup.name],
        )
        self.assertEqual(self.recorded("uv"), [UPGRADE])
        self.assertEqual(self.recorded("uv saw backups"), [["uv saw backups", backup.name]])
        self.assertIn(RESTART, self.recorded("systemctl"))
        self.assertEqual(self.recorded("omnigent-hub"), [])

    def test_missing_extra_reinstalls_the_installed_version_after_a_backup(self):
        self.install("0.21.0", litellm=False)
        self.chat_db()
        self.assert_success(self.run_script())
        self.assertEqual(
            self.recorded("uv"),
            [["uv", "tool", "install", "--force", "omnigent==0.21.0", "--with", "litellm"]],
        )
        self.assertEqual(len(self.backups()), 1)
        self.assertIn(RESTART, self.recorded("systemctl"))

    def test_fresh_desktop_without_chat_db_installs_without_a_backup(self):
        result = self.run_script()
        self.assert_success(result)
        self.assertEqual(self.recorded("uv"), [UPGRADE])
        self.assertEqual(self.backups(), [])
        self.assertNotIn("backed up", result.stdout)

    def test_inside_an_omnigent_session_the_install_is_refused_before_anything_changes(self):
        self.env["OMNIGENT_RUNNER_ID"] = "runner_test"
        self.install("0.15.0")
        self.chat_db()
        for profile in ("desktop", "work"):
            with self.subTest(profile=profile):
                self.env["DOTFILES_PROFILE"] = profile
                result = self.run_script()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("inside an Omnigent session", result.stderr)
                self.assertIn(f"{self.dotfiles}/bin/omnigent-version-ensure", result.stderr)
                self.assert_untouched()

    def test_failed_backup_refuses_the_upgrade(self):
        self.install("0.15.0")
        db = self.data / "chat.db"
        for case, reason in (
            (self.not_a_database, "file is not a database"),
            (self.copies_but_fails_integrity_check, "integrity_check reported"),
        ):
            with self.subTest(case=case.__name__):
                db.unlink(missing_ok=True)
                case(db)
                before = db.read_bytes()
                result = self.run_script()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(reason, result.stderr)
                self.assertIn("OMNIGENT_SKIP_RECOVERY_POINT=1", result.stderr)
                self.assert_untouched()
                self.assertEqual(db.read_bytes(), before)
                self.assertEqual(sorted(path.name for path in self.data.iterdir()), ["chat.db"])

    @staticmethod
    def not_a_database(db):
        db.write_bytes(b"not a sqlite database\n" * 64)

    @staticmethod
    def copies_but_fails_integrity_check(db):
        """Opens, and the backup copies it page for page, but its index no longer
        matches its definition, so only integrity_check can tell."""
        with sqlite3.connect(db) as writer:
            writer.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY, title TEXT)")
            writer.execute("CREATE INDEX conversations_title ON conversations (title)")
            writer.executemany(
                "INSERT INTO conversations (title) VALUES (?)", [(f"session {n}",) for n in range(50)]
            )
            writer.execute("PRAGMA writable_schema=ON")
            writer.execute(
                "UPDATE sqlite_schema SET sql = 'CREATE INDEX conversations_title ON conversations (title DESC)'"
                " WHERE name = 'conversations_title'"
            )
        writer.close()

    def test_skip_recovery_point_bypasses_the_backup(self):
        self.env["OMNIGENT_SKIP_RECOVERY_POINT"] = "1"
        self.install("0.15.0")
        (self.data / "chat.db").write_bytes(b"not a sqlite database\n")
        self.assert_success(self.run_script())
        self.assertEqual(self.recorded("uv"), [UPGRADE])
        self.assertEqual(self.backups(), [])
        self.assertIn(RESTART, self.recorded("systemctl"))

    def test_work_profile_keeps_the_hub_recovery_point(self):
        self.env["DOTFILES_PROFILE"] = "work"
        self.install("0.15.0")
        self.chat_db()
        self.assert_success(self.run_script())
        self.assertEqual(
            [entry[:2] for entry in self.recorded("omnigent-hub")],
            [["omnigent-hub", "gate"], ["omnigent-hub", "snapshot"]],
        )
        self.assertEqual(self.backups(), [])
        self.assertEqual(self.recorded("uv"), [UPGRADE])
        self.assertIn(RESTART, self.recorded("systemctl"))

    def test_work_profile_still_refuses_without_a_hub_snapshot(self):
        self.env["DOTFILES_PROFILE"] = "work"
        self.env["TEST_SNAPSHOT_EXIT"] = "1"
        self.install("0.15.0")
        result = self.run_script()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("could not take a recovery point", result.stderr)
        self.assertEqual(self.recorded("uv"), [])
        self.assertEqual(self.recorded("systemctl"), [])

    def test_unknown_arguments_are_rejected(self):
        self.install("0.15.0")
        for args in (("--force",), ("--check", "--check"), ("--check", "extra"), ("",)):
            with self.subTest(args=args):
                result = self.run_script(*args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("usage: omnigent-version-ensure [--check]", result.stderr)
                self.assertEqual(self.recorded(), [])


if __name__ == "__main__":
    unittest.main()
