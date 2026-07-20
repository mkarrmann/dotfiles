"""Strict configuration for the standalone watcher service."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .domain import WatcherConfig


@dataclass(frozen=True)
class ServiceSettings:
    server_url: str
    database_path: Path
    delivery_mode: str
    delivery_session_allowlist: frozenset[str]
    reconcile_interval_seconds: float
    scheduler_error_retry_seconds: float
    watcher: WatcherConfig

    @classmethod
    def load(cls, path: Path) -> ServiceSettings:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
        allowed = {
            "server_url",
            "database_path",
            "delivery_mode",
            "delivery_session_allowlist",
            "reconcile_interval_seconds",
            "scheduler_error_retry_seconds",
            "watcher",
        }
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError(f"unknown service settings: {sorted(unknown)}")
        watcher_raw = raw.get("watcher", {})
        if not isinstance(watcher_raw, dict):
            raise ValueError("watcher settings must be a table")
        watcher_allowed = set(WatcherConfig.__dataclass_fields__)
        watcher_unknown = set(watcher_raw) - watcher_allowed
        if watcher_unknown:
            raise ValueError(f"unknown watcher settings: {sorted(watcher_unknown)}")

        mode = raw.get("delivery_mode", "log_only")
        if mode not in {"log_only", "enabled"}:
            raise ValueError("delivery_mode must be log_only or enabled")
        allowlist_raw = raw.get("delivery_session_allowlist", [])
        if not isinstance(allowlist_raw, list) or any(
            not isinstance(item, str) or not item for item in allowlist_raw
        ):
            raise ValueError("delivery_session_allowlist must contain session IDs")
        database_path = raw.get("database_path", "~/.omnigent/diff-watcher.sqlite3")
        if not isinstance(database_path, str) or not database_path:
            raise ValueError("database_path must be a non-empty string")
        configured_url = raw.get("server_url")
        if configured_url is not None and not isinstance(configured_url, str):
            raise ValueError("server_url must be a string")
        server_url = os.environ.get(
            "OMNIGENT_URL",
            configured_url or "http://127.0.0.1:6767",
        ).rstrip("/")
        reconcile = _positive_number(raw, "reconcile_interval_seconds", 15)
        error_retry = _positive_number(raw, "scheduler_error_retry_seconds", 30)
        return cls(
            server_url=server_url,
            database_path=Path(database_path).expanduser(),
            delivery_mode=mode,
            delivery_session_allowlist=frozenset(allowlist_raw),
            reconcile_interval_seconds=reconcile,
            scheduler_error_retry_seconds=error_retry,
            watcher=WatcherConfig(**watcher_raw),
        )


def _positive_number(values: dict[str, object], key: str, default: float) -> float:
    value = values.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{key} must be a positive number")
    return float(value)
