#!/usr/bin/env python3
"""End-to-end check of the installed t3 with mkarrmann/t3code's web client
(bin/t3-ensure step 1b): a throwaway `t3 serve` with two projects, and a
headless Google Chrome Beta in a temporary profile as the window, scoped to the
first project by bin/t3-window-scope. With a draft of the second project on
screen, Cmd+O starts the new thread in the window's own project; upstream's
client starts it in the second. Everything runs on loopback ports and under a
temporary directory.

Run with: python3 tests/test-t3-fork-client-e2e.py   (T3_BIN picks another t3)
"""

import importlib.machinery
import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BROWSER = Path("/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta")
T3 = Path(os.environ.get("T3_BIN") or Path.home() / ".local/bin/t3")


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(module)
    return module


scope = load("t3_window_scope", ROOT / "bin/t3-window-scope")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(what, probe, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = probe()
        if value:
            return value
        time.sleep(0.25)
    raise SystemExit(f"timed out waiting for {what}")


def check(label, expected, actual):
    print(f"  {'ok' if expected == actual else 'FAIL'}: {label}")
    if expected != actual:
        raise SystemExit(f"expected {expected!r}, got {actual!r}")


def main():
    if not BROWSER.exists() or not T3.exists():
        print("skipped: needs Google Chrome Beta and the t3 CLI")
        return
    tmp = Path(tempfile.mkdtemp(prefix="t3-window-project-e2e."))
    home, profile = tmp / "t3home", tmp / "chrome"
    alpha, bravo = tmp / "home/alpha", tmp / "home/bravo"
    (home / "userdata").mkdir(parents=True)
    shutil.copy(ROOT / "t3code/keybindings.json", home / "userdata/keybindings.json")
    env = {**os.environ, "T3CODE_HOME": str(home)}
    port = free_port()
    origin = f"http://w1.t3.localhost:{port}"
    procs = [subprocess.Popen([str(T3), "serve", "--host", "127.0.0.1", "--port", str(port), "--no-browser"],
                              cwd=tmp, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
    try:
        loopback = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def answers():
            try:
                return loopback.open(f"http://127.0.0.1:{port}/", timeout=1).status == 200
            except OSError:
                return False

        wait_for("the server", answers)
        for project in (alpha, bravo):
            project.mkdir(parents=True)
            wait_for(f"{project.name} to be added", lambda p=project: subprocess.run(
                [str(T3), "project", "add", str(p)], env=env, capture_output=True).returncode == 0)
        token = subprocess.run([str(T3), "pair", "--ttl", "5m", "--label", "e2e"], env=env,
                               capture_output=True, text=True, check=True).stdout
        token = next(line.split(": ", 1)[1].strip() for line in token.splitlines() if line.startswith("Token: "))

        procs.append(subprocess.Popen([
            str(BROWSER), f"--user-data-dir={profile}", "--headless=new", "--remote-debugging-port=0",
            "--no-first-run", "--no-default-browser-check", f"{origin}/pair#token={token}",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        subprocess.run([sys.executable, str(ROOT / "bin/t3-window-scope"), str(profile), origin, str(alpha)],
                       check=True, timeout=120)

        def page():
            target = scope.app_page(scope.debug_targets(str(profile)) or [], origin)
            devtools = scope.DevToolsPage(target["webSocketDebuggerUrl"])
            devtools.focus()
            return devtools

        devtools = page()
        projects = {p["workspaceRoot"]: p["id"] for p in scope.fetch_json(devtools, scope.PROJECTS_PATH)["projects"]}
        alpha_id, bravo_id = projects[str(alpha)], projects[str(bravo)]
        platform = devtools.evaluate("navigator.platform") or ""
        new_local = scope.key_event("mod+o", platform)
        check("the window starts on a draft in its project, alpha", alpha_id, scope.draft_project_id(devtools))

        def bravo_draft():
            scope.press(devtools, scope.key_event(scope.shortcut("chat.new"), platform))
            time.sleep(1)
            devtools.call("Input.insertText", {"text": "bravo"})
            time.sleep(0.5)
            scope.press(devtools, {"key": "Enter", "code": "Enter", "modifiers": 0, "windowsVirtualKeyCode": 13})
            return wait_for("a draft in bravo", lambda: scope.draft_project_id(devtools) == bravo_id, 5)

        def press_new_local():
            before = devtools.evaluate("location.pathname")
            scope.press(devtools, new_local)
            try:
                wait_for("a new draft", lambda: devtools.evaluate("location.pathname") not in (before, "/new"), 5)
                return wait_for("its project", lambda: scope.draft_project_id(devtools), 5)
            except SystemExit:
                return scope.draft_project_id(devtools)

        bravo_draft()
        check("Cmd+O on bravo's draft starts the thread in the window's project, alpha", alpha_id, press_new_local())
        bravo_draft()
        # T3 Code passes over shortcuts until the project picker has closed.
        wait_for("the picker to close", lambda: not devtools.evaluate("!!document.querySelector('[role=dialog]')"), 5)
        check("and again from another draft of bravo", alpha_id, press_new_local())
    finally:
        for proc in reversed(procs):
            proc.terminate()
            proc.wait(timeout=10)
        shutil.rmtree(tmp, ignore_errors=True)
    print("t3 fork client end-to-end tests passed")


if __name__ == "__main__":
    sys.exit(main())
