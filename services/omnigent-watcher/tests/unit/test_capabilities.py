from __future__ import annotations

import json
import os
import socket
import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from omnigent_watcher import http_api
from omnigent_watcher.capabilities import (
    AGENT_HOME,
    OMNIGENT,
    capabilities_path,
    clear_worker_capabilities,
    worker_session_kinds,
    write_worker_capabilities,
)
from omnigent_watcher.domain import WatcherConfig
from omnigent_watcher.settings import ServiceSettings


def test_the_file_sits_beside_the_database() -> None:
    assert capabilities_path(Path("/x/watcher.sqlite3")) == Path("/x/watcher.capabilities.json")


def test_a_live_worker_advertises_agent_home(tmp_path: Path) -> None:
    path = tmp_path / "watcher.capabilities.json"
    assert worker_session_kinds(path) == {OMNIGENT}
    write_worker_capabilities(path)
    assert worker_session_kinds(path) == {OMNIGENT, AGENT_HOME}
    clear_worker_capabilities(path)
    assert not path.exists()


def _dead_pid() -> int:
    process = subprocess.Popen(["true"])
    process.wait()
    return process.pid


@pytest.mark.parametrize(
    "payload",
    [
        # A newer worker that has since been replaced by older code, which
        # never rewrites the file.
        {"host": socket.gethostname(), "pid": _dead_pid(), "session_kinds": [AGENT_HOME]},
        # A file restored from another hub's snapshot.
        {"host": "elsewhere", "pid": os.getpid(), "session_kinds": [AGENT_HOME]},
        {"host": socket.gethostname(), "pid": "1", "session_kinds": [AGENT_HOME]},
        [],
    ],
)
def test_anything_but_a_live_local_writer_means_omnigent_only(
    tmp_path: Path, payload: object
) -> None:
    path = tmp_path / "watcher.capabilities.json"
    path.write_text(json.dumps(payload))
    assert worker_session_kinds(path) == {OMNIGENT}


def test_clearing_leaves_another_workers_file(tmp_path: Path) -> None:
    path = tmp_path / "watcher.capabilities.json"
    path.write_text(json.dumps({"host": socket.gethostname(), "pid": 1, "session_kinds": []}))
    clear_worker_capabilities(path)
    assert path.exists()


async def test_the_api_reports_what_the_worker_advertised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "watcher.sqlite3"
    settings = ServiceSettings(
        server_url="http://isolated-test",
        database_path=database,
        delivery_mode="log_only",
        delivery_session_allowlist=frozenset(),
        reconcile_interval_seconds=15,
        scheduler_error_retry_seconds=30,
        watcher=WatcherConfig(),
    )
    monkeypatch.setattr(http_api, "_settings", lambda: settings)
    app = FastAPI()
    app.include_router(http_api.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://isolated-test"
    ) as client:
        before = (await client.get("/v1/watches/capabilities")).json()
        write_worker_capabilities(capabilities_path(database))
        after = (await client.get("/v1/watches/capabilities")).json()

    assert before == {"session_kinds": [OMNIGENT]}
    assert after == {"session_kinds": [AGENT_HOME, OMNIGENT]}


def test_an_unwritable_location_does_not_stop_the_worker(tmp_path: Path) -> None:
    path = tmp_path / "missing-dir" / "watcher.capabilities.json"
    write_worker_capabilities(path)
    assert worker_session_kinds(path) == {OMNIGENT}
