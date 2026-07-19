from __future__ import annotations

import io
import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from omnigent_hub.config import HubConfig
from omnigent_hub.models import ActiveHubRecord
from omnigent_hub.runtime import (
    HubRuntimeError,
    activate_transition,
    assert_sessions_quiescent,
    attach_transition_generation,
    begin_transition,
    check_gate,
    force_start,
    initialize,
    read_force_override,
    reconcile_local_route,
    reconcile_services,
    resolve_routing_record,
    service_action,
)
from omnigent_hub.storage import StorageError
from omnigent_hub.storage import read_record as read_shared_record


def test_initialize_gate_transition_and_activate(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os.path, "ismount", lambda path: path == hub_config.storage_mount)
    initial = initialize(hub_config, active_hub="primary.example.com")
    assert initial.epoch == 1
    assert check_gate(hub_config).record == initial

    transition = begin_transition(hub_config, target_hub="standby.example.com")
    assert transition.epoch == 2
    assert transition.state == "transition"
    with pytest.raises(HubRuntimeError, match="fenced by transition"):
        check_gate(hub_config)
    transition = attach_transition_generation(hub_config, generation="generation-2")
    assert transition.restored_generation == "generation-2"

    standby = replace(hub_config, local_fqdn="standby.example.com")
    activated = activate_transition(standby, generation="generation-2")
    assert activated.active_hub == "standby.example.com"
    assert activated.restored_generation == "generation-2"
    assert check_gate(standby).record == activated
    with pytest.raises(HubRuntimeError, match="active hub is standby"):
        check_gate(hub_config)


def test_gate_rejects_stale_activation_marker(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os.path, "ismount", lambda path: path == hub_config.storage_mount)
    initialize(hub_config, active_hub="primary.example.com")
    marker = json.loads(hub_config.activation_marker.read_text(encoding="utf-8"))
    marker["activation_id"] = "stale"
    hub_config.activation_marker.write_text(json.dumps(marker), encoding="utf-8")
    with pytest.raises(HubRuntimeError, match="does not match"):
        check_gate(hub_config)


def test_initialize_is_idempotent(hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os.path, "ismount", lambda path: path == hub_config.storage_mount)
    first = initialize(hub_config, active_hub="primary.example.com")
    second = initialize(hub_config, active_hub="primary.example.com")
    assert second == first


def test_force_start_requires_storage_outage_and_uses_expiring_override(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os.path, "ismount", lambda path: path == hub_config.storage_mount)
    initialize(hub_config, active_hub="primary.example.com")

    def unavailable(config: HubConfig) -> None:
        raise StorageError("unavailable")

    monkeypatch.setattr("omnigent_hub.runtime.read_record", unavailable)
    monkeypatch.setattr(os.path, "ismount", lambda path: False)
    forced = force_start(hub_config, reason="test outage")
    assert forced.epoch == 2
    assert forced.activation_id is not None and forced.activation_id.startswith("force-2-")
    assert read_force_override(hub_config) == forced
    assert check_gate(hub_config).record == forced


