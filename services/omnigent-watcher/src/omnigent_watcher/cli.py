"""Operator CLI for the standalone watcher."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import closing
from pathlib import Path

from .database import adopt_legacy_database, resolve
from .phabricator_source import PhabricatorReviewSource
from .repository import SCHEMA_VERSION, WatcherRepository
from .service import WatcherService
from .settings import ServiceSettings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omnigent-watcher")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "config.toml",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run")
    status = subparsers.add_parser("status")
    status.add_argument("--json", action="store_true")
    once = subparsers.add_parser("once")
    once.add_argument("--json", action="store_true")
    probe = subparsers.add_parser("probe")
    probe.add_argument("diff_id")
    probe.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    settings = ServiceSettings.load(args.config)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.command == "run":
        # Only the long-running sidecar renames the file: it owns the schema,
        # and a second process moving the database is the same hazard as a
        # second process migrating it. Every other entry point resolves
        # read-only and finds the legacy name until this has run once.
        adopt_legacy_database(settings.database_path)
        asyncio.run(WatcherService(settings).run())
        return
    if args.command == "once":
        service = WatcherService(settings)
        asyncio.run(_run_once(service))
        _print_status(service.repository.counts(), as_json=args.json)
        return
    if args.command == "probe":
        payload = asyncio.run(_probe(args.diff_id))
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            for key, value in sorted(payload.items()):
                print(f"{key}: {value}")
        return
    _print_status(_read_status(resolve(settings.database_path)), as_json=args.json)


async def _run_once(service: WatcherService) -> None:
    try:
        await service.run_iteration()
    finally:
        await service.client.close()


async def _probe(diff_id: str) -> dict[str, object]:
    snapshot = await PhabricatorReviewSource().snapshot(diff_id, None)
    return {
        "diff_id": snapshot.subject,
        "lifecycle": snapshot.lifecycle.value,
        "comments_status": snapshot.comments.status,
        "comments_count": len(snapshot.comments.items),
        "comments_error": (
            snapshot.comments.error.category.value if snapshot.comments.error is not None else None
        ),
        "ci_status": snapshot.ci.status,
        "ci_aggregate": snapshot.ci.aggregate.value,
        "ci_failure_count": len(snapshot.ci.failures),
        "ci_error": snapshot.ci.error.category.value if snapshot.ci.error is not None else None,
    }


def _read_status(path: Path) -> dict[str, object]:
    """Inspect without creating, migrating, adopting, or chmodding the database.

    SQLite's read-only connection may maintain transient WAL/SHM bookkeeping;
    it cannot modify the main database or schema.
    """
    payload: dict[str, object] = {
        "database_path": str(path),
        "expected_schema_version": SCHEMA_VERSION,
        "schema_version": None,
        "status": "missing",
    }
    if not path.exists():
        return payload
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("BEGIN")
        schema = int(connection.execute("PRAGMA user_version").fetchone()[0])
        payload["schema_version"] = schema
        if schema != SCHEMA_VERSION:
            payload["status"] = "upgrade_required" if schema < SCHEMA_VERSION else "newer_schema"
            return payload
        payload["status"] = "current"
        payload.update(WatcherRepository.counts_from_connection(connection))
    return payload


def _print_status(payload: Mapping[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, sort_keys=True))
    else:
        for key, value in sorted(payload.items()):
            print(f"{key}: {value}")
