"""What this server must remember across restarts that Omnigent cannot tell it.

T3 Code kills the server 30 s after its last thread closes and resumes threads
by the ids this server gave out, so these live in SQLite:

- `sessions`: the Omnigent session behind an OpenCode session, when it is not
  the one the OpenCode session id names (a rollback points it at a fork).
- `prompts`: the message id T3 Code chose for each prompt, by the Omnigent
  input it became; forks, rollbacks and reconnect backfill address prompts by it.
- `executions`: how each execution ended, after which input, for the `idle`
  markers in a session's history.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Literal

Outcome = Literal["succeeded", "failed", "interrupted"]

SCHEMA = """
create table if not exists sessions (session_id text primary key, conversation_id text not null);
create table if not exists prompts (
    session_id text not null,
    message_id text not null,
    text text not null,
    created integer not null,
    pending_id text,
    item_id text,
    primary key (session_id, message_id)
);
create table if not exists executions (
    session_id text not null,
    ended integer not null,
    outcome text not null,
    message_id text not null
);
"""


def default_path() -> Path:
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(state) / "omnigent-opencode" / "state.db"


class Store:
    def __init__(self, path: Path | None) -> None:
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path) if path is not None else ":memory:", isolation_level=None)
        self._db.executescript(SCHEMA)

    def conversation(self, session_id: str) -> str | None:
        row = self._db.execute("select conversation_id from sessions where session_id = ?", (session_id,)).fetchone()
        return str(row[0]) if row else None

    def set_conversation(self, session_id: str, conversation_id: str) -> None:
        self._db.execute(
            "insert into sessions values (?, ?) on conflict (session_id) do update set conversation_id = excluded.conversation_id",
            (session_id, conversation_id),
        )

    def add_prompt(self, session_id: str, message_id: str, text: str, created: int) -> None:
        self._db.execute(
            "insert or ignore into prompts (session_id, message_id, text, created) values (?, ?, ?, ?)",
            (session_id, message_id, text, created),
        )

    def bind_prompt(self, session_id: str, message_id: str, pending_id: str | None, item_id: str | None) -> None:
        self._db.execute(
            "update prompts set pending_id = coalesce(?, pending_id), item_id = coalesce(?, item_id)"
            " where session_id = ? and message_id = ?",
            (pending_id, item_id, session_id, message_id),
        )

    def bind_consumed(self, session_id: str, pending_id: str, item_id: str) -> None:
        """The Omnigent item a prompt became, once the harness recorded it."""
        self._db.execute(
            "update prompts set item_id = ? where session_id = ? and pending_id = ?", (item_id, session_id, pending_id)
        )

    def prompt_for_pending(self, session_id: str, pending_id: str) -> str | None:
        row = self._db.execute(
            "select message_id from prompts where session_id = ? and pending_id = ?", (session_id, pending_id)
        ).fetchone()
        return str(row[0]) if row else None

    def prompts(self, session_id: str) -> list[tuple[str, str, int, str | None]]:
        """(message_id, text, created, item_id) for each prompt, oldest first."""
        rows = self._db.execute(
            "select message_id, text, created, item_id from prompts where session_id = ? order by created, rowid",
            (session_id,),
        ).fetchall()
        return [(str(m), str(t), int(c), str(i) if i is not None else None) for m, t, c, i in rows]

    def drop_prompts_from(self, session_id: str, message_id: str) -> None:
        """Forget a prompt and every later one: a rollback removed them from the session."""
        row = self._db.execute(
            "select created, rowid from prompts where session_id = ? and message_id = ?", (session_id, message_id)
        ).fetchone()
        if row is None:
            return
        self._db.execute(
            "delete from prompts where session_id = ? and (created > ? or (created = ? and rowid >= ?))",
            (session_id, row[0], row[0], row[1]),
        )

    def add_execution(self, session_id: str, ended: int, outcome: Outcome, message_id: str) -> None:
        self._db.execute("insert into executions values (?, ?, ?, ?)", (session_id, ended, outcome, message_id))

    def executions(self, session_id: str) -> list[tuple[int, Outcome, str]]:
        rows = self._db.execute(
            "select ended, outcome, message_id from executions where session_id = ? order by ended, rowid", (session_id,)
        ).fetchall()
        return [(int(e), o, str(m)) for e, o, m in rows]

    def copy_session(self, source: str, target: str) -> None:
        """Give a fork its source's prompt ids and execution markers, which its copied history still contains."""
        self._db.execute(
            "insert or ignore into prompts select ?, message_id, text, created, pending_id, item_id from prompts where session_id = ?",
            (target, source),
        )
        self._db.execute(
            "insert into executions select ?, ended, outcome, message_id from executions where session_id = ?",
            (target, source),
        )
