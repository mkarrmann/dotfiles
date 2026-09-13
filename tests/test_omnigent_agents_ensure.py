"""Isolated checks for bin/omnigent-agents-ensure on both profiles.

Run with: python3 -m unittest discover -s tests -p test_omnigent_agents_ensure.py

Everything the script talks to is stubbed inside a temporary HOME: the
omnigent CLI (its throwaway seed server is a tiny HTTP server that writes the
agent rows the real one would), the live server it verifies against,
systemctl, and the routing/quiescence helpers. Only curl, python3 and sqlite3
are real. No live service, database, or network is touched.
"""

import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
import unittest
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
WATCH = {"name": "watch", "transport": "stdio", "command": "omnigent-watch-mcp"}

OMNIGENT_STUB = r'''#!/usr/bin/env python3
"""Stand-in for the omnigent CLI: `server status --json` and the seed server."""
import json, os, sqlite3, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

args = sys.argv[1:]
with open(os.environ["TEST_CALLS"], "a") as output:
    output.write(json.dumps(["omnigent", *args]) + "\n")
if args[:2] == ["server", "status"]:
    print(os.environ.get("TEST_STATUS_JSON", "{}"))
    raise SystemExit(0)
if args[:1] != ["server"]:
    raise SystemExit(2)
agents, port, db = [], None, None
it = iter(args[1:])
for arg in it:
    if arg == "--agent":
        agents.append(os.path.basename(next(it)))
    elif arg == "--port":
        port = int(next(it))
    elif arg == "--database-uri":
        db = next(it).removeprefix("sqlite:///")
    elif arg in ("--host", "--artifact-location"):
        next(it)
with sqlite3.connect(db) as conn:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS agents "
        "(name TEXT, version INTEGER, bundle_location TEXT, kind INTEGER)"
    )
    for name in agents:
        row = conn.execute(
            "SELECT version FROM agents WHERE kind = 1 AND name = ?", (name,)
        ).fetchone()
        if row is None:
            conn.execute("INSERT INTO agents VALUES (?, 1, ?, 1)", (name, f"bundle-{name}-1"))
        elif os.environ.get("TEST_BUNDLE_CHANGED") == "1":
            version = row[0] + 1
            conn.execute(
                "UPDATE agents SET version = ?, bundle_location = ? WHERE kind = 1 AND name = ?",
                (version, f"bundle-{name}-{version}", name),
            )
payload = json.dumps({
    "data": [
        {"name": name, "mcp_servers": [
            {"name": "watch", "transport": "stdio", "command": "omnigent-watch-mcp"}
        ]}
        for name in agents
    ]
}).encode()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = payload if self.path.startswith("/v1/agents") else b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


HTTPServer(("127.0.0.1", port), Handler).serve_forever()
'''

LIVE_SERVER = r'''#!/usr/bin/env python3
"""The running server the script verifies against.

Serves the current definitions once the flip file exists (the systemctl stub
creates it on `restart`), stale ones before that.
"""
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

port, flip, names = int(sys.argv[1]), sys.argv[2], sys.argv[3:]
watch = [{"name": "watch", "transport": "stdio", "command": "omnigent-watch-mcp"}]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/v1/agents"):
            servers = watch if os.path.exists(flip) else []
            body = json.dumps({"data": [{"name": n, "mcp_servers": servers} for n in names]}).encode()
        else:
            body = b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


HTTPServer(("127.0.0.1", port), Handler).serve_forever()
'''

SYSTEMCTL_STUB = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ["TEST_CALLS"], "a") as output:
    output.write(json.dumps(["systemctl", *args]) + "\n")
if "is-active" in args:
    raise SystemExit(0 if os.environ.get("TEST_UNIT_ACTIVE") == "1" else 3)
if "restart" in args and os.environ.get("TEST_FLIP_FILE"):
    pathlib.Path(os.environ["TEST_FLIP_FILE"]).touch()
