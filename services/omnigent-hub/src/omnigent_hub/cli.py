from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from omnigent_hub.config import HubConfig, load_config
from omnigent_hub.models import ActiveHubRecord
from omnigent_hub.notify import alert as _alert
from omnigent_hub.orchestrator import HandoffError, HandoffOrchestrator
from omnigent_hub.reconcile import ReconcileError, reconcile_gchat
from omnigent_hub.remote import RemoteClient, RemoteError
from omnigent_hub.runtime import (
    GATE_EXIT_DENIED,
    GATE_EXIT_INDETERMINATE,
    GateDenied,
    GateIndeterminate,
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
    reconcile_host,
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
            "start-host",
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
    snapshot.add_argument(
        "--max-age",
        type=float,
        default=None,
        metavar="SECONDS",
        help=(
            "do nothing if the last snapshot is younger than this. Lets a caller "
            "say 'make sure a recent recovery point exists' without forcing a "
            "fresh multi-hundred-megabyte archive every time it asks."
        ),
    )
    snapshot.add_argument(
        "--fallback-local",
        action="store_true",
        help=(
            "when ownership cannot be established, keep an unpublished snapshot "
            "on local disk instead of failing. Used by the snapshot timer so an "
            "ownership outage does not also stop recovery points."
        ),
    )
    snapshot.add_argument("--json", action="store_true")

    alert = subparsers.add_parser(
        "alert", help="record a unit failure and notify unless it is noise"
    )
    alert.add_argument("unit")
    alert.add_argument("--json", action="store_true")

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
            # Exit codes are load-bearing: systemd reads 1-254 from an
            # ExecCondition= as "skip this unit, all is well" and 255 as "this
            # unit FAILED". Denied is the standby's normal resting state and must
            # stay quiet; indeterminate means nothing can establish ownership and
            # must go red. Collapsing the two is what hid a five-day outage.
            try:
                gate_result = check_gate(config)
            except GateDenied as exc:
                print(f"SKIP: {exc}", file=sys.stderr)
                raise SystemExit(GATE_EXIT_DENIED) from exc
            except (GateIndeterminate, StorageError) as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                raise SystemExit(GATE_EXIT_INDETERMINATE) from exc
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
            outcome = reconcile_services(config)
            _emit(outcome, args.json)
            if outcome.get("state") == "degraded":
                # Local services were reconciled from the routing cache, but the
                # shared record is unreadable: still fail so the outage stays
                # visible in the unit state instead of only in the log body.
                #
                # Deliberately a plain 1 and not GATE_EXIT_INDETERMINATE: 255 only
                # means anything to an ExecCondition=, and this is an ExecStart=,
                # where every nonzero code fails the unit identically. Whether a
                # given cycle is worth escalating is decided by bin/omnigent-alert
                # off the streak this run just recorded, so the threshold lives in
                # one place instead of being smuggled into an exit status.
                raise SystemExit(1)
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
            route = reconcile_local_route(config, restart_host=False)
            service_action(config, "stop-client")
            service_action(config, "start-core")
            # Operator-initiated and gated behind --yes: restart an unregistered
            # host on this single probe rather than deferring to the reconcile
            # timer's consecutive-failure threshold, which would leave this
            # command silently doing nothing about the host it was run to fix.
            reconcile_host(
                config, route_changed=bool(route["changed"]), require_repeated_failure=False
            )
            service_action(config, "start-tail")
            _emit(activation.to_dict(), args.json)
        elif args.command == "force-start":
            _require_yes(parser, args.yes, "force-start")
            if not args.other_hub_confirmed_stopped:
                parser.error("force-start requires --other-hub-confirmed-stopped")
            activation = force_start(config, reason=args.reason)
            route = reconcile_local_route(config, restart_host=False)
            service_action(config, "stop-client")
            service_action(config, "start-core")
            # Operator-initiated and gated behind --yes: restart an unregistered
            # host on this single probe rather than deferring to the reconcile
            # timer's consecutive-failure threshold, which would leave this
            # command silently doing nothing about the host it was run to fix.
            reconcile_host(
                config, route_changed=bool(route["changed"]), require_repeated_failure=False
            )
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
            recent = _recent_backup(config, args.max_age)
            if recent is not None:
                _emit(recent, args.json)
            else:
                record, publish = _snapshot_target(
                    config,
                    publish=not args.no_publish,
                    fallback_local=args.fallback_local,
                )
                with local_lock(config.local_state_dir / "snapshot.lock"):
                    snapshot_result = create_snapshot(
                        config,
                        record,
                        quiesced=args.quiesced,
                        publish=publish,
                    )
                _emit(snapshot_result, args.json)
        elif args.command == "alert":
            # Never fails the caller: this runs as an OnFailure= handler, and a
            # handler that can itself fail just adds a second failed unit to the
            # pile nobody is looking at.
            _emit(alert_on_failure(config, unit=args.unit), args.json)
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


def alert_on_failure(config: HubConfig, *, unit: str) -> dict[str, object]:
    """Escalate a failed unit, swallowing anything that goes wrong doing so.

    Broad by design. This is the last link in the chain that exists because a
    failure went unnoticed for five days; it raising its own exception would put
    it right back in that category.
    """
    try:
        return _alert(config, unit=unit)
    except Exception as exc:  # noqa: BLE001 - see docstring
        return {"unit": unit, "notified": False, "reason": f"alert handler failed: {exc}"}


def _recent_backup(config: HubConfig, max_age: float | None) -> dict[str, object] | None:
    """The last snapshot, if it is younger than *max_age* seconds.

    Answered from ``backup-status.json`` rather than by listing the store: the
    shared store is a FUSE mount where every archive is a couple of hundred
    megabytes, and the whole point of this check is to be cheap enough that a
    caller can make it unconditionally before deciding to do real work.
    """
    if max_age is None:
        return None
    status = _read_json_file(config.backup_status)
    created = status.get("created_at")
    if not isinstance(created, str):
        return None
    try:
        taken = datetime.fromisoformat(created.replace("Z", "+00:00"))
        age = (datetime.now(UTC) - taken).total_seconds()
    except ValueError:
        return None
    if age > max_age:
        return None
    return {**status, "skipped": True, "age_seconds": int(max(0, age))}


def _snapshot_target(
    config: HubConfig, *, publish: bool, fallback_local: bool
) -> tuple[ActiveHubRecord, bool]:
    """Pick the record to stamp into the snapshot, and whether to publish it.

    Publication needs the authoritative record, because publishing is the step
    that can collide with the other hub. Capture does not. With *fallback_local*
    an unreadable store therefore downgrades to an unpublished local snapshot
    instead of producing nothing -- the deployment kept zero recovery points for
    five days the last time these two failed together.
    """
    if not publish:
        return read_record(config), False
    try:
        return read_record(config), True
    except StorageError:
        if not fallback_local:
            raise
    cached = ActiveHubRecord.from_dict(_read_json_file(config.routing_cache), config.topology)
    return cached, False


def _read_json_file(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


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
        except GateDenied:
            # Only a definite denial proves the fence took hold. An indeterminate
            # gate is allowed to propagate and abort the handoff: "I cannot read
            # who owns this" must never be mistaken for "the fence is working".
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
