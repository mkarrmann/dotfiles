"""Desktop app installation checks using fake commands and temporary homes."""

import errno
import hashlib
import json
import os
from pathlib import Path
import pty
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "omnigent-desktop-electron"
VERSION = "0.1.2"
URL = "https://example.invalid/omnigent-desktop-0.1.2-amd64.deb"
PAYLOAD = b"isolated test package\n"

COMMAND_STUB = r'''#!/usr/bin/python3
import json, os, pathlib, subprocess, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["TEST_CALLS"], "a") as output:
    output.write(json.dumps([name, *args]) + "\n")
if name == "uname":
    print(os.environ.get("TEST_PLATFORM", "Linux"))
elif name == "dpkg":
    print(os.environ.get("TEST_ARCH", "amd64"))
elif name == "dpkg-query":
    if not os.environ.get("TEST_INSTALLED_VERSION"):
        raise SystemExit(1)
    fields = {
        "Status": os.environ.get("TEST_PACKAGE_STATUS", "install ok installed"),
        "db:Status-Status": "installed",
        "Version": os.environ["TEST_INSTALLED_VERSION"],
    }
    fmt = next((arg[3:] for arg in args if arg.startswith("-f=")), None)
    if fmt is None:
        fmt = next((arg[9:] for arg in args if arg.startswith("--showformat=")), None)
    if fmt is None and "-f" in args:
        fmt = args[args.index("-f") + 1]
    if fmt is None:
        fmt = "${Status}\t${Version}"
    for field, value in fields.items():
        fmt = fmt.replace("${" + field + "}", value)
    print(fmt.replace("\\n", "\n").replace("\\t", "\t"), end="")
elif name == "curl":
    if os.environ.get("TEST_DOWNLOAD_FAIL"):
        raise SystemExit(22)
    option = "-o" if "-o" in args else "--output"
    output = pathlib.Path(args[args.index(option) + 1])
    payload = b"bad package" if os.environ.get("TEST_BAD_CHECKSUM") else b"isolated test package\n"
    output.write_bytes(payload)
elif name == "dpkg-deb":
    if os.environ.get("TEST_METADATA_FAIL"):
        raise SystemExit(2)
    values = {
        "Package": os.environ.get("TEST_DEB_PACKAGE", "omnigent-desktop-electron"),
        "Version": os.environ.get("TEST_DEB_VERSION", "0.1.2"),
        "Architecture": os.environ.get("TEST_DEB_ARCH", "amd64"),
    }
    fields = [arg for arg in args if arg in values]
    for field in fields:
        print((field + ": " if len(fields) > 1 else "") + values[field])
elif name == "sudo":
    raise SystemExit(subprocess.call(args))
elif name == "apt":
    package = pathlib.Path(args[-1])
    if not package.is_file():
        raise SystemExit("download vanished before apt installation")
    raise SystemExit(int(os.environ.get("TEST_APT_EXIT", "0")))
'''


class OmnigentDesktopAppTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.dotfiles = self.base / "dotfiles"
        self.bin = self.base / "bin"
        self.downloads = self.base / "downloads"
        self.calls = self.base / "calls.jsonl"
        for directory in (
            self.dotfiles / "bin",
            self.dotfiles / "omnigent_config",
            self.bin,
            self.downloads,
            self.base / "home",
        ):
            directory.mkdir(parents=True)
        self.helper = self.dotfiles / "bin/omnigent-desktop-app-ensure"
        shutil.copy2(ROOT / "bin/omnigent-desktop-app-ensure", self.helper)
        profile = self.dotfiles / "bin/dotfiles-profile"
        profile.write_text('#!/bin/bash\necho "$TEST_PROFILE"\n')
        profile.chmod(0o755)
        self.pin = self.dotfiles / "omnigent_config/desktop-app.env"
        self.pin.write_text(
            f"OMNIGENT_DESKTOP_APP_VERSION={VERSION}\n"
            f"OMNIGENT_DESKTOP_APP_URL={URL}\n"
            f"OMNIGENT_DESKTOP_APP_SHA256={hashlib.sha256(PAYLOAD).hexdigest()}\n"
        )
        for command in ("bash", "dirname", "cat", "chmod", "mktemp", "rm", "sha256sum"):
            executable = shutil.which(command)
            self.assertIsNotNone(executable, command)
            (self.bin / command).symlink_to(executable)
        for command in ("uname", "dpkg", "dpkg-query", "dpkg-deb", "curl", "sudo", "apt"):
            stub = self.bin / command
            stub.write_text(COMMAND_STUB)
            stub.chmod(0o755)
        self.env = {
            "HOME": str(self.base / "home"),
            "PATH": str(self.bin),
            "TMPDIR": str(self.downloads),
            "DOTFILES_DIR": str(self.dotfiles),
            "TEST_PROFILE": "desktop",
            "TEST_CALLS": str(self.calls),
        }

    def run_helper(self, *, interactive=False):
        if not interactive:
            return subprocess.run(
                [str(self.helper)], env=self.env, capture_output=True, text=True, timeout=10
            )
        master, slave = pty.openpty()
        try:
            process = subprocess.Popen(
                [str(self.helper)], env=self.env, stdin=slave, stdout=slave, stderr=slave
            )
            os.close(slave)
            slave = None
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise
            chunks = []
            while True:
                try:
                    chunk = os.read(master, 65536)
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                    break
                if not chunk:
                    break
                chunks.append(chunk)
            return subprocess.CompletedProcess(
                [str(self.helper)], process.returncode, b"".join(chunks).decode(), ""
            )
        finally:
            os.close(master)
            if slave is not None:
                os.close(slave)

    def recorded(self, command=None):
        entries = (
            [json.loads(line) for line in self.calls.read_text().splitlines()]
            if self.calls.exists()
            else []
        )
        return [entry for entry in entries if command is None or entry[0] == command]

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_no_install(self):
        for command in ("curl", "sudo", "apt"):
            self.assertEqual(self.recorded(command), [], command)
        self.assertEqual(list(self.downloads.iterdir()), [])

    def test_work_profile_skips(self):
        self.env["TEST_PROFILE"] = "work"
        self.assert_success(self.run_helper(interactive=True))
        self.assert_no_install()

    def test_mac_skips(self):
        self.env["TEST_PLATFORM"] = "Darwin"
        self.assert_success(self.run_helper(interactive=True))
        self.assert_no_install()

    def test_unsupported_architecture_skips(self):
        self.env["TEST_ARCH"] = "arm64"
        self.assert_success(self.run_helper(interactive=True))
        self.assert_no_install()

    def test_missing_apt_skips(self):
        (self.bin / "apt").unlink()
        self.assert_success(self.run_helper(interactive=True))
        self.assert_no_install()

    def test_non_debian_linux_skips(self):
        (self.bin / "dpkg").unlink()
        self.assert_success(self.run_helper(interactive=True))
        self.assert_no_install()

    def test_existing_version_is_not_upgraded_or_downgraded(self):
        for version in ("0.0.1", VERSION, "999.0.0"):
            with self.subTest(version=version):
                self.env["TEST_INSTALLED_VERSION"] = version
                self.assert_success(self.run_helper(interactive=True))
                self.assert_no_install()

    def test_unattended_setup_prints_manual_command_without_downloading(self):
        result = self.run_helper()
        self.assert_success(result)
        self.assertIn("omnigent-desktop-app-ensure", result.stdout + result.stderr)
        self.assert_no_install()

    def test_removed_package_with_remaining_config_is_installed(self):
        self.env["TEST_INSTALLED_VERSION"] = VERSION
        self.env["TEST_PACKAGE_STATUS"] = "deinstall ok config-files"
        self.assert_success(self.run_helper(interactive=True))
        self.assertEqual(len(self.recorded("apt")), 1)

    def test_interactive_install_uses_verified_pinned_download(self):
        self.assert_success(self.run_helper(interactive=True))
        downloads = self.recorded("curl")
        self.assertEqual(len(downloads), 1)
        self.assertIn(URL, downloads[0])
        installs = self.recorded("apt")
        self.assertEqual(len(installs), 1)
        self.assertEqual(installs[0][1], "install")
        self.assertTrue(Path(installs[0][-1]).is_absolute())
        self.assertEqual(len(self.recorded("sudo")), 1)
        self.assertEqual(list(self.downloads.iterdir()), [])

    def test_download_failure_prevents_install_and_cleans_up(self):
        self.env["TEST_DOWNLOAD_FAIL"] = "1"
        self.assert_failed_install()

    def test_checksum_mismatch_prevents_install_and_cleans_up(self):
        self.env["TEST_BAD_CHECKSUM"] = "1"
        self.assert_failed_install()

    def test_metadata_failure_prevents_install_and_cleans_up(self):
        self.env["TEST_METADATA_FAIL"] = "1"
        self.assert_failed_install()

    def test_wrong_package_prevents_install(self):
        self.env["TEST_DEB_PACKAGE"] = "unrelated-package"
        self.assert_failed_install()

    def test_wrong_version_prevents_install(self):
        self.env["TEST_DEB_VERSION"] = "0.1.3"
        self.assert_failed_install()

    def test_wrong_architecture_prevents_install(self):
        self.env["TEST_DEB_ARCH"] = "arm64"
        self.assert_failed_install()

    def test_apt_failure_propagates_and_cleans_up(self):
        self.env["TEST_APT_EXIT"] = "42"
        result = self.run_helper(interactive=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.recorded("apt")), 1)
        self.assertEqual(list(self.downloads.iterdir()), [])

    def assert_failed_install(self):
        result = self.run_helper(interactive=True)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.recorded("sudo"), [])
        self.assertEqual(self.recorded("apt"), [])
        self.assertEqual(list(self.downloads.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
