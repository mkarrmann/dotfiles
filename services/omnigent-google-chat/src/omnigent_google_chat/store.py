from __future__ import annotations

import fcntl
import json
import os
import time
from collections.abc import Sequence
from io import TextIOBase
from pathlib import Path
from types import TracebackType

import aiosqlite

from omnigent_google_chat.models import (
    ClaimResult,
    InboundState,
    MappingState,
    OutboundState,
    SessionThread,
)

SCHEMA_VERSION = "1"
RESTART_AMBIGUOUS_ERROR = "bridge restarted while delivery was in progress"
RESTART_AMBIGUOUS_NOTIFIED_ERROR = f"{RESTART_AMBIGUOUS_ERROR}; user notified"


class StoreError(RuntimeError):
    pass


class StoreLockedError(StoreError):
    pass


class SpaceMismatchError(StoreError):
    pass


class SQLiteStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock_file: TextIOBase | None = None

    async def __aenter__(self) -> SQLiteStore:
        await self.initialize()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    @property
    def path(self) -> Path:
        return self._path

    async def initialize(self) -> None:
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(self._path.parent, 0o700)
        except OSError:
            pass
        self._acquire_lock()
        try:
            async with self._connect() as db:
                await db.execute("PRAGMA journal_mode=WAL")
                await db.execute("PRAGMA synchronous=FULL")
                await db.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS session_threads (
                        omnigent_session_id TEXT PRIMARY KEY,
                        space_name TEXT NOT NULL,
                        thread_name TEXT NOT NULL UNIQUE,
                        root_message_name TEXT NOT NULL UNIQUE,
                        title TEXT NOT NULL,
                        last_item_position TEXT,
                        state TEXT NOT NULL,
                        mirrored_chars INTEGER NOT NULL DEFAULT 0,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        reconciled_at INTEGER
                    );

                    CREATE TABLE IF NOT EXISTS gchat_inbound (
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

                    CREATE INDEX IF NOT EXISTS idx_gchat_inbound_item
                    ON gchat_inbound(omnigent_item_id);

                    CREATE TABLE IF NOT EXISTS gchat_outbound (
                        request_id TEXT PRIMARY KEY,
                        omnigent_session_id TEXT NOT NULL,
                        source_kind TEXT NOT NULL,
                        source_id TEXT NOT NULL,
                        part_index INTEGER NOT NULL,
                        message_name TEXT,
                        state TEXT NOT NULL,
                        attempt_count INTEGER NOT NULL DEFAULT 0,
                        char_count INTEGER NOT NULL DEFAULT 0,
                        error TEXT,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_gchat_outbound_message
                    ON gchat_outbound(message_name);

                    CREATE TABLE IF NOT EXISTS bridge_state (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at INTEGER NOT NULL
                    );
                    """
                )
                await self._set_state_in_transaction(db, "schema_version", SCHEMA_VERSION)
                await db.execute(
                    """
                    UPDATE gchat_inbound
                    SET state = ?, error = ?, updated_at = ?
                    WHERE state = ?
                    """,
                    (
                        InboundState.AMBIGUOUS,
                        RESTART_AMBIGUOUS_ERROR,
                        int(time.time()),
                        InboundState.DISPATCHING,
                    ),
                )
                await db.commit()
            if self._path.exists():
                os.chmod(self._path, 0o600)
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        lock_file = self._lock_file
        self._lock_file = None
        if lock_file is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()

    def _acquire_lock(self) -> None:
        if self._lock_file is not None:
            return
        lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        lock_file = lock_path.open("a+")
        os.chmod(lock_path, 0o600)
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            lock_file.close()
            raise StoreLockedError(f"another bridge owns {lock_path}") from exc
        self._lock_file = lock_file

    def _connect(self) -> aiosqlite.Connection:
        return aiosqlite.connect(self._path, timeout=10)

    async def bind_space(self, space_name: str) -> None:
        existing = await self.get_state("space_name")
        if existing is not None and existing != space_name:
            raise SpaceMismatchError(
                f"database is bound to {existing}; refusing configured space {space_name}"
            )
        await self.set_state("space_name", space_name)

    async def get_state(self, key: str) -> str | None:
        async with self._connect() as db:
            cursor = await db.execute("SELECT value FROM bridge_state WHERE key = ?", (key,))
            row = await cursor.fetchone()
            await cursor.close()
        return str(row[0]) if row else None

    async def set_state(self, key: str, value: str) -> None:
        async with self._connect() as db:
            await self._set_state_in_transaction(db, key, value)
            await db.commit()

    async def _set_state_in_transaction(
        self, db: aiosqlite.Connection, key: str, value: str
    ) -> None:
        await db.execute(
            """
            INSERT INTO bridge_state (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value, int(time.time())),
        )

    async def get_poll_cursor(self) -> tuple[str, str] | None:
        value = await self.get_state("gchat_poll_cursor")
        if value is None:
            return None
        parsed = json.loads(value)
        if (
            not isinstance(parsed, list)
            or len(parsed) != 2
            or not all(isinstance(part, str) for part in parsed)
        ):
            raise StoreError("invalid Google Chat poll cursor")
        return (parsed[0], parsed[1])

    async def set_poll_cursor(self, cursor: tuple[str, str]) -> None:
        await self.set_state("gchat_poll_cursor", json.dumps(list(cursor)))

    async def observe_session_status(self, session_id: str, status: str) -> str:
        key = f"session_status:{session_id}"
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute("SELECT value FROM bridge_state WHERE key = ?", (key,))
            row = await cursor.fetchone()
            await cursor.close()
            generation = 0
            previous_status: str | None = None
            if row:
                try:
                    previous = json.loads(str(row[0]))
                except json.JSONDecodeError as exc:
                    raise StoreError(f"invalid status transition state for {session_id}") from exc
                if not isinstance(previous, dict):
                    raise StoreError(f"invalid status transition state for {session_id}")
                previous_status = previous.get("status")
                raw_generation = previous.get("generation")
                if not isinstance(previous_status, str) or not isinstance(raw_generation, int):
                    raise StoreError(f"invalid status transition state for {session_id}")
                generation = raw_generation
            if previous_status != status:
                generation += 1
            value = json.dumps({"status": status, "generation": generation}, sort_keys=True)
            await self._set_state_in_transaction(db, key, value)
            await db.commit()
        return f"status-transition-{generation}"

    async def get_thread(self, session_id: str) -> SessionThread | None:
        async with self._connect() as db:
            cursor = await db.execute(
                """
                SELECT omnigent_session_id, space_name, thread_name,
                       root_message_name, title, last_item_position, state,
                       mirrored_chars
                FROM session_threads WHERE omnigent_session_id = ?
                """,
                (session_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
        return _thread_from_row(row) if row else None

    async def get_thread_by_name(self, thread_name: str) -> SessionThread | None:
        async with self._connect() as db:
            cursor = await db.execute(
                """
                SELECT omnigent_session_id, space_name, thread_name,
                       root_message_name, title, last_item_position, state,
                       mirrored_chars
                FROM session_threads WHERE thread_name = ?
                """,
                (thread_name,),
            )
            row = await cursor.fetchone()
            await cursor.close()
        return _thread_from_row(row) if row else None

    async def list_active_threads(self) -> list[SessionThread]:
        async with self._connect() as db:
            cursor = await db.execute(
                """
                SELECT omnigent_session_id, space_name, thread_name,
                       root_message_name, title, last_item_position, state,
                       mirrored_chars
                FROM session_threads WHERE state = ?
                ORDER BY created_at
                """,
                (MappingState.ACTIVE,),
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [_thread_from_row(row) for row in rows]

    async def create_thread(
        self,
        session_id: str,
        space_name: str,
        thread_name: str,
        root_message_name: str,
        title: str,
    ) -> None:
        now = int(time.time())
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO session_threads (
                    omnigent_session_id, space_name, thread_name,
                    root_message_name, title, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(omnigent_session_id) DO UPDATE SET
                    title = excluded.title,
                    updated_at = excluded.updated_at
                """,
                (
                    session_id,
                    space_name,
                    thread_name,
                    root_message_name,
                    title,
                    MappingState.ACTIVE,
                    now,
                    now,
                ),
            )
            await db.commit()

    async def update_thread_cursor(
        self, session_id: str, item_position: str, added_chars: int = 0
    ) -> None:
        now = int(time.time())
        async with self._connect() as db:
            await db.execute(
                """
                UPDATE session_threads
                SET last_item_position = ?, mirrored_chars = mirrored_chars + ?,
                    reconciled_at = ?, updated_at = ?
                WHERE omnigent_session_id = ?
                """,
                (item_position, added_chars, now, now, session_id),
            )
            await db.commit()

    async def set_thread_state(self, session_id: str, state: MappingState) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE session_threads SET state = ?, updated_at = ? "
                "WHERE omnigent_session_id = ?",
                (state, int(time.time()), session_id),
            )
            await db.commit()

    async def claim_inbound(
        self,
        *,
        message_name: str,
        thread_name: str,
        actor_id: str,
        created_at_google: str,
        text_sha256: str,
    ) -> ClaimResult:
        now = int(time.time())
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "SELECT text_sha256, state FROM gchat_inbound WHERE message_name = ?",
                (message_name,),
            )
            existing = await cursor.fetchone()
            await cursor.close()
            if existing:
                await db.commit()
                existing_hash = str(existing[0])
                return ClaimResult(
                    claimed=False,
                    changed_content=existing_hash != text_sha256,
                    state=InboundState(str(existing[1])),
                )
            await db.execute(
                """
                INSERT INTO gchat_inbound (
                    message_name, thread_name, actor_id, created_at_google,
                    text_sha256, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_name,
                    thread_name,
                    actor_id,
                    created_at_google,
                    text_sha256,
                    InboundState.CLAIMED,
                    now,
                    now,
                ),
            )
            await db.commit()
        return ClaimResult(claimed=True, state=InboundState.CLAIMED)

    async def set_inbound_state(
        self,
        message_name: str,
        state: InboundState,
        *,
        error: str | None = None,
        omnigent_item_id: str | None = None,
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                """
                UPDATE gchat_inbound
                SET state = ?, error = ?,
                    omnigent_item_id = COALESCE(?, omnigent_item_id), updated_at = ?
                WHERE message_name = ?
                """,
                (state, _sanitize_error(error), omnigent_item_id, int(time.time()), message_name),
            )
            await db.commit()

    async def list_restart_ambiguous(self) -> list[tuple[str, str]]:
        async with self._connect() as db:
            cursor = await db.execute(
                """
                SELECT message_name, thread_name
                FROM gchat_inbound
                WHERE state = ? AND error = ?
                ORDER BY created_at
                """,
                (InboundState.AMBIGUOUS, RESTART_AMBIGUOUS_ERROR),
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [(str(row[0]), str(row[1])) for row in rows]

    async def mark_restart_ambiguous_notified(self, message_name: str) -> None:
        async with self._connect() as db:
            await db.execute(
                """
                UPDATE gchat_inbound
                SET error = ?, updated_at = ?
                WHERE message_name = ? AND state = ? AND error = ?
                """,
                (
                    RESTART_AMBIGUOUS_NOTIFIED_ERROR,
                    int(time.time()),
                    message_name,
                    InboundState.AMBIGUOUS,
                    RESTART_AMBIGUOUS_ERROR,
                ),
            )
            await db.commit()

    async def is_chat_origin_item(self, item_id: str) -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT 1 FROM gchat_inbound WHERE omnigent_item_id = ? LIMIT 1",
                (item_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
        return row is not None

    async def prune_inbound(self, retention_seconds: int) -> None:
        cutoff = int(time.time()) - retention_seconds
        async with self._connect() as db:
            await db.execute(
                "DELETE FROM gchat_inbound WHERE updated_at < ? AND state != ?",
                (cutoff, InboundState.DISPATCHING),
            )
            await db.commit()

    async def prepare_outbound(
        self,
        *,
        request_id: str,
        session_id: str,
        source_kind: str,
        source_id: str,
        part_index: int,
        char_count: int,
    ) -> OutboundState:
        now = int(time.time())
        async with self._connect() as db:
            await db.execute(
                """
                INSERT OR IGNORE INTO gchat_outbound (
                    request_id, omnigent_session_id, source_kind, source_id,
                    part_index, state, char_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    session_id,
                    source_kind,
                    source_id,
                    part_index,
                    OutboundState.PENDING,
                    char_count,
                    now,
                    now,
                ),
            )
            cursor = await db.execute(
                "SELECT state FROM gchat_outbound WHERE request_id = ?", (request_id,)
            )
            row = await cursor.fetchone()
            await cursor.close()
            await db.commit()
        if row is None:
            raise StoreError(f"failed to prepare outbound request {request_id}")
        return OutboundState(str(row[0]))

    async def mark_outbound_attempt(self, request_id: str) -> None:
        async with self._connect() as db:
            await db.execute(
                """
                UPDATE gchat_outbound
                SET attempt_count = attempt_count + 1, state = ?, updated_at = ?
                WHERE request_id = ?
                """,
                (OutboundState.PENDING, int(time.time()), request_id),
            )
            await db.commit()

    async def mark_outbound_sent(self, request_id: str, message_name: str) -> None:
        async with self._connect() as db:
            await db.execute(
                """
                UPDATE gchat_outbound
                SET state = ?, message_name = ?, error = NULL, updated_at = ?
                WHERE request_id = ?
                """,
                (OutboundState.SENT, message_name, int(time.time()), request_id),
            )
            await db.commit()

    async def mark_outbound_failed(self, request_id: str, error: str) -> None:
        async with self._connect() as db:
            await db.execute(
                """
                UPDATE gchat_outbound
                SET state = ?, error = ?, updated_at = ? WHERE request_id = ?
                """,
                (OutboundState.FAILED, _sanitize_error(error), int(time.time()), request_id),
            )
            await db.commit()

    async def get_outbound_message_name(self, request_id: str) -> str | None:
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT message_name FROM gchat_outbound WHERE request_id = ? AND state = ?",
                (request_id, OutboundState.SENT),
            )
            row = await cursor.fetchone()
            await cursor.close()
        return str(row[0]) if row and row[0] else None

    async def is_outbound_message(self, message_name: str) -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT 1 FROM gchat_outbound WHERE message_name = ? LIMIT 1",
                (message_name,),
            )
            row = await cursor.fetchone()
            await cursor.close()
        return row is not None


def _thread_from_row(row: Sequence[object]) -> SessionThread:
    return SessionThread(
        omnigent_session_id=str(row[0]),
        space_name=str(row[1]),
        thread_name=str(row[2]),
        root_message_name=str(row[3]),
        title=str(row[4]),
        last_item_position=str(row[5]) if row[5] is not None else None,
        state=MappingState(str(row[6])),
        mirrored_chars=int(str(row[7])),
    )


def _sanitize_error(error: str | None) -> str | None:
    if error is None:
        return None
    return error.replace("\n", " ")[:1000]
