from __future__ import annotations

import errno
import fcntl
import json
import os
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from omnigent_hub.config import HubConfig
from omnigent_hub.models import ActiveHubRecord

CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


class StorageError(RuntimeError):
    pass


@contextmanager
def local_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        os.chmod(path, 0o600)
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise StorageError(f"another operation owns {path}") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run_command(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, check=False, text=True, capture_output=True)


def force_unmount(mount: Path, runner: CommandRunner) -> bool:
    """Reclaim a FUSE mountpoint whose server process is gone.

    `persistent-storage remount` cannot clear one of these: its unmount step
    needs a live manifoldfs to talk to, so a mount abandoned by a crashed
    manifoldfs stays in the mount table and every remount attempt fails against
    it forever. Only a direct fusermount call frees the mountpoint.
    """
    for flag in ("-u", "-uz"):
        if runner(["fusermount", flag, str(mount)]).returncode == 0:
            return True
    return False


def ensure_storage(
    config: HubConfig,
    *,
    timeout_seconds: float = 120,
    force_remount: bool = False,
    runner: CommandRunner | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    runner = runner or run_command
    config.storage_mount.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    attempt = 0
    last_error = "Persistent Storage did not become readable"
    while True:
        attempt += 1
        # Probe readability without gating on os.path.ismount: an abandoned FUSE
        # mount fails lstat, so ismount reports False even though the stale entry
        # still holds the mountpoint and silently defeats every mount attempt.
        # ENOTCONN is the only reliable signal of that state. force_remount skips
        # the probe on the first attempt only, so a mount that turns out to be
        # abandoned still escalates on the next pass.
        if not (force_remount and attempt == 1):
            try:
                config.storage_mount.stat()
                if os.path.ismount(config.storage_mount):
                    return
            except OSError as exc:
                last_error = str(exc)
                if exc.errno == errno.ENOTCONN:
                    force_unmount(config.storage_mount, runner)
        action = "remount" if os.path.ismount(config.storage_mount) else "mount"
        argv = ["persistent-storage", action]
        delegated_cat = os.environ.get("OMNIGENT_HA_DELEGATED_CAT")
        if delegated_cat:
            argv.extend(("--delegated-cat", delegated_cat))
        argv.append(config.storage_mount_name)
        result = runner(argv)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            last_error = detail or f"persistent-storage {action} failed"
        elif os.path.ismount(config.storage_mount):
            try:
                config.storage_mount.stat()
                return
            except OSError as exc:
                last_error = str(exc)
        if time.monotonic() >= deadline:
            raise StorageError(last_error)
        sleep(min(2 ** min(attempt - 1, 4), 10))


def read_record(config: HubConfig, *, ensure_mounted: bool = True) -> ActiveHubRecord:
    if ensure_mounted:
        ensure_storage(config)
    try:
        payload = json.loads(config.record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if not ensure_mounted:
            raise StorageError(f"cannot read active-hub record: {exc}") from exc
        ensure_storage(config, force_remount=True)
        try:
            payload = json.loads(config.record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StorageError(f"cannot read active-hub record: {exc}") from exc
    return ActiveHubRecord.from_dict(payload, config.topology)


def publish_record(config: HubConfig, record: ActiveHubRecord) -> ActiveHubRecord:
    ensure_storage(config)
    config.storage_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = config.record_path.with_name(f".{config.record_path.name}.{uuid.uuid4().hex}.tmp")
    payload = json.dumps(record.to_dict(), indent=2, sort_keys=True) + "\n"
    try:
        with temp.open("x", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, config.record_path)
    finally:
        temp.unlink(missing_ok=True)
    observed = read_record(config, ensure_mounted=False)
    if observed != record:
        raise StorageError("active-hub record did not round-trip after publication")
    return observed


def write_json_atomic(path: Path, value: dict[str, Any], *, mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("x", encoding="utf-8") as handle:
            os.chmod(temp, mode)
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
