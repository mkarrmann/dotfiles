"""What T3 Code's own state says about the thread behind an OpenCode session.

Read from the server's SQLite, read-only: the provider thread whose native ref
is the session, if its thread still uses it (orchestration-v2 ProjectionStore).
A thread is settled while `settledOverride` is "settled"; T3 Code clears it on
the next turn (`thread.unsettled`), including a turn `t3 thread send` starts.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

STATE_DB: Final = "userdata/statev2.sqlite"
THREAD_QUERY: Final = """
select
  p.thread_id,
  json_extract(t.payload_json, '$.settledOverride'),
  t.archived_at,
  t.deleted_at
from orchestration_v2_projection_provider_threads p
join orchestration_v2_projection_threads t on t.thread_id = p.thread_id
where json_extract(p.payload_json, '$.nativeThreadRef.nativeId') = ?
  and t.active_provider_thread_id = p.provider_thread_id
order by (t.archived_at is null and t.deleted_at is null) desc, p.updated_at desc
limit 1
"""

ThreadStatus = Literal["live", "settled", "archived", "deleted"]


@dataclass(frozen=True)
class Thread:
    id: str
    status: ThreadStatus

    @property
    def done(self) -> bool:
        """Put away in T3 Code: its session should hold no resources and take no wakeups."""
        return self.status != "live"


def thread_for(t3_home: Path, session_id: str) -> Thread | None:
    """The T3 Code thread that runs OpenCode session `session_id`, in the T3 Code server whose home is `t3_home`."""
    db = t3_home / STATE_DB
    if not db.exists():
        return None
    with closing(sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True, timeout=5)) as connection:
        row = connection.execute(THREAD_QUERY, (session_id,)).fetchone()
    if row is None:
        return None
    thread_id, settled_override, archived_at, deleted_at = row
    status: ThreadStatus = (
        "deleted"
        if deleted_at is not None
        else "archived"
        if archived_at is not None
        else "settled"
        if settled_override == "settled"
        else "live"
    )
    return Thread(str(thread_id), status)
