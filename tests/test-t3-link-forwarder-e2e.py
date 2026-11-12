#!/usr/bin/env python3
"""End-to-end check of t3code/link-forwarder with bin-macos/t3-open-in-chrome:
a throwaway headless Google Chrome Beta (its own temp profile, so it never
touches a running one) loads the extension, a page opens links, and the native
host's `open` is a stub that records what would have gone to Google Chrome.

Run with: python3 tests/test-t3-link-forwarder-e2e.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "bin-macos/t3-open-in-chrome"
EXTENSION = ROOT / "t3code/link-forwarder"
BROWSER = Path("/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta")


class Cdp:
    """Chrome DevTools Protocol over --remote-debugging-pipe (fds 3 and 4)."""

    def __init__(self, args, env):
        to_chrome_r, self.to_chrome = os.pipe()
        self.from_chrome, from_chrome_w = os.pipe()

        def wire_fds():
            os.dup2(to_chrome_r, 3)
            os.dup2(from_chrome_w, 4)

        self.proc = subprocess.Popen(
            args, env=env, preexec_fn=wire_fds, pass_fds=(3, 4, to_chrome_r, from_chrome_w),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        os.close(to_chrome_r)
        os.close(from_chrome_w)
        self.next_id, self.buffer = 0, b""

    def call(self, method, session=None, **params):
        self.next_id += 1
        message = {"id": self.next_id, "method": method, "params": params}
        if session:
            message["sessionId"] = session
        os.write(self.to_chrome, json.dumps(message).encode() + b"\0")
        while True:
            while b"\0" not in self.buffer:
                chunk = os.read(self.from_chrome, 65536)
                if not chunk:
                    raise RuntimeError("browser closed the pipe")
                self.buffer += chunk
            raw, self.buffer = self.buffer.split(b"\0", 1)
            reply = json.loads(raw)
            if reply.get("id") == self.next_id:
                if "error" in reply:
                    raise RuntimeError(f"{method}: {reply['error']}")
                return reply["result"]

    def close(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def wait_for(condition, seconds=15):
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            raise SystemExit("timed out")
        time.sleep(0.2)


def check(label, ok):
    print(f"  {'ok' if ok else 'FAIL'}: {label}")
    if not ok:
        raise SystemExit(1)


def main():
    if not BROWSER.exists():
        print("skipped: Google Chrome Beta is not installed")
        return
    tmp = Path(tempfile.mkdtemp(prefix="t3-link-forwarder-e2e."))
    profile, calls, stub = tmp / "profile", tmp / "calls", tmp / "open"
    stub.write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\n')
    stub.chmod(0o755)
    subprocess.run([str(HOST), "--install", str(profile / "NativeMessagingHosts")], check=True, capture_output=True)

    env = {**os.environ, "T3_OPEN_IN_CHROME_OPEN": str(stub), "T3_LINK_CHROME_PROFILE": "Work"}
    cdp = Cdp([
        str(BROWSER), f"--user-data-dir={profile}", "--headless=new", "--remote-debugging-pipe",
        "--enable-unsafe-extension-debugging", "--no-first-run", "--no-default-browser-check", "about:blank",
    ], env)
    try:
        loaded = cdp.call("Extensions.loadUnpacked", path=str(EXTENSION))["id"]
        check("the extension loads with the id the host allows", f"chrome-extension://{loaded}/" in
              (profile / "NativeMessagingHosts/com.mkarrmann.t3_open_in_chrome.json").read_text())
        wait_for(lambda: any(t["type"] == "service_worker" and loaded in t["url"]
                             for t in cdp.call("Target.getTargets")["targetInfos"]))

        page = cdp.call("Target.createTarget",
                        url="data:text/html,<a id=a target=_blank href='https://example.invalid/clicked'>x</a>")["targetId"]
        session = cdp.call("Target.attachToTarget", targetId=page, flatten=True)["sessionId"]
        wait_for(lambda: cdp.call("Runtime.evaluate", session, expression="document.readyState")["result"]["value"]
                 == "complete")
        for expression in (
            "window.open('https://example.invalid/opened?q=1', '_blank', 'noopener,noreferrer')",
            "document.getElementById('a').click()",
            "window.open('http://w1.t3.localhost:9/thread', '_blank')",
        ):
            cdp.call("Runtime.evaluate", session, expression=expression, userGesture=True)

        def opened_count():
            return len(calls.read_text().splitlines()) if calls.exists() else 0

        try:
            wait_for(lambda: opened_count() >= 2)
        except SystemExit:
            pass
        opened = sorted(calls.read_text().splitlines()) if calls.exists() else []
        check("both outside links went to Google Chrome's chosen profile", opened == [
            "-na Google Chrome --args --profile-directory=Work https://example.invalid/clicked",
            "-na Google Chrome --args --profile-directory=Work https://example.invalid/opened?q=1",
        ])

        def page_urls():
            return [t["url"] for t in cdp.call("Target.getTargets")["targetInfos"] if t["type"] == "page"]

        try:
            wait_for(lambda: not any(u.startswith("https://example.invalid") for u in page_urls()), 10)
        except SystemExit:
            pass
        urls = page_urls()
        check("their tabs closed in the browser", not any(u.startswith("https://example.invalid") for u in urls))
        check("a T3 Code link stayed in the browser", any(u.startswith("http://w1.t3.localhost") for u in urls))
    finally:
        cdp.close()
        shutil.rmtree(tmp, ignore_errors=True)
    print("t3 link forwarder end-to-end tests passed")


if __name__ == "__main__":
    sys.exit(main())
