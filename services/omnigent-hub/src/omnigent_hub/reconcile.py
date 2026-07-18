from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import time
import urllib.request
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from omnigent_hub.config import HubConfig, parse_env_file
from omnigent_hub.runtime import check_gate, service_action

MetaRunner = Callable[[list[str]], dict[str, Any]]


class ReconcileError(RuntimeError):
    pass


def reconcile_gchat(
    config: HubConfig,
    *,
    resubmit: str | None = None,
    start_bridge: bool = True,
    meta_runner: MetaRunner | None = None,
) -> dict[str, Any]:
    check_gate(config)
    service_action(config, "stop-bridge")
    if not config.bridge_db.is_file():
        raise ReconcileError(f"bridge database is missing: {config.bridge_db}")
    policy = parse_env_file(config.dotfiles / "omnigent_config/google-chat.env")
    space = _required_policy(policy, "OMNIGENT_GCHAT_SPACE")
    allowed_actor = _required_policy(policy, "OMNIGENT_GCHAT_ALLOWED_ACTOR_ID")
    runner = meta_runner or _run_meta
    backup = _backup_bridge_database(config)

    if resubmit:
        result = _resubmit(config, runner, resubmit, allowed_actor)
        if start_bridge:
            service_action(config, "start-bridge")
        result["database_backup"] = str(backup)
        result["bridge_started"] = start_bridge
        return result

    with sqlite3.connect(config.bridge_db) as db:
        cursor = _read_cursor(db)
        created_after = _overlap_start(cursor)
        messages = _list_messages(runner, space, created_after)
        mappings = {
            str(thread): str(session)
            for thread, session in db.execute(
                "SELECT thread_name, omnigent_session_id FROM session_threads"
            ).fetchall()
        }
        classifications: list[dict[str, Any]] = []
        for message in messages:
            classification = _classify_message(
                db,
                config.chat_db,
                message,
                mappings,
                allowed_actor,
            )
            if classification is not None:
                classifications.append(classification)
        if messages:
            boundary = max(_ordering_key(message) for message in messages)
            _write_cursor(db, boundary)
        db.commit()

    if start_bridge:
        service_action(config, "start-bridge")
        time.sleep(2)
    return {
        "database_backup": str(backup),
        "created_after": created_after,
        "message_count": len(messages),
        "classifications": classifications,
        "ambiguous_count": sum(
            1 for item in classifications if item.get("classification") == "ambiguous-consumed"
        ),
        "bridge_started": start_bridge,
    }


def _backup_bridge_database(config: HubConfig) -> Path:
    root = config.local_state_dir / "gchat-reconcile"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = root / f"{int(time.time())}-{uuid.uuid4().hex[:8]}.sqlite3"
    with sqlite3.connect(f"file:{config.bridge_db}?mode=ro", uri=True) as source:
        with sqlite3.connect(destination) as target:
            source.backup(target)
    os.chmod(destination, 0o600)
    return destination


def _read_cursor(db: sqlite3.Connection) -> tuple[str, str] | None:
    row = db.execute("SELECT value FROM bridge_state WHERE key = 'gchat_poll_cursor'").fetchone()
    if row is None:
        return None
    try:
        value = json.loads(str(row[0]))
    except json.JSONDecodeError as exc:
        raise ReconcileError("restored Google Chat cursor is invalid") from exc
    if (
        not isinstance(value, list)
        or len(value) != 2
        or not all(isinstance(part, str) for part in value)
    ):
        raise ReconcileError("restored Google Chat cursor has an invalid shape")
    return (value[0], value[1])


def _overlap_start(cursor: tuple[str, str] | None) -> str:
    if cursor is None:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")
    timestamp = datetime.fromisoformat(cursor[0].replace("Z", "+00:00"))
    return (timestamp - timedelta(seconds=120)).astimezone(UTC).isoformat().replace("+00:00", "Z")


