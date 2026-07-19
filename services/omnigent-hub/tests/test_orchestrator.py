from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pytest

from omnigent_hub.config import HubConfig
from omnigent_hub.models import ActiveHubRecord
from omnigent_hub.orchestrator import HandoffError, HandoffOrchestrator, _status_warnings
from omnigent_hub.remote import RemoteError, RemoteResult


def active_record() -> ActiveHubRecord:
    return ActiveHubRecord(
        format_version=1,
        epoch=1,
        state="active",
        active_hub="primary.example.com",
        activation_id="activation-1",
        restored_generation="old-generation",
        updated_at="2026-07-18T20:00:00Z",
        updated_by="tester",
    )


def interrupted_transition() -> ActiveHubRecord:
    return ActiveHubRecord(
        format_version=1,
        epoch=2,
        state="transition",
        active_hub=None,
        activation_id=None,
        restored_generation=None,
        updated_at="2026-07-18T20:01:00Z",
        updated_by="tester",
        source_hub="primary.example.com",
        target_hub="standby.example.com",
        transition_id="transition-2",
    )


@dataclass
class FakeRemote:
    record: ActiveHubRecord
    fail_quiesce: bool = False
    mismatch_target_version: bool = False

    def __post_init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def resolve(self) -> tuple[ActiveHubRecord, str, dict[str, str]]:
        return self.record, "primary.example.com", {}

    def run(
        self,
        host: str,
        args: Sequence[str],
        *,
        timeout: float = 180,
        check: bool = True,
    ) -> RemoteResult:
        del timeout, check
        self.calls.append((host, tuple(args)))
        return RemoteResult(host, tuple(args), 1, "", "fenced")

    def json(self, host: str, args: Sequence[str], *, timeout: float = 180) -> dict[str, Any]:
        del timeout
        values = tuple(args)
        self.calls.append((host, values))
        command = values[0]
        if command == "quiesce-check" and self.fail_quiesce:
            raise RemoteError("active turn")
        if command == "local-status":
            omnigent = (
                "omnigent 0.other"
                if self.mismatch_target_version and host == "standby.example.com"
                else "omnigent 0.test"
            )
            return {"versions": {"omnigent": omnigent, "bridge": "sha256:bridge"}}
        if command == "begin-transition":
            return {
                "format_version": 1,
                "epoch": 2,
                "state": "transition",
                "active_hub": None,
                "activation_id": None,
                "restored_generation": None,
                "updated_at": "2026-07-18T20:01:00Z",
                "updated_by": "tester",
                "source_hub": "primary.example.com",
                "target_hub": "standby.example.com",
                "transition_id": "transition-2",
            }
        if command == "begin-unexpected-transition":
            return {
                "format_version": 1,
                "epoch": 2,
                "state": "transition",
                "active_hub": None,
                "activation_id": None,
                "restored_generation": "generation-2",
                "updated_at": "2026-07-18T20:01:00Z",
                "updated_by": "tester",
                "source_hub": "primary.example.com",
                "target_hub": "standby.example.com",
                "transition_id": "unexpected-2",
            }
        if command == "snapshot":
            return {"generation_id": "generation-2", "archive_path": "/snap/generation-2.tar.gz"}
        if command == "attach-generation":
            return {
                "format_version": 1,
                "epoch": 2,
                "state": "transition",
                "active_hub": None,
                "activation_id": None,
                "restored_generation": "generation-2",
                "updated_at": "2026-07-18T20:02:00Z",
                "updated_by": "tester",
                "source_hub": "primary.example.com",
                "target_hub": "standby.example.com",
                "transition_id": "transition-2",
            }
        if command == "cache-routing":
            if host == "standby.example.com":
                transition_started = any(
                    call[1][0]
                    in {"begin-transition", "begin-unexpected-transition", "attach-generation"}
                    for call in self.calls
                )
                if not transition_started:
                    return active_record().to_dict()
                transition_id = (
                    "unexpected-2"
                    if any(call[1][0] == "begin-unexpected-transition" for call in self.calls)
                    else "transition-2"
                )
                return {
                    "format_version": 1,
                    "epoch": 2,
                    "state": "transition",
                    "active_hub": None,
                    "activation_id": None,
                    "restored_generation": "generation-2",
                    "updated_at": "2026-07-18T20:02:00Z",
                    "updated_by": "tester",
                    "source_hub": "primary.example.com",
                    "target_hub": "standby.example.com",
                    "transition_id": transition_id,
                }
            return {
                "format_version": 1,
                "epoch": 2,
                "state": "active",
                "active_hub": "standby.example.com",
                "activation_id": "activation-2",
                "restored_generation": "generation-2",
                "updated_at": "2026-07-18T20:03:00Z",
                "updated_by": "tester",
            }
        if command == "snapshots":
            return {"snapshots": ["/snap/generation-2.tar.gz"]}
        if command == "validate-snapshot":
            return {"generation_id": "generation-2"}
        if command == "activate":
            return {
                "epoch": 2,
                "state": "active",
                "active_hub": "standby.example.com",
                "activation_id": "activation-2",
                "restored_generation": "generation-2",
            }
        return {}


