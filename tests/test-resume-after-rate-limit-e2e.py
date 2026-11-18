#!/usr/bin/env python3
"""End-to-end check of claude_config/hooks/resume-after-rate-limit.py in a real
interactive Claude Code: a throwaway `claude` in its own tmux server, pointed at
a loopback fake of the Messages API that refuses the main loop with
gateway-style 429s (``x-should-retry: false``, so Claude Code gives up at once
and fires StopFailure) before letting it through. The hook is registered the way
sync.sh registers it, in a temporary CLAUDE_CONFIG_DIR; ~/.claude is untouched.

Two scenarios run side by side:
- resumes: two 429s in a row. The hook wakes Claude after 60s, the retry hits
  the limit again, it wakes Claude after 120s, and the third request succeeds.
- moved_on: one 429, then the user types during the wait. That message starts
  the next turn, and the hook stays quiet when its wait ends.

Takes about three and a half minutes. Artifacts (fake API log, transcripts,
final screens) are kept under the printed directory.

Run with: python3 tests/test-resume-after-rate-limit-e2e.py   (CLAUDE_BIN picks another claude)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "claude_config/hooks/resume-after-rate-limit.py"
CLAUDE = Path(os.environ.get("CLAUDE_BIN", "/usr/local/bin/claude_code/native/claude"))
MARKER = "[auto-resume]"
TYPED = "Are you still there?"


@dataclass
class MainRequest:
    at: float
    status: int
    last_user_text: str


@dataclass
class FakeAPI:
    """Fails the first ``failures`` main-loop requests (the ones carrying tools) with a 429."""

    failures: int
    log: Path
    main_requests: list[MainRequest] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    changed: threading.Condition = field(init=False)

    def __post_init__(self) -> None:
        self.changed = threading.Condition(self.lock)

    def record(self, line: dict) -> None:
        with self.log.open("a") as out:
            out.write(json.dumps({"at": round(time.time(), 3), **line}) + "\n")


def last_user_text(body: dict) -> str:
    for message in reversed(body.get("messages", [])):
        if message.get("role") == "user":
            return json.dumps(message.get("content"))
    return ""


def sse(text: str) -> bytes:
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_e2e", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta",
                           "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                           "usage": {"output_tokens": 5}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return b"".join(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode() for name, data in events)


def handler_for(api: FakeAPI) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def reply(self, status: int, body: bytes, content_type: str, headers: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(body)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
            path = self.path.split("?")[0]
            if path.endswith("/count_tokens"):
                self.reply(200, b'{"input_tokens": 10}', "application/json")
                return
            if not path.endswith("/v1/messages"):
                api.record({"path": path, "status": 404})
                self.reply(404, b'{"type":"error","error":{"type":"not_found_error","message":"e2e"}}', "application/json")
                return
            main = bool(body.get("tools"))
            text = last_user_text(body)
            with api.changed:
                status = 429 if main and api.failures > 0 else 200
                if status == 429:
                    api.failures -= 1
                if main:
                    api.main_requests.append(MainRequest(time.time(), status, text))
                    api.changed.notify_all()
            api.record({"path": path, "main": main, "status": status, "stream": bool(body.get("stream")),
                        "marker_in_last_user_message": MARKER in text, "typed_in_last_user_message": TYPED in text})
            if status == 429:
                self.reply(429, json.dumps({"type": "error", "error": {
                    "type": "rate_limit_error",
                    "message": "e2e: shared model capacity is temporarily exhausted"}}).encode(),
                    "application/json", {"x-should-retry": "false"})
            elif body.get("stream"):
                self.reply(200, sse("Hello from the fake API."), "text/event-stream")
            else:
                self.reply(200, json.dumps({
                    "id": "msg_e2e", "type": "message", "role": "assistant", "model": body.get("model", ""),
                    "content": [{"type": "text", "text": "e2e"}], "stop_reason": "end_turn",
                    "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}).encode(),
                    "application/json")

    return Handler


class Scenario:
    def __init__(self, name: str, failures: int, artifacts: Path) -> None:
        self.name = name
        self.dir = artifacts / name
        self.config = self.dir / "config"
        self.work = self.dir / "work"
        self.home = self.dir / "home"
        for directory in (self.config, self.work, self.home):
            directory.mkdir(parents=True)
        self.api = FakeAPI(failures, self.dir / "fake-api.jsonl")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.api))
        self.socket = f"resume-e2e-{os.getpid()}-{name}"
        self.failures: list[str] = []

    def check(self, ok: bool, message: str) -> None:
        print(f"[{self.name}] {'ok  ' if ok else 'FAIL'} {message}", flush=True)
        if not ok:
            self.failures.append(message)

    def tmux(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["tmux", "-L", self.socket, "-f", "/dev/null", *args],
                              capture_output=True, text=True, check=False)

    def start(self) -> None:
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        (self.config / "settings.json").write_text(json.dumps({"hooks": {"StopFailure": [{
            "matcher": "rate_limit",
            "hooks": [{"type": "command", "command": f"python3 {HOOK}", "asyncRewake": True, "timeout": 600}],
        }]}}))
        (self.config / ".claude.json").write_text(json.dumps({
            "hasCompletedOnboarding": True,
            "projects": {str(self.work.resolve()): {"hasTrustDialogAccepted": True,
                                                    "hasCompletedProjectOnboarding": True}},
            "customApiKeyResponses": {"approved": ["e2e-test-key"], "rejected": []},
        }))
        env = {
            "HOME": str(self.home), "PATH": os.environ["PATH"], "TERM": "xterm-256color",
            "LANG": os.environ.get("LANG", "en_US.UTF-8"), "USER": os.environ.get("USER", ""),
            "CLAUDE_CONFIG_DIR": str(self.config),
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{self.server.server_address[1]}",
            "ANTHROPIC_API_KEY": "e2e-test-key",
            "DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }
        command = ["env", "-i", *(f"{key}={value}" for key, value in env.items()),
                   str(CLAUDE), "--model", "claude-sonnet-5-5", "Reply with one word."]
        result = self.tmux("new-session", "-d", "-s", "e2e", "-x", "200", "-y", "50", "-c", str(self.work), *command)
        if result.returncode != 0:
            raise RuntimeError(f"tmux failed: {result.stderr}")

    def wait_for_main_requests(self, count: int, timeout: float) -> list[MainRequest]:
        deadline = time.time() + timeout
        with self.api.changed:
            while len(self.api.main_requests) < count and time.time() < deadline:
                self.api.changed.wait(timeout=max(0.0, deadline - time.time()))
            return list(self.api.main_requests)

    def finish(self) -> None:
        screen = self.tmux("capture-pane", "-p", "-t", "e2e").stdout
        (self.dir / "screen.txt").write_text(screen)
        self.tmux("kill-server")
        self.server.shutdown()

    def run_resumes(self) -> None:
        requests = self.wait_for_main_requests(3, timeout=330)
        self.check(len(requests) == 3, f"three main-loop requests arrived (got {len(requests)})")
        if len(requests) < 3:
            return
        first, second, third = requests[:3]
        self.check([r.status for r in requests[:3]] == [429, 429, 200], "statuses were 429, 429, 200")
        self.check(MARKER not in first.last_user_text, "the first request is the user's own prompt")
        self.check(MARKER in second.last_user_text and MARKER in third.last_user_text,
                   "both retries carry the hook's wake message")
        gap1, gap2 = second.at - first.at, third.at - second.at
        self.check(58 <= gap1 <= 80, f"first resume waited ~60s (waited {gap1:.1f}s)")
        self.check(118 <= gap2 <= 140, f"second resume waited ~120s (waited {gap2:.1f}s)")
        time.sleep(5)
        self.check(len(self.api.main_requests) == 3, "nothing further after the successful turn")

    def run_moved_on(self) -> None:
        requests = self.wait_for_main_requests(1, timeout=60)
        self.check(len(requests) == 1 and requests[0].status == 429, "the first request hit a 429")
        if not requests:
            return
        failed_at = requests[0].at
        time.sleep(5)
        self.tmux("send-keys", "-t", "e2e", "-l", TYPED)
        time.sleep(1)
        self.tmux("send-keys", "-t", "e2e", "Enter")
        requests = self.wait_for_main_requests(2, timeout=30)
        self.check(len(requests) >= 2 and TYPED in requests[1].last_user_text and requests[1].status == 200,
                   "the typed message started the next turn")
        time.sleep(max(0.0, failed_at + 80 - time.time()))
        woken = [r for r in self.api.main_requests if MARKER in r.last_user_text]
        self.check(not woken, f"the hook stayed quiet after its wait ({len(self.api.main_requests)} main requests, "
                              f"{len(woken)} carrying the wake message)")


def main() -> int:
    if not CLAUDE.exists():
        print(f"no claude at {CLAUDE}; set CLAUDE_BIN", file=sys.stderr)
        return 2
    artifacts = Path(tempfile.mkdtemp(prefix="resume-after-rate-limit-e2e-"))
    print(f"artifacts: {artifacts}  claude: {CLAUDE}", flush=True)
    scenarios = {"resumes": Scenario("resumes", 2, artifacts), "moved_on": Scenario("moved_on", 1, artifacts)}
    threads = []
    try:
        for scenario in scenarios.values():
            scenario.start()
        for name, scenario in scenarios.items():
            target = scenario.run_resumes if name == "resumes" else scenario.run_moved_on
            threads.append(threading.Thread(target=target))
            threads[-1].start()
        for thread in threads:
            thread.join()
    finally:
        for scenario in scenarios.values():
            scenario.finish()
    failures = [f"{s.name}: {f}" for s in scenarios.values() for f in s.failures]
    print("PASS" if not failures else "FAIL\n  " + "\n  ".join(failures))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
