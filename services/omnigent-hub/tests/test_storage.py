from __future__ import annotations

import errno
import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

from omnigent_hub.config import HubConfig
from omnigent_hub.storage import StorageError, ensure_storage, local_lock, read_record


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


def _mount_harness(
    hub_config: HubConfig,
    monkeypatch: pytest.MonkeyPatch,
    *,
    state: dict[str, Any],
    heal_on: str,
) -> list[list[str]]:
    """Drive ensure_storage against a scriptable mountpoint.

    `state` carries `ismount` (what os.path.ismount reports) and `stat_errno`
    (None once the mountpoint is readable); `heal_on` names the command that
    makes the mountpoint healthy.
    """
    commands: list[list[str]] = []
    original_stat = type(hub_config.storage_mount).stat

    def fake_stat(path: Path, *args: Any, **kwargs: Any) -> os.stat_result:
        if path == hub_config.storage_mount and state["stat_errno"] is not None:
            raise OSError(state["stat_errno"], "mountpoint is not readable")
        return original_stat(path, *args, **kwargs)

    def record_command(argv: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(argv)
        if argv[0] == "fusermount":
            # The stale entry is gone; the bare mountpoint directory remains.
            state.update(ismount=False, stat_errno=errno.ENOENT)
        elif argv[:2] == ["persistent-storage", heal_on]:
            state.update(ismount=True, stat_errno=None)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(os.path, "ismount", lambda path: bool(state["ismount"]))
    monkeypatch.setattr(type(hub_config.storage_mount), "stat", fake_stat)
    ensure_storage(hub_config, runner=record_command, sleep=lambda seconds: None)
    return commands


def test_abandoned_mount_is_force_unmounted_before_remounting(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An abandoned FUSE mount fails lstat, so os.path.ismount reports False even
    # though the stale entry still holds the mountpoint. Detection must not gate
    # on ismount, or the escalation never fires.
    commands = _mount_harness(
        hub_config,
        monkeypatch,
        state={"ismount": False, "stat_errno": errno.ENOTCONN},
        heal_on="mount",
    )

    assert commands == [
        ["fusermount", "-u", str(hub_config.storage_mount)],
        ["persistent-storage", "mount", "private-30d"],
    ]


def test_unreadable_mount_is_remounted_without_forcing_an_unmount(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Only an abandoned mount earns a fusermount teardown; anything else stays
    # on the ordinary remount path.
    commands = _mount_harness(
        hub_config,
        monkeypatch,
        state={"ismount": True, "stat_errno": errno.EACCES},
        heal_on="remount",
    )

    assert commands == [["persistent-storage", "remount", "private-30d"]]


def _hold_lock(path: Path, seconds: float) -> None:
    """Hold *path* from another thread for *seconds*, returning once it is held."""
    held = threading.Event()

    def hold() -> None:
        with local_lock(path):
            held.set()
            threading.Event().wait(seconds)

    threading.Thread(target=hold, daemon=True).start()
    assert held.wait(5)


def test_a_held_lock_fails_at_once_by_default(tmp_path: Path) -> None:
    lock = tmp_path / "snapshot.lock"
    _hold_lock(lock, 5)

    with pytest.raises(StorageError, match="another operation owns"):
        with local_lock(lock):
            pass


def test_a_waiting_lock_is_acquired_once_the_holder_releases(tmp_path: Path) -> None:
    lock = tmp_path / "snapshot.lock"
    _hold_lock(lock, 0.5)
    announcements: list[bool] = []

    with local_lock(lock, wait_seconds=10, on_wait=lambda: announcements.append(True)):
        pass

    assert announcements == [True]


def test_a_waiting_lock_gives_up_at_its_deadline(tmp_path: Path) -> None:
    lock = tmp_path / "snapshot.lock"
    _hold_lock(lock, 5)

    with pytest.raises(StorageError, match="another operation owns"):
        with local_lock(lock, wait_seconds=0.3):
            pass
