"""Behavioural tests for the claude-native tmux.json self-heal.

Each scenario runs in a fresh interpreter with this directory on
``PYTHONPATH``, as ``omnigent-host`` launches runners. The real
``sitecustomize`` installs the patch, and the scenario calls the
``_ensure_native_terminal`` that ``resource_routes`` imports from
``omnigent.runner.native``. ``TMPDIR`` points into a scratch dir, so the bridge
root (``$TMPDIR/omnigent-<uid>/claude-native``) never touches the live one.

The terminal registry and the server's labels endpoint are faked: they stand
for live runner state and the network. The assertions use upstream's own
consumer, ``bridge._wait_for_tmux_info``, which delivery uses to find the
pane.

Run: ~/.local/share/uv/tools/omnigent/bin/python test_omnigent_tmux_self_heal.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from omnigent_tmux_self_heal import install  # noqa: E402

SCENARIO = r"""
import asyncio, json, sys
from dataclasses import dataclass
from pathlib import Path

import httpx
import omnigent.runner.native as native
from omnigent.entities.session_resources import SessionResourceView, terminal_resource_id
from omnigent.harnesses.claude_native.bridge import (
    BRIDGE_ID_LABEL_KEY,
    TmuxSessionNotAdvertised,
    _wait_for_tmux_info,
    bridge_dir_for_bridge_id,
    write_tmux_target,
)

case = json.loads(sys.argv[1])
SESSION = "2b23627d9e34427393bb4d18b697797d"
LIVE_SOCKET = case["live_socket"]


@dataclass
class Instance:
    running: bool
    socket_path: Path
    tmux_target: str


class TerminalRegistry:
    def get(self, session_id, name, key):
        if (session_id, name, key) == (SESSION, "claude", "main"):
            return Instance(case["running"], Path(LIVE_SOCKET), "main")
        return None


class ResourceRegistry:
    terminal_registry = TerminalRegistry()

    async def get_terminal_resource(self, session_id, terminal_id):
        return SessionResourceView(
            id=terminal_resource_id("claude", "main"),
            type="terminal",
            session_id=session_id,
            name="claude",
        )


def labels(request):
    return httpx.Response(200, json={"labels": {BRIDGE_ID_LABEL_KEY: case["bridge_id"]}})


async def main():
    bridge_dir = bridge_dir_for_bridge_id(case["bridge_id"])
    if case["make_bridge_dir"]:
        bridge_dir.mkdir(parents=True, mode=0o700)
    if case["stale_socket"]:
        write_tmux_target(bridge_dir, socket_path=Path(case["stale_socket"]), tmux_target="old")
    async with httpx.AsyncClient(
        base_url="http://server.test", transport=httpx.MockTransport(labels)
    ) as client:
        ctx = native.NativeLaunchContext(
            session_id=SESSION,
            resource_registry=ResourceRegistry(),
            publish_event=lambda *_: None,
            server_client=client,
        )
        response = await native._ensure_native_terminal("claude", ctx, ensure_locks={})
    try:
        advertised = _wait_for_tmux_info(bridge_dir, timeout_s=0.3)
    except TmuxSessionNotAdvertised:
        advertised = None
    print(json.dumps({
        "status": response.status_code,
        "advertised": advertised,
        "bridge_dir_exists": bridge_dir.is_dir(),
    }))


asyncio.run(main())
"""


def run_scenario(scratch: Path, *, env_overrides: dict[str, str] | None = None, **case):
    tmpdir = Path(tempfile.mkdtemp(dir=scratch))
    case.setdefault("bridge_id", "bridge_label_id")
    case.setdefault("live_socket", str(tmpdir / "omnigent-terminal-live" / "tmux.sock"))
    case.setdefault("running", True)
    case.setdefault("make_bridge_dir", True)
    case.setdefault("stale_socket", None)
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("OMNIGENT_")},
        "PYTHONPATH": str(HERE),
        "TMPDIR": str(tmpdir),
        **(env_overrides or {}),
    }
    proc = subprocess.run(
        [sys.executable, "-c", SCENARIO, json.dumps(case)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmpdir,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"scenario crashed:\n{proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1]), case


fails = []


def check(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         got={got!r}\n         want={want!r}")
        fails.append(label)


scratch = Path(tempfile.mkdtemp(prefix="tmuxheal-"))
try:
    print("\n1. upstream (patch disabled): a lost tmux.json is never rewritten")
    out, case = run_scenario(scratch, env_overrides={"OMNIGENT_TMUX_SELF_HEAL": "0"})
    check("ensure still succeeds", out["status"], 200)
    check("delivery cannot find the pane", out["advertised"], None)

    print("\n2. patched: missing tmux.json is restored from the live terminal")
    out, case = run_scenario(scratch)
    check("ensure still succeeds", out["status"], 200)
    check(
        "delivery finds the live pane (in the labelled bridge dir)",
        out["advertised"],
        {"socket_path": case["live_socket"], "tmux_target": "main"},
    )

    print("\n3. patched: a tmux.json naming another socket is corrected")
    out, case = run_scenario(scratch, stale_socket="/nonexistent/old/tmux.sock")
    check(
        "delivery finds the live pane",
        out["advertised"],
        {"socket_path": case["live_socket"], "tmux_target": "main"},
    )

    print("\n4. patched: a terminal that is not running is not advertised")
    out, case = run_scenario(scratch, running=False)
    check("nothing advertised", out["advertised"], None)

    print("\n5. patched: an unknown bridge dir is not created")
    out, case = run_scenario(scratch, make_bridge_dir=False)
    check("bridge dir still absent", out["bridge_dir_exists"], False)
    check("nothing advertised", out["advertised"], None)

    print("\n6. upgrade guard: missing upstream names leave the module untouched")
    stripped = ModuleType("omnigent.runner.native.orchestration")
    stripped._ensure_native_terminal = sentinel = object()
    check("install reports not installed", install(stripped), False)
    check("ensure function unchanged", stripped._ensure_native_terminal is sentinel, True)
finally:
    shutil.rmtree(scratch, ignore_errors=True)

print(
    f"\n{'ALL PASSED' if not fails else str(len(fails)) + ' FAILURE(S): ' + ', '.join(fails)}"
)
sys.exit(1 if fails else 0)
