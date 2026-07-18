from __future__ import annotations

import pytest

from omnigent_hub.models import ActiveHubRecord, Topology, ValidationError

TOPOLOGY = Topology("primary.example.com", "standby.example.com", 6767)


def test_active_record_round_trips() -> None:
    payload = {
        "format_version": 1,
        "epoch": 4,
        "state": "active",
        "active_hub": "primary.example.com",
        "activation_id": "activation-4",
        "restored_generation": "generation-3",
        "updated_at": "2026-07-18T20:00:00Z",
        "updated_by": "tester",
    }
    record = ActiveHubRecord.from_dict(payload, TOPOLOGY)
    assert record.to_dict() == payload


def test_transition_record_requires_no_active_hub() -> None:
    payload = {
        "format_version": 1,
        "epoch": 5,
        "state": "transition",
        "active_hub": None,
        "activation_id": None,
        "restored_generation": "generation-4",
        "updated_at": "2026-07-18T20:00:00Z",
        "updated_by": "tester",
        "source_hub": "primary.example.com",
        "target_hub": "standby.example.com",
        "transition_id": "transition-5",
    }
    record = ActiveHubRecord.from_dict(payload, TOPOLOGY)
    assert record.state == "transition"
    assert record.active_hub is None


@pytest.mark.parametrize(
    "change",
    [
        {"epoch": 0},
        {"active_hub": "other.example.com"},
        {"activation_id": None},
        {"state": "unknown"},
    ],
)
def test_invalid_active_records_are_rejected(change: dict[str, object]) -> None:
    payload: dict[str, object] = {
        "format_version": 1,
        "epoch": 1,
        "state": "active",
        "active_hub": "primary.example.com",
        "activation_id": "activation-1",
        "restored_generation": None,
        "updated_at": "2026-07-18T20:00:00Z",
        "updated_by": "tester",
    }
    payload.update(change)
    with pytest.raises(ValidationError):
        ActiveHubRecord.from_dict(payload, TOPOLOGY)
