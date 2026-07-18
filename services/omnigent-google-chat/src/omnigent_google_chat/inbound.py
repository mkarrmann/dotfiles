from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from omnigent_google_chat.meta_chat import GoogleChatSender, MetaChatError, MetaGoogleChatClient
from omnigent_google_chat.models import GoogleChatMessage, InboundState, MappingState
from omnigent_google_chat.omnigent import (
    OmnigentAmbiguousDeliveryError,
    OmnigentClient,
    OmnigentPreDeliveryError,
    OmnigentRejectedError,
    RunnerUnavailableError,
)
from omnigent_google_chat.store import SQLiteStore
from omnigent_google_chat.text import BRIDGE_PREFIX, format_status, normalize_input, text_sha256


class InboundProcessor:
    def __init__(
        self,
        *,
        store: SQLiteStore,
        omnigent: OmnigentClient,
        sender: GoogleChatSender,
        space_name: str,
        allowed_actor_id: str,
        meta_bot_actor_id: str,
        max_input_chars: int,
        pre_delivery_attempts: int = 3,
    ) -> None:
        self._store = store
        self._omnigent = omnigent
        self._sender = sender
        self._space_name = space_name
        self._allowed_actor_id = allowed_actor_id
        self._meta_bot_actor_id = meta_bot_actor_id
        self._max_input_chars = max_input_chars
        self._pre_delivery_attempts = pre_delivery_attempts
        self._logger = logging.getLogger(__name__)

    async def process(self, message: GoogleChatMessage) -> None:
        digest = text_sha256(message.text)
        claim = await self._store.claim_inbound(
            message_name=message.name,
            thread_name=message.thread_name,
            actor_id=message.actor_id,
            created_at_google=message.create_time,
            text_sha256=digest,
        )
        if not claim.claimed:
            if claim.changed_content:
                self._logger.error(
                    "Google Chat message changed after claim name=%s; ignoring replay",
                    message.name,
                )
            return

        rejection = await self._validate(message)
        if rejection:
            await self._reject(message, rejection)
            notice = _rejection_notice(rejection)
            if notice is not None:
                mapping = await self._store.get_thread_by_name(message.thread_name)
                if (
                    mapping is not None
                    and mapping.state is MappingState.ACTIVE
                    and message.actor_id == self._allowed_actor_id
                    and message.actor_type.upper() == "HUMAN"
                ):
                    await self._notice(
                        message,
                        mapping.omnigent_session_id,
                        "input-rejected",
                        notice,
                    )
            return

        mapping = await self._store.get_thread_by_name(message.thread_name)
        assert mapping is not None
        text = normalize_input(message.text)
        if text.startswith("!"):
            await self._handle_command(message, mapping.omnigent_session_id, text)
            return
        await self._dispatch_message(message, mapping.omnigent_session_id, text)

    async def _validate(self, message: GoogleChatMessage) -> str | None:
        if message.space_name != self._space_name:
            return "message belongs to an unconfigured space"
        if message.actor_id == self._meta_bot_actor_id or message.actor_type.upper() != "HUMAN":
            return "message is not authored by the allowlisted human"
        if message.actor_id != self._allowed_actor_id:
            return "message actor is not allowlisted"
        if await self._store.is_outbound_message(message.name):
            return "message is bridge output"
        if _is_forwarded_or_cross_posted(message.raw):
            return "forwarded or cross-posted messages are not accepted"
        mapping = await self._store.get_thread_by_name(message.thread_name)
        if mapping is None or mapping.state is not MappingState.ACTIVE:
            return "message thread has no active Omnigent mapping"
        if message.has_attachments:
            return "attachments are not supported"
        text = normalize_input(message.text)
        if not text:
            return "message has no supported text"
        if len(text) > self._max_input_chars:
            return f"message exceeds the {self._max_input_chars}-character input limit"
        if text.startswith(BRIDGE_PREFIX):
            return "message has the bridge output prefix"
        return None

    async def _handle_command(
        self, message: GoogleChatMessage, session_id: str, command: str
    ) -> None:
        if command == "!status":
            try:
                session = await self._omnigent.get_session(session_id)
                await self._store.set_inbound_state(message.name, InboundState.SUBMITTED)
                await self._notice(
                    message,
                    session_id,
                    "status",
                    format_status(session),
                )
            except Exception as exc:
                await self._store.set_inbound_state(
                    message.name, InboundState.REJECTED, error=str(exc)
                )
                await self._notice(message, session_id, "status-error", "Unable to read status.")
            return
        if command == "!detach":
            await self._store.set_thread_state(session_id, MappingState.DETACHED)
            await self._store.set_inbound_state(message.name, InboundState.SUBMITTED)
            await self._notice(
                message,
                session_id,
                "detached",
                f"Detached session {session_id}. Future thread input and output are disabled.",
            )
            return
        if command == "!stop":
            await self._dispatch_event(
                message,
                session_id,
                dispatch=lambda: self._omnigent.interrupt(session_id),
                success_notice=f"Interrupt requested for session {session_id}.",
            )
            return
        await self._reject(message, f"unknown command: {command.split()[0]}")
        await self._notice(
            message,
            session_id,
            "unknown-command",
            "Unknown command. Supported commands: !status, !stop, !detach.",
        )

    async def _dispatch_message(
        self, message: GoogleChatMessage, session_id: str, text: str
    ) -> None:
        async def submit() -> None:
            item_id = await self._omnigent.submit_message(
                session_id,
                text,
                source_message_name=message.name,
            )
            if item_id:
                await self._store.set_inbound_state(
                    message.name,
                    InboundState.DISPATCHING,
                    omnigent_item_id=item_id,
                )

        await self._dispatch_event(message, session_id, dispatch=submit)

    async def _dispatch_event(
        self,
        message: GoogleChatMessage,
        session_id: str,
        *,
        dispatch: Callable[[], Awaitable[None]],
        success_notice: str | None = None,
    ) -> None:
        await self._store.set_inbound_state(message.name, InboundState.DISPATCHING)
        last_pre_delivery: OmnigentPreDeliveryError | None = None
        runner_recovery_attempted = False
        for attempt in range(1, self._pre_delivery_attempts + 1):
            try:
                await dispatch()
                await self._store.set_inbound_state(message.name, InboundState.SUBMITTED)
                if success_notice:
                    await self._notice(message, session_id, "command-success", success_notice)
                return
            except RunnerUnavailableError as exc:
                if runner_recovery_attempted or attempt >= self._pre_delivery_attempts:
                    await self._reject(message, str(exc))
                    await self._notice(
                        message,
                        session_id,
                        "runner-unavailable",
                        "The session runner is unavailable; the instruction was not delivered.",
                    )
                    return
                try:
                    await self._omnigent.recover_bound_runner(session_id)
                    runner_recovery_attempted = True
                except Exception as exc:
                    await self._reject(message, f"runner recovery failed: {exc}")
                    await self._notice(
                        message,
                        session_id,
                        "runner-unavailable",
                        "The session runner is unavailable; the instruction was not delivered.",
                    )
                    return
                continue
            except OmnigentPreDeliveryError as exc:
                last_pre_delivery = exc
                if attempt < self._pre_delivery_attempts:
                    await asyncio.sleep(min(2 ** (attempt - 1), 4))
                    continue
            except OmnigentRejectedError as exc:
                await self._reject(message, str(exc))
                await self._notice(
                    message,
                    session_id,
                    "rejected",
                    "Omnigent rejected the instruction; it was not delivered.",
                )
                return
            except OmnigentAmbiguousDeliveryError as exc:
                await self._store.set_inbound_state(
                    message.name, InboundState.AMBIGUOUS, error=str(exc)
                )
                await self._notice(
                    message,
                    session_id,
                    "ambiguous",
                    "Delivery is uncertain and will not be retried automatically. "
                    "Check Omnigent before resending.",
                )
                return
        assert last_pre_delivery is not None
        await self._reject(message, str(last_pre_delivery))
        await self._notice(
            message,
            session_id,
            "unreachable",
            "Could not connect to Omnigent; the instruction was not delivered.",
        )

    async def _reject(self, message: GoogleChatMessage, reason: str) -> None:
        await self._store.set_inbound_state(message.name, InboundState.REJECTED, error=reason)
        self._logger.warning("Rejected Google Chat input name=%s reason=%s", message.name, reason)

    async def _notice(
        self,
        message: GoogleChatMessage,
        session_id: str,
        notice_kind: str,
        text: str,
    ) -> None:
        try:
            await self._sender.send(
                session_id=session_id,
                source_kind="notice",
                source_id=f"{message.name}:{notice_kind}",
                text=text,
                thread_name=message.thread_name,
            )
        except MetaChatError:
            self._logger.warning(
                "Could not post Google Chat notice name=%s kind=%s",
                message.name,
                notice_kind,
                exc_info=True,
            )


