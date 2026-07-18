from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from omnigent_google_chat.app import _resolve_meta_executable
from omnigent_google_chat.config import PhaseZeroSettings
from omnigent_google_chat.meta_chat import MetaChatOutputError, MetaGoogleChatClient
from omnigent_google_chat.text import stable_request_id

InputFunction = Callable[[str], str]


async def run_phase_zero(
    settings: PhaseZeroSettings, *, input_function: InputFunction = input
) -> None:
    client = MetaGoogleChatClient(
        executable=_resolve_meta_executable(str(settings.meta_cli)),
        space_name=settings.space_name,
        timeout_seconds=settings.chat_timeout_seconds,
    )
    started_at = datetime.now(UTC) - timedelta(minutes=1)
    scope = f"phase0:{settings.space_name}"
    root_request_id = stable_request_id(f"{scope}:root")
    root = await client.send_message(
        text="Phase 0 transport probe. Synthetic content only.",
        request_id=root_request_id,
        mention_unixname=settings.mention_unixname,
    )
    if root.actor_type is not None and root.actor_type.upper() == "HUMAN":
        raise MetaChatOutputError("--as-meta-bot produced a human sender")
    print(f"Root: {root.name}")
    print(f"Thread: {root.thread_name}")
    print(f"Meta Bot actor: {root.actor_id or 'not present in send response'}")
    root_notified = await _confirm(
        input_function,
        "With Chat backgrounded or the phone locked, did the root notify? [y/N] ",
    )

    await _prompt(
        input_function,
        "Follow the new thread on the phone, then press Enter to send an unmentioned reply. ",
    )
    await client.send_message(
        text="Phase 0 unmentioned thread notification probe.",
        request_id=stable_request_id(f"{scope}:unmentioned"),
        thread_name=root.thread_name,
    )
    unmentioned_notified = await _confirm(
        input_function,
        "With Chat still backgrounded or locked, did that reply notify? [y/N] ",
    )

    await _prompt(
        input_function,
        "Press Enter to send the required real self-mention notification probe. ",
    )
    await client.send_message(
        text="Phase 0 mentioned thread notification probe.",
        request_id=stable_request_id(f"{scope}:mentioned"),
        thread_name=root.thread_name,
        mention_unixname=settings.mention_unixname,
    )
    mentioned_notified = await _confirm(
        input_function,
        "With Chat backgrounded or locked, did the mentioned reply notify? [y/N] ",
    )
    if not mentioned_notified:
        raise RuntimeError("Phase 0 failed: a real Meta Bot mention did not notify the phone")

    await _prompt(
        input_function,
        "Reply to the probe thread from the phone, then press Enter to fetch it. ",
    )
    start = time.monotonic()
    messages = await client.list_all_messages(
        created_after=started_at.isoformat().replace("+00:00", "Z")
    )
    list_seconds = time.monotonic() - start
    thread_messages = [message for message in messages if message.thread_name == root.thread_name]
    human_messages = [
        message for message in thread_messages if message.actor_type.upper() == "HUMAN"
    ]
    if not human_messages:
        raise RuntimeError("Phase 0 failed: no human phone reply was visible in the raw list")
    human_actor_ids = sorted({message.actor_id for message in human_messages})

    retried_root = await client.send_message(
        text="Phase 0 transport probe. Synthetic content only.",
        request_id=root_request_id,
        mention_unixname=settings.mention_unixname,
    )
    if retried_root.name != root.name or retried_root.thread_name != root.thread_name:
        raise RuntimeError("Phase 0 failed: retrying the request ID created a different root")

    bot_messages = [message for message in thread_messages if message.actor_type.upper() != "HUMAN"]
    bot_actor_ids = sorted({message.actor_id for message in bot_messages})
    if not bot_actor_ids:
        raise RuntimeError("Phase 0 failed: raw listing did not expose a distinct bot actor")
    if set(bot_actor_ids) & set(human_actor_ids):
        raise RuntimeError("Phase 0 failed: bot and human actor identities overlap")

    print("\nPhase 0 passed.")
    print(f"Root push notification: {'yes' if root_notified else 'no'}")
    print(f"Unmentioned thread push: {'yes' if unmentioned_notified else 'no'}")
    print("Mentioned thread push: yes")
    print(f"Human actor ID(s): {', '.join(human_actor_ids)}")
    print(f"Meta Bot actor ID(s): {', '.join(bot_actor_ids)}")
    print(f"Uncached message-list latency: {list_seconds:.2f}s")
    if list_seconds >= 10:
        print("Warning: list latency is at or above the intended active poll interval.")
    print("Set OMNIGENT_GCHAT_PHASE0_VALIDATED=true only after reviewing these values.")


async def _confirm(input_function: InputFunction, prompt: str) -> bool:
    answer = await asyncio.to_thread(input_function, prompt)
    return answer.strip().lower() in {"y", "yes"}


async def _prompt(input_function: InputFunction, prompt: str) -> None:
    await asyncio.to_thread(input_function, prompt)
