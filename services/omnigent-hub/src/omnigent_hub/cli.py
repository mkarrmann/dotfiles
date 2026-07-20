from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from collections.abc import Mapping
from pathlib import Path

from omnigent_hub.config import HubConfig, load_config
from omnigent_hub.orchestrator import HandoffError, HandoffOrchestrator
from omnigent_hub.reconcile import ReconcileError, reconcile_gchat
from omnigent_hub.remote import RemoteClient, RemoteError
from omnigent_hub.runtime import (
    HubRuntimeError,
    abort_transition,
    activate_transition,
    assert_sessions_quiescent,
    attach_transition_generation,
    begin_transition,
    begin_unexpected_transition,
    check_gate,
    force_start,
    initialize,
    local_status,
    reconcile_local_route,
    reconcile_services,
    repair_force_start,
    resolve_record,
    service_action,
    write_routing_cache,
)
from omnigent_hub.smoke import SmokeError, restore_smoke
from omnigent_hub.snapshot import (
    SnapshotError,
    create_snapshot,
    list_valid_snapshots,
    restore_snapshot,
    validate_snapshot,
)
from omnigent_hub.storage import StorageError, ensure_storage, local_lock, read_record


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omnigent-hub")
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve = subparsers.add_parser("resolve", help="read the authoritative active-hub record")
    resolve.add_argument("--json", action="store_true")

    gate = subparsers.add_parser("gate", help="verify this host owns the active lineage")
    gate.add_argument("--json", action="store_true")

    init = subparsers.add_parser("initialize", help="initialize the first active-hub record")
    init.add_argument("--active", required=True)
    init.add_argument("--yes", action="store_true")
    init.add_argument("--json", action="store_true")

    cache = subparsers.add_parser("cache-routing", help="refresh the local routing cache")
    cache.add_argument("--force-remount", action="store_true")
    cache.add_argument("--json", action="store_true")

    transition = subparsers.add_parser("begin-transition", help="publish a no-active-hub fence")
    transition.add_argument("--target", required=True)
    transition.add_argument("--yes", action="store_true")
    transition.add_argument("--json", action="store_true")

    unexpected = subparsers.add_parser(
        "begin-unexpected-transition",
        help="fence a confirmed failed source and target this host",
    )
    unexpected.add_argument("--source", required=True)
    unexpected.add_argument("--generation", required=True)
    unexpected.add_argument("--source-confirmed-stopped", action="store_true")
    unexpected.add_argument("--yes", action="store_true")
    unexpected.add_argument("--json", action="store_true")

    activate = subparsers.add_parser("activate", help="activate this transition target")
    activate.add_argument("--generation", required=True)
    activate.add_argument("--yes", action="store_true")
    activate.add_argument("--json", action="store_true")

    attach = subparsers.add_parser(
        "attach-generation", help="attach the final snapshot to a transition"
    )
    attach.add_argument("--generation", required=True)
    attach.add_argument("--json", action="store_true")

    status = subparsers.add_parser("local-status", help="report this machine's hub state")
    status.add_argument("--json", action="store_true")

    quiesce = subparsers.add_parser(
        "quiesce-check", help="refuse handoff while a session turn is active"
    )
    quiesce.add_argument("--json", action="store_true")

    services = subparsers.add_parser("services", help="perform a validated local service action")
    services.add_argument(
        "action",
        choices=(
            "stop-ingress",
            "stop-server",
            "stop-bridge",
            "stop-hub",
            "stop-client",
            "stop-all",
            "start-core",
            "start-tail",
            "start-bridge",
            "start-watcher",
            "start-timer",
            "start-client",
            "restart-host",
        ),
    )
    services.add_argument("--json", action="store_true")

    route = subparsers.add_parser("route-ensure", help="reconcile this devserver's clients")
    route.add_argument("--restart-host", action="store_true")
    route.add_argument("--json", action="store_true")

    reconcile_units = subparsers.add_parser(
        "reconcile-services", help="match local services to shared ownership"
    )
    reconcile_units.add_argument("--json", action="store_true")

    status_all = subparsers.add_parser("status", help="report both hubs and invariants")
    status_all.add_argument("--json", action="store_true")

    discover = subparsers.add_parser("discover", help="resolve active hub through candidates")
    discover.add_argument("--json", action="store_true")

    watch = subparsers.add_parser(
        "watch-activation", help="wait while one activation remains current"
    )
    watch.add_argument("--epoch", required=True, type=int)
    watch.add_argument("--activation-id", required=True)
    watch.add_argument("--interval", type=float, default=30)
    watch.add_argument("--json", action="store_true")

    backup = subparsers.add_parser("backup", help="create a handoff snapshot")
    backup.add_argument("--quiesced", action="store_true")
    backup.add_argument("--yes", action="store_true")
    backup.add_argument("--json", action="store_true")

    promote = subparsers.add_parser("promote", help="transfer ownership to a hub")
    promote.add_argument("target")
    promote.add_argument("--unexpected-failure", action="store_true")
    promote.add_argument("--source-confirmed-stopped", action="store_true")
    promote.add_argument("--dry-run", action="store_true")
    promote.add_argument("--yes", action="store_true")
    promote.add_argument("--json", action="store_true")

    failback = subparsers.add_parser("failback", help="transfer ownership to CCO")
    failback.add_argument("target", nargs="?", default="cco")
    failback.add_argument("--dry-run", action="store_true")
    failback.add_argument("--yes", action="store_true")
    failback.add_argument("--json", action="store_true")

    reconcile = subparsers.add_parser(
        "reconcile-gchat", help="classify stale phone input after unexpected restore"
    )
    reconcile.add_argument("--resubmit")
    reconcile.add_argument("--no-start-bridge", action="store_true")
    reconcile.add_argument("--yes", action="store_true")
    reconcile.add_argument("--json", action="store_true")

    abort = subparsers.add_parser("abort-transition", help="return ownership to the source")
    abort.add_argument("--yes", action="store_true")
    abort.add_argument("--json", action="store_true")

    force = subparsers.add_parser(
        "force-start", help="start locally during a confirmed coordination-store outage"
    )
    force.add_argument("--other-hub-confirmed-stopped", action="store_true")
    force.add_argument("--reason", required=True)
    force.add_argument("--yes", action="store_true")
    force.add_argument("--json", action="store_true")

    repair = subparsers.add_parser(
        "repair-force-start", help="publish a forced lineage after storage recovers"
    )
    repair.add_argument("--yes", action="store_true")
    repair.add_argument("--json", action="store_true")

    snapshot = subparsers.add_parser("snapshot", help="create an online state snapshot")
    snapshot.add_argument("--quiesced", action="store_true")
    snapshot.add_argument("--no-publish", action="store_true")
    snapshot.add_argument("--json", action="store_true")

    snapshots = subparsers.add_parser("snapshots", help="list valid published snapshots")
    snapshots.add_argument("--json", action="store_true")

    validate = subparsers.add_parser("validate-snapshot", help="validate a snapshot archive")
    validate.add_argument("archive", type=Path)
    validate.add_argument("--json", action="store_true")

    restore = subparsers.add_parser("restore", help="restore a validated snapshot locally")
    restore.add_argument("archive", type=Path)
    restore.add_argument("--yes", action="store_true")
    restore.add_argument("--json", action="store_true")

    smoke = subparsers.add_parser(
        "smoke-restore", help="boot a restored snapshot on an isolated port"
    )
    smoke.add_argument("archive", nargs="?", type=Path)
    smoke.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config()
    try:
        if args.command == "resolve":
            record = resolve_record(config)
            _emit(record.to_dict(), args.json)
        elif args.command == "gate":
            gate_result = check_gate(config)
            _emit(
                {
                    "allowed": True,
                    "record": gate_result.record.to_dict(),
                    "marker": gate_result.marker,
                },
                args.json,
            )
        elif args.command == "initialize":
            _require_yes(parser, args.yes, "initialize")
            _emit(initialize(config, active_hub=args.active).to_dict(), args.json)
        elif args.command == "cache-routing":
            if args.force_remount:
                ensure_storage(config, force_remount=True)
                record = read_record(config, ensure_mounted=False)
            else:
                record = resolve_record(config)
            write_routing_cache(config, record)
            _emit(record.to_dict(), args.json)
        elif args.command == "begin-transition":
            _require_yes(parser, args.yes, "begin-transition")
            _emit(begin_transition(config, target_hub=args.target).to_dict(), args.json)
        elif args.command == "begin-unexpected-transition":
            _require_yes(parser, args.yes, "begin-unexpected-transition")
            if not args.source_confirmed_stopped:
                parser.error("begin-unexpected-transition requires --source-confirmed-stopped")
            _emit(
                begin_unexpected_transition(
                    config,
                    expected_source=args.source,
                    generation=args.generation,
                ).to_dict(),
                args.json,
            )
        elif args.command == "activate":
            _require_yes(parser, args.yes, "activate")
            _emit(activate_transition(config, generation=args.generation).to_dict(), args.json)
        elif args.command == "attach-generation":
            _emit(
                attach_transition_generation(config, generation=args.generation).to_dict(),
                args.json,
            )
        elif args.command == "local-status":
            _emit(local_status(config), args.json)
        elif args.command == "quiesce-check":
            _emit(assert_sessions_quiescent(config), args.json)
        elif args.command == "services":
            _emit(service_action(config, args.action), args.json)
        elif args.command == "route-ensure":
            _emit(reconcile_local_route(config, restart_host=args.restart_host), args.json)
        elif args.command == "reconcile-services":
            _emit(reconcile_services(config), args.json)
        elif args.command == "status":
            remote = RemoteClient(config)
            _emit(HandoffOrchestrator(config, remote).status(), args.json)
        elif args.command == "discover":
            record, supplier, errors = RemoteClient(config).resolve()
            write_routing_cache(config, record)
            _emit(
                {"record": record.to_dict(), "supplier": supplier, "errors": errors},
                args.json,
            )
        elif args.command == "watch-activation":
            _watch_activation(config, args.epoch, args.activation_id, args.interval, args.json)
        elif args.command == "backup":
            if not args.quiesced:
                parser.error("backup currently requires --quiesced")
            _require_yes(parser, args.yes, "backup --quiesced")
            with local_lock(config.local_state_dir / "handoff.lock"):
                _emit(_run_quiesced_backup(config), args.json)
        elif args.command in ("promote", "failback"):
            if not args.dry_run:
                _require_yes(parser, args.yes, args.command)
            target = _target_fqdn(config, args.target)
            remote = RemoteClient(config)
            with local_lock(config.local_state_dir / "handoff.lock"):
                result = HandoffOrchestrator(config, remote).handoff(
                    target,
                    unexpected=(args.unexpected_failure if args.command == "promote" else False),
                    source_confirmed_stopped=(
                        args.source_confirmed_stopped if args.command == "promote" else False
                    ),
                    dry_run=args.dry_run,
                )
            _emit(result.to_dict(), args.json)
        elif args.command == "reconcile-gchat":
            if args.resubmit:
                _require_yes(parser, args.yes, "reconcile-gchat --resubmit")
            _emit(
                reconcile_gchat(
                    config,
                    resubmit=args.resubmit,
                    start_bridge=not args.no_start_bridge,
                ),
                args.json,
            )
        elif args.command == "abort-transition":
            _require_yes(parser, args.yes, "abort-transition")
            activation = abort_transition(config)
            reconcile_local_route(config, restart_host=False)
            service_action(config, "stop-client")
            service_action(config, "start-core")
            service_action(config, "restart-host")
            service_action(config, "start-tail")
            _emit(activation.to_dict(), args.json)
        elif args.command == "force-start":
            _require_yes(parser, args.yes, "force-start")
            if not args.other_hub_confirmed_stopped:
                parser.error("force-start requires --other-hub-confirmed-stopped")
            activation = force_start(config, reason=args.reason)
            reconcile_local_route(config, restart_host=False)
            service_action(config, "stop-client")
            service_action(config, "start-core")
            service_action(config, "restart-host")
            service_action(config, "start-bridge")
            service_action(config, "start-watcher")
            _emit(activation.to_dict(), args.json)
        elif args.command == "repair-force-start":
            _require_yes(parser, args.yes, "repair-force-start")
            activation = repair_force_start(config)
            service_action(config, "start-watcher")
            service_action(config, "start-timer")
            _emit(activation.to_dict(), args.json)
        elif args.command == "snapshot":
            record = read_record(config)
            with local_lock(config.local_state_dir / "snapshot.lock"):
                snapshot_result = create_snapshot(
                    config,
                    record,
                    quiesced=args.quiesced,
                    publish=not args.no_publish,
                )
            _emit(snapshot_result, args.json)
        elif args.command == "snapshots":
            values = [str(path) for path in list_valid_snapshots(config)]
            _emit({"snapshots": values}, args.json)
        elif args.command == "validate-snapshot":
            manifest, temporary = validate_snapshot(config, args.archive)
            shutil.rmtree(temporary, ignore_errors=True)
            _emit(manifest, args.json)
        elif args.command == "restore":
            if not args.yes:
                parser.error("restore requires --yes and stopped Omnigent hub services")
            restore_result = restore_snapshot(config, args.archive)
            _emit(restore_result, args.json)
        elif args.command == "smoke-restore":
            archive = args.archive
            if archive is None:
                snapshots = list_valid_snapshots(config)
                if not snapshots:
                    raise SnapshotError("no valid snapshot is available")
                archive = snapshots[0]
            _emit(restore_smoke(config, archive), args.json)
        else:
            parser.error("unknown command")
    except (
        HandoffError,
        HubRuntimeError,
        RemoteError,
        ReconcileError,
        SnapshotError,
        SmokeError,
        StorageError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def _emit(value: Mapping[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, sort_keys=True))
        return
    print(json.dumps(value, indent=2, sort_keys=True))


def _require_yes(parser: argparse.ArgumentParser, approved: bool, command: str) -> None:
    if not approved:
        parser.error(f"{command} changes hub ownership and requires --yes")


def _target_fqdn(config: HubConfig, target: str) -> str:
    # Kept here rather than in static topology so temporary ownership never
    # requires editing a tracked file.
    topology = config.topology
    aliases = {
        "cco": topology.primary_fqdn,
        "primary": topology.primary_fqdn,
        "ftw": topology.standby_fqdn,
        "standby": topology.standby_fqdn,
        topology.primary_fqdn: topology.primary_fqdn,
        topology.standby_fqdn: topology.standby_fqdn,
    }
    try:
        return aliases[target.lower()]
    except KeyError as exc:
        raise ValueError(f"unknown hub target {target!r}") from exc


def _run_quiesced_backup(config: HubConfig) -> Mapping[str, object]:
    if config.local_fqdn not in config.topology.hubs:
        remote = RemoteClient(config)
        record, _, _ = remote.resolve()
        if record.state != "active" or record.active_hub is None:
            raise HandoffError("backup cannot begin while a transition is already active")
        return remote.json(
            record.active_hub,
            ("backup", "--quiesced", "--yes", "--json"),
            timeout=360,
        )
    record = read_record(config)
    if record.state == "transition":
        if record.source_hub != config.local_fqdn:
            raise HandoffError("this host is not the current transition source")
        if record.restored_generation:
            return record.to_dict()
        service_action(config, "stop-all")
        transition = record
    else:
        if record.active_hub != config.local_fqdn:
            raise HandoffError("quiesced backup must run on the active hub")
        target = next(host for host in config.topology.hubs if host != config.local_fqdn)
        service_action(config, "stop-ingress")
        try:
            assert_sessions_quiescent(config)
        except HubRuntimeError:
            reconcile_services(config)
            raise
        transition = begin_transition(config, target_hub=target)
        try:
            check_gate(config)
        except HubRuntimeError:
            pass
        else:
            raise HandoffError("source startup gate still passes after transition fence")
        service_action(config, "stop-server")
    with local_lock(config.local_state_dir / "snapshot.lock"):
        manifest = create_snapshot(config, transition, quiesced=True, publish=True)
    generation = manifest.get("generation_id")
    if not isinstance(generation, str):
        raise HandoffError("snapshot did not return a generation id")
    attached = attach_transition_generation(config, generation=generation)
    return {
        "transition": attached.to_dict(),
        "generation_id": generation,
        "archive_path": manifest.get("archive_path"),
    }


def _watch_activation(
    config: HubConfig,
    epoch: int,
    activation_id: str,
    interval: float,
    as_json: bool,
) -> None:
    if interval < 1:
        raise ValueError("watch interval must be at least one second")
    while True:
        record = resolve_record(config)
        current = (
            record.state == "active"
            and record.active_hub == config.local_fqdn
            and record.epoch == epoch
            and record.activation_id == activation_id
        )
        if not current:
            _emit(
                {
                    "current": False,
                    "expected_epoch": epoch,
                    "expected_activation_id": activation_id,
                    "record": record.to_dict(),
                },
                as_json,
            )
            raise SystemExit(3)
        time.sleep(interval)
