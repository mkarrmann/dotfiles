"""Which session backends the running worker can wake.

The MCP server, the server-mounted API, and the worker load this package
separately and restart independently. Agent Home support lives in the worker,
so an MCP process that already has the new code must not accept an
``agenthome:`` watch while an older worker is running: that worker would ask
Omnigent for the session, get a 404, and retire the watch as deleted -- a
watch accepted and never fired.

The worker therefore advertises what it can wake in a file beside its
database, and the API reports it. The file names the writer's host and pid so
a worker that later starts from older code, which never rewrites the file,
is not mistaken for the one that wrote it.
"""

from __future__ import annotations

import json
import logging
import os
import socket
from pathlib import Path

_logger = logging.getLogger(__name__)

OMNIGENT = "omnigent"
AGENT_HOME = "agenthome"
WORKER_SESSION_KINDS: frozenset[str] = frozenset({OMNIGENT, AGENT_HOME})
_BASELINE: frozenset[str] = frozenset({OMNIGENT})


def capabilities_path(database_path: Path) -> Path:
    return database_path.with_name(f"{database_path.stem}.capabilities.json")


def write_worker_capabilities(path: Path) -> None:
    """Advertise this worker. Never fatal: without the file, the MCP surface
    simply keeps refusing Agent Home watches, and Omnigent ones are unaffected."""
    try:
        _write(path)
    except OSError:
        _logger.warning("could not advertise worker capabilities at %s", path, exc_info=True)


def _write(path: Path) -> None:
    payload = {
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "session_kinds": sorted(WORKER_SESSION_KINDS),
    }
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(path)


def clear_worker_capabilities(path: Path) -> None:
    """Remove the file if this process wrote it; another worker's is left alone."""
    if _writer_pid(path) == os.getpid():
        try:
            path.unlink(missing_ok=True)
        except OSError:
            _logger.warning("could not remove worker capabilities at %s", path, exc_info=True)


def worker_session_kinds(path: Path) -> frozenset[str]:
    """What the live worker can wake; Omnigent alone unless it says otherwise."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _BASELINE
    if not isinstance(payload, dict) or payload.get("host") != socket.gethostname():
        return _BASELINE
    pid = payload.get("pid")
    kinds = payload.get("session_kinds")
    if not isinstance(pid, int) or not _alive(pid) or not isinstance(kinds, list):
        return _BASELINE
    return _BASELINE | frozenset(kind for kind in kinds if isinstance(kind, str))


def _writer_pid(path: Path) -> int | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pid = payload.get("pid") if isinstance(payload, dict) else None
    return pid if isinstance(pid, int) else None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
