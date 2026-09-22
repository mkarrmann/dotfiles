from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from omnigent_hub.cli import _run_quiesced_backup, main
from omnigent_hub.config import HubConfig
from omnigent_hub.models import ActiveHubRecord
from omnigent_hub.runtime import (
    GATE_EXIT_DENIED,
    GATE_EXIT_INDETERMINATE,
    GateDenied,
    GateIndeterminate,
    HubRuntimeError,
)
from omnigent_hub.storage import StorageError


def test_direct_quiesced_backup_restores_services_when_turn_is_active(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = ActiveHubRecord(
        format_version=1,
        epoch=1,
        state="active",
        active_hub="primary.example.com",
        activation_id="activation-1",
        restored_generation=None,
        updated_at="2026-07-18T22:00:00Z",
        updated_by="tester",
    )
    actions: list[str] = []
    reconciled: list[bool] = []
    monkeypatch.setattr("omnigent_hub.cli.read_record", lambda config: record)

    def record_action(config: HubConfig, action: str) -> dict[str, str]:
        actions.append(action)
        return {}

    def reject(config: HubConfig) -> dict[str, object]:
        raise HubRuntimeError("active turn")

    def record_reconcile(config: HubConfig) -> dict[str, object]:
        reconciled.append(True)
        return {}

    monkeypatch.setattr("omnigent_hub.cli.service_action", record_action)
    monkeypatch.setattr("omnigent_hub.cli.assert_sessions_quiescent", reject)
    monkeypatch.setattr("omnigent_hub.cli.reconcile_services", record_reconcile)

    with pytest.raises(HubRuntimeError, match="active turn"):
        _run_quiesced_backup(hub_config)

    assert actions == ["stop-ingress"]
    assert reconciled == [True]


def _force_start_actions(
    hub_config: HubConfig,
    monkeypatch: pytest.MonkeyPatch,
    *,
    route_changed: bool,
    host_registered: bool,
) -> list[str]:
    record = ActiveHubRecord(
        format_version=1,
        epoch=6,
        state="active",
        active_hub="primary.example.com",
        activation_id="force-6-abc",
        restored_generation=None,
        updated_at="2026-08-17T22:42:54Z",
        updated_by="tester",
    )
    actions: list[str] = []

    def record_action(config: HubConfig, action: str) -> dict[str, str]:
        actions.append(action)
        return {}

    monkeypatch.setattr("omnigent_hub.cli.load_config", lambda: hub_config)
    monkeypatch.setattr("omnigent_hub.cli.force_start", lambda config, reason: record)
    monkeypatch.setattr(
        "omnigent_hub.cli.reconcile_local_route",
        lambda config, restart_host: {"changed": route_changed, "url": "http://127.0.0.1:6767"},
    )
    monkeypatch.setattr("omnigent_hub.cli.service_action", record_action)
    monkeypatch.setattr("omnigent_hub.runtime.service_action", record_action)
    monkeypatch.setattr("omnigent_hub.runtime.systemd_state", lambda unit: "active")
    monkeypatch.setattr("omnigent_hub.runtime.unit_active_seconds", lambda unit: 600.0)
    monkeypatch.setattr(
        "omnigent_hub.runtime.probe_host_registered", lambda config: host_registered
    )

    main(
        [
            "force-start",
            "--other-hub-confirmed-stopped",
            "--reason",
            "test outage",
            "--yes",
            "--json",
        ]
    )
    return actions


def test_force_start_leaves_a_healthy_host_alone(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reclaiming ownership this host already held must not kill its runners."""
    actions = _force_start_actions(
        hub_config, monkeypatch, route_changed=False, host_registered=True
    )

    assert "restart-host" not in actions
    assert actions == ["stop-client", "start-core", "start-bridge", "start-watcher"]


def test_force_start_retargets_the_host_when_the_route_moves(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    actions = _force_start_actions(
        hub_config, monkeypatch, route_changed=True, host_registered=True
    )

    assert actions == [
        "stop-client",
        "start-core",
        "restart-host",
        "start-bridge",
        "start-watcher",
    ]


def test_force_start_restarts_a_host_that_is_not_registered(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unchanged route still needs a restart if the host serves no runners."""
    actions = _force_start_actions(
        hub_config, monkeypatch, route_changed=False, host_registered=False
    )

    assert "restart-host" in actions


def _gate_exit_code(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> int:
    def refuse(config: HubConfig) -> object:
        raise failure

    monkeypatch.setattr("omnigent_hub.cli.load_config", lambda: hub_config)
    monkeypatch.setattr("omnigent_hub.cli.check_gate", refuse)
    with pytest.raises(SystemExit) as exit_info:
        main(["gate", "--json"])
    assert isinstance(exit_info.value.code, int)
    return exit_info.value.code


def test_a_denied_gate_exits_with_systemd_s_clean_skip_code(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """systemd.service(5): ExecCondition= exit 1-254 skips the unit, unfailed.

    This is the standby's normal state. It must stay quiet.
    """
    code = _gate_exit_code(hub_config, monkeypatch, GateDenied("active hub is the other one"))

    assert code == GATE_EXIT_DENIED
    assert 1 <= code <= 254


def test_an_indeterminate_gate_exits_with_systemd_s_failure_code(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """255 is the only ExecCondition= code that marks the unit FAILED.

    Regression pin for the five-day outage: every gated unit declined to start
    because ownership was unreadable, and because that exited 1 alongside the
    ordinary standby case, systemd recorded it as a clean skip and nothing
    anywhere went red.
    """
    code = _gate_exit_code(
        hub_config, monkeypatch, GateIndeterminate("cannot establish hub ownership")
    )

    assert code == GATE_EXIT_INDETERMINATE
    assert code == 255


def test_unreadable_storage_at_the_gate_also_fails_the_unit(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = _gate_exit_code(hub_config, monkeypatch, StorageError("mount is gone"))

    assert code == GATE_EXIT_INDETERMINATE



    return ActiveHubRecord(
        format_version=1,
        epoch=7,
        state="active",
        active_hub="primary.example.com",
        activation_id="activation-7",
        restored_generation=None,
        updated_at="2026-09-22T04:17:58Z",
        updated_by="tester",
    )
