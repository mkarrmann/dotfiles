from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from omnigent_google_chat.discovery import SessionReconciler
from omnigent_google_chat.inbound import GoogleChatPoller, InboundProcessor
from omnigent_google_chat.meta_chat import GoogleChatSender, MetaGoogleChatClient
from omnigent_google_chat.mirror import SessionMirror
from omnigent_google_chat.models import SessionSummary
from omnigent_google_chat.omnigent import OmnigentClient
from omnigent_google_chat.store import SQLiteStore


def fake_meta_executable(tmp_path: Path, state_path: Path) -> Path:
    executable = tmp_path / "fake-meta"
    executable.write_text(
        f"""#!/usr/bin/env python3
import json
import pathlib
import sys

state_path = pathlib.Path({str(state_path)!r})
state = json.loads(state_path.read_text())
args = sys.argv[1:]
action = args[1]
if action == "send":
    body = sys.stdin.read()
    request_id = next(arg.split("=", 1)[1] for arg in args if arg.startswith("--request-id="))
    existing = next(
        (m for m in state["messages"] if m.get("clientAssignedMessageId") == request_id),
        None,
    )
    if existing is None:
        thread_arg = next((arg for arg in args if arg.startswith("--reply-in-thread=")), None)
        index = len(state["messages"]) + 1
        thread = thread_arg.split("=", 1)[1] if thread_arg else f"spaces/s/threads/{{index}}"
        existing = {{
            "name": f"spaces/s/messages/{{index}}",
            "space": {{"name": "spaces/s"}},
            "thread": {{"name": thread}},
            "sender": {{"name": "users/bot", "type": "BOT"}},
            "createTime": f"2026-01-01T00:00:{{index:02d}}Z",
            "text": body,
            "clientAssignedMessageId": request_id,
        }}
        state["messages"].append(existing)
        state_path.write_text(json.dumps(state))
    print(json.dumps(existing))
elif action == "list":
    print(json.dumps({{"messages": state["messages"]}}))
else:
    raise SystemExit(2)
"""
    )
    executable.chmod(0o700)
    return executable


async def test_existing_session_mirror_phone_reply_and_restart_dedup(tmp_path: Path) -> None:
    state_path = tmp_path / "chat.json"
    state_path.write_text(json.dumps({"messages": []}))
    executable = fake_meta_executable(tmp_path, state_path)
    submitted: list[dict[str, object]] = []
    items = [
        {
            "id": "item_assistant",
            "type": "message",
            "data": {
                "role": "assistant",
                "content": [{"type": "output_text", "text": "durable answer"}],
            },
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/items"):
            after = request.url.params.get("after")
            data = [] if after == "item_assistant" else items
            return httpx.Response(
                200,
                json={
                    "data": data,
                    "last_id": data[-1]["id"] if data else after,
                    "has_more": False,
                },
            )
        if request.method == "POST" and request.url.path.endswith("/events"):
            submitted.append(json.loads(request.content))
            return httpx.Response(202, json={"queued": True, "item_id": "item_phone"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    http = httpx.AsyncClient(
        base_url="http://omnigent.test", transport=httpx.MockTransport(handler)
    )
    omnigent = OmnigentClient(
        base_url="http://omnigent.test",
        configured_host_id="host_1",
        client=http,
    )
    store_path = tmp_path / "bridge.sqlite3"
    store = SQLiteStore(store_path)
    await store.initialize()
    await store.bind_space("spaces/s")
    chat = MetaGoogleChatClient(executable=executable, space_name="spaces/s")
    sender = GoogleChatSender(client=chat, store=store, max_message_chars=1000)
    session = SessionSummary(
        id="conv",
        title="CodeCompanion session",
        status="running",
        labels={"omnigent.google_chat.enabled": True},
        host_id="host_1",
        workspace="/repo",
        updated_at=2**31,
    )
    reconciler = SessionReconciler(
        store=store,
        omnigent=omnigent,
        sender=sender,
        space_name="spaces/s",
        host_id="host_1",
        discovery_mode="label",
        discovery_label="omnigent.google_chat.enabled",
        lookback_hours=24,
        interval_seconds=10,
        recent_active_seconds=120,
        mirror_mode="concise",
        mention_unixname="owner",
        mention_enabled=True,
        mention_on_root=True,
        mention_on_completion=True,
        meta_bot_actor_id="users/bot",
        max_session_chars=10_000,
        poll_trigger=lambda: None,
    )
    try:
        await reconciler._create_mapping(session)
        mapping = await store.get_thread("conv")
        assert mapping is not None
        mirror = SessionMirror(
            session_id="conv",
            store=store,
            omnigent=omnigent,
            sender=sender,
            mirror_mode="concise",
            mention_unixname="owner",
            mention_enabled=True,
            mention_on_completion=True,
            max_session_chars=10_000,
            status_changed=lambda session_id, status: None,
        )
        await mirror.reconcile_items()

        state = json.loads(state_path.read_text())
        root_time = datetime.now(UTC) - timedelta(minutes=1)
        phone_message = {
            "name": "spaces/s/messages/phone",
            "space": {"name": "spaces/s"},
            "thread": {"name": mapping.thread_name},
            "sender": {"name": "users/human", "type": "HUMAN"},
            "createTime": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "text": "continue from my phone",
        }
        state["messages"].append(phone_message)
        state_path.write_text(json.dumps(state))
        await store.set_state("gchat_poll_floor", root_time.isoformat().replace("+00:00", "Z"))
        await store.set_poll_cursor((root_time.isoformat().replace("+00:00", "Z"), ""))
        inbound = InboundProcessor(
            store=store,
            omnigent=omnigent,
            sender=sender,
            space_name="spaces/s",
            allowed_actor_id="users/human",
            meta_bot_actor_id="users/bot",
            max_input_chars=1000,
        )
        poller = GoogleChatPoller(
            client=chat,
            store=store,
            processor=inbound,
            active_poll_seconds=10,
            idle_poll_seconds=30,
            overlap_seconds=120,
            health_stale_seconds=120,
            inbound_retention_seconds=30 * 24 * 60 * 60,
            is_active=lambda: True,
        )
        await poller.poll_once()
        assert submitted == [
            {
                "type": "message",
                "data": {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "continue from my phone",
                            "source": {
                                "type": "google_chat",
                                "message_name": "spaces/s/messages/phone",
                            },
                        }
                    ],
                },
            }
        ]
        message_count = len(json.loads(state_path.read_text())["messages"])
    finally:
        store.close()
        await http.aclose()

    restarted = SQLiteStore(store_path)
    await restarted.initialize()
    try:
        assert (await restarted.get_thread("conv")) is not None
        assert len(json.loads(state_path.read_text())["messages"]) == message_count
    finally:
        restarted.close()
