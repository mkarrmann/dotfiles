"""Operator CLI for the standalone watcher."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections.abc import Sequence
from pathlib import Path

from .phabricator_source import PhabricatorReviewSource
from .repository import WatcherRepository
from .service import DiffWatcherService
from .settings import ServiceSettings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omnigent-diff-watcher")
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
        asyncio.run(DiffWatcherService(settings).run())
        return
    if args.command == "once":
        service = DiffWatcherService(settings)
        asyncio.run(_run_once(service))
        _print_status(service.repository, as_json=args.json)
        return
    if args.command == "probe":
        payload = asyncio.run(_probe(args.diff_id))
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            for key, value in sorted(payload.items()):
                print(f"{key}: {value}")
        return
    _print_status(WatcherRepository(settings.database_path), as_json=args.json)


async def _run_once(service: DiffWatcherService) -> None:
    try:
        await service.run_iteration()
    finally:
        await service.client.close()


async def _probe(diff_id: str) -> dict[str, object]:
    snapshot = await PhabricatorReviewSource().snapshot(diff_id, None)
    return {
        "diff_id": snapshot.diff_id,
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


def _print_status(repository: WatcherRepository, *, as_json: bool) -> None:
    payload = repository.counts()
    if as_json:
        print(json.dumps(payload, sort_keys=True))
    else:
        for key, value in sorted(payload.items()):
            print(f"{key}: {value}")
