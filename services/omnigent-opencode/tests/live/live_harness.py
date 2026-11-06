"""Live checks against a real Omnigent server, through T3 Code's own OpenCode 2 client.

    uv run python tests/live/live_harness.py SCENARIO [--agent claude-native-ui] [--model sonnet]

Scenarios create real Omnigent sessions on this machine's host and print the
event timeline T3 Code would see. They are not part of the offline suite.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiohttp.test_utils import TestServer  # noqa: E402
from conftest import HARNESS, PASSWORD, T3_NODE_MODULES, T3CODE, T3Client  # noqa: E402

from omnigent_opencode.omnigent import OmnigentClient  # noqa: E402
from omnigent_opencode.server import App, Config, make_app  # noqa: E402
from omnigent_opencode.store import Store  # noqa: E402

START = time.monotonic()


def stamp() -> str:
    return f"{time.monotonic() - START:7.2f}s"


def describe(event: dict[str, Any]) -> str:
    data = event.get("data") or {}
    detail = ""
    if event["type"].endswith(".delta"):
        detail = repr(data.get("delta"))[:60]
    elif event["type"] in ("session.text.ended", "session.reasoning.ended"):
        detail = repr(data.get("text"))[:120]
    elif event["type"].startswith("session.tool."):
        detail = json.dumps({k: data[k] for k in ("name", "input", "content", "error") if k in data})[:200]
    elif event["type"].startswith("session.inbox."):
        detail = str(data.get("inboxID"))
    return f"{stamp()} {event['type']} {detail}"


async def run(scenario: str, agent: str, model: str, directory: str) -> None:
    config = Config(password=PASSWORD, host_spec="auto", agent_specs=[agent], default_directory=directory)
    store_dir = Path(tempfile.mkdtemp(prefix="oc-live-"))
    app = App(config, OmnigentClient(os.environ.get("OMNIGENT_URL", "http://127.0.0.1:6767")), Store(store_dir / "state.db"))
    server = TestServer(make_app(app))
    await server.start_server()
    workdir = store_dir / "harness"
    workdir.mkdir()
    (workdir / "node_modules").symlink_to(T3_NODE_MODULES)
    shutil.copy(HARNESS, workdir / "t3-client.mjs")
    process = await asyncio.create_subprocess_exec(
        "node",
        str(workdir / "t3-client.mjs"),
        str(server.make_url("")).rstrip("/"),
        PASSWORD,
        cwd=workdir,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        env={**os.environ, "T3CODE_REPO": str(T3CODE)},
    )
    t3 = T3Client(process)
    await asyncio.wait_for(t3.stream_open.wait(), 15)
    printed = 0

    def show() -> None:
        nonlocal printed
        for event in t3.events[printed:]:
            print(describe(event), flush=True)
        printed = len(t3.events)

    async def wait(predicate: Any, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while not predicate():
            show()
            if time.monotonic() > deadline:
                raise TimeoutError
            await asyncio.sleep(0.2)
        show()

    asks = [{"action": a, "resource": "*", "effect": "ask"} for a in ("shell", "edit", "external_directory")]
    permissions = asks if scenario in ("approve", "decline") else [{"action": "*", "resource": "*", "effect": "allow"}]
    session = await t3.call(
        "session.create",
        {"location": {"directory": directory}, "model": {"providerID": agent, "id": model}, "permissions": permissions},
    )
    sid = session["id"]
    print(f"{stamp()} session {sid}", flush=True)
    executions = lambda: sum(t.startswith("session.execution.") and t != "session.execution.started" for t in t3.types(sid))  # noqa: E731

    if scenario == "turn":
        await t3.call(
            "session.prompt", {"sessionID": sid, "id": f"msg_t3_turn_{sid}:1", "text": "Reply with exactly the word: pong"}
        )
        await wait(lambda: executions() >= 1, 120)
    elif scenario == "steer":
        await t3.call(
            "session.prompt",
            {
                "sessionID": sid,
                "id": f"msg_t3_turn_{sid}:1",
                "text": "Run this exact shell command in the foreground (not in the background) and wait for it: python3 -c \"import time; time.sleep(25); print('WORK_DONE')\". Then tell me its output.",
            },
        )
        await wait(lambda: "session.tool.called" in t3.types(sid), 120)
        await asyncio.sleep(3)
        print(f"{stamp()} >>> steering while the command runs", flush=True)
        await t3.call(
            "session.prompt",
            {
                "sessionID": sid,
                "id": f"msg_t3_steer_{sid}:2",
                "text": "Also, end your reply with the word BANANA.",
                "delivery": "steer",
            },
        )
        await wait(lambda: executions() >= 1 and not app.sessions[sid].steers and not app.sessions[sid].running, 240)
    elif scenario in ("approve", "decline"):
        probe = Path(directory) / "approval-probe.txt"
        await t3.call(
            "session.prompt",
            {"sessionID": sid, "id": f"msg_t3_turn_{sid}:1", "text": f"Run this exact shell command: touch {probe}"},
        )
        await wait(lambda: any(e["type"] == "permission.asked" for e in t3.events), 120)
        request = next(e for e in t3.events if e["type"] == "permission.asked")["data"]
        print(f"{stamp()} >>> asked: {json.dumps(request)}", flush=True)
        snapshot = await app.client.session_snapshot(app.sessions[sid].conversation_id, items=False)
        print(f"{stamp()} claude permission mode: {(snapshot.get('labels') or {}).get('omnigent.claude_native.permission_mode')}")
        if scenario == "approve":
            await t3.call("permission.reply", {"sessionID": sid, "requestID": request["id"], "decision": "once"})
        else:
            note = "The user declined the shell request. Do not retry it; continue without it or ask the user how to proceed."
            await t3.call("session.synthetic", {"sessionID": sid, "text": note, "delivery": "steer", "resume": False})
            await t3.call(
                "permission.reply",
                {
                    "sessionID": sid,
                    "requestID": request["id"],
                    "decision": "reject",
                    "message": "The user declined this request.",
                },
            )
        await wait(lambda: executions() >= 1, 180)
        print(f"{stamp()} probe file exists: {probe.exists()}", flush=True)
    elif scenario == "question":
        await t3.call(
            "session.prompt",
            {
                "sessionID": sid,
                "id": f"msg_t3_turn_{sid}:1",
                "text": "Use your AskUserQuestion tool to ask me whether I prefer red or blue (those two options). "
                "Then reply with exactly the color I picked.",
            },
        )
        await wait(lambda: any(e["type"] == "form.created" for e in t3.events), 120)
        form = next(e for e in t3.events if e["type"] == "form.created")["data"]["form"]
        print(f"{stamp()} >>> form: {json.dumps(form)}", flush=True)
        field = form["fields"][0]
        blue = next(o["value"] for o in field["options"] if "blue" in o["value"].lower())
        await t3.call("session.form.reply", {"sessionID": sid, "formID": form["id"], "answer": {field["key"]: blue}})
        await wait(lambda: executions() >= 1, 180)
    elif scenario == "plan":
        await t3.call("session.switchAgent", {"sessionID": sid, "agent": "plan"})
        await t3.call(
            "session.prompt",
            {"sessionID": sid, "id": f"msg_t3_turn_{sid}:1", "text": "Plan (do not implement) adding a hello.txt file here."},
        )
        await wait(lambda: executions() >= 1, 240)
        snapshot = await app.client.session_snapshot(app.sessions[sid].conversation_id, items=False)
        print(f"{stamp()} claude permission mode: {(snapshot.get('labels') or {}).get('omnigent.claude_native.permission_mode')}")
    elif scenario == "smoke":
        ok: list[str] = []

        def text_of(session_id: str, after: int = 0) -> str:
            return " ".join(e["data"]["text"] for e in t3.of(session_id)[after:] if e["type"] == "session.text.ended")

        async def turn(session_id: str, n: str, text: str, ends: int, **extra: Any) -> str:
            mark = len(t3.of(session_id))
            await t3.call(
                "session.prompt", {"sessionID": session_id, "id": f"msg_t3_turn_{session_id}:{n}", "text": text, **extra}
            )
            done = lambda: sum(  # noqa: E731
                t.startswith("session.execution.") and t != "session.execution.started" for t in t3.types(session_id)
            )
            await wait(lambda: done() >= ends, 180)
            return text_of(session_id, mark)

        first = await turn(sid, "1", "Remember the codeword PAPAYA. Reply with exactly: noted", 1)
        print(f"{stamp()} turn 1: {first!r}", flush=True)
        await t3.call("session.switchModel", {"sessionID": sid, "model": {"providerID": agent, "id": "opus"}})
        snapshot = await app.client.session_snapshot(app.sessions[sid].conversation_id, items=False)
        ok.append(f"model switch: model_override={snapshot.get('model_override')}")
        second = await turn(sid, "2", "Reply with exactly the codeword I asked you to remember.", 2)
        ok.append(f"turn after switch: {second!r}")
        png = Path(directory) / "red.png"
        png.write_bytes(_red_png())
        third = await turn(
            sid,
            "3",
            "What single color fills the attached image? Reply with one word.",
            3,
            files=[{"uri": f"file://{png}", "name": "red.png"}],
        )
        ok.append(f"image: {third!r}")
        mark = len(t3.of(sid))
        await t3.call("session.compact", {"sessionID": sid, "id": f"msg_t3_turn_{sid}:c1"})
        await wait(
            lambda: any(
                e["type"].startswith("session.execution.") and e["type"] != "session.execution.started" for e in t3.of(sid)[mark:]
            ),
            600,
        )
        ok.append(
            "compaction: "
            + ",".join(
                e["type"] for e in t3.of(sid)[mark:] if "compaction" in e["type"] or e["type"].startswith("session.execution.")
            )
        )
        forked = await t3.call("session.fork", {"sessionID": sid, "before": f"msg_t3_turn_{sid}:2"})
        fork_reply = await turn(forked["id"], "f1", "Reply with exactly the codeword I asked you to remember, or NONE.", 1)
        ok.append(f"fork before turn 2 ({forked['id']}): {fork_reply!r}")
        await t3.call("session.revert.stage", {"sessionID": sid, "messageID": f"msg_t3_turn_{sid}:3", "files": False})
        await t3.call("session.revert.commit", {"sessionID": sid})
        listed = (await t3.call("message.list", {"sessionID": sid, "limit": 100}))["data"]
        ok.append(f"rollback before turn 3: users={[m['id'].rsplit(':', 1)[-1] for m in listed if m['type'] == 'user']}")
        title_session = await t3.call(
            "session.create",
            {
                "title": "T3 Code generateThreadTitle",
                "location": {"directory": directory},
                "model": {"providerID": agent, "id": model},
                "permissions": [{"action": "*", "resource": "*", "effect": "ask"}],
            },
        )
        title_text = await turn(
            title_session["id"],
            "t1",
            "Write a 3-word title for a chat about baking sourdough bread. Reply with only the title.",
            1,
        )
        ok.append(f"text generation: {title_text!r}")
        for line in ok:
            print(f"{stamp()} SMOKE {line}", flush=True)
    elif scenario == "edit":
        target = Path(directory) / "greeting.py"
        target.write_text('GREETING = "hello"\n')
        await t3.call(
            "session.prompt",
            {
                "sessionID": sid,
                "id": f"msg_t3_turn_{sid}:1",
                "text": f'Use your Edit tool to change "hello" to "goodbye" in {target}. Do nothing else.',
            },
        )
        await wait(lambda: executions() >= 1, 180)
        for e in t3.of(sid):
            if e["type"] == "session.tool.called":
                print(f"{stamp()} EDIT input keys: {sorted(e['data']['input'])}", flush=True)
            if e["type"] == "session.tool.success":
                print(f"{stamp()} EDIT metadata: {json.dumps(e['data'].get('metadata'))}", flush=True)
        print(f"{stamp()} EDIT file now: {target.read_text()!r}", flush=True)
    elif scenario == "stop":
        await t3.call(
            "session.prompt",
            {"sessionID": sid, "id": f"msg_t3_turn_{sid}:1", "text": "Run this exact shell command: sleep 60 && echo SLEEP_DONE"},
        )
        await wait(lambda: "session.tool.called" in t3.types(sid), 120)
        await asyncio.sleep(2)
        print(f"{stamp()} >>> stop: {await t3.call('session.interrupt', {'sessionID': sid})}", flush=True)
        await wait(lambda: executions() >= 1, 60)
        await t3.call(
            "session.prompt", {"sessionID": sid, "id": f"msg_t3_turn_{sid}:2", "text": "Reply with exactly the word: still-here"}
        )
        await wait(lambda: executions() >= 2, 120)
    else:
        raise SystemExit(f"unknown scenario {scenario}")
    print(f"{stamp()} done; Omnigent session {app.sessions[sid].conversation_id}", flush=True)
    process.kill()
    await server.close()


def _red_png() -> bytes:
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = b"".join(b"\x00" + b"\xff\x00\x00" * 64 for _ in range(64))
    header = struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def main() -> None:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore", "aiohttp.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=["turn", "steer", "stop", "approve", "decline", "question", "plan", "smoke", "edit"])
    parser.add_argument("--agent", default="claude-native-ui")
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--directory", default=tempfile.mkdtemp(prefix="oc-live-cwd-"))
    args = parser.parse_args()
    asyncio.run(run(args.scenario, args.agent, args.model, args.directory))


if __name__ == "__main__":
    main()
