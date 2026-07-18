from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omnigent_google_chat.models import (
    GoogleChatMessage,
    GoogleChatPage,
    OutboundState,
    SentMessage,
)
from omnigent_google_chat.store import SQLiteStore
from omnigent_google_chat.text import BRIDGE_PREFIX, split_message, stable_request_id

MAX_PROCESS_OUTPUT_BYTES = 8 * 1024 * 1024
ProcessRunner = Callable[[list[str], bytes | None, float], Awaitable["ProcessResult"]]
PollTrigger = Callable[[], None]
_monotonic = time.monotonic


class MetaChatError(RuntimeError):
    pass


class MetaChatTimeoutError(MetaChatError):
    pass


class MetaChatOutputError(MetaChatError):
    pass


@dataclass(frozen=True, slots=True)
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    stdout_truncated: bool = False
    stderr_truncated: bool = False


class MetaGoogleChatClient:
    def __init__(
        self,
        *,
        executable: Path,
        space_name: str,
        timeout_seconds: float = 30.0,
        runner: ProcessRunner | None = None,
    ) -> None:
        self._executable = str(executable)
        self.space_name = space_name
        self._timeout_seconds = timeout_seconds
        self._runner = runner or run_process
        self._logger = logging.getLogger(__name__)

    async def send_message(
        self,
        *,
        text: str,
        request_id: str,
        thread_name: str | None = None,
        mention_unixname: str | None = None,
        dry_run: bool = False,
    ) -> SentMessage:
        argv = [
            self._executable,
            "google.chat.message",
            "send",
            f"--space-name={self.space_name}",
            "--as-meta-bot",
            f"--request-id={request_id}",
            f"--message-prefix={BRIDGE_PREFIX}",
            "--stdin",
            "--raw-json",
            "--no-color",
        ]
        if thread_name:
            argv.append(f"--reply-in-thread={thread_name}")
        if mention_unixname:
            argv.append(f"--mention={mention_unixname}")
        if dry_run:
            argv.append("--dry-run")

        self._logger.debug(
            "Sending Google Chat message request_id=%s threaded=%s chars=%s mention=%s",
            request_id,
            thread_name is not None,
            len(text),
            mention_unixname is not None,
        )
        payload = await self._invoke(argv, text.encode("utf-8"))
        return parse_sent_message(payload, expected_thread=thread_name)

    async def list_page(
        self,
        *,
        created_after: str,
        page_token: str | None = None,
        limit: int = 200,
    ) -> GoogleChatPage:
        argv = [
            self._executable,
            "google.chat.message",
            "list",
            f"--space-name={self.space_name}",
            f"--created-after={created_after}",
            "--oldest",
            f"--limit={limit}",
            "--raw-json",
            "--skip-cache",
            "--no-color",
        ]
        if page_token:
            argv.append(f"--page-token={page_token}")
        payload = await self._invoke(argv)
        return parse_message_page(payload, expected_space=self.space_name)

    async def list_all_messages(self, *, created_after: str) -> list[GoogleChatMessage]:
        messages: list[GoogleChatMessage] = []
        page_token: str | None = None
        seen_tokens: set[str] = set()
        while True:
            page = await self.list_page(created_after=created_after, page_token=page_token)
            messages.extend(page.messages)
            page_token = page.next_page_token
            if not page_token:
                break
            if page_token in seen_tokens:
                raise MetaChatOutputError("Google Chat returned a repeated page token")
            seen_tokens.add(page_token)
        deduplicated = {message.name: message for message in messages}
        return sorted(deduplicated.values(), key=lambda message: message.ordering_key)

    async def list_member_actor_ids(self) -> set[str]:
        argv = [
            self._executable,
            "google.chat.member",
            "list",
            f"--space-name={self.space_name}",
            "--limit=200",
            "--output=json",
            "--no-color",
        ]
        payload = await self._invoke(argv)
        if isinstance(payload, dict):
            members = payload.get("members", payload.get("data", []))
        else:
            members = payload
        if not isinstance(members, list):
            raise MetaChatOutputError("member list response must be a list")
        actor_ids: set[str] = set()
        for member in members:
            if not isinstance(member, dict) or str(member.get("type", "")).upper() != "HUMAN":
                continue
            name = member.get("name")
            if isinstance(name, str) and "/members/" in name:
                actor_ids.add(f"users/{name.rsplit('/', 1)[-1]}")
            for key in ("user_id", "email"):
                value = member.get(key)
                if isinstance(value, str) and value:
                    actor_id = value if key == "email" else f"users/{value.removeprefix('users/')}"
                    actor_ids.add(actor_id)
        return actor_ids

    async def _invoke(self, argv: list[str], stdin: bytes | None = None) -> Any:
        started = _monotonic()
        action = " ".join(argv[1:3])
        try:
            result = await self._runner(argv, stdin, self._timeout_seconds)
        except TimeoutError as exc:
            elapsed = _monotonic() - started
            self._logger.warning(
                "Meta CLI timed out action=%s duration_seconds=%.3f",
                action,
                elapsed,
            )
            raise MetaChatTimeoutError(
                f"meta command timed out after {self._timeout_seconds:g}s"
            ) from exc
        elapsed = _monotonic() - started
        if elapsed >= 5:
            self._logger.warning(
                "Meta CLI was slow action=%s duration_seconds=%.3f returncode=%s",
                action,
                elapsed,
                result.returncode,
            )
        else:
            self._logger.debug(
                "Meta CLI completed action=%s duration_seconds=%.3f returncode=%s",
                action,
                elapsed,
                result.returncode,
            )
        if result.stdout_truncated or result.stderr_truncated:
            raise MetaChatOutputError("meta command output exceeded the configured bound")
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").replace("\n", " ")[:1000]
            raise MetaChatError(f"meta command failed with {result.returncode}: {stderr}")
        try:
            return _parse_json_output(result.stdout)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise MetaChatOutputError("meta command did not return valid JSON") from exc


