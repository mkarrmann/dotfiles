from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from omnigent_watcher import http_api
from omnigent_watcher.domain import WatcherConfig
from omnigent_watcher.repository import SCHEMA_VERSION, WatcherRepository
from omnigent_watcher.settings import ServiceSettings


@pytest.mark.parametrize("database_version", [SCHEMA_VERSION - 1, SCHEMA_VERSION + 1])
async def test_incompatible_database_returns_503_without_migrating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, database_version: int
) -> None:
    path = tmp_path / "watcher.sqlite3"
    repository = WatcherRepository(path)
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version={database_version}")
    settings = ServiceSettings(
        server_url="http://isolated-test",
        database_path=path,
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
        response = await client.get(
            "/v1/watches", params={"session_id": "conv_test", "sources": "command"}
        )

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert f"schema {database_version}" in detail
    assert str(SCHEMA_VERSION) in detail
    if database_version < SCHEMA_VERSION:
        assert "coordinated server/worker upgrade procedure" in detail
    assert repository.schema_version() == database_version
