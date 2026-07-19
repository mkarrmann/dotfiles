from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from omnigent_hub.config import HubConfig
from omnigent_hub.models import ActiveHubRecord, ValidationError
from omnigent_hub.snapshot import (
    bridge_version,
    hub_version,
    list_valid_snapshots,
    load_manifest_from_archive,
    omnigent_version,
)
from omnigent_hub.storage import StorageError, publish_record, read_record, write_json_atomic


class HubRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GateResult:
    record: ActiveHubRecord
    marker: dict[str, Any]


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HubRuntimeError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise HubRuntimeError(f"{path} must contain a JSON object")
    return value


def activation_value(record: ActiveHubRecord) -> dict[str, Any]:
    if record.state != "active" or record.active_hub is None or record.activation_id is None:
        raise HubRuntimeError("cannot create an activation marker from transition state")
    return {
        "format_version": 1,
        "epoch": record.epoch,
        "active_hub": record.active_hub,
        "activation_id": record.activation_id,
        "restored_generation": record.restored_generation,
        "written_at": utc_now(),
    }


def write_activation_marker(config: HubConfig, record: ActiveHubRecord) -> None:
    if record.active_hub != config.local_fqdn:
        raise HubRuntimeError("refusing to write another hub's local activation marker")
    write_json_atomic(config.activation_marker, activation_value(record))


def write_routing_cache(config: HubConfig, record: ActiveHubRecord) -> None:
    write_json_atomic(config.routing_cache, record.to_dict())


def check_gate(config: HubConfig) -> GateResult:
    config.topology.validate_hub(config.local_fqdn)
    record = resolve_record(config)
    if record.state != "active":
        raise HubRuntimeError(
            f"deployment is fenced by transition {record.transition_id} at epoch {record.epoch}"
        )
    if record.active_hub != config.local_fqdn:
        raise HubRuntimeError(
            f"active hub is {record.active_hub}, not local host {config.local_fqdn}"
        )
    marker = read_json_object(config.activation_marker)
    expected = {
        "format_version": 1,
        "epoch": record.epoch,
        "active_hub": record.active_hub,
        "activation_id": record.activation_id,
        "restored_generation": record.restored_generation,
    }
    observed = {key: marker.get(key) for key in expected}
    if observed != expected:
        raise HubRuntimeError(
            f"local activation marker does not match epoch {record.epoch} activation "
            f"{record.activation_id}"
        )
    return GateResult(record=record, marker=marker)


def resolve_record(config: HubConfig) -> ActiveHubRecord:
    forced = read_force_override(config)
    if forced is not None and not os.path.ismount(config.storage_mount):
        return forced
    try:
        return read_record(config)
    except StorageError:
        if forced is None:
            raise
        return forced


def read_force_override(config: HubConfig) -> ActiveHubRecord | None:
    path = config.local_state_dir / "force-start.json"
    try:
        value = read_json_object(path)
    except HubRuntimeError:
        return None
    expires_at = value.get("expires_at")
    forced_record = value.get("record")
    if not isinstance(expires_at, str) or not isinstance(forced_record, dict):
        return None
    try:
        if _parse_utc(expires_at) <= datetime.now(UTC):
            return None
        record = ActiveHubRecord.from_dict(forced_record, config.topology)
    except (ValueError, ValidationError):
        return None
    if record.state != "active" or record.active_hub != config.local_fqdn:
        return None
    return record


def initialize(config: HubConfig, *, active_hub: str) -> ActiveHubRecord:
    config.topology.validate_hub(active_hub)
    if active_hub != config.local_fqdn:
        raise HubRuntimeError("initialization must run on the requested active hub")
    if config.record_path.exists():
        existing = read_record(config)
        if existing.state == "active" and existing.active_hub == active_hub:
            write_activation_marker(config, existing)
            write_routing_cache(config, existing)
            return existing
        raise HubRuntimeError("active-hub record already exists with different ownership")
    record = ActiveHubRecord(
        format_version=1,
        epoch=1,
        state="active",
        active_hub=active_hub,
        activation_id=f"activation-1-{uuid.uuid4().hex}",
        restored_generation=None,
        updated_at=utc_now(),
        updated_by=config.home.name,
    )
    publish_record(config, record)
    write_activation_marker(config, record)
    write_routing_cache(config, record)
    return record


