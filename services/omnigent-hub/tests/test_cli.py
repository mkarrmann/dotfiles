from __future__ import annotations

import pytest

from omnigent_hub.cli import _run_quiesced_backup
from omnigent_hub.config import HubConfig
from omnigent_hub.models import ActiveHubRecord
from omnigent_hub.runtime import HubRuntimeError


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
