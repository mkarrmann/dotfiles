"""Where the watcher's database lives, and how it moved.

The file was ``diff-watcher.sqlite3`` while the whole service was named after
one of its sources. Renaming it is not cosmetic -- two processes open it by
path, and the hub copies it by name into handoff snapshots -- so the move is
handled here in one place rather than spread across the callers.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["DEFAULT_DATABASE_PATH", "LEGACY_DATABASE_NAME", "adopt_legacy_database", "resolve"]

DEFAULT_DATABASE_PATH = "~/.omnigent/watcher.sqlite3"
LEGACY_DATABASE_NAME = "diff-watcher.sqlite3"
# SQLite's sidecar files. A rename that leaves these behind strands a hot WAL,
# which is how a rename silently loses the most recent writes.
_SQLITE_SUFFIXES = ("-wal", "-shm")


def resolve(configured: Path) -> Path:
    """The database to open: *configured*, or the legacy file until it moves.

    Read-only. Any process may call it, including one that must not migrate or
    mutate anything, so it never touches the filesystem beyond ``exists``.
    """
    if configured.exists():
        return configured
    legacy = configured.with_name(LEGACY_DATABASE_NAME)
    return legacy if legacy.exists() else configured


def adopt_legacy_database(configured: Path) -> Path:
    """Rename the legacy database into place, once, and return the path in use.

    Only the sidecar calls this: it owns the schema, and a second process
    renaming the file out from under it is the same hazard as a second process
    migrating it. A no-op when the new name already exists -- including when
    both do, where the new name wins and the legacy file is left alone rather
    than silently discarded.
    """
    if configured.exists():
        return configured
    legacy = configured.with_name(LEGACY_DATABASE_NAME)
    if not legacy.exists():
        return configured
    configured.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # The main file last: until it moves, the legacy set is still consistent,
    # so an interrupted adoption leaves a database that opens either way.
    for suffix in _SQLITE_SUFFIXES:
        sidecar = legacy.with_name(legacy.name + suffix)
        if sidecar.exists():
            sidecar.replace(configured.with_name(configured.name + suffix))
    legacy.replace(configured)
    return configured