raise SystemExit(0)
'''


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class AgentsEnsureTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.home = self.base / "home"
        self.dotfiles = self.base / "dotfiles"
        self.bin = self.base / "bin"
        self.data = self.home / ".omnigent"
        self.calls = self.base / "calls.jsonl"
        self.flip = self.base / "restarted"
        for directory in (self.home, self.dotfiles / "bin", self.bin, self.data, self.base / "tmp"):
            directory.mkdir(parents=True)
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "DOTFILES_DIR": str(self.dotfiles),
            "OMNIGENT_DATA_DIR": str(self.data),
            "OMNIGENT_BIN": str(self.bin / "omnigent"),
            "OMNIGENT_PY": str(self.bin / "omnigent-python"),
            "TMPDIR": str(self.base / "tmp"),
            "TEST_CALLS": str(self.calls),
            "TEST_FLIP_FILE": str(self.flip),
            "TEST_PROFILE": "desktop",
            "TEST_PLATFORM": "Linux",
            "LC_ALL": "C",
        }
        self.script(self.bin / "omnigent", OMNIGENT_STUB)
        self.script(self.bin / "systemctl", SYSTEMCTL_STUB)
        self.script(self.bin / "uname", '#!/bin/bash\necho "$TEST_PLATFORM"\n')
        # Overlay materializer: the real one needs omnigent's packaged bundles.
        self.script(
            self.bin / "omnigent-python",
            '#!/bin/bash\nset -eu\nshift\nroot="$1"; shift\nfor name; do mkdir -p "$root/$name"; done\n',
        )
        (self.dotfiles / "omnigent_config/materialize_agent_overlays.py").parent.mkdir(parents=True)
        (self.dotfiles / "omnigent_config/materialize_agent_overlays.py").touch()
        for name in ("claude", "codex", "dvsc"):
            spec = self.dotfiles / "omnigent_config/agents" / name
            spec.mkdir(parents=True)
            (spec / "config.yaml").write_text(f"name: {name}\n")
        recorder = 'printf "%s\\n" "$(python3 -c \'import json,sys;print(json.dumps(sys.argv[1:]))\' "${0##*/}" "$@")" >> "$TEST_CALLS"\n'
        self.script(self.dotfiles / "bin/dotfiles-profile", '#!/bin/bash\necho "$TEST_PROFILE"\n')
        self.script(
            self.dotfiles / "bin/omnigent-server-url",
            "#!/bin/bash\n" + recorder
            + 'case "${1:-}" in\n'
            '  --is-hub) exit "${TEST_IS_HUB:-1}" ;;\n'
            '  --is-candidate) exit 1 ;;\n'
            '  "") echo "${TEST_HUB_URL:?}" ;;\n'
            '  *) exit 2 ;;\n'
            'esac\n',
        )
        self.script(
            self.dotfiles / "bin/omnigent-hub",
            "#!/bin/bash\n" + recorder + 'exit "${TEST_QUIESCE_EXIT:-0}"\n',
        )
        self.script(
            self.dotfiles / "bin/omnigent-server-config-stale",
            "#!/bin/bash\n" + recorder + 'exit "${TEST_CONFIG_STALE_EXIT:-1}"\n',
        )
        self.script(self.base / "live-server.py", LIVE_SERVER)
        self.target = self.dotfiles / "bin/omnigent-agents-ensure"
        shutil.copy2(ROOT / "bin/omnigent-agents-ensure", self.target)

    @staticmethod
    def script(path, contents):
        path.write_text(contents)
        path.chmod(0o755)

    def run_script(self):
        return subprocess.run(
            [str(self.target)],
            cwd=self.dotfiles,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=90,
        )

    def recorded(self, command):
        if not self.calls.exists():
            return []
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        return [call for call in calls if call[0] == command]

    def registered_agents(self):
        db = self.data / "chat.db"
        if not db.exists():
            return set()
        with sqlite3.connect(db) as conn:
            try:
                return {row[0] for row in conn.execute("SELECT name FROM agents WHERE kind = 1")}
            except sqlite3.OperationalError:
                return set()

    def preregister(self, *names):
        with sqlite3.connect(self.data / "chat.db") as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS agents "
                "(name TEXT, version INTEGER, bundle_location TEXT, kind INTEGER)"
            )
            conn.executemany(
                "INSERT INTO agents VALUES (?, 1, ?, 1)",
                [(name, f"bundle-{name}-1") for name in names],
            )

    def start_live_server(self, *names, current):
        port = free_port()
        if current:
            self.flip.touch()
        process = subprocess.Popen(
            ["/usr/bin/python3", str(self.base / "live-server.py"), str(port), str(self.flip), *names],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        url = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                urllib.request.urlopen(f"{url}/health", timeout=1).read()
                return url
            except OSError:
                time.sleep(0.05)
        self.fail("live server stub did not start")

    def status(self, url, live_sessions):
        return json.dumps({"running": True, "url": url, "live_sessions": live_sessions})

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def restarts(self):
        return [call for call in self.recorded("systemctl") if "restart" in call or "try-restart" in call]

    # -- desktop ---------------------------------------------------------------

    def test_desktop_seeds_without_a_running_server(self):
        result = self.run_script()
        self.assert_success(result)
        self.assertIn("no server currently running", result.stdout)
        self.assertEqual(self.registered_agents(), {"claude", "codex", "polly", "debby"})
        self.assertEqual(self.recorded("omnigent-server-url"), [])
        self.assertEqual(self.restarts(), [])

    def test_desktop_current_server_is_left_alone(self):
        self.preregister("claude", "codex", "polly", "debby")
        url = self.start_live_server("claude", "codex", current=True)
        self.env["TEST_STATUS_JSON"] = self.status(url, 2)
        result = self.run_script()
        self.assert_success(result)
        self.assertIn("agent bundles current", result.stdout)
        self.assertEqual(self.restarts(), [])
        self.assertEqual(self.recorded("omnigent-server-url"), [])

    def test_desktop_restarts_idle_host_when_live_definitions_are_stale(self):
        self.preregister("claude", "codex", "polly", "debby")
        url = self.start_live_server("claude", "codex", current=False)
        self.env["TEST_STATUS_JSON"] = self.status(url, 0)
        self.env["TEST_UNIT_ACTIVE"] = "1"
        result = self.run_script()
        self.assert_success(result)
        self.assertEqual(self.restarts(), [["systemctl", "--user", "restart", "omnigent-host.service"]])
        self.assertIn("agent bundles current", result.stdout)
        self.assertEqual(self.recorded("omnigent-hub"), [])

    def test_desktop_refuses_to_restart_under_connected_sessions(self):
        self.preregister("claude", "codex", "polly", "debby")
        url = self.start_live_server("claude", "codex", current=False)
        self.env["TEST_STATUS_JSON"] = self.status(url, 3)
        self.env["TEST_UNIT_ACTIVE"] = "1"
        result = self.run_script()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("3 session(s) are connected", result.stdout)
        self.assertIn("systemctl --user restart omnigent-host.service", result.stdout)
        self.assertEqual(self.restarts(), [])

    def test_desktop_bundle_change_alone_restarts_idle_host(self):
        self.preregister("claude", "codex", "polly", "debby")
        url = self.start_live_server("claude", "codex", current=True)
        self.env["TEST_STATUS_JSON"] = self.status(url, 0)
        self.env["TEST_UNIT_ACTIVE"] = "1"
        self.env["TEST_BUNDLE_CHANGED"] = "1"
        result = self.run_script()
        self.assert_success(result)
        self.assertEqual(self.restarts(), [["systemctl", "--user", "restart", "omnigent-host.service"]])

    def test_desktop_stale_config_alone_restarts_idle_host(self):
        self.preregister("claude", "codex", "polly", "debby")
        url = self.start_live_server("claude", "codex", current=True)
        self.env["TEST_STATUS_JSON"] = self.status(url, 0)
        self.env["TEST_UNIT_ACTIVE"] = "1"
        self.env["TEST_CONFIG_STALE_EXIT"] = "0"
        result = self.run_script()
        self.assert_success(result)
        self.assertEqual(self.restarts(), [["systemctl", "--user", "restart", "omnigent-host.service"]])

    def test_desktop_reports_when_the_unit_does_not_own_the_server(self):
        self.preregister("claude", "codex", "polly", "debby")
        url = self.start_live_server("claude", "codex", current=False)
        self.env["TEST_STATUS_JSON"] = self.status(url, 0)
        result = self.run_script()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("omnigent-host.service is not active under systemd", result.stdout)
        self.assertEqual(self.restarts(), [])

    def test_personal_mac_skips(self):
        self.env["TEST_PLATFORM"] = "Darwin"
        result = self.run_script()
        self.assert_success(result)
        self.assertIn("client host detected", result.stdout)
        self.assertEqual(self.recorded("omnigent"), [])

    # -- work ------------------------------------------------------------------

    def test_work_off_hub_skips_before_touching_the_database(self):
        self.env["TEST_PROFILE"] = "work"
        self.env["TEST_IS_HUB"] = "1"
        result = self.run_script()
        self.assert_success(result)
        self.assertIn("non-active hub/client detected", result.stdout)
        self.assertEqual(self.recorded("omnigent"), [])
        self.assertEqual(self.registered_agents(), set())

    def test_work_hub_registers_dvsc_and_restarts_quiescent_server(self):
        self.env["TEST_PROFILE"] = "work"
        self.env["TEST_IS_HUB"] = "0"
        url = self.start_live_server("claude", "codex", "dvsc", current=False)
        self.env["TEST_HUB_URL"] = url
        self.env["TEST_UNIT_ACTIVE"] = "1"
        result = self.run_script()
        self.assert_success(result)
        self.assertEqual(self.registered_agents(), {"claude", "codex", "dvsc", "polly", "debby"})
        self.assertEqual(
            self.restarts(),
            [
                ["systemctl", "--user", "restart", "omnigent-server.service"],
                ["systemctl", "--user", "try-restart", "omnigent-watcher.service"],
            ],
        )
        self.assertEqual(self.recorded("omnigent-hub"), [["omnigent-hub", "quiesce-check", "--json"]])

    def test_work_hub_busy_server_is_left_alone(self):
        self.env["TEST_PROFILE"] = "work"
        self.env["TEST_IS_HUB"] = "0"
        self.preregister("claude", "codex", "dvsc", "polly", "debby")
        url = self.start_live_server("claude", "codex", "dvsc", current=False)
        self.env["TEST_HUB_URL"] = url
        self.env["TEST_UNIT_ACTIVE"] = "1"
        self.env["TEST_QUIESCE_EXIT"] = "1"
        result = self.run_script()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("active server is busy", result.stdout)
        self.assertEqual(self.restarts(), [])


if __name__ == "__main__":
    unittest.main()
