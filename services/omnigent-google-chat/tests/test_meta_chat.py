from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from omnigent_google_chat.meta_chat import (
    GoogleChatSender,
    MetaChatOutputError,
    MetaGoogleChatClient,
    ProcessResult,
    outbound_request_id,
    parse_chat_message,
    parse_message_page,
)
from omnigent_google_chat.store import SQLiteStore


def raw_message(
    name: str = "spaces/s/messages/m",
    *,
    thread: str = "spaces/s/threads/t",
    actor: str = "users/human",
    actor_type: str = "HUMAN",
    created: str = "2026-01-01T00:00:00Z",
    text: str = "hello",
) -> dict[str, object]:
    return {
        "name": name,
        "space": {"name": "spaces/s"},
        "thread": {"name": thread},
        "sender": {"name": actor, "type": actor_type},
        "createTime": created,
        "text": text,
    }


async def test_send_uses_exact_argv_stdin_and_parses_raw_response() -> None:
    calls: list[tuple[list[str], bytes | None]] = []

    async def runner(argv: list[str], stdin: bytes | None, timeout_seconds: float) -> ProcessResult:
        calls.append((argv, stdin))
        payload = raw_message(actor="users/bot", actor_type="BOT")
        return ProcessResult(0, json.dumps(payload).encode(), b"")

    client = MetaGoogleChatClient(executable=Path("meta"), space_name="spaces/s", runner=runner)
    result = await client.send_message(
        text="body with $shell",
        request_id="request",
        thread_name="spaces/s/threads/t",
        mention_unixname="owner",
    )
    argv, stdin = calls[0]
    assert argv[:3] == ["meta", "google.chat.message", "send"]
    assert "--as-meta-bot" in argv
    assert "--reply-in-thread=spaces/s/threads/t" in argv
    assert "--mention=owner" in argv
    assert "body with $shell" not in argv
    assert stdin == b"body with $shell"
    assert result.actor_type == "BOT"


async def test_send_parses_meta_summary_followed_by_raw_json() -> None:
    async def runner(argv: list[str], stdin: bytes | None, timeout_seconds: float) -> ProcessResult:
        payload = raw_message(actor="users/bot", actor_type="BOT")
        stdout = b"Sent message\n  name: spaces/s/messages/m\n" + json.dumps(payload).encode()
        return ProcessResult(0, stdout, b"")

    client = MetaGoogleChatClient(executable=Path("meta"), space_name="spaces/s", runner=runner)
    result = await client.send_message(text="body", request_id="request")
    assert result.name == "spaces/s/messages/m"


async def test_non_json_output_still_fails_loudly() -> None:
    async def runner(argv: list[str], stdin: bytes | None, timeout_seconds: float) -> ProcessResult:
        return ProcessResult(0, b"Sent message without a raw payload\n", b"")

    client = MetaGoogleChatClient(executable=Path("meta"), space_name="spaces/s", runner=runner)
    with pytest.raises(MetaChatOutputError, match="valid JSON"):
        await client.send_message(text="body", request_id="request")