class GoogleChatSender:
    def __init__(
        self,
        *,
        client: MetaGoogleChatClient,
        store: SQLiteStore,
        max_message_chars: int,
        max_attempts: int = 3,
        poll_trigger: PollTrigger | None = None,
    ) -> None:
        self._client = client
        self._store = store
        self._max_message_chars = max_message_chars
        self._max_attempts = max_attempts
        self._poll_trigger = poll_trigger
        self._logger = logging.getLogger(__name__)

    def set_poll_trigger(self, trigger: PollTrigger) -> None:
        self._poll_trigger = trigger

    async def send(
        self,
        *,
        session_id: str,
        source_kind: str,
        source_id: str,
        text: str,
        thread_name: str | None,
        mention_unixname: str | None = None,
    ) -> list[SentMessage]:
        chunks = split_message(text, self._max_message_chars)
        sent: list[SentMessage] = []
        for part_index, chunk in enumerate(chunks):
            request_id = outbound_request_id(
                source_kind=source_kind,
                session_id=session_id,
                source_id=source_id,
                part_index=part_index,
            )
            state = await self._store.prepare_outbound(
                request_id=request_id,
                session_id=session_id,
                source_kind=source_kind,
                source_id=source_id,
                part_index=part_index,
                char_count=len(chunk),
            )
            cached_name = await self._store.get_outbound_message_name(request_id)
            if state is OutboundState.SENT and cached_name and thread_name:
                sent.append(SentMessage(name=cached_name, thread_name=thread_name))
                continue
            message = await self._send_with_retry(
                text=chunk,
                request_id=request_id,
                thread_name=thread_name,
                mention_unixname=mention_unixname if part_index == 0 else None,
            )
            sent.append(message)
        return sent

    async def _send_with_retry(
        self,
        *,
        text: str,
        request_id: str,
        thread_name: str | None,
        mention_unixname: str | None,
    ) -> SentMessage:
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            await self._store.mark_outbound_attempt(request_id)
            try:
                message = await self._client.send_message(
                    text=text,
                    request_id=request_id,
                    thread_name=thread_name,
                    mention_unixname=mention_unixname,
                )
                await self._store.mark_outbound_sent(request_id, message.name)
                if self._poll_trigger is not None:
                    self._poll_trigger()
                return message
            except (MetaChatError, MetaChatTimeoutError) as exc:
                last_error = exc
                await self._store.mark_outbound_failed(request_id, str(exc))
                if attempt < self._max_attempts:
                    delay = min(2 ** (attempt - 1), 8) + random.uniform(0, 0.25)
                    self._logger.warning(
                        "Google Chat send failed request_id=%s attempt=%s; retrying",
                        request_id,
                        attempt,
                    )
                    await asyncio.sleep(delay)
        assert last_error is not None
        raise last_error


async def run_process(
    argv: list[str], stdin: bytes | None, timeout_seconds: float
) -> ProcessResult:
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    stdout_task = asyncio.create_task(_read_bounded(process.stdout))
    stderr_task = asyncio.create_task(_read_bounded(process.stderr))
    if stdin is not None:
        assert process.stdin is not None
        process.stdin.write(stdin)
        await process.stdin.drain()
        process.stdin.close()
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
    except TimeoutError:
        process.kill()
        await process.wait()
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        raise
    stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
    return ProcessResult(
        returncode=process.returncode or 0,
        stdout=stdout[0],
        stderr=stderr[0],
        stdout_truncated=stdout[1],
        stderr_truncated=stderr[1],
    )


async def _read_bounded(
    stream: asyncio.StreamReader, limit: int = MAX_PROCESS_OUTPUT_BYTES
) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    size = 0
    truncated = False
    while chunk := await stream.read(64 * 1024):
        remaining = limit - size
        if remaining > 0:
            chunks.append(chunk[:remaining])
            size += min(len(chunk), remaining)
        if len(chunk) > remaining:
            truncated = True
    return b"".join(chunks), truncated


