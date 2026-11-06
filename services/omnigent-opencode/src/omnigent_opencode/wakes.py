"""Asking T3 Code for a turn on a thread, for an Omnigent turn T3 Code did not start.

T3 Code unloads a thread 30 minutes after its last turn and drops what an
OpenCode server then emits for it, and nothing an OpenCode server can see marks
the unload. So every outside turn is put to T3 Code as a message on its thread
with `t3 thread send` (cli/thread.ts), which posts it as the agent's ("Agent"
in the thread), loads the thread if it must, and queues it behind a running
turn; the prompt of the turn it starts is the message itself.

The thread behind an OpenCode session is read from T3 Code's state, read-only:
the provider thread whose native ref is the session, if its thread still uses
it and is neither archived nor deleted (orchestration-v2 ProjectionStore).
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from .sessions import Session, WakeOutcome

LOG = logging.getLogger(__name__)

STATE_DB: Final = "userdata/statev2.sqlite"
SEND_TIMEOUT_S: Final = 60.0
THREAD_QUERY: Final = """
select p.thread_id
from orchestration_v2_projection_provider_threads p
join orchestration_v2_projection_threads t on t.thread_id = p.thread_id
where json_extract(p.payload_json, '$.nativeThreadRef.nativeId') = ?
  and t.active_provider_thread_id = p.provider_thread_id
  and t.archived_at is null
  and t.deleted_at is null
order by p.updated_at desc
limit 1
"""


def thread_of(t3_home: Path, session_id: str) -> str | None:
    """The T3 Code thread that runs OpenCode session `session_id`, in the T3 Code server whose home is `t3_home`."""
    db = t3_home / STATE_DB
    if not db.exists():
        return None
    with closing(sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True, timeout=5)) as connection:
        row = connection.execute(THREAD_QUERY, (session_id,)).fetchone()
    return str(row[0]) if row else None


class Waker:
    def __init__(self, t3: str, home_of: Callable[[str], Path | None]) -> None:
        self._t3 = t3
        self._home_of = home_of

    async def __call__(self, session: Session, trigger: str) -> WakeOutcome:
        home = self._home_of(session.id)
        if home is None:
            LOG.info("no T3 Code server has opened %s", session.id)
            return "never"
        try:
            thread = await asyncio.to_thread(thread_of, home, session.id)
        except sqlite3.Error as exc:
            LOG.warning("could not read T3 Code's threads in %s: %s", home, exc)
            return "later"
        if thread is None:
            LOG.info("no live T3 Code thread in %s runs %s", home, session.id)
            return "never"
        try:
            process = await asyncio.create_subprocess_exec(
                self._t3,
                "thread",
                "send",
                "--base-dir",
                str(home),
                "--json",
                thread,
                "-",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            LOG.warning("cannot run %s: %s", self._t3, exc)
            return "never"
        try:
            out, err = await asyncio.wait_for(process.communicate(trigger.encode()), SEND_TIMEOUT_S)
        except TimeoutError:
            process.kill()
            await process.wait()
            LOG.warning("t3 thread send %s did not finish in %.0fs", thread, SEND_TIMEOUT_S)
            return "later"
        if process.returncode != 0:
            # Most often T3 Code is not running; the trigger is sent again later.
            LOG.warning("t3 thread send %s failed (%s): %s", thread, process.returncode, (err or out).decode().strip()[:500])
            return "later"
        LOG.info("t3 thread send %s: %s", thread, out.decode().strip()[:500])
        return "sent"
