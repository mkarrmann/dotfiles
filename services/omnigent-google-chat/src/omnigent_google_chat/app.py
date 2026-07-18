from __future__ import annotations

import asyncio
import logging
import shutil
import signal
from pathlib import Path

from omnigent_google_chat.config import Settings
from omnigent_google_chat.discovery import SessionReconciler
from omnigent_google_chat.inbound import GoogleChatPoller, InboundProcessor
from omnigent_google_chat.meta_chat import GoogleChatSender, MetaChatError, MetaGoogleChatClient
from omnigent_google_chat.models import MappingState
from omnigent_google_chat.omnigent import OmnigentAuth, OmnigentClient
from omnigent_google_chat.store import SQLiteStore


async def run(settings: Settings) -> None:
    settings.validate_daemon_gate()
    _configure_logging(settings.log_level)
    logger = logging.getLogger(__name__)
    meta_executable = _resolve_meta_executable(str(settings.meta_cli))
    configured_host_id = (
        settings.omnigent_host_id if settings.omnigent_host_scope == "configured" else None
    )

    store = SQLiteStore(settings.database_path)
    await store.initialize()
    omnigent = OmnigentClient(
        base_url=settings.omnigent_base_url,
        auth=OmnigentAuth(
            email=settings.omnigent_auth_email,
            header_name=settings.omnigent_auth_header_name,
            session_cookie=settings.omnigent_session_cookie,
        ),
        timeout_seconds=settings.omnigent_timeout_seconds,
        configured_host_id=configured_host_id,
        runner_launch_timeout_seconds=settings.omnigent_runner_launch_timeout_seconds,
    )
    try:
        await store.bind_space(settings.space_name)
        if configured_host_id is not None:
            await omnigent.validate_host()
        sessions = await omnigent.list_sessions()
        if any(
            session.permission_level is not None and session.permission_level < 2
            for session in sessions
        ):
            logger.debug("Some sessions are read-only and will not be eligible for the bridge")

        chat = MetaGoogleChatClient(
            executable=meta_executable,
            space_name=settings.space_name,
            timeout_seconds=settings.chat_timeout_seconds,
        )
        sender = GoogleChatSender(
            client=chat,
            store=store,
            max_message_chars=settings.max_message_chars,
        )
        await _notify_restart_ambiguities(store, sender, logger)
        inbound = InboundProcessor(
            store=store,
            omnigent=omnigent,
            sender=sender,
            space_name=settings.space_name,
            allowed_actor_id=settings.allowed_actor_id,
            meta_bot_actor_id=settings.meta_bot_actor_id,
            max_input_chars=settings.max_input_chars,
        )

        reconciler_ref: SessionReconciler | None = None

        def has_active_sessions() -> bool:
            return reconciler_ref is not None and reconciler_ref.has_active_sessions()

        poller = GoogleChatPoller(
            client=chat,
            store=store,
            processor=inbound,
            active_poll_seconds=settings.active_poll_seconds,
            idle_poll_seconds=settings.idle_poll_seconds,
            overlap_seconds=settings.poll_overlap_seconds,
            health_stale_seconds=settings.health_stale_seconds,
            inbound_retention_seconds=settings.inbound_retention_days * 24 * 60 * 60,
            is_active=has_active_sessions,
        )
        sender.set_poll_trigger(poller.trigger)
        member_actor_ids = await _list_member_actor_ids_with_retry(chat)
        if settings.allowed_actor_id not in member_actor_ids:
            raise ValueError(
                "the allowlisted Google Chat actor is not a human member of the configured space"
            )
        reconciler = SessionReconciler(
            store=store,
            omnigent=omnigent,
            sender=sender,
            space_name=settings.space_name,
            host_id=configured_host_id,
            discovery_mode=settings.discovery,
            discovery_label=settings.discovery_label,
            lookback_hours=settings.session_lookback_hours,
            interval_seconds=settings.discovery_interval_seconds,
            recent_active_seconds=settings.recent_active_seconds,
            mirror_mode=settings.mirror_mode,
            mention_unixname=settings.mention_unixname,
            mention_on_root=settings.mention_on_root,
            mention_on_completion=settings.mention_on_completion,
            meta_bot_actor_id=settings.meta_bot_actor_id,
            max_session_chars=settings.max_session_chars,
            poll_trigger=poller.trigger,
        )
        reconciler_ref = reconciler

        await poller.poll_once()
        stop = asyncio.Event()
        _install_signal_handlers(stop)
        logger.info(
            "Google Chat bridge started space=%s discovery=%s host_scope=%s database=%s",
            settings.space_name,
            settings.discovery,
            settings.omnigent_host_scope,
            settings.database_path,
        )
        tasks = {
            asyncio.create_task(poller.run(stop), name="gchat-poller"),
            asyncio.create_task(reconciler.run(stop), name="session-reconciler"),
        }
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        shutdown_requested = stop.is_set()
        stop.set()
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            error = task.exception()
            if error is not None:
                raise error
        if not shutdown_requested:
            raise RuntimeError("a bridge component exited unexpectedly")
    finally:
        await omnigent.aclose()
        store.close()


def _resolve_meta_executable(executable: str) -> Path:
    resolved = shutil.which(executable)
    if resolved is None:
        raise ValueError(f"META_CLI is not executable: {executable}")
    return Path(resolved).resolve()


async def _list_member_actor_ids_with_retry(
    chat: MetaGoogleChatClient, *, attempts: int = 3
) -> set[str]:
    last_error: MetaChatError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await chat.list_member_actor_ids()
        except MetaChatError as exc:
            last_error = exc
            if attempt < attempts:
                await asyncio.sleep(2 ** (attempt - 1))
    assert last_error is not None
    raise last_error


async def _notify_restart_ambiguities(
    store: SQLiteStore, sender: GoogleChatSender, logger: logging.Logger
) -> None:
    for message_name, thread_name in await store.list_restart_ambiguous():
        mapping = await store.get_thread_by_name(thread_name)
        if mapping is None or mapping.state is not MappingState.ACTIVE:
            logger.error(
                "Ambiguous inbound cannot be reported name=%s thread=%s",
                message_name,
                thread_name,
            )
            continue
        logger.warning(
            "Reporting ambiguous inbound after restart name=%s session_id=%s",
            message_name,
            mapping.omnigent_session_id,
        )
        try:
            await sender.send(
                session_id=mapping.omnigent_session_id,
                source_kind="notice",
                source_id=f"{message_name}:restart-ambiguous",
                text=(
                    "A phone instruction had uncertain delivery when the bridge restarted. "
                    "Check the Omnigent transcript before resending it."
                ),
                thread_name=thread_name,
            )
        except MetaChatError:
            logger.error(
                "Could not report ambiguous inbound name=%s",
                message_name,
                exc_info=True,
            )
            continue
        await store.mark_restart_ambiguous_notified(message_name)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
        except NotImplementedError:
            pass