def test_planned_handoff_orders_fence_restore_and_tail(hub_config: HubConfig) -> None:
    remote = FakeRemote(active_record())
    result = HandoffOrchestrator(hub_config, remote).handoff(
        "standby.example.com",
        unexpected=False,
        source_confirmed_stopped=False,
        dry_run=False,
    )
    assert result.generation == "generation-2"
    assert result.gchat_reconciliation_required is False
    calls = remote.calls
    stop_ingress = calls.index(("primary.example.com", ("services", "stop-ingress", "--json")))
    begin_transition = calls.index(
        (
            "primary.example.com",
            ("begin-transition", "--target", "standby.example.com", "--yes", "--json"),
        )
    )
    quiesce = calls.index(("primary.example.com", ("quiesce-check", "--json")))
    assert stop_ingress < quiesce < begin_transition
    stop_server = calls.index(("primary.example.com", ("services", "stop-server", "--json")))
    snapshot = calls.index(("primary.example.com", ("snapshot", "--quiesced", "--json")))
    assert stop_server < snapshot
    stop_client = calls.index(("standby.example.com", ("services", "stop-client", "--json")))
    refresh_target = calls.index(
        ("standby.example.com", ("cache-routing", "--force-remount", "--json"))
    )
    snapshots = calls.index(("standby.example.com", ("snapshots", "--json")))
    restore = calls.index(
        ("standby.example.com", ("restore", "/snap/generation-2.tar.gz", "--yes", "--json"))
    )
    assert refresh_target < snapshots < restore
    start_core = calls.index(("standby.example.com", ("services", "start-core", "--json")))
    assert stop_client < start_core
    refresh_source = calls.index(
        ("primary.example.com", ("cache-routing", "--force-remount", "--json"))
    )
    reconcile_source = calls.index(("primary.example.com", ("reconcile-services", "--json")))
    assert refresh_source < reconcile_source
    assert (
        "primary.example.com",
        ("reconcile-services", "--json"),
    ) in calls
    assert calls[-1] == ("standby.example.com", ("services", "start-tail", "--json"))


def test_dry_run_does_not_issue_mutating_calls(hub_config: HubConfig) -> None:
    remote = FakeRemote(active_record())
    result = HandoffOrchestrator(hub_config, remote).handoff(
        "standby.example.com",
        unexpected=False,
        source_confirmed_stopped=False,
        dry_run=True,
    )
    assert remote.calls == []
    assert result.generation == "<new-quiesced-generation>"
    assert "stop ingress" in result.steps[0]


def test_planned_handoff_restores_ingress_when_turn_is_active(
    hub_config: HubConfig,
) -> None:
    remote = FakeRemote(active_record(), fail_quiesce=True)

    with pytest.raises(HandoffError, match="active turn"):
        HandoffOrchestrator(hub_config, remote).handoff(
            "standby.example.com",
            unexpected=False,
            source_confirmed_stopped=False,
            dry_run=False,
        )

    assert remote.calls == [
        ("primary.example.com", ("local-status", "--json")),
        ("standby.example.com", ("local-status", "--json")),
        ("primary.example.com", ("services", "stop-ingress", "--json")),
        ("primary.example.com", ("quiesce-check", "--json")),
        ("primary.example.com", ("reconcile-services", "--json")),
    ]


