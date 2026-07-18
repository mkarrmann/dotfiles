from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable

from omnigent_google_chat.models import SessionSummary

BRIDGE_NAMESPACE = uuid.UUID("b88bc543-2d7f-55f4-b26f-99807a85d793")
BRIDGE_PREFIX = "[Omnigent]"
CONTROL_PREFIX = "!"


def stable_request_id(source: str) -> str:
    return str(uuid.uuid5(BRIDGE_NAMESPACE, source))


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_input(text: str) -> str:
    return text.strip()


def short_workspace(workspace: str | None) -> str:
    if not workspace:
        return "unknown"
    parts = [part for part in workspace.rstrip("/").split("/") if part]
    return "/".join(parts[-2:]) if len(parts) >= 2 else workspace


def format_root(session: SessionSummary) -> str:
    return "\n".join(
        (
            session.title,
            f"Workspace: {short_workspace(session.workspace)}",
            f"Session: {session.id}",
            f"Status: {session.status}",
            "",
            "Reply in this thread to message the same agent.",
        )
    )


def format_status(session: SessionSummary) -> str:
    details = [f"Status: {session.status}"]
    if session.pending_elicitations_count:
        details.append(f"Approvals waiting: {session.pending_elicitations_count}")
    if session.runner_online is not None:
        details.append(f"Runner online: {'yes' if session.runner_online else 'no'}")
    return "\n".join(details)


def item_id(item: dict[str, object]) -> str | None:
    for key in ("id", "item_id"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def item_role(item: dict[str, object]) -> str | None:
    data = item.get("data")
    source = data if isinstance(data, dict) else item
    role = source.get("role")
    return role if isinstance(role, str) else None


def item_text(item: dict[str, object]) -> str | None:
    if item.get("type") != "message":
        return None
    data = item.get("data")
    source = data if isinstance(data, dict) else item
    content = source.get("content")
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type not in (None, "input_text", "output_text", "text"):
            continue
        value = block.get("text")
        if isinstance(value, str):
            parts.append(value)
    result = "".join(parts).strip()
    return result or None


def format_user_item(item: dict[str, object]) -> str | None:
    text = item_text(item)
    if not text:
        return None
    data = item.get("data")
    source = data if isinstance(data, dict) else item
    actor = source.get("actor") or source.get("user") or source.get("author")
    attribution = actor if isinstance(actor, str) and actor else "Omnigent client"
    return f"{attribution}: {text}"


def split_message(text: str, limit: int) -> list[str]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    if not text:
        return [""]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = _preferred_cut(window)
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    chunks.append(remaining)
    return chunks


def _preferred_cut(window: str) -> int:
    for delimiter in ("\n\n", "\n", " "):
        position = window.rfind(delimiter)
        if position > 0:
            return position + len(delimiter)
    return len(window)


def total_chars(chunks: Iterable[str]) -> int:
    return sum(len(chunk) for chunk in chunks)
