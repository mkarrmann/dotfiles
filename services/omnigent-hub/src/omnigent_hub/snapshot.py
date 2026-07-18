from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from omnigent_hub.config import HubConfig
from omnigent_hub.models import ActiveHubRecord
from omnigent_hub.storage import ensure_storage, write_json_atomic

ARCHIVE_FORMAT_VERSION = 1


class SnapshotError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def bridge_version(project: Path) -> str:
    digest = hashlib.sha256()
    paths = [project / "pyproject.toml", project / "uv.lock"]
    source = project / "src"
    if source.exists():
        paths.extend(
            path
            for path in source.rglob("*")
            if path.is_file()
            and path.suffix != ".pyc"
            and not any(part == "__pycache__" for part in path.parts)
        )
    existing = sorted((path for path in paths if path.is_file()), key=lambda p: str(p))
    if not existing:
        raise SnapshotError(f"Google Chat bridge source is missing under {project}")
    for path in existing:
        relative = path.relative_to(project).as_posix().encode()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return f"sha256:{digest.hexdigest()}"


def omnigent_version(binary: Path) -> str:
    result = subprocess.run([str(binary), "--version"], check=False, text=True, capture_output=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SnapshotError(f"cannot determine Omnigent version: {detail}")
    version = result.stdout.strip()
    if not version:
        raise SnapshotError("Omnigent version output was empty")
    return version


def sqlite_backup(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise SnapshotError(f"required database is missing: {source}")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as source_db:
            with sqlite3.connect(destination) as destination_db:
                source_db.backup(destination_db)
    except sqlite3.Error as exc:
        raise SnapshotError(f"SQLite backup failed for {source}: {exc}") from exc
    os.chmod(destination, 0o600)


def sqlite_summary(path: Path) -> dict[str, Any]:
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
            integrity = db.execute("PRAGMA integrity_check").fetchone()
            if integrity != ("ok",):
                raise SnapshotError(f"integrity_check failed for {path}: {integrity}")
            table_rows = db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            table_counts = {
                str(name): int(db.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
                for (name,) in table_rows
            }
            schema_versions: list[str] = []
            if "alembic_version" in table_counts:
                schema_versions = [
                    str(row[0])
                    for row in db.execute(
                        "SELECT version_num FROM alembic_version ORDER BY version_num"
                    ).fetchall()
                ]
    except sqlite3.Error as exc:
        raise SnapshotError(f"cannot inspect SQLite database {path}: {exc}") from exc
    return {"table_counts": table_counts, "schema_versions": schema_versions}


def credential_summary(path: Path) -> dict[str, int]:
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
            tables = {
                str(row[0])
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            account_tokens = (
                int(db.execute("SELECT COUNT(*) FROM account_tokens").fetchone()[0])
                if "account_tokens" in tables
                else 0
            )
            user_columns = (
                {str(row[1]) for row in db.execute("PRAGMA table_info(users)").fetchall()}
                if "users" in tables
                else set()
            )
            password_hashes = (
                int(
                    db.execute(
                        "SELECT COUNT(*) FROM users WHERE password_hash IS NOT NULL"
                    ).fetchone()[0]
                )
                if "password_hash" in user_columns
                else 0
            )
    except sqlite3.Error as exc:
        raise SnapshotError(f"cannot inspect snapshot credentials in {path}: {exc}") from exc
    summary = {"account_tokens": account_tokens, "password_hashes": password_hashes}
    if account_tokens or password_hashes:
        raise SnapshotError(
            "refusing to archive account authentication material: "
            f"account_tokens={account_tokens}, password_hashes={password_hashes}"
        )
    return summary


def _copy_artifacts(source: Path, destination: Path) -> tuple[int, int]:
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not source.exists():
        return (0, 0)
    count = 0
    total_bytes = 0
    for path in source.rglob("*"):
        if path.is_symlink():
            raise SnapshotError(f"artifact tree contains a symbolic link: {path}")
        if path.is_file():
            count += 1
            total_bytes += path.stat().st_size
    shutil.copytree(source, destination, dirs_exist_ok=True)
    return (count, total_bytes)


def create_snapshot(
    config: HubConfig,
    record: ActiveHubRecord,
    *,
    quiesced: bool,
    publish: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    owns_active = record.state == "active" and record.active_hub == config.local_fqdn
    owns_transition = (
        quiesced and record.state == "transition" and record.source_hub == config.local_fqdn
    )
    if not owns_active and not owns_transition:
        raise SnapshotError("only the active hub or fenced transition source may snapshot")
    timestamp = (now or datetime.now(UTC)).replace(microsecond=0)
    generation = f"{timestamp.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}"
    config.local_state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="snapshot-", dir=config.local_state_dir) as temporary:
        temp = Path(temporary)
        state = temp / "omnigent-state"
        state.mkdir(mode=0o700)
        chat_copy = state / "chat.db"
        bridge_copy = state / "google-chat.sqlite3"

        sqlite_backup(config.chat_db, chat_copy)
        sqlite_backup(config.bridge_db, bridge_copy)
        credentials = credential_summary(chat_copy)
        chat_summary = sqlite_summary(chat_copy)
        bridge_summary = sqlite_summary(bridge_copy)
        artifact_count, artifact_bytes = _copy_artifacts(config.artifacts_dir, state / "artifacts")

        manifest: dict[str, Any] = {
            "format_version": ARCHIVE_FORMAT_VERSION,
            "generation_id": generation,
            "source_host": config.local_fqdn,
            "active_hub_epoch": record.epoch,
            "activation_id": record.activation_id,
            "created_at": timestamp.isoformat().replace("+00:00", "Z"),
            "snapshot_kind": "quiesced" if quiesced else "online",
            "omnigent_version": omnigent_version(config.omnigent_bin),
            "bridge_version": bridge_version(config.bridge_project),
            "credentials": credentials,
            "databases": {
                "chat.db": {
                    "sha256": sha256_file(chat_copy),
                    **chat_summary,
                },
                "google-chat.sqlite3": {
                    "sha256": sha256_file(bridge_copy),
                    **bridge_summary,
                },
            },
            "artifacts": {"count": artifact_count, "total_bytes": artifact_bytes},
        }
        (state / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        archive = temp / f"{generation}.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(state, arcname="omnigent-state", recursive=True)
        archive_digest = sha256_file(archive)
        manifest["archive_sha256"] = archive_digest
        manifest["archive_bytes"] = archive.stat().st_size

        if publish:
            published_archive = publish_snapshot(config, archive, archive_digest)
            manifest["archive_path"] = str(published_archive)
            prune_snapshots(config)
        write_json_atomic(
            config.backup_status,
            {
                "generation_id": generation,
                "created_at": manifest["created_at"],
                "snapshot_kind": manifest["snapshot_kind"],
                "archive_sha256": archive_digest,
                "archive_bytes": manifest["archive_bytes"],
                "published": publish,
            },
        )
        return manifest


def publish_snapshot(config: HubConfig, archive: Path, digest: str) -> Path:
    ensure_storage(config)
    config.snapshots_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = config.snapshots_dir / archive.name
    temp = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    sidecar = destination.with_suffix(destination.suffix + ".sha256")
    sidecar_temp = sidecar.with_name(f".{sidecar.name}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copyfile(archive, temp)
        if sha256_file(temp) != digest:
            raise SnapshotError("published archive checksum differs before rename")
        os.replace(temp, destination)
        if sha256_file(destination) != digest:
            raise SnapshotError("published archive checksum differs after rename")
        sidecar_temp.write_text(f"{digest}  {destination.name}\n", encoding="ascii")
        os.replace(sidecar_temp, sidecar)
        if read_sidecar(sidecar) != digest:
            raise SnapshotError("published checksum sidecar did not round-trip")
    finally:
        temp.unlink(missing_ok=True)
        sidecar_temp.unlink(missing_ok=True)
    return destination


def read_sidecar(path: Path) -> str:
    try:
        digest = path.read_text(encoding="ascii").split()[0]
    except (OSError, IndexError) as exc:
        raise SnapshotError(f"invalid snapshot sidecar {path}") from exc
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise SnapshotError(f"invalid SHA-256 in {path}")
    return digest


def list_valid_snapshots(config: HubConfig) -> list[Path]:
    ensure_storage(config)
    if not config.snapshots_dir.exists():
        return []
    valid: list[Path] = []
    for sidecar in config.snapshots_dir.glob("*.tar.gz.sha256"):
        archive = sidecar.with_suffix("")
        try:
            if archive.is_file() and sha256_file(archive) == read_sidecar(sidecar):
                valid.append(archive)
        except SnapshotError:
            continue
    return sorted(valid, reverse=True)


def prune_snapshots(config: HubConfig, *, recent_count: int = 12, daily_count: int = 7) -> None:
    snapshots = list_valid_snapshots(config)
    keep = set(snapshots[:recent_count])
    daily_dates: set[str] = set()
    for archive in snapshots:
        date = archive.name[:8]
        if len(date) == 8 and date.isdigit() and date not in daily_dates:
            if len(daily_dates) < daily_count:
                daily_dates.add(date)
                keep.add(archive)
    for archive in snapshots:
        if archive not in keep:
            archive.unlink(missing_ok=True)
            archive.with_suffix(archive.suffix + ".sha256").unlink(missing_ok=True)


def validate_snapshot(
    config: HubConfig,
    archive: Path,
    *,
    require_local_versions: bool = True,
) -> tuple[dict[str, Any], Path]:
    config.local_state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    sidecar = archive.with_suffix(archive.suffix + ".sha256")
    expected_archive_hash = read_sidecar(sidecar)
    actual_archive_hash = sha256_file(archive)
    if expected_archive_hash != actual_archive_hash:
        raise SnapshotError(f"archive checksum mismatch for {archive}")

    temporary = Path(tempfile.mkdtemp(prefix="restore-", dir=config.local_state_dir))
    try:
        with tarfile.open(archive, "r:gz") as tar:
            _safe_extract(tar, temporary)
        state = temporary / "omnigent-state"
        manifest_path = state / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("format_version") != ARCHIVE_FORMAT_VERSION:
            raise SnapshotError("unsupported snapshot format_version")
        if require_local_versions:
            current_omnigent = omnigent_version(config.omnigent_bin)
            current_bridge = bridge_version(config.bridge_project)
            if manifest.get("omnigent_version") != current_omnigent:
                raise SnapshotError(
                    "Omnigent version mismatch: "
                    f"snapshot={manifest.get('omnigent_version')!r}, local={current_omnigent!r}"
                )
            if manifest.get("bridge_version") != current_bridge:
                raise SnapshotError(
                    "Google Chat bridge version mismatch: "
                    f"snapshot={manifest.get('bridge_version')!r}, local={current_bridge!r}"
                )
        databases = manifest.get("databases")
        if not isinstance(databases, dict):
            raise SnapshotError("snapshot manifest has no databases object")
        for name in ("chat.db", "google-chat.sqlite3"):
            details = databases.get(name)
            if not isinstance(details, dict) or not isinstance(details.get("sha256"), str):
                raise SnapshotError(f"snapshot manifest is missing checksum for {name}")
            database = state / name
            if sha256_file(database) != details["sha256"]:
                raise SnapshotError(f"inner checksum mismatch for {name}")
            summary = sqlite_summary(database)
            if summary.get("table_counts") != details.get("table_counts"):
                raise SnapshotError(f"table counts changed for {name}")
            if summary.get("schema_versions") != details.get("schema_versions"):
                raise SnapshotError(f"schema version changed for {name}")
        credentials = credential_summary(state / "chat.db")
        recorded_credentials = manifest.get("credentials")
        if recorded_credentials is not None and recorded_credentials != credentials:
            raise SnapshotError("snapshot credential summary changed")
        return manifest, temporary
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def restore_snapshot(config: HubConfig, archive: Path) -> dict[str, Any]:
    manifest, temporary = validate_snapshot(config, archive)
    state = temporary / "omnigent-state"
    recovery_root = config.local_state_dir / "pre-restore"
    recovery_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    recovery = recovery_root / f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    recovery.mkdir(mode=0o700)
    try:
        for current in (config.chat_db, config.bridge_db, config.artifacts_dir):
            if current.exists():
                shutil.move(str(current), recovery / current.name)
        for suffix in ("-wal", "-shm"):
            (config.data_dir / f"chat.db{suffix}").unlink(missing_ok=True)
            (config.data_dir / f"google-chat.sqlite3{suffix}").unlink(missing_ok=True)
        config.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.move(str(state / "chat.db"), config.chat_db)
        shutil.move(str(state / "google-chat.sqlite3"), config.bridge_db)
        shutil.move(str(state / "artifacts"), config.artifacts_dir)
        os.chmod(config.chat_db, 0o600)
        os.chmod(config.bridge_db, 0o600)
        sqlite_summary(config.chat_db)
        sqlite_summary(config.bridge_db)
    except Exception:
        for installed in (config.chat_db, config.bridge_db, config.artifacts_dir):
            if installed.is_dir():
                shutil.rmtree(installed, ignore_errors=True)
            else:
                installed.unlink(missing_ok=True)
        for old in recovery.iterdir():
            shutil.move(str(old), config.data_dir / old.name)
        raise
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    manifest["pre_restore_backup"] = str(recovery)
    return manifest


def _safe_extract(tar: tarfile.TarFile, destination: Path) -> None:
    members = tar.getmembers()
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise SnapshotError(f"unsafe archive member: {member.name}")
        if member.issym() or member.islnk() or member.isdev():
            raise SnapshotError(f"unsupported archive member: {member.name}")
    for member in members:
        tar.extract(member, destination, set_attrs=True, filter="fully_trusted")


def load_manifest_from_archive(config: HubConfig, archive: Path) -> dict[str, Any]:
    manifest, temporary = validate_snapshot(config, archive, require_local_versions=False)
    shutil.rmtree(temporary, ignore_errors=True)
    return manifest