def test_planned_handoff_rejects_version_drift_before_stopping_source(
    hub_config: HubConfig,
) -> None:
    remote = FakeRemote(active_record(), mismatch_target_version=True)

    with pytest.raises(HandoffError, match="omnigent version mismatch"):
        HandoffOrchestrator(hub_config, remote).handoff(
            "standby.example.com",
            unexpected=False,
            source_confirmed_stopped=False,
            dry_run=False,
        )

    assert remote.calls == [
        ("primary.example.com", ("local-status", "--json")),
        ("standby.example.com", ("local-status", "--json")),
    ]


def test_planned_handoff_resumes_transition_missing_final_generation(
    hub_config: HubConfig,
) -> None:
    remote = FakeRemote(interrupted_transition())

    result = HandoffOrchestrator(hub_config, remote).handoff(
        "standby.example.com",
        unexpected=False,
        source_confirmed_stopped=False,
        dry_run=False,
    )

    assert result.generation == "generation-2"
    stop_all = remote.calls.index(("primary.example.com", ("services", "stop-all", "--json")))
    snapshot = remote.calls.index(("primary.example.com", ("snapshot", "--quiesced", "--json")))
    attach = remote.calls.index(
        (
            "primary.example.com",
            ("attach-generation", "--generation", "generation-2", "--json"),
        )
    )
    assert stop_all < snapshot < attach


def test_unexpected_handoff_requires_fencing_and_leaves_bridge_stopped(
    hub_config: HubConfig,
) -> None:
    remote = FakeRemote(active_record())
    with pytest.raises(HandoffError, match="independent confirmation"):
        HandoffOrchestrator(hub_config, remote).handoff(
            "standby.example.com",
            unexpected=True,
            source_confirmed_stopped=False,
            dry_run=False,
        )
    assert remote.calls == []

    result = HandoffOrchestrator(hub_config, remote).handoff(
        "standby.example.com",
        unexpected=True,
        source_confirmed_stopped=True,
        dry_run=False,
    )

    assert result.gchat_reconciliation_required is True
    refresh = remote.calls.index(
        ("standby.example.com", ("cache-routing", "--force-remount", "--json"))
    )
    snapshots = remote.calls.index(("standby.example.com", ("snapshots", "--json")))
    stop_all = remote.calls.index(("standby.example.com", ("services", "stop-all", "--json")))
    fence = remote.calls.index(
        (
            "standby.example.com",
            (
                "begin-unexpected-transition",
                "--source",
                "primary.example.com",
                "--generation",
                "generation-2",
                "--source-confirmed-stopped",
                "--yes",
                "--json",
            ),
        )
    )
    assert refresh < snapshots < stop_all < fence
    assert remote.calls[-1] == (
        "standby.example.com",
        ("services", "start-timer", "--json"),
    )
    assert not any(call[1][:2] == ("services", "start-bridge") for call in remote.calls)


def test_status_warns_for_stale_cache_and_missing_standby_proxy() -> None:
    record = active_record()
    active_services = {
        "omnigent-server.service": "active",
        "omnigent-prodnet.service": "active",
        "omnigent-google-chat.service": "active",
        "omnigent-snapshot.timer": "active",
        "omnigent-client-proxy.service": "inactive",
    }
    hosts = {
        "primary.example.com": {
            "routing_cache": record.to_dict(),
            "services": active_services,
            "gate": {"allowed": True},
        },
        "standby.example.com": {
            "routing_cache": {**record.to_dict(), "epoch": 0},
            "services": {"omnigent-client-proxy.service": "inactive"},
            "gate": {"allowed": False},
        },
    }

    warnings = _status_warnings(record, hosts)

    assert "WARNING: standby.example.com routing cache is stale" in warnings
    assert any("client proxy owners [] differ from standby" in warning for warning in warnings)