def _list_messages(runner: MetaRunner, space: str, created_after: str) -> list[dict[str, Any]]:
    messages: dict[str, dict[str, Any]] = {}
    page_token: str | None = None
    seen_tokens: set[str] = set()
    while True:
        argv = [
            "/usr/local/bin/meta",
            "google.chat.message",
            "list",
            f"--space-name={space}",
            f"--created-after={created_after}",
            "--oldest",
            "--limit=200",
            "--raw-json",
            "--skip-cache",
            "--no-color",
        ]
        if page_token:
            argv.append(f"--page-token={page_token}")
        payload = runner(argv)
        raw_messages = payload.get("messages", [])
        if not isinstance(raw_messages, list):
            raise ReconcileError("Google Chat list response has no messages list")
        for message in raw_messages:
            if isinstance(message, dict) and isinstance(message.get("name"), str):
                messages[message["name"]] = message
        next_token = payload.get("nextPageToken") or payload.get("next_page_token")
        if next_token is None:
            break
        if not isinstance(next_token, str) or not next_token or next_token in seen_tokens:
            raise ReconcileError("Google Chat returned an invalid page token")
        seen_tokens.add(next_token)
        page_token = next_token
    return sorted(messages.values(), key=_ordering_key)


def _classify_message(
    bridge_db: sqlite3.Connection,
    chat_db_path: Path,
    message: dict[str, Any],
    mappings: dict[str, str],
    allowed_actor: str,
) -> dict[str, Any] | None:
    sender = message.get("sender")
    if not isinstance(sender, dict):
        return None
    if sender.get("type") != "HUMAN" or sender.get("name") != allowed_actor:
        return None
    name = _required_message_string(message, "name")
    thread_value = message.get("thread")
    if not isinstance(thread_value, dict) or not isinstance(thread_value.get("name"), str):
        return None
    thread = str(thread_value["name"])
    session = mappings.get(thread)
    if session is None:
        return {
            "message_name": name,
            "classification": "ignored-unmapped-thread",
            "thread_name": thread,
        }
    text = str(message.get("argumentText") or message.get("text") or "")
    row = bridge_db.execute(
        "SELECT state, omnigent_item_id FROM gchat_inbound WHERE message_name = ?",
        (name,),
    ).fetchone()
    if row is not None:
        state = str(row[0])
        if state in ("claimed", "dispatching"):
            bridge_db.execute(
                "UPDATE gchat_inbound SET state = 'ambiguous', error = ?, updated_at = ? "
                "WHERE message_name = ?",
                (
                    "unexpected recovery: in-flight delivery defaulted to consumed",
                    int(time.time()),
                    name,
                ),
            )
            classification = "ambiguous-consumed"
        else:
            classification = f"existing-{state}"
        return {
            "message_name": name,
            "classification": classification,
            "session_id": session,
            "omnigent_item_id": row[1],
        }

    item_id = _find_durable_source(chat_db_path, session, name)
    classification = "durable-source-match" if item_id else "ambiguous-consumed"
    state = "submitted" if item_id else "ambiguous"
    error = None if item_id else "unexpected recovery: delivery unproven; defaulted to consumed"
    now = int(time.time())
    bridge_db.execute(
        """
        INSERT INTO gchat_inbound (
            message_name, thread_name, actor_id, created_at_google,
            text_sha256, state, omnigent_item_id, error, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            thread,
            allowed_actor,
            _required_message_string(message, "createTime"),
            hashlib.sha256(text.encode()).hexdigest(),
            state,
            item_id,
            error,
            now,
            now,
        ),
    )
    result: dict[str, Any] = {
        "message_name": name,
        "classification": classification,
        "session_id": session,
        "omnigent_item_id": item_id,
    }
    if not item_id:
        result["text"] = text
        result["transcript_tail"] = _transcript_tail(chat_db_path, session)
    return result


def _find_durable_source(database: Path, session: str, message_name: str) -> str | None:
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
        rows = db.execute(
            "SELECT id, data FROM conversation_items "
            "WHERE conversation_id = ? AND data LIKE ? ORDER BY position DESC",
            (session, f"%{message_name}%"),
        ).fetchall()
    for item_id, raw_data in rows:
        try:
            data = json.loads(str(raw_data))
        except json.JSONDecodeError:
            continue
        content = data.get("content") if isinstance(data, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            source = block.get("source") if isinstance(block, dict) else None
            if (
                isinstance(source, dict)
                and source.get("type") == "google_chat"
                and source.get("message_name") == message_name
            ):
                return str(item_id)
    return None


def _transcript_tail(database: Path, session: str) -> list[str]:
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
        rows = db.execute(
            "SELECT data FROM conversation_items WHERE conversation_id = ? "
            "ORDER BY position DESC LIMIT 5",
            (session,),
        ).fetchall()
    tail: list[str] = []
    for (raw_data,) in reversed(rows):
        try:
            data = json.loads(str(raw_data))
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        content = data.get("content")
        if not isinstance(content, list):
            continue
        text = "".join(str(block.get("text", "")) for block in content if isinstance(block, dict))
        if text:
            tail.append(text[:500])
    return tail


def _write_cursor(db: sqlite3.Connection, cursor: tuple[str, str]) -> None:
    now = int(time.time())
    db.execute(
        """
        INSERT INTO bridge_state (key, value, updated_at)
        VALUES ('gchat_poll_cursor', ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (json.dumps(list(cursor)), now),
    )


def _resubmit(
    config: HubConfig,
    runner: MetaRunner,
    message_name: str,
    allowed_actor: str,
) -> dict[str, Any]:
    with sqlite3.connect(config.bridge_db) as db:
        row = db.execute(
            "SELECT thread_name, state FROM gchat_inbound WHERE message_name = ?",
            (message_name,),
        ).fetchone()
        if row is None or str(row[1]) != "ambiguous":
            raise ReconcileError("only an ambiguous classified message may be resubmitted")
        mapping = db.execute(
            "SELECT omnigent_session_id FROM session_threads WHERE thread_name = ?",
            (row[0],),
        ).fetchone()
        if mapping is None:
            raise ReconcileError("ambiguous message thread is no longer mapped")
        session = str(mapping[0])
        message = runner(
            [
                "/usr/local/bin/meta",
                "google.chat.message",
                "get",
                f"--resource-name={message_name}",
                "--raw-json",
                "--skip-cache",
                "--no-color",
            ]
        )
        sender = message.get("sender")
        if not isinstance(sender, dict) or sender.get("name") != allowed_actor:
            raise ReconcileError("message actor no longer matches the allowlist")
        text = str(message.get("argumentText") or message.get("text") or "")
        item_id = _submit_to_omnigent(config, session, text, message_name)
        db.execute(
            "UPDATE gchat_inbound SET state = 'submitted', omnigent_item_id = ?, "
            "error = 'explicitly resubmitted during recovery', updated_at = ? "
            "WHERE message_name = ?",
            (item_id, int(time.time()), message_name),
        )
        db.commit()
    return {
        "message_name": message_name,
        "classification": "explicitly-resubmitted",
        "session_id": session,
        "omnigent_item_id": item_id,
    }


def _submit_to_omnigent(config: HubConfig, session: str, text: str, source: str) -> str | None:
    body = json.dumps(
        {
            "type": "message",
            "data": {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": text,
                        "source": {"type": "google_chat", "message_name": source},
                    }
                ],
            },
        }
    ).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{config.topology.port}/v1/sessions/{session}/events",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    item_id = payload.get("item_id") if isinstance(payload, dict) else None
    return str(item_id) if item_id else None


def _run_meta(argv: list[str]) -> dict[str, Any]:
    result = subprocess.run(argv, check=False, text=True, capture_output=True, timeout=120)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ReconcileError(f"Meta Google Chat command failed: {detail}")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ReconcileError("Meta Google Chat command returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ReconcileError("Meta Google Chat command returned non-object JSON")
    return value


def _ordering_key(message: dict[str, Any]) -> tuple[str, str]:
    return (
        _required_message_string(message, "createTime"),
        _required_message_string(message, "name"),
    )


def _required_message_string(message: dict[str, Any], key: str) -> str:
    value = message.get(key)
    if not isinstance(value, str) or not value:
        raise ReconcileError(f"Google Chat message has no {key}")
    return value


def _required_policy(policy: dict[str, str], key: str) -> str:
    value = policy.get(key)
    if not value:
        raise ReconcileError(f"tracked Google Chat policy is missing {key}")
    return value
