from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from omnigent_hub.config import HubConfig
from omnigent_hub.models import ActiveHubRecord
from omnigent_hub.orchestrator import HandoffOrchestrator
from omnigent_hub.remote import RemoteResult
from omnigent_hub.runtime import (
    HubRuntimeError,
    activate_transition,
    attach_transition_generation,
    begin_transition,
    check_gate,
    initialize,
    write_routing_cache,
)
from omnigent_hub.snapshot import (
    bridge_version,
    create_snapshot,
    hub_version,
    list_valid_snapshots,
    omnigent_version,
    restore_snapshot,
    validate_snapshot,
)
from omnigent_hub.storage import read_record


class InProcessRemote:
    def __init__(self, configs: dict[str, HubConfig]) -> None:
        self.configs = configs
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def resolve(self) -> tuple[ActiveHubRecord, str, dict[str, str]]:
        host = next(iter(self.configs))
        return read_record(self.configs[host]), host, {}

    def run(
        self,
        host: str,
        args: Sequence[str],
        *,
        timeout: float = 180,
        check: bool = True,
    ) -> RemoteResult:
        del timeout, check
        values = tuple(args)
        self.calls.append((host, values))
        if values[0] == "gate":
            try:
                check_gate(self.configs[host])
            except HubRuntimeError as exc:
                return RemoteResult(host, values, 1, "", str(exc))
            return RemoteResult(host, values, 0, "{}", "")
        raise AssertionError(f"unexpected run call: {host} {values}")

    def json(self, host: str, args: Sequence[str], *, timeout: float = 180) -> dict[str, Any]:
        del timeout
        values = tuple(args)
        self.calls.append((host, values))
        config = self.configs[host]
        command = values[0]
        if command == "begin-transition":
            return begin_transition(config, target_hub=argument(values, "--target")).to_dict()
        if command == "snapshot":
            return create_snapshot(config, read_record(config), quiesced="--quiesced" in values)
        if command == "attach-generation":
            return attach_transition_generation(
                config, generation=argument(values, "--generation")
            ).to_dict()
        if command == "cache-routing":
            record = read_record(config)
            write_routing_cache(config, record)
            return record.to_dict()
        if command == "snapshots":
            return {"snapshots": [str(path) for path in list_valid_snapshots(config)]}
        if command == "validate-snapshot":
            manifest, temporary = validate_snapshot(config, Path(values[1]))
            shutil.rmtree(temporary)
            return manifest
        if command == "restore":
            return restore_snapshot(config, Path(values[1]))
        if command == "activate":
            return activate_transition(
                config, generation=argument(values, "--generation")
            ).to_dict()
        if command == "local-status":
            return {
                "versions": {
                    "omnigent": omnigent_version(config.omnigent_bin),
                    "bridge": bridge_version(config.bridge_project),
                    "hub": hub_version(config.dotfiles / "services/omnigent-hub"),
                }
            }
        if command in {"services", "route-ensure", "reconcile-services", "quiesce-check"}:
            return {}
        raise AssertionError(f"unexpected json call: {host} {values}")


def argument(values: tuple[str, ...], flag: str) -> str:
    return values[values.index(flag) + 1]


def test_real_snapshot_promotion_and_failback_preserve_both_eras(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "omnigent_hub.storage.os.path.ismount",
        lambda path: path == hub_config.storage_mount,
    )
    standby = standby_config(hub_config, tmp_path)
    initialize(hub_config, active_hub="primary.example.com")
    remote = InProcessRemote({"primary.example.com": hub_config, "standby.example.com": standby})

    promoted = HandoffOrchestrator(hub_config, remote).handoff(
        "standby.example.com",
        unexpected=False,
        source_confirmed_stopped=False,
        dry_run=False,
    )

    assert promoted.target == "standby.example.com"
    assert check_gate(standby).record.active_hub == "standby.example.com"
    assert database_count(standby.chat_db, "sessions") == 3
    assert database_count(standby.bridge_db, "gchat_inbound") == 2
    assert (standby.artifacts_dir / "blob").read_bytes() == b"artifact"

    with sqlite3.connect(standby.chat_db) as db:
        db.execute("INSERT INTO sessions (value) VALUES ('ftw-era')")
    with sqlite3.connect(standby.bridge_db) as db:
        db.execute("INSERT INTO gchat_inbound (value) VALUES ('ftw-phone-reply')")
    (standby.artifacts_dir / "ftw-era").write_bytes(b"ftw")

    failed_back = HandoffOrchestrator(standby, remote).handoff(
        "primary.example.com",
        unexpected=False,
        source_confirmed_stopped=False,
        dry_run=False,
    )

    assert failed_back.target == "primary.example.com"
    assert check_gate(hub_config).record.active_hub == "primary.example.com"
    assert database_count(hub_config.chat_db, "sessions") == 4
    assert database_count(hub_config.bridge_db, "gchat_inbound") == 3
    assert (hub_config.artifacts_dir / "ftw-era").read_bytes() == b"ftw"


def standby_config(primary: HubConfig, tmp_path: Path) -> HubConfig:
    home = tmp_path / "standby-home"
    data = home / ".omnigent"
    data.mkdir(parents=True)
    create_database(data / "chat.db", "sessions", "standby-old")
    create_database(data / "google-chat.sqlite3", "gchat_inbound", "standby-old")
    (data / "artifacts").mkdir()
    return replace(
        primary,
        home=home,
        local_fqdn="standby.example.com",
        data_dir=data,
        local_state_dir=home / ".local/state/omnigent-hub",
        routing_cache=home / ".config/omnigent/active-hub.json",
    )


def create_database(path: Path, table: str, value: str) -> None:
    with sqlite3.connect(path) as db:
        db.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, value TEXT)")
        db.execute(f"INSERT INTO {table} (value) VALUES (?)", (value,))


def database_count(path: Path, table: str) -> int:
    with sqlite3.connect(path) as db:
        return int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
