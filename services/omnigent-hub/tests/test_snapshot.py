from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from omnigent_hub.config import HubConfig
from omnigent_hub.models import ActiveHubRecord
from omnigent_hub.snapshot import (
    SnapshotError,
    create_snapshot,
    hub_version,
    list_valid_snapshots,
    restore_snapshot,
    sha256_file,
    sqlite_summary,
    validate_snapshot,
)


def record() -> ActiveHubRecord:
    return ActiveHubRecord(
        format_version=1,
        epoch=1,
        state="active",
        active_hub="primary.example.com",
        activation_id="activation-1",
        restored_generation=None,
        updated_at="2026-07-18T20:00:00Z",
        updated_by="tester",
    )


def test_create_validate_and_restore_snapshot(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os.path, "ismount", lambda path: path == hub_config.storage_mount)
    manifest = create_snapshot(
        hub_config,
        record(),
        quiesced=False,
        now=datetime(2026, 7, 18, 20, 15, tzinfo=UTC),
    )
    archive = Path(str(manifest["archive_path"]))
    assert archive.is_file()
    assert archive.with_suffix(archive.suffix + ".sha256").is_file()
    assert manifest["databases"]["chat.db"]["table_counts"]["sessions"] == 3
    assert manifest["databases"]["watcher.sqlite3"]["table_counts"] == {"subscriptions": 4}
    assert manifest["credentials"] == {"account_tokens": 0, "password_hashes": 0}
    assert manifest["artifacts"] == {"count": 1, "total_bytes": 8}

    validated, temporary = validate_snapshot(hub_config, archive)
    assert validated["generation_id"] == manifest["generation_id"]
    shutil.rmtree(temporary)

    with sqlite3.connect(hub_config.chat_db) as db:
        db.execute("DELETE FROM sessions")
    with sqlite3.connect(hub_config.watcher_db) as db:
        db.execute("DELETE FROM subscriptions")
    (hub_config.artifacts_dir / "blob").write_bytes(b"changed")
    restored = restore_snapshot(hub_config, archive)
    assert restored["generation_id"] == manifest["generation_id"]
    assert sqlite_summary(hub_config.chat_db)["table_counts"]["sessions"] == 3
    assert sqlite_summary(hub_config.watcher_db)["table_counts"]["subscriptions"] == 4
    assert (hub_config.artifacts_dir / "blob").read_bytes() == b"artifact"
    assert Path(str(restored["pre_restore_backup"])).is_dir()


def test_archive_corruption_is_rejected(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os.path, "ismount", lambda path: path == hub_config.storage_mount)
    manifest = create_snapshot(hub_config, record(), quiesced=True)
    archive = Path(str(manifest["archive_path"]))
    with archive.open("ab") as handle:
        handle.write(b"corrupt")
    with pytest.raises(SnapshotError, match="archive checksum mismatch"):
        validate_snapshot(hub_config, archive)


def test_snapshot_without_completion_sidecar_is_rejected(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os.path, "ismount", lambda path: path == hub_config.storage_mount)
    manifest = create_snapshot(hub_config, record(), quiesced=True)
    archive = Path(str(manifest["archive_path"]))
    archive.with_suffix(archive.suffix + ".sha256").unlink()

    assert list_valid_snapshots(hub_config) == []
    with pytest.raises(SnapshotError, match="invalid snapshot sidecar"):
        validate_snapshot(hub_config, archive)


def test_snapshot_requires_active_local_hub(hub_config: HubConfig) -> None:
    wrong = ActiveHubRecord(
        format_version=1,
        epoch=2,
        state="active",
        active_hub="standby.example.com",
        activation_id="activation-2",
        restored_generation=None,
        updated_at="2026-07-18T20:00:00Z",
        updated_by="tester",
    )
    with pytest.raises(SnapshotError, match="only the active hub or fenced transition source"):
        create_snapshot(hub_config, wrong, quiesced=False, publish=False)


def test_bridge_source_change_blocks_restore(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os.path, "ismount", lambda path: path == hub_config.storage_mount)
    manifest = create_snapshot(hub_config, record(), quiesced=False)
    archive = Path(str(manifest["archive_path"]))
    source = hub_config.bridge_project / "src/omnigent_google_chat/__init__.py"
    source.write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(SnapshotError, match="bridge version mismatch"):
        validate_snapshot(hub_config, archive)


def test_watcher_source_change_blocks_restore(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os.path, "ismount", lambda path: path == hub_config.storage_mount)
    manifest = create_snapshot(hub_config, record(), quiesced=False)
    archive = Path(str(manifest["archive_path"]))
    source = hub_config.watcher_project / "src/omnigent_watcher/__init__.py"
    source.write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(SnapshotError, match="watcher version mismatch"):
        validate_snapshot(hub_config, archive)