async def test_slow_meta_log_has_action_and_duration_but_not_message_body(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    ticks = iter([10.0, 16.25])
    monkeypatch.setattr("omnigent_google_chat.meta_chat._monotonic", lambda: next(ticks))

    async def runner(argv: list[str], stdin: bytes | None, timeout_seconds: float) -> ProcessResult:
        payload = raw_message(actor="users/bot", actor_type="BOT")
        return ProcessResult(0, json.dumps(payload).encode(), b"")

    caplog.set_level(logging.WARNING)
    client = MetaGoogleChatClient(executable=Path("meta"), space_name="spaces/s", runner=runner)
    await client.send_message(text="sensitive body", request_id="request")
    assert "google.chat.message send" in caplog.text
    assert "duration_seconds=6.250" in caplog.text
    assert "sensitive body" not in caplog.text


async def test_list_pages_and_deduplicates_by_message_name() -> None:
    calls: list[list[str]] = []

    async def runner(argv: list[str], stdin: bytes | None, timeout_seconds: float) -> ProcessResult:
        calls.append(argv)
        payload: dict[str, object]
        if any(arg == "--page-token=next" for arg in argv):
            payload = {"messages": [raw_message("spaces/s/messages/2")]}
        else:
            payload = {
                "messages": [raw_message("spaces/s/messages/1")],
                "nextPageToken": "next",
            }
        return ProcessResult(0, json.dumps(payload).encode(), b"")

    client = MetaGoogleChatClient(executable=Path("meta"), space_name="spaces/s", runner=runner)
    messages = await client.list_all_messages(created_after="2026-01-01T00:00:00Z")
    assert [message.name for message in messages] == [
        "spaces/s/messages/1",
        "spaces/s/messages/2",
    ]
    assert "--skip-cache" in calls[0]
    assert "--oldest" in calls[0]
    assert "--page-token=next" in calls[1]


def test_parse_raw_page_and_reject_wrong_space() -> None:
    page = parse_message_page({"messages": [raw_message()]}, expected_space="spaces/s")
    assert page.messages[0].actor_id == "users/human"
    wrong = raw_message()
    wrong["space"] = {"name": "spaces/other"}
    with pytest.raises(MetaChatOutputError, match="belongs"):
        parse_chat_message(wrong, expected_space="spaces/s")


def test_create_times_are_canonical_for_tuple_ordering() -> None:
    whole_second = parse_chat_message(
        raw_message(name="spaces/s/messages/1", created="2026-01-01T00:00:00Z"),
        expected_space="spaces/s",
    )
    fractional = parse_chat_message(
        raw_message(name="spaces/s/messages/2", created="2026-01-01T00:00:00.1Z"),
        expected_space="spaces/s",
    )
    assert whole_second.create_time == "2026-01-01T00:00:00.000000Z"
    assert whole_second.ordering_key < fractional.ordering_key


async def test_truncated_process_output_fails_loud() -> None:
    async def runner(argv: list[str], stdin: bytes | None, timeout_seconds: float) -> ProcessResult:
        return ProcessResult(0, b"{}", b"", stdout_truncated=True)

    client = MetaGoogleChatClient(executable=Path("meta"), space_name="spaces/s", runner=runner)
    with pytest.raises(MetaChatOutputError, match="bound"):
        await client.list_page(created_after="2026-01-01T00:00:00Z")


async def test_member_list_converts_member_resource_to_sender_actor_id() -> None:
    async def runner(argv: list[str], stdin: bytes | None, timeout_seconds: float) -> ProcessResult:
        assert argv[1:3] == ["google.chat.member", "list"]
        payload = [
            {
                "name": "spaces/s/members/123",
                "type": "HUMAN",
                "email": "owner@meta.com",
            },
            {"name": "spaces/s/members/999", "type": "BOT"},
        ]
        return ProcessResult(0, json.dumps(payload).encode(), b"")

    client = MetaGoogleChatClient(executable=Path("meta"), space_name="spaces/s", runner=runner)
    assert await client.list_member_actor_ids() == {"users/123", "owner@meta.com"}


async def test_sender_chunks_and_mentions_only_first_part(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    async def runner(argv: list[str], stdin: bytes | None, timeout_seconds: float) -> ProcessResult:
        calls.append(argv)
        index = len(calls)
        payload = raw_message(f"spaces/s/messages/{index}", actor="users/bot", actor_type="BOT")
        return ProcessResult(0, json.dumps(payload).encode(), b"")

    store = SQLiteStore(tmp_path / "bridge.sqlite3")
    await store.initialize()
    try:
        sender = GoogleChatSender(
            client=MetaGoogleChatClient(
                executable=Path("meta"), space_name="spaces/s", runner=runner
            ),
            store=store,
            max_message_chars=5,
        )
        sent = await sender.send(
            session_id="conv",
            source_kind="status",
            source_id="response:failed",
            text="abcdefgh",
            thread_name="spaces/s/threads/t",
            mention_unixname="owner",
        )
        assert len(sent) == 2
        assert "--mention=owner" in calls[0]
        assert all("--mention=owner" not in call for call in calls[1:])
        assert calls[0] != calls[1]
    finally:
        store.close()


async def test_successful_send_triggers_immediate_poll(tmp_path: Path) -> None:
    triggered = 0

    async def runner(argv: list[str], stdin: bytes | None, timeout_seconds: float) -> ProcessResult:
        payload = raw_message(actor="users/bot", actor_type="BOT")
        return ProcessResult(0, json.dumps(payload).encode(), b"")

    def trigger() -> None:
        nonlocal triggered
        triggered += 1

    store = SQLiteStore(tmp_path / "bridge.sqlite3")
    await store.initialize()
    try:
        sender = GoogleChatSender(
            client=MetaGoogleChatClient(
                executable=Path("meta"), space_name="spaces/s", runner=runner
            ),
            store=store,
            max_message_chars=100,
            poll_trigger=trigger,
        )
        await sender.send(
            session_id="conv",
            source_kind="notice",
            source_id="one",
            text="notice",
            thread_name="spaces/s/threads/t",
        )
        assert triggered == 1
    finally:
        store.close()


def test_request_id_contracts() -> None:
    assert outbound_request_id(
        source_kind="root", session_id="conv", source_id="ignored", part_index=0
    ) == outbound_request_id(source_kind="root", session_id="conv", source_id="other", part_index=0)
    assert outbound_request_id(
        source_kind="item", session_id="conv", source_id="item", part_index=0
    ) != outbound_request_id(source_kind="item", session_id="conv", source_id="item", part_index=1)