def begin_transition(config: HubConfig, *, target_hub: str) -> ActiveHubRecord:
    config.topology.validate_hub(target_hub)
    current = check_gate(config).record
    if target_hub == config.local_fqdn:
        raise HubRuntimeError("transition target must differ from the active hub")
    transition = ActiveHubRecord(
        format_version=1,
        epoch=current.epoch + 1,
        state="transition",
        active_hub=None,
        activation_id=None,
        restored_generation=None,
        updated_at=utc_now(),
        updated_by=config.home.name,
        source_hub=config.local_fqdn,
        target_hub=target_hub,
        transition_id=f"transition-{current.epoch + 1}-{uuid.uuid4().hex}",
    )
    publish_record(config, transition)
    write_routing_cache(config, transition)
    return transition


def begin_unexpected_transition(
    config: HubConfig,
    *,
    expected_source: str,
    generation: str,
) -> ActiveHubRecord:
    config.topology.validate_hub(expected_source)
    if expected_source == config.local_fqdn:
        raise HubRuntimeError("unexpected recovery target must differ from the failed source")
    current = read_record(config)
    if current.state != "active" or current.active_hub != expected_source:
        raise HubRuntimeError(
            f"shared record does not show expected failed source {expected_source} as active"
        )
    transition = ActiveHubRecord(
        format_version=1,
        epoch=current.epoch + 1,
        state="transition",
        active_hub=None,
        activation_id=None,
        restored_generation=generation,
        updated_at=utc_now(),
        updated_by=config.home.name,
        source_hub=expected_source,
        target_hub=config.local_fqdn,
        transition_id=f"unexpected-{current.epoch + 1}-{uuid.uuid4().hex}",
    )
    publish_record(config, transition)
    write_routing_cache(config, transition)
    return transition


def activate_transition(config: HubConfig, *, generation: str) -> ActiveHubRecord:
    transition = read_record(config)
    if transition.state != "transition":
        raise HubRuntimeError("there is no transition to activate")
    if transition.target_hub != config.local_fqdn:
        raise HubRuntimeError(
            f"transition target is {transition.target_hub}, not local host {config.local_fqdn}"
        )
    activation = ActiveHubRecord(
        format_version=1,
        epoch=transition.epoch,
        state="active",
        active_hub=config.local_fqdn,
        activation_id=f"activation-{transition.epoch}-{uuid.uuid4().hex}",
        restored_generation=generation,
        updated_at=utc_now(),
        updated_by=config.home.name,
    )
    write_activation_marker(config, activation)
    publish_record(config, activation)
    write_routing_cache(config, activation)
    return activation


def attach_transition_generation(config: HubConfig, *, generation: str) -> ActiveHubRecord:
    transition = read_record(config)
    if transition.state != "transition":
        raise HubRuntimeError("there is no transition to update")
    if transition.source_hub != config.local_fqdn:
        raise HubRuntimeError("only the transition source may attach the final generation")
    if transition.restored_generation not in (None, generation):
        raise HubRuntimeError(
            f"transition already names generation {transition.restored_generation}"
        )
    updated = ActiveHubRecord(
        format_version=transition.format_version,
        epoch=transition.epoch,
        state=transition.state,
        active_hub=None,
        activation_id=None,
        restored_generation=generation,
        updated_at=utc_now(),
        updated_by=config.home.name,
        source_hub=transition.source_hub,
        target_hub=transition.target_hub,
        transition_id=transition.transition_id,
    )
    publish_record(config, updated)
    write_routing_cache(config, updated)
    return updated