def test_hub_version_tracks_controller_source(hub_config: HubConfig) -> None:
    project = hub_config.dotfiles / "services/omnigent-hub"
    initial = hub_version(project)

    (project / "src/omnigent_hub/__init__.py").write_text("VALUE = 2\n", encoding="utf-8")

    assert hub_version(project) != initial


def test_omnigent_version_change_blocks_restore(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os.path, "ismount", lambda path: path == hub_config.storage_mount)
    manifest = create_snapshot(hub_config, record(), quiesced=False)
    archive = Path(str(manifest["archive_path"]))
    hub_config.omnigent_bin.write_text(
        "#!/bin/sh\necho 'omnigent different-version'\n", encoding="utf-8"
    )

    with pytest.raises(SnapshotError, match="Omnigent version mismatch"):
        validate_snapshot(hub_config, archive)


def test_backup_status_is_written(hub_config: HubConfig) -> None:
    create_snapshot(hub_config, record(), quiesced=False, publish=False)
    status = json.loads(hub_config.backup_status.read_text(encoding="utf-8"))
    assert status["published"] is False
    assert status["snapshot_kind"] == "online"


def test_snapshot_rejects_account_authentication_material(hub_config: HubConfig) -> None:
    with sqlite3.connect(hub_config.chat_db) as db:
        db.execute("CREATE TABLE account_tokens (id TEXT PRIMARY KEY)")
        db.execute("INSERT INTO account_tokens (id) VALUES ('secret-token')")

    with pytest.raises(SnapshotError, match="refusing to archive account authentication"):
        create_snapshot(hub_config, record(), quiesced=False, publish=False)


def test_a_host_still_on_the_legacy_watcher_filename_is_captured(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rename lands before every host has restarted its sidecar.

    A snapshot taken in that window must capture the file that exists, not skip
    the watcher database or fail, and must store it under the new name so the
    restoring host does not inherit the old one.
    """
    monkeypatch.setattr(os.path, "ismount", lambda path: path == hub_config.storage_mount)
    hub_config.watcher_db.rename(hub_config.legacy_watcher_db)
    assert not hub_config.watcher_db.exists()

    manifest = create_snapshot(
        hub_config,
        record(),
        quiesced=False,
        now=datetime(2026, 7, 18, 20, 15, tzinfo=UTC),
    )

    assert manifest["databases"]["watcher.sqlite3"]["table_counts"] == {"subscriptions": 4}
    assert "diff-watcher.sqlite3" not in manifest["databases"]


def test_a_snapshot_written_before_the_rename_still_restores(
    hub_config: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An archive on disk from before the rename keys the watcher database by
    its old name. Refusing it would strand every existing snapshot."""
    monkeypatch.setattr(os.path, "ismount", lambda path: path == hub_config.storage_mount)
    manifest = create_snapshot(
        hub_config,
        record(),
        quiesced=False,
        now=datetime(2026, 7, 18, 20, 15, tzinfo=UTC),
    )
    archive = Path(str(manifest["archive_path"]))
    _rewrite_archive_to_legacy_names(archive)

    restored = restore_snapshot(hub_config, archive)

    assert restored["generation_id"] == manifest["generation_id"]
    assert hub_config.watcher_db.is_file()
    with sqlite3.connect(hub_config.watcher_db) as db:
        assert db.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0] == 4


def _rewrite_archive_to_legacy_names(archive: Path) -> None:
    """Rebuild *archive* as a pre-rename snapshot: old filename, old manifest key."""
    import json
    import tarfile
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        work = Path(raw)
        with tarfile.open(archive, "r:gz") as handle:
            handle.extractall(work)
        state = next(work.glob("**/omnigent-state"))
        (state / "watcher.sqlite3").rename(state / "diff-watcher.sqlite3")
        manifest_path = next(work.glob("**/manifest.json"))
        payload = json.loads(manifest_path.read_text())
        payload["databases"]["diff-watcher.sqlite3"] = payload["databases"].pop("watcher.sqlite3")
        manifest_path.write_text(json.dumps(payload))
        archive.unlink()
        with tarfile.open(archive, "w:gz") as handle:
            for entry in sorted(work.iterdir()):
                handle.add(entry, arcname=entry.name)
    digest = archive.with_suffix(archive.suffix + ".sha256")
    if digest.exists():
        digest.write_text(f"{sha256_file(archive)}  {archive.name}\n")
