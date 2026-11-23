"""Asking T3 Code for a turn on a thread, for an Omnigent turn T3 Code did not start.

T3 Code unloads a thread 30 minutes after its last turn and drops what an
OpenCode server then emits for it, and nothing an OpenCode server can see marks
the unload. So every outside turn is put to T3 Code as a message on its thread
with `t3 thread send` (cli/thread.ts), which posts it as the agent's ("Agent"
in the thread), loads the thread if it must, and queues it behind a running
turn; the prompt of the turn it starts is the message itself.

The thread is the one T3 Code's state names (`t3state`), and only a live one:
a settled thread takes no wakeups (sending one would un-settle it), and an
archived or deleted one is gone.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Final

from .t3state import thread_for

if TYPE_CHECKING:
    from .sessions import Session, WakeOutcome

LOG = logging.getLogger(__name__)

SEND_TIMEOUT_S: Final = 60.0


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
            found = await asyncio.to_thread(thread_for, home, session.id)
        except sqlite3.Error as exc:
            LOG.warning("could not read T3 Code's threads in %s: %s", home, exc)
            return "later"
        if found is None or found.done:
            LOG.info("no live T3 Code thread in %s runs %s%s", home, session.id, f" ({found.status})" if found else "")
            return "never"
        thread = found.id
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