def parse_sent_message(payload: Any, *, expected_thread: str | None = None) -> SentMessage:
    candidate = _unwrap_message(payload)
    name = candidate.get("name")
    thread = candidate.get("thread")
    thread_name = thread.get("name") if isinstance(thread, dict) else thread
    sender = candidate.get("sender")
    actor_id = sender.get("name") if isinstance(sender, dict) else None
    actor_type = sender.get("type") if isinstance(sender, dict) else None
    if not isinstance(name, str) or not name:
        raise MetaChatOutputError("send response is missing message name")
    if not isinstance(thread_name, str) or not thread_name:
        raise MetaChatOutputError("send response is missing thread name")
    if expected_thread is not None and thread_name != expected_thread:
        raise MetaChatOutputError(
            f"send response thread {thread_name!r} does not match {expected_thread!r}"
        )
    return SentMessage(
        name=name,
        thread_name=thread_name,
        actor_id=actor_id if isinstance(actor_id, str) else None,
        actor_type=actor_type if isinstance(actor_type, str) else None,
    )


def parse_message_page(payload: Any, *, expected_space: str) -> GoogleChatPage:
    raw_messages: Any
    if isinstance(payload, list):
        raw_messages = payload
        next_page_token = None
    elif isinstance(payload, dict):
        raw_messages = payload.get("messages", payload.get("data", []))
        next_page_token = payload.get("nextPageToken") or payload.get("next_page_token")
    else:
        raise MetaChatOutputError("message list response must be an object or list")
    if not isinstance(raw_messages, list):
        raise MetaChatOutputError("message list response has non-list messages")
    messages = [parse_chat_message(item, expected_space=expected_space) for item in raw_messages]
    if next_page_token is not None and not isinstance(next_page_token, str):
        raise MetaChatOutputError("message list next page token is not a string")
    return GoogleChatPage(messages=messages, next_page_token=next_page_token or None)


def parse_chat_message(payload: Any, *, expected_space: str) -> GoogleChatMessage:
    if not isinstance(payload, dict):
        raise MetaChatOutputError("Google Chat message must be an object")
    name = payload.get("name")
    thread = payload.get("thread")
    thread_name = thread.get("name") if isinstance(thread, dict) else thread
    space = payload.get("space")
    space_name = space.get("name") if isinstance(space, dict) else space
    sender = payload.get("sender")
    actor_id = sender.get("name") if isinstance(sender, dict) else None
    actor_type = sender.get("type") if isinstance(sender, dict) else None
    create_time = payload.get("createTime") or payload.get("create_time")
    text = payload.get("text")
    for field_name, value in (
        ("name", name),
        ("thread.name", thread_name),
        ("sender.name", actor_id),
        ("sender.type", actor_type),
        ("createTime", create_time),
    ):
        if not isinstance(value, str) or not value:
            raise MetaChatOutputError(f"Google Chat message is missing {field_name}")
    assert isinstance(name, str)
    assert isinstance(thread_name, str)
    assert isinstance(actor_id, str)
    assert isinstance(actor_type, str)
    assert isinstance(create_time, str)
    canonical_create_time = _canonical_timestamp(create_time)
    if space_name is None:
        space_name = expected_space
    if space_name != expected_space:
        raise MetaChatOutputError(
            f"message {name!r} belongs to {space_name!r}, expected {expected_space!r}"
        )
    attachments = payload.get("attachments", payload.get("attachment", []))
    return GoogleChatMessage(
        name=name,
        space_name=space_name,
        thread_name=thread_name,
        actor_id=actor_id,
        actor_type=actor_type,
        create_time=canonical_create_time,
        text=text if isinstance(text, str) else "",
        has_attachments=isinstance(attachments, list) and bool(attachments),
        raw=payload,
    )


def outbound_request_id(
    *, source_kind: str, session_id: str, source_id: str, part_index: int
) -> str:
    if source_kind == "root":
        source = f"root:{session_id}"
    elif source_kind == "item":
        source = f"item:{session_id}:{source_id}:{part_index}"
    elif source_kind == "status":
        source = f"status:{session_id}:{source_id}"
        if part_index:
            source += f":{part_index}"
    else:
        source = f"{source_kind}:{session_id}:{source_id}:{part_index}"
    return stable_request_id(source)


def _unwrap_message(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), dict):
            return _unwrap_message(payload["data"])
        if isinstance(payload.get("message"), dict):
            return _unwrap_message(payload["message"])
        return payload
    raise MetaChatOutputError("send response must be an object")


def _parse_json_output(stdout: bytes) -> Any:
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        # `message send --raw-json` emits a display summary before one JSON line.
        final_line = stdout.rstrip().rsplit(b"\n", 1)[-1]
        return json.loads(final_line)


def _canonical_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MetaChatOutputError("Google Chat message has an invalid createTime") from exc
    if parsed.tzinfo is None:
        raise MetaChatOutputError("Google Chat message createTime has no timezone")
    return parsed.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