class GoogleChatPoller:
    def __init__(
        self,
        *,
        client: MetaGoogleChatClient,
        store: SQLiteStore,
        processor: InboundProcessor,
        active_poll_seconds: float,
        idle_poll_seconds: float,
        overlap_seconds: int,
        health_stale_seconds: float,
        inbound_retention_seconds: int,
        is_active: Callable[[], bool],
    ) -> None:
        self._client = client
        self._store = store
        self._processor = processor
        self._active_poll_seconds = active_poll_seconds
        self._idle_poll_seconds = idle_poll_seconds
        self._overlap_seconds = overlap_seconds
        self._health_stale_seconds = health_stale_seconds
        self._inbound_retention_seconds = inbound_retention_seconds
        self._is_active = is_active
        self._immediate = asyncio.Event()
        self._last_success: float | None = None
        self._logger = logging.getLogger(__name__)

    def trigger(self) -> None:
        self._immediate.set()

    async def run(self, stop: asyncio.Event) -> None:
        failures = 0
        while not stop.is_set():
            try:
                await self.poll_once()
                self._last_success = asyncio.get_running_loop().time()
                failures = 0
                interval = (
                    self._active_poll_seconds if self._is_active() else self._idle_poll_seconds
                )
            except Exception:
                failures += 1
                interval = min(2 ** min(failures, 6), 60)
                self._logger.warning(
                    "Google Chat poll failed; cursor was not advanced", exc_info=True
                )
            await self._wait(stop, interval + random.uniform(0, min(interval * 0.05, 1)))

    async def poll_once(self) -> int:
        cursor = await self._store.get_poll_cursor()
        floor = await self._store.get_state("gchat_poll_floor")
        if cursor is None or floor is None:
            now = _utc_now()
            cursor = (now, "")
            floor = now
            await self._store.set_state("gchat_poll_floor", floor)
            await self._store.set_poll_cursor(cursor)

        created_after = _subtract_seconds(cursor[0], self._overlap_seconds)
        messages = await self._client.list_all_messages(created_after=created_after)
        eligible = [message for message in messages if message.create_time >= floor]
        for message in eligible:
            await self._processor.process(message)

        high_water = cursor
        if eligible:
            high_water = max(cursor, max(message.ordering_key for message in eligible))
        await self._store.set_poll_cursor(high_water)
        await self._store.prune_inbound(self._inbound_retention_seconds)
        return len(eligible)

    async def _wait(self, stop: asyncio.Event, seconds: float) -> None:
        if self._immediate.is_set():
            self._immediate.clear()
            return
        stop_task = asyncio.create_task(stop.wait())
        immediate_task = asyncio.create_task(self._immediate.wait())
        try:
            done, _ = await asyncio.wait(
                {stop_task, immediate_task},
                timeout=seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done and self._last_success is not None:
                stale = asyncio.get_running_loop().time() - self._last_success
                if stale > self._health_stale_seconds:
                    self._logger.error("Google Chat polling is stale by %.1f seconds", stale)
            if immediate_task in done:
                self._immediate.clear()
        finally:
            stop_task.cancel()
            immediate_task.cancel()
            await asyncio.gather(stop_task, immediate_task, return_exceptions=True)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _subtract_seconds(value: str, seconds: int) -> str:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (
        (parsed - timedelta(seconds=seconds))
        .astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _is_forwarded_or_cross_posted(payload: dict[str, object]) -> bool:
    metadata_keys = {
        "crossPostMetadata",
        "cross_post_metadata",
        "forwardedMessage",
        "forwardedMessageMetadata",
        "forwarded_message",
        "forwarded_message_metadata",
    }
    return any(key in payload for key in metadata_keys)


def _rejection_notice(reason: str) -> str | None:
    if reason == "attachments are not supported":
        return "Attachments are not supported. Send a text-only instruction."
    if reason.startswith("message exceeds the "):
        return "That instruction is too long for the mobile bridge. Send a shorter message."
    if reason == "message has no supported text":
        return "No supported text was found. Send a text-only instruction."
    return None
