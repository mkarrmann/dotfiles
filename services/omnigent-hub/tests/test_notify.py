from __future__ import annotations

import json
import os

import pytest

from omnigent_hub.config import HubConfig
from omnigent_hub.notify import ALERT_REPEAT_SECONDS, alert
from omnigent_hub.runtime import DEGRADED_STREAK_ALERT_THRESHOLD, initialize
from omnigent_hub.storage import write_json_atomic


@pytest.fixture
def owned(hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch) -> HubConfig:
    monkeypatch.setattr(os.path, "ismount", lambda path: path == hub_config.storage_mount)
    initialize(hub_config, active_hub="primary.example.com")
    return hub_config


def _sent(config: HubConfig, **kwargs: object) -> list[str]:
    messages: list[str] = []
    alert(config, sender=messages.append, **kwargs)  # type: ignore[arg-type]
    return messages


def _alerts(config: HubConfig) -> list[dict[str, object]]:
    path = config.local_state_dir / "alerts.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_a_single_degraded_cycle_does_not_wake_anyone(owned: HubConfig) -> None:
    """A blip must stay quiet or the channel stops being worth reading."""
    write_json_atomic(owned.degraded_streak, {"count": 1, "since": "2026-09-16T23:00:00Z"})

    assert _sent(owned, unit="omnigent-hub-reconcile.service") == []


def test_a_sustained_degraded_streak_escalates(owned: HubConfig) -> None:
    write_json_atomic(
        owned.degraded_streak,
        {"count": DEGRADED_STREAK_ALERT_THRESHOLD, "since": "2026-09-16T23:00:00Z"},
    )

    messages = _sent(owned, unit="omnigent-hub-reconcile.service")

    assert len(messages) == 1
    assert "omnigent-hub-reconcile.service" in messages[0]
    assert f"{DEGRADED_STREAK_ALERT_THRESHOLD} consecutive reconcile cycles" in messages[0]


def test_a_failed_unit_escalates_regardless_of_the_streak(owned: HubConfig) -> None:
    """The streak only gates reconcile. A gated unit failing is already rare."""
    assert len(_sent(owned, unit="omnigent-server.service")) == 1


def test_the_same_unit_does_not_re_notify_within_the_repeat_window(owned: HubConfig) -> None:
    first = _sent(owned, unit="omnigent-server.service", now=1_000_000.0)
    soon = _sent(owned, unit="omnigent-server.service", now=1_000_000.0 + 60)
    later = _sent(owned, unit="omnigent-server.service", now=1_000_000.0 + ALERT_REPEAT_SECONDS + 1)

    assert (len(first), len(soon), len(later)) == (1, 0, 1)


def test_every_occurrence_is_recorded_even_when_it_is_not_sent(owned: HubConfig) -> None:
    """Suppressing a notification must not suppress the evidence."""
    _sent(owned, unit="omnigent-server.service", now=1_000_000.0)
    _sent(owned, unit="omnigent-server.service", now=1_000_000.0 + 60)

    entries = _alerts(owned)
    assert [entry["notified"] for entry in entries] == [True, False]
    assert "already notified" in str(entries[1]["reason"])


def test_a_channel_failure_still_leaves_a_durable_record(owned: HubConfig) -> None:
    def explode(text: str) -> None:
        raise OSError("meta CLI is unavailable")

    alert(owned, unit="omnigent-server.service", sender=explode)

    entries = _alerts(owned)
    assert entries[-1]["notified"] is False
    assert "send failed" in str(entries[-1]["reason"])


def test_a_failed_send_is_not_recorded_as_notified(owned: HubConfig) -> None:
    """Otherwise the repeat window would suppress the retry of a lost message."""

    def explode(text: str) -> None:
        raise OSError("meta CLI is unavailable")

    alert(owned, unit="omnigent-server.service", sender=explode, now=1_000_000.0)
    retried = _sent(owned, unit="omnigent-server.service", now=1_000_000.0 + 60)

    assert len(retried) == 1


def test_an_expiring_storage_parent_is_called_out(
    owned: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The warning that would have caught this a month early."""
    monkeypatch.setattr("omnigent_hub.notify.manifold_ttl_seconds", lambda path: 3 * 24 * 60 * 60)

    messages = _sent(owned, unit="omnigent-server.service")

    assert "expires in 3.0 days" in messages[0]
    assert "CANNOT be refreshed" in messages[0]


def test_a_non_expiring_storage_parent_says_nothing(
    owned: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("omnigent_hub.notify.manifold_ttl_seconds", lambda path: 0)

    messages = _sent(owned, unit="omnigent-server.service")

    assert "expires in" not in messages[0]


def test_a_reconcile_failure_with_no_streak_escalates(owned: HubConfig) -> None:
    """No streak means it did not fail on storage -- so it failed on something
    less explicable, and that is exactly what should not be throttled."""
    assert not owned.degraded_streak.exists()

    assert len(_sent(owned, unit="omnigent-hub-reconcile.service")) == 1
