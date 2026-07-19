from __future__ import annotations

import json

import pytest

from omnigent_hub.config import HubConfig
from omnigent_hub.storage import read_record


def test_invalid_cached_record_forces_remount_and_retry(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub_config.record_path.write_text('{"epoch": 2, "state": "transit', encoding="utf-8")
    valid = {
        "format_version": 1,
        "epoch": 3,
        "state": "active",
        "active_hub": "primary.example.com",
        "activation_id": "activation-3",
        "restored_generation": "generation-2",
        "updated_at": "2026-07-19T00:02:58Z",
        "updated_by": "tester",
    }
    remounts: list[bool] = []

    def ensure(config: HubConfig, *, force_remount: bool = False, **_: object) -> None:
        remounts.append(force_remount)
        if force_remount:
            config.record_path.write_text(json.dumps(valid), encoding="utf-8")

    monkeypatch.setattr("omnigent_hub.storage.ensure_storage", ensure)

    record = read_record(hub_config)

    assert record.epoch == 3
    assert record.active_hub == "primary.example.com"
    assert remounts == [False, True]
