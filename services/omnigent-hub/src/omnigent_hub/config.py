from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path

from omnigent_hub.models import Topology, ValidationError

WATCHER_DB_NAME = "watcher.sqlite3"
# The name the watcher database had while the service was called
# omnigent-diff-watcher. The sidecar renames it on its next start.
LEGACY_WATCHER_DB_NAME = "diff-watcher.sqlite3"

# The active-hub record lives flat at the mount root, NOT under storage_root.
#
# Every directory manifoldfs creates carries its own 30-day Manifold TTL that
# counts down on wall clock alone: writing files inside it does not push it out,
# and neither does mkdir(exist_ok=True). When that directory expires, Manifold
# deletes everything beneath it regardless of the children's own expiry. The
# mount root is the one exception -- manifoldfs maintains it at ttl=0 (infinite,
# and userData=0), so a file placed directly there has only its OWN expiry,
# which a rewrite genuinely does reset.
#
# This is not hypothetical. `omnigent-ha/` was created 2026-08-17 15:42 and the
# whole tree -- record and snapshots/ together -- was swept 2026-09-16 15:42, to
# the hour, even though the record itself had been republished two days earlier.
# That republish (see _refresh_record_ttl) was the right mechanism aimed at the
# wrong object: it kept the file young inside a directory that was already dying.
#
# Snapshots deliberately stay under storage_root. They are pruned at <= 7 days,
# so the 30-day file TTL never reaches them, and publish_snapshot recreates the
# directory, so a directory sweep self-heals on the next timer tick. The record
# cannot tolerate that gap; the archives can.
RECORD_NAME = "omnigent-ha-active-hub.json"


@dataclass(frozen=True, slots=True)
class HubConfig:
    home: Path
    dotfiles: Path
    topology_path: Path
    topology: Topology
    owner_fbid: str
    local_fqdn: str
    data_dir: Path
    local_state_dir: Path
    routing_cache: Path
    storage_mount: Path
    storage_root: Path
    record_path: Path
    legacy_record_path: Path
    snapshots_dir: Path
    omnigent_bin: Path
    bridge_project: Path
    storage_mount_name: str

    @property
    def chat_db(self) -> Path:
        return self.data_dir / "chat.db"

    @property
    def bridge_db(self) -> Path:
        return self.data_dir / "google-chat.sqlite3"

    @property
    def watcher_db(self) -> Path:
        return self.data_dir / WATCHER_DB_NAME

    @property
    def legacy_watcher_db(self) -> Path:
        """Where the watcher database lived before the service was renamed.

        The sidecar renames it on its next start. Until then this host still
        has the old name, and a snapshot taken in that window must capture the
        file that actually exists.
        """
        return self.data_dir / LEGACY_WATCHER_DB_NAME

    @property
    def watcher_project(self) -> Path:
        return self.dotfiles / "services/omnigent-watcher"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    @property
    def activation_marker(self) -> Path:
        return self.local_state_dir / "activation.json"

    @property
    def backup_status(self) -> Path:
        return self.local_state_dir / "backup-status.json"

    @property
    def host_probe_failures(self) -> Path:
        """Consecutive failed host-registration probes, across reconcile runs.

        The reconciler is a timer-driven process, so the streak cannot live in
        memory. Local state, not shared storage: it describes this machine's
        view of its own host and means nothing to the other candidate.
        """
        return self.local_state_dir / "host-probe-failures.json"

    @property
    def local_snapshots_dir(self) -> Path:
        """Where snapshots land when they cannot be published to shared storage.

        A holding area, not a store: publication is what makes a snapshot part of
        the deployment's recovery history, and these are pruned hard because they
        exist only to cover an outage window.
        """
        return self.local_state_dir / "local-snapshots"

    @property
    def degraded_streak(self) -> Path:
        """Consecutive reconcile cycles that could not read shared storage.

        Local state for the same reason ``host_probe_failures`` is: the timer
        starts a fresh process every cycle, so a streak cannot live in memory,
        and it describes this machine's view rather than shared truth.

        The streak exists so a blip and an outage look different. Reconcile wrote
        ``"state": "degraded"`` 7,298 times across the five days the active-hub
        record was missing and nothing escalated, because a single failed cycle
        and the four-thousandth one exited identically.
        """
        return self.local_state_dir / "degraded-streak.json"


def load_config(environ: dict[str, str] | None = None) -> HubConfig:
    env = os.environ if environ is None else environ
    home = Path(env.get("HOME", str(Path.home()))).expanduser()
    dotfiles = Path(env.get("DOTFILES_DIR", str(home / "dotfiles"))).expanduser()
    topology_path = Path(
        env.get("OMNIGENT_TOPOLOGY_FILE", str(dotfiles / "omnigent_config/topology.env"))
    ).expanduser()
    file_values = parse_env_file(topology_path)

    def setting(name: str, default: str) -> str:
        return env.get(name, file_values.get(name, default))

    primary = setting("OMNIGENT_PRIMARY_FQDN", "devvm20365.cco0.facebook.com")
    standby = setting("OMNIGENT_STANDBY_FQDN", "devvm36111.ftw0.facebook.com")
    if primary == standby:
        raise ValidationError("primary and standby FQDNs must differ")
    try:
        port = int(setting("OMNIGENT_PORT", "6767"))
    except ValueError as exc:
        raise ValidationError("OMNIGENT_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValidationError("OMNIGENT_PORT is out of range")
    topology = Topology(primary_fqdn=primary, standby_fqdn=standby, port=port)

    storage_mount = Path(
        setting("OMNIGENT_HA_STORAGE_MOUNT", str(home / "persistent/private-30d"))
    ).expanduser()
    storage_root = Path(
        setting("OMNIGENT_HA_STORAGE_ROOT", str(storage_mount / "omnigent-ha"))
    ).expanduser()
    data_dir = Path(setting("OMNIGENT_DATA_DIR", str(home / ".omnigent"))).expanduser()
    state_dir = Path(
        setting("OMNIGENT_HA_STATE_DIR", str(home / ".local/state/omnigent-hub"))
    ).expanduser()
    routing_cache = Path(
        setting("OMNIGENT_HA_ROUTING_CACHE", str(home / ".config/omnigent/active-hub.json"))
    ).expanduser()
    return HubConfig(
        home=home,
        dotfiles=dotfiles,
        topology_path=topology_path,
        topology=topology,
        owner_fbid=setting("OMNIGENT_OWNER_FBID", "1097089018461839"),
        local_fqdn=setting("OMNIGENT_LOCAL_FQDN", socket.getfqdn()),
        data_dir=data_dir,
        local_state_dir=state_dir,
        routing_cache=routing_cache,
        storage_mount=storage_mount,
        storage_root=storage_root,
        record_path=storage_mount / RECORD_NAME,
        legacy_record_path=storage_root / "active-hub.json",
        snapshots_dir=storage_root / "snapshots",
        omnigent_bin=Path(setting("OMNIGENT_BIN", str(home / ".local/bin/omnigent"))),
        bridge_project=Path(
            setting(
                "OMNIGENT_GCHAT_PROJECT",
                str(dotfiles / "services/omnigent-google-chat"),
            )
        ),
        storage_mount_name=setting("OMNIGENT_HA_STORAGE_MOUNT_NAME", "private-30d"),
    )


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValidationError(f"{path}:{number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key or not key.replace("_", "").isalnum():
            raise ValidationError(f"{path}:{number}: invalid environment key")
        values[key] = value
    return values
