from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from omnigent_hub.config import HubConfig
from omnigent_hub.models import ActiveHubRecord
from omnigent_hub.remote import RemoteError, RemoteResult


class HandoffError(RuntimeError):
    pass


class RemoteOperations(Protocol):
    def resolve(self) -> tuple[ActiveHubRecord, str, dict[str, str]]: ...

    def run(
        self,
        host: str,
        args: Sequence[str],
        *,
        timeout: float = 180,
        check: bool = True,
    ) -> RemoteResult: ...

    def json(self, host: str, args: Sequence[str], *, timeout: float = 180) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class HandoffResult:
    source: str
    target: str
    generation: str
    epoch: int
    unexpected: bool
    gchat_reconciliation_required: bool
    steps: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "target": self.target,
            "generation": self.generation,
            "epoch": self.epoch,
            "unexpected": self.unexpected,
            "gchat_reconciliation_required": self.gchat_reconciliation_required,
            "steps": list(self.steps),
        }


class HandoffOrchestrator:
    _ACTIVATION_REFRESH_DELAYS = (0.0, 1.0, 2.0, 4.0, 8.0, 15.0)

    def __init__(
        self,
        config: HubConfig,
        remote: RemoteOperations,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._remote = remote
        self._sleep = sleep
        self._steps: list[str] = []

    def status(self) -> dict[str, Any]:
        record: ActiveHubRecord | None = None
        supplier: str | None = None
        resolution_errors: dict[str, str] = {}
        try:
            record, supplier, resolution_errors = self._remote.resolve()
        except RemoteError as exc:
            resolution_errors["resolution"] = str(exc)
        hosts: dict[str, Any] = {}
        for host in self._config.topology.hubs:
            try:
                hosts[host] = self._remote.json(host, ("local-status", "--json"))
            except RemoteError as exc:
                hosts[host] = {"reachable": False, "error": str(exc)}
        warnings = _status_warnings(record, hosts)
        return {
            "preferred_primary": self._config.topology.primary_fqdn,
            "standby": self._config.topology.standby_fqdn,
            "record": record.to_dict() if record else None,
            "record_supplier": supplier,
            "resolution_errors": resolution_errors,
            "hosts": hosts,
            "warnings": warnings,
        }

    def handoff(
        self,
        target: str,
        *,
        unexpected: bool,
        source_confirmed_stopped: bool,
        dry_run: bool,
    ) -> HandoffResult:
        self._config.topology.validate_hub(target)
        record, _, _ = self._remote.resolve()
        if record.state == "active":
            source = record.active_hub
            assert source is not None
            if source == target:
                raise HandoffError(f"{target} is already active")
            if dry_run:
                return self._dry_run_result(record, source, target, unexpected)
            if not unexpected:
                self._preflight_versions(source, target)
            if unexpected:
                if not source_confirmed_stopped:
                    raise HandoffError(
                        "unexpected recovery requires independent confirmation "
                        "that the source stopped"
                    )
                self._call(
                    target,
                    "cache-routing",
                    "--force-remount",
                    "--json",
                )
                generation, _ = self._newest_snapshot(target)
                self._call(target, "services", "stop-all", "--json")
                transition = self._call(
                    target,
                    "begin-unexpected-transition",
                    "--source",
                    source,
                    "--generation",
                    generation,
                    "--source-confirmed-stopped",
                    "--yes",
                    "--json",
                )
            else:
                transition = self._planned_quiesce(source, target)
                generation = _required_string(transition, "restored_generation")
        else:
            source = record.source_hub
            if source is None or record.target_hub != target:
                raise HandoffError(
                    f"unrelated transition {record.transition_id} is already in progress"
                )
            transition_id = record.transition_id
            assert transition_id is not None
            if unexpected and not transition_id.startswith("unexpected-"):
                raise HandoffError("existing transition is planned, not unexpected recovery")
            if not unexpected and transition_id.startswith("unexpected-"):
                raise HandoffError("existing transition requires unexpected recovery workflow")
            if dry_run:
                return self._dry_run_result(record, source, target, unexpected)
            if not unexpected:
                self._preflight_versions(source, target)
            transition = record.to_dict()
            if not unexpected and transition.get("restored_generation") is None:
                transition = self._resume_planned_snapshot(source)
            generation = _required_string(transition, "restored_generation")

        observed_transition = self._call(
            target,
            "cache-routing",
            "--force-remount",
            "--json",
        )
        _require_same_transition(observed_transition, transition)
        archive = self._archive_path(target, generation)
        self._call(target, "services", "stop-hub", "--json")
        self._call(target, "validate-snapshot", archive, "--json", timeout=300)
        self._call(target, "restore", archive, "--yes", "--json", timeout=300)
        activation = self._call(
            target,
            "activate",
            "--generation",
            generation,
            "--yes",
            "--json",
        )
        self._call(target, "services", "stop-client", "--json")
        self._call(target, "route-ensure", "--json")
        self._call(target, "services", "start-core", "--json", timeout=180)
        try:
            self._reconcile_source_after_activation(source, activation)
        except RemoteError as exc:
            self._steps.append(f"{source}: reconciliation deferred: {exc}")
        if unexpected:
            self._call(target, "services", "start-timer", "--json")
        else:
            self._call(target, "services", "start-tail", "--json")
        return HandoffResult(
            source=source,
            target=target,
            generation=generation,
            epoch=int(activation["epoch"]),
            unexpected=unexpected,
            gchat_reconciliation_required=unexpected,
            steps=tuple(self._steps),
        )

    def _reconcile_source_after_activation(
        self,
        source: str,
        activation: dict[str, Any],
    ) -> None:
        last_error: RemoteError | HandoffError | None = None
        for attempt, delay in enumerate(self._ACTIVATION_REFRESH_DELAYS, start=1):
            if delay:
                self._sleep(delay)
            try:
                observed = self._call(
                    source,
                    "cache-routing",
                    "--force-remount",
                    "--json",
                )
                _require_same_activation(observed, activation)
            except (RemoteError, HandoffError) as exc:
                last_error = exc
                if attempt < len(self._ACTIVATION_REFRESH_DELAYS):
                    self._steps.append(
                        f"{source}: activation refresh attempt {attempt} did not converge; retrying"
                    )
                continue
            self._call(source, "reconcile-services", "--json")
            return
        assert last_error is not None
        raise last_error

    def _planned_quiesce(self, source: str, target: str) -> dict[str, Any]:
        self._call(source, "services", "stop-ingress", "--json")
        try:
            self._call(source, "quiesce-check", "--json")
        except RemoteError as exc:
            try:
                self._call(source, "reconcile-services", "--json")
            except RemoteError as recovery_exc:
                self._steps.append(
                    f"{source}: ingress restoration failed after quiesce rejection: {recovery_exc}"
                )
            raise HandoffError(str(exc)) from exc
        transition = self._call(source, "begin-transition", "--target", target, "--yes", "--json")
        gate = self._remote.run(source, ("gate", "--json"), check=False)
        if gate.returncode == 0:
            raise HandoffError("source startup gate still passes after transition fence")
        self._steps.append(f"{source}: verified transition fence")
        self._call(source, "services", "stop-server", "--json")
        manifest = self._call(source, "snapshot", "--quiesced", "--json", timeout=300)
        generation = _required_string(manifest, "generation_id")
        transition = self._call(
            source,
            "attach-generation",
            "--generation",
            generation,
            "--json",
        )
        return transition

    def _resume_planned_snapshot(self, source: str) -> dict[str, Any]:
        self._call(source, "services", "stop-all", "--json")
        manifest = self._call(source, "snapshot", "--quiesced", "--json", timeout=300)
        generation = _required_string(manifest, "generation_id")
        return self._call(
            source,
            "attach-generation",
            "--generation",
            generation,
            "--json",
        )

    def _preflight_versions(self, source: str, target: str) -> None:
        source_status = self._call(source, "local-status", "--json")
        target_status = self._call(target, "local-status", "--json")
        source_versions = source_status.get("versions")
        target_versions = target_status.get("versions")
        if not isinstance(source_versions, dict) or not isinstance(target_versions, dict):
            raise HandoffError("version preflight returned an invalid status payload")
        for component in ("omnigent", "bridge"):
            source_version = source_versions.get(component)
            target_version = target_versions.get(component)
            if (
                not isinstance(source_version, str)
                or not isinstance(target_version, str)
                or source_version.startswith("ERROR:")
                or target_version.startswith("ERROR:")
                or source_version != target_version
            ):
                raise HandoffError(
                    f"{component} version mismatch: source={source_version!r}, "
                    f"target={target_version!r}"
                )
        self._steps.append(f"{source} and {target}: version preflight passed")

    def _newest_snapshot(self, host: str) -> tuple[str, str]:
        payload = self._call(host, "snapshots", "--json")
        snapshots = payload.get("snapshots")
        if not isinstance(snapshots, list) or not snapshots or not isinstance(snapshots[0], str):
            raise HandoffError("no valid recovery snapshot is available")
        archive = snapshots[0]
        manifest = self._call(host, "validate-snapshot", archive, "--json", timeout=300)
        return _required_string(manifest, "generation_id"), archive

    def _archive_path(self, host: str, generation: str) -> str:
        payload = self._call(host, "snapshots", "--json")
        snapshots = payload.get("snapshots")
        if not isinstance(snapshots, list):
            raise HandoffError("invalid snapshot listing")
        matches = [path for path in snapshots if isinstance(path, str) and generation in path]
        if len(matches) != 1:
            raise HandoffError(f"expected one archive for generation {generation}")
        return matches[0]

    def _call(self, host: str, *args: str, timeout: float = 180) -> dict[str, Any]:
        self._steps.append(f"{host}: {' '.join(args)}")
        return self._remote.json(host, args, timeout=timeout)

    def _dry_run_result(
        self,
        record: ActiveHubRecord,
        source: str,
        target: str,
        unexpected: bool,
    ) -> HandoffResult:
        if unexpected:
            generation = "<newest-valid-generation>"
            steps = (
                f"select newest validated snapshot visible from {target}",
                f"publish unexpected transition from {source} to {target}",
                f"restore and activate {target}",
                "reconcile routes and start core services plus snapshot timer",
                "leave Google Chat stopped pending reconcile-gchat",
            )
        else:
            generation = (
                record.restored_generation
                if record.state == "transition" and record.restored_generation
                else "<new-quiesced-generation>"
            )
            steps = (
                f"stop ingress on {source}",
                f"publish transition fence from {source} to {target}",
                f"stop {source} server and create final quiesced snapshot",
                f"validate, restore, and activate {target}",
                "reconcile routes, restart hosts, then start bridge and snapshot timer",
            )
        return HandoffResult(
            source=source,
            target=target,
            generation=generation,
            epoch=record.epoch if record.state == "transition" else record.epoch + 1,
            unexpected=unexpected,
            gchat_reconciliation_required=unexpected,
            steps=steps,
        )


def _required_string(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise HandoffError(f"response is missing {key}")
    return item


def _require_same_transition(observed: dict[str, Any], expected: dict[str, Any]) -> None:
    keys = ("epoch", "state", "source_hub", "target_hub", "transition_id", "restored_generation")
    if any(observed.get(key) != expected.get(key) for key in keys):
        raise HandoffError("target did not observe the exact published transition after remount")


def _require_same_activation(observed: dict[str, Any], expected: dict[str, Any]) -> None:
    keys = ("epoch", "state", "active_hub", "activation_id", "restored_generation")
    if any(observed.get(key) != expected.get(key) for key in keys):
        raise HandoffError("source did not observe the target activation after remount")


def _status_warnings(record: ActiveHubRecord | None, hosts: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    active_services: dict[str, list[str]] = {}
    for host, payload in hosts.items():
        if not isinstance(payload, dict):
            continue
        if payload.get("reachable") is False:
            warnings.append(f"WARNING: {host} is unreachable")
            continue
        routing = payload.get("routing")
        if isinstance(routing, dict):
            if routing.get("cli_stale") is True:
                warnings.append(f"WARNING: {host} CLI server URL is stale")
            if routing.get("environment_stale") is True:
                warnings.append(f"WARNING: {host} OMNIGENT_URL is stale")
            stale_pids = routing.get("nvim_stale_pids")
            if isinstance(stale_pids, list) and stale_pids:
                warnings.append(f"WARNING: {host} Neovim processes hold a stale URL: {stale_pids}")
        cache = payload.get("routing_cache")
        if (
            record
            and record.state == "active"
            and (
                not isinstance(cache, dict)
                or cache.get("epoch") != record.epoch
                or cache.get("activation_id") != record.activation_id
            )
        ):
            warnings.append(f"WARNING: {host} routing cache is stale")
        snapshot = payload.get("newest_snapshot")
        if isinstance(snapshot, dict):
            age = snapshot.get("age_seconds")
            if isinstance(age, int) and age > 600:
                warnings.append(f"WARNING: newest snapshot seen by {host} is {age}s old")
            versions = payload.get("versions")
            if isinstance(versions, dict):
                if versions.get("omnigent") != snapshot.get("omnigent_version"):
                    warnings.append(f"CRITICAL: {host} Omnigent version differs from snapshot")
                if versions.get("bridge") != snapshot.get("bridge_version"):
                    warnings.append(f"CRITICAL: {host} bridge version differs from snapshot")
        elif payload.get("snapshot_error"):
            warnings.append(f"WARNING: {host} cannot validate a recovery snapshot")
        services = payload.get("services")
        if not isinstance(services, dict):
            continue
        for service, state in services.items():
            if state == "active":
                active_services.setdefault(str(service), []).append(host)
    for service in (
        "omnigent-server.service",
        "omnigent-prodnet.service",
        "omnigent-google-chat.service",
        "omnigent-snapshot.timer",
    ):
        owners = active_services.get(service, [])
        if len(owners) > 1:
            warnings.append(f"CRITICAL: {service} is active on multiple hubs: {owners}")
        if record and record.state == "active" and owners and owners != [record.active_hub]:
            warnings.append(
                f"CRITICAL: {service} owner {owners} disagrees with {record.active_hub}"
            )
        if (
            record
            and record.state == "active"
            and record.active_hub is not None
            and not owners
            and _host_is_reachable(hosts.get(record.active_hub))
        ):
            warnings.append(f"WARNING: {service} is not active on {record.active_hub}")
    if record and record.state == "active" and record.active_hub:
        reachable_standbys = [
            host
            for host, payload in hosts.items()
            if host != record.active_hub and _host_is_reachable(payload)
        ]
        expected_proxy = reachable_standbys[0] if len(reachable_standbys) == 1 else None
        proxy_owners = active_services.get("omnigent-client-proxy.service", [])
        if record.active_hub in proxy_owners:
            warnings.append(f"CRITICAL: client proxy is active on serving hub {record.active_hub}")
        if expected_proxy and proxy_owners != [expected_proxy]:
            warnings.append(
                f"WARNING: client proxy owners {proxy_owners} differ from standby {expected_proxy}"
            )
    if record and record.state == "active":
        for host, payload in hosts.items():
            if not isinstance(payload, dict) or payload.get("reachable") is False:
                continue
            gate = payload.get("gate")
            if not isinstance(gate, dict):
                continue
            allowed = gate.get("allowed") is True
            should_allow = host == record.active_hub
            if allowed != should_allow:
                warnings.append(
                    f"CRITICAL: startup gate on {host} allowed={allowed}, expected={should_allow}"
                )
    return warnings


def _host_is_reachable(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("reachable") is not False
