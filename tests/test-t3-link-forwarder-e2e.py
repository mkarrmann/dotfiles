#!/usr/bin/env python3
"""End-to-end check of t3code/link-forwarder with bin-macos/t3-open-in-chrome:
a throwaway headless Google Chrome Beta (its own temp profile, so it never
touches a running one) loads the extension, a page on a *.t3.localhost origin
opens links, and the native host's `open` is a stub that records what would
have gone to Google Chrome.

Run with: python3 tests/test-t3-link-forwarder-e2e.py
"""

import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
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
        self.next_id, self.buffer, self.events = 0, b"", []

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
            if "method" in reply:
                self.events.append(reply)
            elif reply.get("id") == self.next_id:
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


PAGE = b"""<!doctype html>
<a id=blank target=_blank href="https://example.invalid/blank">blank</a>
<a id=plain href="https://example.invalid/plain">plain</a>
<a id=handled href="https://example.invalid/handled"
   onclick="event.preventDefault(); window.open(this.href, '_blank', 'noopener')">handled</a>
<a id=middle href="https://example.invalid/middle">middle</a>
"""


def serve_page():
    class Page(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(PAGE)

        def log_message(self, *_):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    if not BROWSER.exists():
        print("skipped: Google Chrome Beta is not installed")
        return
    tmp = Path(tempfile.mkdtemp(prefix="t3-link-forwarder-e2e."))
    profile, calls, stub = tmp / "profile", tmp / "calls", tmp / "open"
    stub.write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\n')
    stub.chmod(0o755)
    subprocess.run([str(HOST), "--install", str(profile / "NativeMessagingHosts")], check=True, capture_output=True)
    server = serve_page()
    page_url = f"http://e2e.t3.localhost:{server.server_address[1]}/"

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

        page = cdp.call("Target.createTarget", url=page_url)["targetId"]
        session = cdp.call("Target.attachToTarget", targetId=page, flatten=True)["sessionId"]

        def evaluate(expression):
            return cdp.call("Runtime.evaluate", session, expression=expression, userGesture=True)["result"].get("value")

        wait_for(lambda: evaluate("document.readyState") == "complete")
        cdp.call("Target.setDiscoverTargets", discover=True)
        cdp.events.clear()
        for expression in (
            "window.open('https://example.invalid/opened?q=1', '_blank', 'noopener,noreferrer')",
            "document.getElementById('blank').click()",
            "document.getElementById('plain').click()",
            "document.getElementById('handled').click()",
            "document.getElementById('middle').dispatchEvent("
            "new MouseEvent('auxclick', {button: 1, bubbles: true, cancelable: true}))",
            "window.open('http://w1.t3.localhost:9/thread', '_blank')",
            "window.open('', '_blank').location.href = 'https://example.invalid/late'",
        ):
            evaluate(expression)

        def opened():
            return calls.read_text().splitlines() if calls.exists() else []

        links = ["blank", "handled", "late", "middle", "opened?q=1", "plain"]
        try:
            wait_for(lambda: len(opened()) >= 2 * len(links))
        except SystemExit:
            pass
        time.sleep(1)
        check("each outside link went to Google Chrome's chosen profile once", sorted(
            line for line in opened() if line.startswith("-na")
        ) == [f"-na Google Chrome --args --profile-directory=Work https://example.invalid/{link}" for link in links])
        check("Chrome was brought forward for each", opened().count("-a Google Chrome") == len(links))

        created = [e["params"]["targetInfo"] for e in cdp.events
                   if e.get("method") == "Target.targetCreated" and e["params"]["targetInfo"]["type"] == "page"]
        check("only the T3 Code link and the blank window opened tabs", len(created) == 2)
        check("the page stayed where it was", evaluate("location.href") == page_url)

        def page_urls():
            return [t["url"] for t in cdp.call("Target.getTargets")["targetInfos"] if t["type"] == "page"]

        try:
            wait_for(lambda: not any(u.startswith("https://example.invalid") for u in page_urls()), 10)
        except SystemExit:
            pass
        urls = page_urls()
        check("the blank window, once it pointed outside, closed", not any(u.startswith("https://example.invalid") for u in urls))
        check("a T3 Code link stayed in the browser", any(u.startswith("http://w1.t3.localhost") for u in urls))
    finally:
        cdp.close()
        server.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    print("t3 link forwarder end-to-end tests passed")


if __name__ == "__main__":
    sys.exit(main())
