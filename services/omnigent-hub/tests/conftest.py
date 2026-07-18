from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from omnigent_hub.config import HubConfig
from omnigent_hub.models import Topology


@pytest.fixture
def hub_config(tmp_path: Path) -> HubConfig:
    home = tmp_path / "home"
    dotfiles = home / "dotfiles"
    data = home / ".omnigent"
    storage_mount = home / "persistent/private-30d"
    storage_root = storage_mount / "omnigent-ha"
    bridge = dotfiles / "services/omnigent-google-chat"
    bridge_source = bridge / "src/omnigent_google_chat"
    bridge_source.mkdir(parents=True)
    (bridge / "pyproject.toml").write_text("[project]\nname='bridge'\n", encoding="utf-8")
    (bridge / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (bridge_source / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    omnigent = home / ".local/bin/omnigent"
    omnigent.parent.mkdir(parents=True)
    omnigent.write_text("#!/bin/sh\necho 'omnigent 0.test'\n", encoding="utf-8")
    omnigent.chmod(0o755)
    data.mkdir(parents=True)
    _create_database(data / "chat.db", "sessions", 3)
    _create_database(data / "google-chat.sqlite3", "gchat_inbound", 2)
    artifacts = data / "artifacts"
    artifacts.mkdir()
    (artifacts / "blob").write_bytes(b"artifact")
    storage_root.mkdir(parents=True)
    state = home / ".local/state/omnigent-hub"
    return HubConfig(
        home=home,
        dotfiles=dotfiles,
        topology_path=dotfiles / "omnigent_config/topology.env",
        topology=Topology("primary.example.com", "standby.example.com", 6767),
        owner_fbid="1097089018461839",
        local_fqdn="primary.example.com",
        data_dir=data,
        local_state_dir=state,
        routing_cache=home / ".config/omnigent/active-hub.json",
        storage_mount=storage_mount,
        storage_root=storage_root,
        record_path=storage_root / "active-hub.json",
        snapshots_dir=storage_root / "snapshots",
        omnigent_bin=omnigent,
        bridge_project=bridge,
        storage_mount_name="private-30d",
    )


def _create_database(path: Path, table: str, rows: int) -> None:
    with sqlite3.connect(path) as db:
        db.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, value TEXT)")
        db.executemany(
            f"INSERT INTO {table} (value) VALUES (?)",
            [(f"value-{index}",) for index in range(rows)],
        )