def test_record_read_remounts_after_stale_mount_error(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = initialize_record_for_test(hub_config, "primary.example.com")
    hub_config.record_path.write_text(json.dumps(record.to_dict()), encoding="utf-8")
    monkeypatch.setattr(os.path, "ismount", lambda path: path == hub_config.storage_mount)
    original_read_text = type(hub_config.record_path).read_text
    attempts = 0
    commands: list[list[str]] = []

    def flaky_read(path: Path, *args: Any, **kwargs: Any) -> str:
        nonlocal attempts
        if path == hub_config.record_path:
            attempts += 1
            if attempts == 1:
                raise OSError("stale CAT")
        return original_read_text(path, *args, **kwargs)

    def record_command(argv: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(type(hub_config.record_path), "read_text", flaky_read)
    monkeypatch.setattr("omnigent_hub.storage.run_command", record_command)

    observed = read_shared_record(hub_config, ensure_mounted=True)

    assert observed == record
    assert commands == [["persistent-storage", "remount", "private-30d"]]


def test_active_reconciliation_frees_loopback_before_starting_server(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    actions: list[str] = []
    monkeypatch.setattr(
        "omnigent_hub.runtime.resolve_record",
        lambda config: initialize_record_for_test(config, "primary.example.com"),
    )
    monkeypatch.setattr(
        "omnigent_hub.runtime.reconcile_local_route",
        lambda config, restart_host: {"changed": False, "url": "http://127.0.0.1:6767"},
    )

    def record_action(config: HubConfig, action: str) -> dict[str, str]:
        actions.append(action)
        return {}

    monkeypatch.setattr("omnigent_hub.runtime.service_action", record_action)

    result = reconcile_services(hub_config)

    assert result["state"] == "active"
    assert actions == ["stop-client", "start-core", "start-tail"]


def test_standby_reconciliation_starts_proxy_before_restarting_host(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    standby = replace(hub_config, local_fqdn="standby.example.com")
    actions: list[str] = []
    monkeypatch.setattr(
        "omnigent_hub.runtime.resolve_record",
        lambda config: initialize_record_for_test(config, "primary.example.com"),
    )
    monkeypatch.setattr(
        "omnigent_hub.runtime.reconcile_local_route",
        lambda config, restart_host: {"changed": True, "url": "http://127.0.0.1:6767"},
    )

    def record_action(config: HubConfig, action: str) -> dict[str, str]:
        actions.append(action)
        return {}

    monkeypatch.setattr("omnigent_hub.runtime.service_action", record_action)

    result = reconcile_services(standby)

    assert result["state"] == "standby"
    assert actions == ["stop-hub", "start-client", "restart-host"]


def test_candidate_route_changes_when_activation_changes_without_url_change(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_record = initialize_record_for_test(hub_config, "primary.example.com")
    old_record = replace(old_record, epoch=1, activation_id="activation-1")
    new_record = initialize_record_for_test(hub_config, "standby.example.com")
    hub_config.routing_cache.parent.mkdir(parents=True)
    hub_config.routing_cache.write_text(json.dumps(old_record.to_dict()), encoding="utf-8")
    hub_config.data_dir.joinpath("config.yaml").write_text(
        "server: http://127.0.0.1:6767\n",
        encoding="utf-8",
    )
    environment_file = hub_config.home / ".config/environment.d/omnigent.conf"
    environment_file.parent.mkdir(parents=True)
    environment_file.write_text("OMNIGENT_URL=http://127.0.0.1:6767\n", encoding="utf-8")
    ensure = hub_config.dotfiles / "bin/omnigent-dvsc-ensure"
    ensure.parent.mkdir(parents=True)
    ensure.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    ensure.chmod(0o755)
    monkeypatch.setattr("omnigent_hub.runtime.resolve_record", lambda config: new_record)

    result = reconcile_local_route(hub_config, restart_host=False)

    assert result["url"] == "http://127.0.0.1:6767"
    assert result["changed"] is True
    assert json.loads(hub_config.routing_cache.read_text(encoding="utf-8")) == new_record.to_dict()


def test_peer_route_uses_discovered_cache_without_shared_storage(hub_config: HubConfig) -> None:
    peer = replace(hub_config, local_fqdn="peer.example.com")
    record = initialize_record_for_test(peer, "standby.example.com")
    peer.routing_cache.parent.mkdir(parents=True)
    peer.routing_cache.write_text(json.dumps(record.to_dict()), encoding="utf-8")

    observed = resolve_routing_record(peer)

    assert observed == record


def test_stop_ingress_stops_timer_before_active_snapshot(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "inactive\n", "")

    monkeypatch.setattr("omnigent_hub.runtime.subprocess.run", run)

    service_action(hub_config, "stop-ingress")

    mutations = [call for call in calls if "stop" in call]
    assert mutations == [
        ["systemctl", "--user", "stop", "omnigent-google-chat.service"],
        ["systemctl", "--user", "stop", "omnigent-snapshot.timer"],
        ["systemctl", "--user", "stop", "omnigent-snapshot.service"],
        ["systemctl", "--user", "stop", "omnigent-prodnet.service"],
    ]


def test_quiesce_check_accepts_terminal_sessions(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_session_payload(monkeypatch, [("idle-session", "idle"), ("failed-session", "failed")])

    result = assert_sessions_quiescent(hub_config)

    assert result == {"quiescent": True, "session_count": 2, "busy_sessions": []}


def test_quiesce_check_rejects_running_and_waiting_sessions(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_session_payload(
        monkeypatch,
        [("idle-session", "idle"), ("run-session", "running"), ("wait-session", "waiting")],
    )

    with pytest.raises(HubRuntimeError, match="run-session=running, wait-session=waiting"):
        assert_sessions_quiescent(hub_config)


def test_quiesce_check_fails_closed_when_session_list_is_truncated(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_session_payload(monkeypatch, [("idle-session", "idle")], has_more=True)

    with pytest.raises(HubRuntimeError, match="more than 1000 sessions"):
        assert_sessions_quiescent(hub_config)


def install_session_payload(
    monkeypatch: pytest.MonkeyPatch,
    sessions: list[tuple[str, str]],
    *,
    has_more: bool = False,
) -> None:
    payload = {
        "data": [{"id": session_id, "status": status} for session_id, status in sessions],
        "has_more": has_more,
    }

    class FakeOpener:
        def open(self, url: str, timeout: int) -> io.BytesIO:
            assert url.endswith("/v1/sessions?limit=1000&kind=any")
            assert timeout == 10
            return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(
        "omnigent_hub.runtime.urllib.request.build_opener", lambda *handlers: FakeOpener()
    )


def initialize_record_for_test(config: HubConfig, active_hub: str) -> ActiveHubRecord:
    return ActiveHubRecord(
        format_version=1,
        epoch=2,
        state="active",
        active_hub=active_hub,
        activation_id="activation-2",
        restored_generation="generation-2",
        updated_at="2026-07-18T22:00:00Z",
        updated_by=config.local_fqdn,
    )
