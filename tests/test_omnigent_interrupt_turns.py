"""Checks for bin/omnigent-interrupt-turns against a fake Omnigent server.

Run with: python3 -m unittest tests.test_omnigent_interrupt_turns
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import threading
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "bin/omnigent-interrupt-turns"


class FakeOmnigent:
    """Sessions whose status an interrupt sets to idle, except the stubborn
    ones (a runner the interrupt does not reach) and the refused ones (the
    server answers 409)."""

    def __init__(self, statuses, stubborn=(), refused=()):
        self.statuses = dict(statuses)
        self.stubborn = set(stubborn)
        self.refused = set(refused)
        self.interrupts = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if not self.path.startswith("/v1/sessions?"):
                    return self.reply(404, {})
                data = [
                    {"id": sid, "status": status, "agent_name": "claude-native-ui", "title": f"t-{sid}"}
                    for sid, status in fake.statuses.items()
                ]
                self.reply(200, {"data": data, "has_more": False})

            def do_POST(self):
                sid = self.path.removeprefix("/v1/sessions/").removesuffix("/events")
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.interrupts.append((sid, body))
                if sid in fake.refused:
                    return self.reply(409, {"error": "not yet"})
                if sid not in fake.stubborn:
                    fake.statuses[sid] = "idle"
                self.reply(202, {"queued": False})

            def reply(self, code, payload):
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class InterruptTurnsTest(unittest.TestCase):
    def fake(self, *args, **kwargs):
        fake = FakeOmnigent(*args, **kwargs)
        self.addCleanup(fake.close)
        return fake

    def run_script(self, url, timeout="3"):
        return subprocess.run(
            [str(SCRIPT), url, "--timeout", timeout], capture_output=True, text=True, timeout=30
        )

    def test_interrupts_only_active_sessions_and_waits_for_them_to_settle(self):
        fake = self.fake({"a": "running", "b": "idle", "c": "waiting", "d": "failed"})
        result = self.run_script(fake.url)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            sorted(fake.interrupts),
            [("a", {"type": "interrupt", "data": {}}), ("c", {"type": "interrupt", "data": {}})],
        )
        self.assertEqual(fake.statuses, {"a": "idle", "b": "idle", "c": "idle", "d": "failed"})
        self.assertIn("all 2 interrupted session(s) settled", result.stderr)

    def test_nothing_active_sends_nothing(self):
        fake = self.fake({"a": "idle"})
        result = self.run_script(fake.url)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(fake.interrupts, [])
        self.assertIn("no active turns", result.stderr)

    def test_a_session_that_stays_active_is_reported_at_the_timeout(self):
        fake = self.fake({"a": "running", "b": "running"}, stubborn={"b"})
        result = self.run_script(fake.url, timeout="1")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("still active after 1s", result.stderr)
        self.assertIn("b=running (claude-native-ui: 't-b')", result.stderr)
        self.assertNotIn("a=running (claude", result.stderr.split("still active")[1])

    def test_a_refused_interrupt_does_not_stop_the_others(self):
        fake = self.fake({"a": "running", "b": "running"}, refused={"a"})
        result = self.run_script(fake.url, timeout="1")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("interrupt refused: HTTP 409", result.stderr)
        self.assertEqual(fake.statuses["b"], "idle")

    def test_an_unreachable_server_exits_2(self):
        fake = self.fake({})
        url = fake.url
        fake.close()
        result = self.run_script(url)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("cannot read the server", result.stderr)


if __name__ == "__main__":
    unittest.main()
