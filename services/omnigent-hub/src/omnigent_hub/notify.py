"""Escalation for hub failures that would otherwise be silent.

Everything in this deployment is a systemd --user unit on a devserver nobody
watches. That is fine while units fail loudly, and it is how a storage expiry
turned into a five-day outage when one did not: the reconcile timer recorded
``"state": "degraded"`` 7,298 times, the server declined to start on every
attempt, and no surface anywhere said so.

This module is the other half of that fix. Making the gate exit 255 gets systemd
to mark a unit failed; this decides whether a failed unit is worth interrupting
someone over, and sends it somewhere a phone will see.

Two rules keep it quiet enough to stay trusted:

* a unit re-notifies at most once an hour, so a timer failing every 60 seconds
  produces one message rather than sixty;
* a single degraded reconcile cycle is a blip and says nothing. Only a streak
  past ``DEGRADED_STREAK_ALERT_THRESHOLD`` escalates, which is the same threshold
  reconcile records against, read from one place rather than duplicated.

The durable record is written unconditionally: a notification that could not be
sent must still leave evidence that the condition happened.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from omnigent_hub.config import HubConfig
from omnigent_hub.runtime import (
    DEGRADED_STREAK_ALERT_THRESHOLD,
    GateDenied,
    HubRuntimeError,
    check_gate,
    manifold_ttl_seconds,
    utc_now,
)
from omnigent_hub.storage import StorageError, write_json_atomic

#: Minimum gap between notifications about the same unit.
ALERT_REPEAT_SECONDS = 60 * 60

#: Remaining shared-storage lifetime below which the expiry is worth mentioning
#: in the alert body. A week is enough warning to act without rushing.
STORAGE_TTL_WARN_SECONDS = 7 * 24 * 60 * 60

MessageSender = Callable[[str], None]


def _meta_chat_sender(space: str) -> MessageSender:
    """Post through the same `meta` CLI path the Google Chat bridge uses.

    Reused rather than reimplemented because it is already authenticated, already
    reaches the phone, and already the channel these messages belong next to.
    """

    def send(text: str) -> None:
        subprocess.run(
            [
                "meta",
                "google.chat.message",
                "send",
                f"--space-name={space}",
                "--as-meta-bot",
                "--stdin",
                "--no-color",
            ],
            input=text.encode("utf-8"),
            check=True,
            capture_output=True,
            timeout=30,
        )

    return send


def resolve_sender(environ: Mapping[str, str] | None = None) -> MessageSender | None:
    env = os.environ if environ is None else environ
    space = env.get("OMNIGENT_GCHAT_SPACE")
    return _meta_chat_sender(space) if space else None


def _alert_state_path(config: HubConfig) -> Path:
    return config.local_state_dir / "alert-state.json"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _degraded_streak(config: HubConfig) -> int:
    count = _read_json(config.degraded_streak).get("count")
    return count if isinstance(count, int) and count > 0 else 0


def _gate_summary(config: HubConfig) -> str:
    try:
        check_gate(config)
    except GateDenied as exc:
        return f"gate denied (normal for a standby): {exc}"
    except (HubRuntimeError, StorageError, ValueError) as exc:
        return f"gate INDETERMINATE: {exc}"
    return "gate allows"


def _storage_warning(config: HubConfig) -> str | None:
    """Flag a shared-storage expiry while there is still time to act on it.

    The parent is the one that matters: no supported operation refreshes a
    manifoldfs directory's expiry, and when it fires it deletes its whole subtree
    however young the files inside are.
    """
    parent_ttl = manifold_ttl_seconds(config.record_path.parent)
    if parent_ttl is None or parent_ttl <= 0:
        return None
    if parent_ttl > STORAGE_TTL_WARN_SECONDS:
        return None
    days = parent_ttl / 86400
    return (
        f"the record's parent directory {config.record_path.parent} expires in "
        f"{days:.1f} days and CANNOT be refreshed -- move the record before then"
    )


def should_notify(config: HubConfig, *, unit: str, now: float) -> tuple[bool, str]:
    """Whether *unit* failing right now is worth a message, and why or why not."""
    streak = _degraded_streak(config)
    # Only a *degraded* reconcile is throttled, and the streak is what proves it
    # was one: _record_degraded_cycle writes the file before the process exits
    # nonzero, so by the time this runs a degraded cycle has always left a count
    # of at least 1. A reconcile failure with no streak therefore failed for some
    # other reason entirely -- an exception, a systemd error -- which is rarer
    # and less explicable than a storage outage, and always worth escalating.
    if unit.startswith("omnigent-hub-reconcile") and 0 < streak < DEGRADED_STREAK_ALERT_THRESHOLD:
        return False, f"degraded streak {streak} is below {DEGRADED_STREAK_ALERT_THRESHOLD}"
    last = _read_json(_alert_state_path(config)).get(unit)
    if isinstance(last, int | float) and now - last < ALERT_REPEAT_SECONDS:
        return False, f"already notified {int(now - last)}s ago"
    return True, "escalating"


def alert(
    config: HubConfig,
    *,
    unit: str,
    sender: MessageSender | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Record a unit failure, and notify unless it is noise."""
    now = time.time() if now is None else now
    notify, reason = should_notify(config, unit=unit, now=now)
    detail = {
        "unit": unit,
        "host": config.local_fqdn,
        "at": utc_now(),
        "degraded_streak": _degraded_streak(config),
        "gate": _gate_summary(config),
        "storage_warning": _storage_warning(config),
        "notified": False,
        "reason": reason,
    }

    if notify:
        sender = sender if sender is not None else resolve_sender()
        if sender is None:
            detail["reason"] = "no notification channel configured"
        else:
            try:
                sender(_format(detail))
            except (OSError, subprocess.SubprocessError) as exc:
                # A channel failure must not lose the event -- the durable
                # record below is the part that always has to happen.
                detail["reason"] = f"send failed: {exc}"
            else:
                detail["notified"] = True
                state = _read_json(_alert_state_path(config))
                state[unit] = now
                write_json_atomic(_alert_state_path(config), state)

    config.local_state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (config.local_state_dir / "alerts.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(detail, sort_keys=True) + "\n")
    return detail


def _format(detail: Mapping[str, Any]) -> str:
    lines = [
        f"Omnigent hub alert on {detail['host']}",
        f"unit: {detail['unit']}",
        f"{detail['gate']}",
    ]
    streak = detail.get("degraded_streak")
    if isinstance(streak, int) and streak:
        lines.append(f"shared storage unreadable for {streak} consecutive reconcile cycles")
    warning = detail.get("storage_warning")
    if warning:
        lines.append(str(warning))
    lines.append("check: omnigent-hub local-status --json | jq '.gate, .storage_expiry'")
    return "\n".join(lines)