def abort_transition(config: HubConfig) -> ActiveHubRecord:
    transition = read_record(config)
    if transition.state != "transition" or transition.source_hub != config.local_fqdn:
        raise HubRuntimeError("only the transition source may abort this handoff")
    epoch = transition.epoch + 1
    activation = ActiveHubRecord(
        format_version=1,
        epoch=epoch,
        state="active",
        active_hub=config.local_fqdn,
        activation_id=f"activation-{epoch}-{uuid.uuid4().hex}",
        restored_generation=transition.restored_generation,
        updated_at=utc_now(),
        updated_by=config.home.name,
    )
    write_activation_marker(config, activation)
    publish_record(config, activation)
    write_routing_cache(config, activation)
    return activation


def force_start(config: HubConfig, *, reason: str, ttl_hours: int = 24) -> ActiveHubRecord:
    config.topology.validate_hub(config.local_fqdn)
    try:
        current = read_record(config)
    except StorageError:
        current = None
    if current is not None:
        raise HubRuntimeError(
            "Persistent Storage is readable; use normal promotion or abort instead of force-start"
        )
    cache = read_json_object(config.routing_cache)
    cached = ActiveHubRecord.from_dict(cache, config.topology)
    epoch = cached.epoch + 1
    activation = ActiveHubRecord(
        format_version=1,
        epoch=epoch,
        state="active",
        active_hub=config.local_fqdn,
        activation_id=f"force-{epoch}-{uuid.uuid4().hex}",
        restored_generation=cached.restored_generation,
        updated_at=utc_now(),
        updated_by=config.home.name,
    )
    expires = datetime.now(UTC) + timedelta(hours=ttl_hours)
    override = {
        "format_version": 1,
        "record": activation.to_dict(),
        "reason": reason,
        "other_hub_confirmed_stopped": True,
        "created_at": utc_now(),
        "expires_at": expires.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    write_activation_marker(config, activation)
    write_routing_cache(config, activation)
    write_json_atomic(config.local_state_dir / "force-start.json", override)
    _append_audit(config, {"action": "force-start", **override})
    return activation


def repair_force_start(config: HubConfig) -> ActiveHubRecord:
    forced = read_force_override(config)
    if forced is None:
        raise HubRuntimeError("there is no valid force-start override to repair")
    shared = read_record(config)
    if shared.epoch >= forced.epoch and shared != forced:
        raise HubRuntimeError(
            f"shared epoch {shared.epoch} is not older than forced epoch {forced.epoch}"
        )
    publish_record(config, forced)
    (config.local_state_dir / "force-start.json").unlink(missing_ok=True)
    _append_audit(
        config,
        {"action": "repair-force-start", "record": forced.to_dict(), "created_at": utc_now()},
    )
    return forced


def local_status(config: HubConfig) -> dict[str, Any]:
    record: ActiveHubRecord | None = None
    record_error: str | None = None
    try:
        record = resolve_record(config)
    except (HubRuntimeError, StorageError, ValidationError, ValueError) as exc:
        record_error = str(exc)
    marker = _read_optional_json(config.activation_marker)
    cache = _read_optional_json(config.routing_cache)
    gate_error: str | None = None
    gate_allowed = False
    try:
        check_gate(config)
        gate_allowed = True
    except (HubRuntimeError, StorageError, ValidationError, ValueError) as exc:
        gate_error = str(exc)
    newest_snapshot: dict[str, Any] | None = None
    snapshot_error: str | None = None
    try:
        snapshots = list_valid_snapshots(config)
        if snapshots:
            newest_snapshot = load_manifest_from_archive(config, snapshots[0])
            newest_snapshot["archive_path"] = str(snapshots[0])
            created = _parse_utc(str(newest_snapshot["created_at"]))
            newest_snapshot["age_seconds"] = max(
                0, int((datetime.now(UTC) - created).total_seconds())
            )
    except Exception as exc:
        snapshot_error = str(exc)
    expected_url: str | None = None
    if record and record.state == "active" and record.active_hub:
        if config.local_fqdn in config.topology.hubs:
            expected_url = f"http://127.0.0.1:{config.topology.port}"
        else:
            expected_url = f"http://{record.active_hub}:{config.topology.port}"
    cli_url = _read_config_server(config.data_dir / "config.yaml")
    environment_url = _read_environment_url(config.home / ".config/environment.d/omnigent.conf")
    nvim_urls = _nvim_environment_urls()
    services = {
        unit: systemd_state(unit)
        for unit in (
            "omnigent-server.service",
            "omnigent-prodnet.service",
            "omnigent-client-proxy.service",
            "omnigent-google-chat.service",
            "omnigent-snapshot.service",
            "omnigent-snapshot.timer",
            "omnigent-host.service",
        )
    }
    return {
        "host": config.local_fqdn,
        "record": record.to_dict() if record else None,
        "record_error": record_error,
        "gate": {"allowed": gate_allowed, "error": gate_error},
        "activation_marker": marker,
        "routing_cache": cache,
        "routing": {
            "expected_url": expected_url,
            "cli_server_url": cli_url,
            "environment_url": environment_url,
            "cli_stale": expected_url is not None and cli_url != expected_url,
            "environment_stale": expected_url is not None and environment_url != expected_url,
            "nvim_process_urls": nvim_urls,
            "nvim_stale_pids": sorted(
                pid
                for pid, url in nvim_urls.items()
                if expected_url is not None
                and (url or f"http://127.0.0.1:{config.topology.port}") != expected_url
            ),
        },
        "services": services,
        "versions": {
            "omnigent": _capture_version(config),
            "bridge": _capture_bridge_version(config),
            "hub": _capture_hub_version(config),
        },
        "paths": {
            "chat_db": str(config.chat_db),
            "bridge_db": str(config.bridge_db),
            "artifacts": str(config.artifacts_dir),
            "storage_root": str(config.storage_root),
        },
        "newest_snapshot": newest_snapshot,
        "snapshot_error": snapshot_error,
    }


def systemd_state(unit: str) -> str:
    result = subprocess.run(
        ["systemctl", "--user", "is-active", unit],
        check=False,
        text=True,
        capture_output=True,
    )
    state = result.stdout.strip()
    return state or "unknown"


def service_action(config: HubConfig, action: str) -> dict[str, str]:
    actions = {
        "stop-ingress": (
            ("stop", "omnigent-google-chat.service"),
            ("stop", "omnigent-snapshot.timer"),
            ("stop", "omnigent-snapshot.service"),
            ("stop", "omnigent-prodnet.service"),
        ),
        "stop-server": (("stop", "omnigent-server.service"),),
        "stop-bridge": (("stop", "omnigent-google-chat.service"),),
        "stop-hub": (
            ("stop", "omnigent-google-chat.service"),
            ("stop", "omnigent-snapshot.timer"),
            ("stop", "omnigent-snapshot.service"),
            ("stop", "omnigent-prodnet.service"),
            ("stop", "omnigent-server.service"),
        ),
        "stop-client": (("stop", "omnigent-client-proxy.service"),),
        "stop-all": (
            ("stop", "omnigent-google-chat.service"),
            ("stop", "omnigent-snapshot.timer"),
            ("stop", "omnigent-snapshot.service"),
            ("stop", "omnigent-prodnet.service"),
            ("stop", "omnigent-server.service"),
            ("stop", "omnigent-client-proxy.service"),
        ),
        "start-core": (
            ("start", "omnigent-server.service"),
            ("start", "omnigent-prodnet.service"),
        ),
        "start-tail": (
            ("start", "omnigent-google-chat.service"),
            ("start", "omnigent-snapshot.timer"),
        ),
        "start-bridge": (("start", "omnigent-google-chat.service"),),
        "start-timer": (("start", "omnigent-snapshot.timer"),),
        "start-client": (("start", "omnigent-client-proxy.service"),),
        "restart-host": (("restart", "omnigent-host.service"),),
    }
    if action not in actions:
        raise HubRuntimeError(f"unsupported service action {action!r}")
    commands = actions[action]
    if action in {"start-core", "start-tail", "start-bridge", "start-timer"}:
        check_gate(config)
    units: list[str] = []
    for command in commands:
        unit = command[-1]
        units.append(unit)
        result = subprocess.run(
            ["systemctl", "--user", *command],
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise HubRuntimeError(f"systemctl {' '.join(command)} failed: {detail}")
    if action.startswith("stop"):
        subprocess.run(
            ["systemctl", "--user", "reset-failed", *units],
            check=False,
            text=True,
            capture_output=True,
        )
    if action in {"start-core", "start-client"}:
        wait_for_health(config)
    return {unit: systemd_state(unit) for unit in units}


def wait_for_health(config: HubConfig, *, timeout_seconds: float = 60) -> None:
    url = f"http://127.0.0.1:{config.topology.port}/health"
    deadline = time.monotonic() + timeout_seconds
    last_error = "unhealthy"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
                last_error = f"HTTP {response.status}"
        except (OSError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(0.5)
    raise HubRuntimeError(f"Omnigent did not become healthy at {url}: {last_error}")


def assert_sessions_quiescent(config: HubConfig) -> dict[str, Any]:
    url = f"http://127.0.0.1:{config.topology.port}/v1/sessions?limit=1000&kind=any"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=10) as response:
            payload = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise HubRuntimeError(f"cannot inspect Omnigent sessions before handoff: {exc}") from exc
    sessions = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(sessions, list):
        raise HubRuntimeError("Omnigent session list returned an invalid payload")
    if payload.get("has_more") is True:
        raise HubRuntimeError("cannot prove quiescence with more than 1000 sessions")
    busy: list[dict[str, str]] = []
    for session in sessions:
        if not isinstance(session, dict):
            raise HubRuntimeError("Omnigent session list contains a non-object item")
        session_id = session.get("id")
        status = session.get("status")
        if not isinstance(session_id, str) or not isinstance(status, str):
            raise HubRuntimeError("Omnigent session list contains an item without id/status")
        if status not in {"idle", "failed"}:
            busy.append({"id": session_id, "status": status})
    if busy:
        detail = ", ".join(f"{item['id']}={item['status']}" for item in busy)
        raise HubRuntimeError(
            f"handoff requires idle sessions; wait for or interrupt active turns: {detail}"
        )
    return {"quiescent": True, "session_count": len(sessions), "busy_sessions": []}


def reconcile_local_route(config: HubConfig, *, restart_host: bool) -> dict[str, Any]:
    record = resolve_routing_record(config)
    previous_cache = _read_optional_json(config.routing_cache)
    previous_cli_url = _read_config_server(config.data_dir / "config.yaml")
    previous_environment_url = _read_environment_url(
        config.home / ".config/environment.d/omnigent.conf"
    )
    write_routing_cache(config, record)
    if record.state != "active" or record.active_hub is None:
        raise HubRuntimeError("cannot reconcile client routes during transition state")
    if config.local_fqdn in config.topology.hubs:
        url = f"http://127.0.0.1:{config.topology.port}"
    else:
        url = f"http://{record.active_hub}:{config.topology.port}"
    environment_file = config.home / ".config/environment.d/omnigent.conf"
    environment_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    environment_file.write_text(f"OMNIGENT_URL={url}\n", encoding="utf-8")
    ensure = config.dotfiles / "bin/omnigent-dvsc-ensure"
    result = subprocess.run(
        [str(ensure), "--config-only"], check=False, text=True, capture_output=True
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise HubRuntimeError(f"Omnigent CLI configuration reconciliation failed: {detail}")
    if config.local_fqdn == record.active_hub:
        gchat_ensure = config.dotfiles / "bin/omnigent-google-chat-ensure"
        result = subprocess.run([str(gchat_ensure)], check=False, text=True, capture_output=True)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise HubRuntimeError(f"Google Chat configuration reconciliation failed: {detail}")
    previous_identity = None
    if previous_cache is not None:
        previous_identity = (
            previous_cache.get("epoch"),
            previous_cache.get("active_hub"),
            previous_cache.get("activation_id"),
        )
    current_identity = (record.epoch, record.active_hub, record.activation_id)
    changed = (
        previous_cli_url != url
        or previous_environment_url != url
        or previous_identity != current_identity
    )
    if restart_host and changed:
        service_action(config, "restart-host")
    return {
        "host": config.local_fqdn,
        "epoch": record.epoch,
        "activation_id": record.activation_id,
        "url": url,
        "changed": changed,
        "host_restarted": restart_host and changed,
    }


def resolve_routing_record(config: HubConfig) -> ActiveHubRecord:
    if config.local_fqdn in config.topology.hubs:
        return resolve_record(config)
    cache = read_json_object(config.routing_cache)
    return ActiveHubRecord.from_dict(cache, config.topology)


def reconcile_services(config: HubConfig) -> dict[str, Any]:
    record = resolve_record(config)
    if record.state != "active":
        services = service_action(config, "stop-all")
        return {
            "host": config.local_fqdn,
            "state": record.state,
            "epoch": record.epoch,
            "services": services,
        }
    if record.active_hub != config.local_fqdn:
        services = service_action(config, "stop-hub")
        route = reconcile_local_route(config, restart_host=False)
        client = service_action(config, "start-client")
        if route["changed"]:
            service_action(config, "restart-host")
        return {
            "host": config.local_fqdn,
            "state": "standby",
            "epoch": record.epoch,
            "route": route,
            "services": {**services, **client},
        }
    service_action(config, "stop-client")
    route = reconcile_local_route(config, restart_host=False)
    core = service_action(config, "start-core")
    if route["changed"]:
        service_action(config, "restart-host")
    tail = service_action(config, "start-tail")
    return {
        "host": config.local_fqdn,
        "state": "active",
        "epoch": record.epoch,
        "route": route,
        "services": {**core, **tail},
    }


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    try:
        return read_json_object(path)
    except HubRuntimeError:
        return None


def _capture_version(config: HubConfig) -> str:
    try:
        return omnigent_version(config.omnigent_bin)
    except Exception as exc:
        return f"ERROR: {exc}"


def _capture_bridge_version(config: HubConfig) -> str:
    try:
        return bridge_version(config.bridge_project)
    except Exception as exc:
        return f"ERROR: {exc}"


def _capture_hub_version(config: HubConfig) -> str:
    try:
        return hub_version(config.dotfiles / "services/omnigent-hub")
    except Exception as exc:
        return f"ERROR: {exc}"


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _read_config_server(path: Path) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith("server:"):
            value = line.split(":", 1)[1].strip().strip('"').strip("'")
            return value or None
    return None


def _read_environment_url(path: Path) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith("OMNIGENT_URL="):
            return line.split("=", 1)[1].strip() or None
    return None


def _nvim_environment_urls() -> dict[int, str | None]:
    result = subprocess.run(
        ["pgrep", "-u", str(os.getuid()), "-x", "nvim"],
        check=False,
        text=True,
        capture_output=True,
    )
    urls: dict[int, str | None] = {}
    for raw_pid in result.stdout.splitlines():
        try:
            pid = int(raw_pid)
            environment = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
        except (OSError, ValueError):
            continue
        prefix = b"OMNIGENT_URL="
        value = next(
            (
                entry[len(prefix) :].decode(errors="replace")
                for entry in environment
                if entry.startswith(prefix)
            ),
            None,
        )
        urls[pid] = value
    return urls


def _append_audit(config: HubConfig, value: dict[str, Any]) -> None:
    path = config.local_state_dir / "audit.jsonl"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, sort_keys=True) + "\n")
    os.chmod(path, 0o600)
