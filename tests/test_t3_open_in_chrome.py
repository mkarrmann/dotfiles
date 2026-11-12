"""Checks for bin-macos/t3-open-in-chrome, run the way the browser runs it, with
`open` replaced by a stub that records its arguments.

Run with: python3 -m unittest discover -s tests -p test_t3_open_in_chrome.py
"""

import json
import os
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "bin-macos/t3-open-in-chrome"
ORIGIN = "chrome-extension://pdcoehmlpmfjiimeabommdgknpnjadca/"


class Host(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.calls = self.tmp / "calls"
        self.stub = self.tmp / "open"
        self.set_stub(0)

    def set_stub(self, status):
        self.stub.write_text(f'#!/bin/sh\necho "$@" >> "{self.calls}"\necho "stub failed" >&2\nexit {status}\n')
        self.stub.chmod(0o755)

    def send(self, message):
        body = json.dumps(message).encode()
        env = {**os.environ, "T3_OPEN_IN_CHROME_OPEN": str(self.stub)}
        env.pop("T3_LINK_CHROME_PROFILE", None)
        out = subprocess.run(
            [str(HOST), ORIGIN], input=struct.pack("=I", len(body)) + body, capture_output=True, env=env, check=True
        ).stdout
        (length,) = struct.unpack("=I", out[:4])
        self.assertEqual(len(out), 4 + length)
        return json.loads(out[4:])

    def opened(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def test_opens_a_link_in_chromes_work_profile(self):
        self.assertEqual(self.send({"url": "https://example.com/a?b=c#d"}), {"ok": True})
        self.assertEqual(self.opened(), ["-na Google Chrome --args --profile-directory=Default https://example.com/a?b=c#d"])

    def test_refuses_anything_but_an_http_link(self):
        for url in ("javascript:alert(1)", "file:///etc/passwd", "--load-extension=/tmp/x", "https://", 42):
            with self.subTest(url=url):
                self.assertFalse(self.send({"url": url})["ok"])
        self.assertFalse(self.send({})["ok"])
        self.assertEqual(self.opened(), [])

    def test_reports_a_failed_open(self):
        self.set_stub(1)
        self.assertEqual(self.send({"url": "http://localhost:3000/"}), {"ok": False, "error": "stub failed"})

    def test_install_registers_the_host_once(self):
        hosts = self.tmp / "NativeMessagingHosts"
        first = subprocess.run([str(HOST), "--install", str(hosts)], capture_output=True, text=True, check=True)
        self.assertIn("registered", first.stdout)
        manifest = json.loads((hosts / "com.mkarrmann.t3_open_in_chrome.json").read_text())
        self.assertEqual(manifest["path"], str(HOST.resolve()))
        self.assertEqual(manifest["allowed_origins"], [ORIGIN])
        again = subprocess.run([str(HOST), "--install", str(hosts)], capture_output=True, text=True, check=True)
        self.assertEqual(again.stdout, "")


if __name__ == "__main__":
    unittest.main()
