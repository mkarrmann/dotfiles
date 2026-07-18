from __future__ import annotations

import json
import os
import sqlite3

import pytest

from omnigent_hub.config import HubConfig
from omnigent_hub.reconcile import reconcile_gchat
from omnigent_hub.runtime import initialize


def test_reconcile_uses_exact_source_and_consumes_ambiguous(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os.path, "ismount", lambda path: path == hub_config.storage_mount)
    monkeypatch.setattr("omnigent_hub.reconcile.service_action", lambda config, action: {})
    initialize(hub_config, active_hub="primary.example.com")
    policy = hub_config.dotfiles / "omnigent_config/google-chat.env"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text(
        "OMNIGENT_GCHAT_SPACE=spaces/space\nOMNIGENT_GCHAT_ALLOWED_ACTOR_ID=users/human\n",
        encoding="utf-8",
    )
    _create_databases(hub_config)

    messages = [
        _message("messages/exact", "2026-07-18T20:01:00Z", "delivered"),
        _message("messages/unknown", "2026-07-18T20:02:00Z", "uncertain"),
    ]

    def meta_runner(argv: list[str]) -> dict[str, object]:
        assert "google.chat.message" in argv
        return {"messages": messages}

    result = reconcile_gchat(
        hub_config,
        start_bridge=False,
        meta_runner=meta_runner,
    )
    assert result["ambiguous_count"] == 1
    classifications = {
        item["message_name"]: item["classification"] for item in result["classifications"]
    }
    assert classifications == {
        "messages/exact": "durable-source-match",
        "messages/unknown": "ambiguous-consumed",
    }
    with sqlite3.connect(hub_config.bridge_db) as db:
        rows = dict(db.execute("SELECT message_name, state FROM gchat_inbound"))
        cursor = json.loads(
            db.execute("SELECT value FROM bridge_state WHERE key = 'gchat_poll_cursor'").fetchone()[
                0
            ]
        )
    assert rows == {"messages/exact": "submitted", "messages/unknown": "ambiguous"}
    assert cursor == ["2026-07-18T20:02:00Z", "messages/unknown"]


def _create_databases(config: HubConfig) -> None:
    config.chat_db.unlink()
    config.bridge_db.unlink()
    with sqlite3.connect(config.chat_db) as db:
        db.execute(
            "CREATE TABLE conversation_items "
            "(id TEXT, conversation_id TEXT, position INTEGER, data TEXT)"
        )
        db.execute(
            "INSERT INTO conversation_items VALUES (?, ?, ?, ?)",
            (
                "item-exact",
                "session-1",
                1,
                json.dumps(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "delivered",
                                "source": {
                                    "type": "google_chat",
                                    "message_name": "messages/exact",
                                },
                            }
                        ],
                    }
                ),
            ),
        )
    with sqlite3.connect(config.bridge_db) as db:
        db.executescript(
            """
            CREATE TABLE session_threads (
                omnigent_session_id TEXT PRIMARY KEY,
                thread_name TEXT NOT NULL
            );
            CREATE TABLE gchat_inbound (
                message_name TEXT PRIMARY KEY,
                thread_name TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                created_at_google TEXT NOT NULL,
                text_sha256 TEXT NOT NULL,
                state TEXT NOT NULL,
                omnigent_item_id TEXT,
                error TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE bridge_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            );
            """
        )
        db.execute("INSERT INTO session_threads VALUES ('session-1', 'threads/thread-1')")
        db.execute(
            "INSERT INTO bridge_state VALUES "
            "('gchat_poll_cursor', '[\"2026-07-18T20:00:00Z\", \"messages/old\"]', 1)"
        )


def _message(name: str, created_at: str, text: str) -> dict[str, object]:
    return {
        "name": name,
        "sender": {"name": "users/human", "type": "HUMAN"},
        "createTime": created_at,
        "text": text,
        "argumentText": text,
        "thread": {"name": "threads/thread-1"},
    }
