# Personal Omnigent primary/standby availability

**Status:** Implemented and rehearsed across CCO, FTW, and macOS. A real
planned promotion and failback preserved FTW-era state, kept the old primary
fenced, and returned ownership to CCO.

**Owner:** `mkarrmann`

**Scope:** The personal Omnigent deployment managed by this dotfiles
repository. This document is intentionally outside the open-source Omnigent
repository because it describes Meta-specific infrastructure, personal
hostnames, Google Chat integration, and machine-local operations.

## 1. Decision

Keep the larger CCO devserver as the normal Omnigent control-plane primary.
Prepare FTW as an inactive standby that can be promoted for planned CCO
maintenance or after an unexpected CCO failure.

Use:

- one writable Omnigent server at a time;
- one Google Chat bridge, colocated with the active server;
- local SQLite and local artifacts on the active hub;
- frequent, consistent snapshots in Meta Persistent Storage;
- a small shared active-hub record to fence service startup;
- a dotfiles command that performs promotion, failback, and validation; and
- manual promotion rather than automatic failover.

Dotfiles pin the Omnigent, Google Chat bridge, and hub-controller versions used
by both hubs. For the bridge and controller, "version" means a reproducible
source-content digest, not a manually entered label. CCO and FTW must remain in
lockstep even while FTW is inactive.

`init.sh` is the supported onboarding and reconciliation entry point on every
machine. On a Linux candidate it mounts or refreshes coordination state,
retires incompatible pre-HA launchers only while that machine is inactive,
reconciles systemd services to the current role, and runs an end-state health
check. A cold Persistent Storage mount may require one interactive
Meta-authenticated `omnigent-hub status` invocation; this credential bootstrap
is the only intentional machine-local onboarding step.

An ordinary execution devserver does not mount Persistent Storage. During
`init.sh` it asks the configured candidates for the highest valid epoch,
materializes the routing cache locally, and starts its execution host against
that active hub. Thus onboarding a new execution devserver requires only the
tracked topology plus connectivity to either candidate.

Do not permanently relocate the control plane to FTW. That would only exchange
one single point of failure for another and would put the normal workload on
the smaller machine.

This design is primary/standby disaster recovery, not production-grade HA. It
is deliberately sized for a personal development setup.

## 2. What this solves

### 2.1 Planned CCO maintenance

Before shutting down CCO:

1. Quiesce Omnigent input.
2. Publish a transition fence under which neither hub may newly start.
3. Stop the CCO bridge, snapshot timer, proxy, and server.
4. Create and verify a final snapshot.
5. Restore that snapshot on FTW.
6. Activate FTW and redirect clients.

There is no committed-state loss. Omnigent history, the API, CodeCompanion,
and Google Chat become available through FTW while CCO is down. New work can
run in FTW workspaces.

After CCO returns, the same procedure transfers the newer FTW state back to
CCO and restores CCO as primary.

Promotion is for extended maintenance, machine replacement, or an outage that
would otherwise block useful work. A routine reboot does not require a
promotion. For a normal short reboot, CCO's server restarts in place, clients
reconnect, and the user accepts a brief interruption. Avoiding that brief
interruption would require more automation and a materially higher split-brain
risk than this personal deployment needs.

### 2.2 Unexpected CCO failure

FTW restores the newest completed snapshot and is promoted manually. The
maximum transcript loss is the time since that snapshot. A five-minute
snapshot interval gives an initial recovery-point objective of at most five
minutes, subject to backup health.

No SQLite snapshot scheme can guarantee zero loss after sudden machine loss.
That would require synchronous replicated storage or a different database.
For this personal deployment, bounded loss plus simple recovery is a better
tradeoff than maintaining a custom DSQLite integration or a distributed
Omnigent control plane.

### 2.3 What it does not solve

Promotion preserves Omnigent's durable control-plane state. It cannot migrate:

- a running Claude or Codex process;
- an in-flight tool call;
- an Eden workspace located only on CCO;
- uncommitted CCO files; or
- process-local runner, WebSocket, SSE, or elicitation state.

While CCO is down, its sessions remain visible in history but their CCO-bound
runners cannot execute. Work that must continue should be started or forked in
an FTW workspace. When CCO returns, its host reconnects and its sessions become
recoverable there.

## 3. Topology

### 3.1 Normal operation

```text
                         CCO devserver (PRIMARY)
                  devvm20365.cco0.facebook.com
                  +--------------------------------+
                  | omnigent-server                |
                  |   127.0.0.1:6767               |
                  |   ~/.omnigent/chat.db          |
                  |   ~/.omnigent/artifacts/       |
                  |                                |
                  | omnigent-prodnet proxy         |
                  | omnigent-google-chat           |
                  | snapshot timer                 |
                  +--------------------------------+
                         ^                 |
                         |                 v
              prodnet HTTP/WebSocket   Meta Persistent Storage
                         |             active-hub.json + snapshots
                         |
                  +------+-------------------------+
                  | FTW devserver (STANDBY)         |
                  | omnigent-host                   |
                  | loopback client proxy -> CCO   |
                  | server and bridge stopped      |
                  | same pinned software installed |
                  +--------------------------------+

          Mac --ET tunnel to CCO:6767--> CCO loopback:6767
```

CCO remains both an execution host and the control-plane hub. FTW remains an
execution host, but its server, prodnet proxy, bridge, and snapshot timer are
inactive. Its client proxy owns `127.0.0.1:6767`, so FTW CLI and already-open
Neovim clients keep a stable loopback URL while reaching CCO.

### 3.2 During CCO maintenance

```text
                         CCO devserver (OFFLINE)
                  devvm20365.cco0.facebook.com

                         FTW devserver (ACTIVE)
                  devvm36111.ftw0.facebook.com
                  +--------------------------------+
                  | restored Omnigent state        |
                  | omnigent-server                |
                  | omnigent-prodnet proxy         |
                  | omnigent-google-chat           |
                  | snapshot timer                 |
                  +--------------------------------+

          Mac --ET tunnel to FTW:6767--> FTW loopback:6767
```

FTW is not a concurrent replica. Promotion transfers ownership of the one
logical Omnigent history.

## 4. State model

### 4.1 State that moves with the active hub

| Path | Purpose |
|---|---|
| `~/.omnigent/chat.db` | Sessions, durable items, labels, hosts, permissions, and other server state |
| `~/.omnigent/artifacts/` | Agent bundles, uploads, and content-addressed server artifacts |
| `~/.omnigent/google-chat.sqlite3` | Thread mappings, inbound claims, outbound request IDs, cursors, and ambiguity state |

These three stores form one operational recovery unit. Restoring only
`chat.db` could duplicate Google Chat threads or replay phone input. Omitting
artifacts could leave database rows referring to missing content.

### 4.2 State that never moves

Do not copy:

- `~/.omnigent/config.yaml` host identity;
- host or runner PID files;
- x509 material, tokens, or auth caches;
- logs and reconstructable caches;
- native Claude/Codex process state; or
- Eden workspaces.

Each physical machine keeps its own `host.host_id`. CCO does not impersonate
FTW, and FTW does not impersonate CCO.

### 4.3 One-writer invariant

There is no merge algorithm for two independently writable copies of
`chat.db`. The central invariant is therefore:

> At most one Omnigent server and one Google Chat bridge may be active for the
> deployment.

Promotion must fence the previous hub before the replacement accepts writes.
Failback must transfer the replacement's newer state; it must never restart
the primary from its stale pre-maintenance database.

## 5. Coordination and fencing

### 5.1 Tracked topology

Dotfiles should describe stable topology, not the current incident state:

```text
OMNIGENT_PRIMARY_FQDN=devvm20365.cco0.facebook.com
OMNIGENT_STANDBY_FQDN=devvm36111.ftw0.facebook.com
OMNIGENT_PORT=6767
```

CCO is always the preferred home. A temporary promotion does not require a
source-control commit that later has to be reverted.

### 5.2 Shared active-hub record

Store one small record in the private Persistent Storage namespace:

```json
{
  "format_version": 1,
  "epoch": 7,
  "active_hub": "devvm20365.cco0.facebook.com",
  "activation_id": "activation-7-4f8c...",
  "restored_generation": "20260718T201500Z-91ab...",
  "updated_at": "2026-07-18T20:16:03Z",
  "updated_by": "mkarrmann"
}
```

This record is coordination metadata, not a live database or an automatic
leader-election system.

Every hub-only systemd service has an `ExecCondition` or `ExecStartPre` check:

1. Persistent Storage must be mounted and readable.
2. The record must parse and pass validation.
3. `active_hub` must equal the local FQDN.
4. A local activation marker must contain the same `epoch`, `activation_id`,
   and `restored_generation` as the shared record.

The check is not a one-shot test against an unmounted ManifoldFS path. The
implemented startup helper:

1. waits for normal network readiness through systemd ordering;
2. uses an already-readable private mount when available;
3. mounts or remounts the namespace with bounded exponential backoff for up
   to two minutes;
4. remounts once and retries if the mount directory is readable but the record
   read fails, which is the stale-CAT failure mode; and
5. validates the record only after the record itself is readable.

Private Persistent Storage does not support a durable unattended personal
credential. The implementation therefore does not persist a bearer CAT and
does not claim fully autonomous cold-boot recovery. An authenticated operator
command or the Mac tunnel bootstrap mints a 15-minute delegated CAT with
`clicat`, passes it only in the remote process environment, and lets the
candidate mount or refresh the namespace. A successful mount can then serve
the systemd reconciler and snapshots. If no authenticated bootstrap is
available after a cold boot, the 60-second reconcile timer keeps retrying but
the hub remains fail-closed. This is an intentional availability tradeoff for
a personal deployment and avoids introducing IES or a production identity.

The activation marker is a lineage token, not a hash of the live database.
It is created once when a hub is initialized or promoted and remains stable as
the database accepts normal writes. Consequently, a routine restart of the
rightful primary succeeds. Initial deployment creates both the shared CCO
record and CCO's matching local marker before enabling the startup condition.
Promotion writes the replacement's marker only after restoring and validating
the named snapshot. Publishing a transition and reconciling a hub to standby
delete its old marker, so a stale well-formed record cannot re-enable a
previous owner merely by matching retained local lineage.

If any check fails, startup fails closed. Once a server is running, a transient
Persistent Storage outage does not kill it. The check primarily prevents a
rebooted CCO from automatically becoming a second writer while FTW is active.

The promotion tool updates the record through a write-new, verify, and rename
sequence. ManifoldFS rename is locally atomic but another mounted client can
briefly retain a partial or stale cached view. A parse failure therefore
forces one authenticated remount and retry. More importantly, a handoff
force-remounts the target after the source publishes the transition and
verifies every transition identity field before restore. After activation it
retries authenticated remounts on the old source for up to 30 seconds before
service reconciliation because the real rehearsal observed a brief
same-epoch stale view. An unresolved identity mismatch leaves the target's
tail services stopped; an unreachable old source is reported as deferred and
its periodic reconciler converges later. Because only one human-operated
command changes the record, this does not need a general consensus protocol.
A monotonically increasing epoch makes stale local routing state detectable.

There is no automatic preferred-primary exception when Persistent Storage is
unreadable. Such an exception is unsafe: CCO could return during an FTW
promotion and start from stale state while FTW is already writable. Provide a
manual `force-start` recovery operation instead. It requires the operator to
confirm that the other hub's server, proxy, and bridge are stopped, records a
loud audit warning, and repairs the shared record and local marker when
Persistent Storage returns. Even while a force override exists, every record
resolution first attempts to mount and read the shared record. The override is
used only after that read fails; a readable equal or newer shared epoch retires
the override so it cannot revive during a later storage outage.

### 5.3 Stable client endpoint on both devservers

CCO and FTW always configure Omnigent clients with:

```text
http://127.0.0.1:6767
```

On the active hub, the server owns that socket. On the inactive hub,
`omnigent-client-proxy.service` owns it and relays to the active hub's prodnet
listener. The reconciler stops the client proxy before starting a local
server, and stops all hub services before starting the client proxy. During a
transition, both are stopped.

This stable endpoint eliminates promotion-time Neovim restarts on both
devservers. `omnigent-dvsc-ensure --config-only` still reconciles
`~/.omnigent/config.yaml`, and `~/.config/environment.d/omnigent.conf` still
records `OMNIGENT_URL`, but their values no longer change between CCO and FTW
ownership. A changed or missing value restarts `omnigent-host`; an ownership
epoch change alone does not.

`omnigent-hub status` compares each candidate's `config.yaml` server,
generated `OMNIGENT_URL`, routing epoch, hub services, and standby client
proxy with the active-hub record. This
explicitly guards against the stale-port/configuration failure previously seen
with `16767` versus `6767`.

The shared record is the startup fence. The local files are routing caches.
They are not independent sources of truth.

### 5.4 Mac discovery is pull-based

The Mac does not mount Meta Persistent Storage and never reads
`active-hub.json` directly. Persistent Storage access is restricted to the CCO
and FTW hub-control helpers.

The tracked topology on the Mac contains only static candidates:

```text
OMNIGENT_PRIMARY_FQDN=devvm20365.cco0.facebook.com
OMNIGENT_STANDBY_FQDN=devvm36111.ftw0.facebook.com
OMNIGENT_PORT=6767
```

On tunnel startup, periodic reconciliation, and connection failure, the Mac:

1. Contacts a reachable candidate through the existing authenticated SSH/ET
   path.
2. Runs a read-only remote command such as
   `omnigent-hub resolve --json`.
3. The Mac mints an ephemeral delegated CAT, passes it in the remote command
   environment, and the helper mounts or refreshes Persistent Storage before
   returning the active hub, epoch, state, and activation ID. The token is
   neither logged nor written to disk.
4. If the first candidate is unavailable, the Mac queries the other.
5. If valid responses disagree at the same epoch, resolution fails closed. A
   higher validated epoch supersedes a lower stale response.
6. A transition-state response means no hub is currently active; the Mac
   keeps retrying and does not guess a target.
7. Once an active record is obtained, the Mac points its local port 6767 ET
   forward at that hub and verifies the remote Omnigent health endpoint.

The Mac caches the last validated response for diagnostics, not authority. If
neither candidate can answer, it may keep an already-healthy tunnel running,
but it must not retarget from stale cache. If the existing tunnel is unhealthy
and discovery is unavailable, it reports Omnigent disconnected and retries.

The tracked `bin-macos/omnigent-tunnel` process is launched in its own Ghostty
window by `startup-windows`. It keeps an ET forward open while a remote
`watch-activation` command confirms the epoch every 30 seconds, probes local
health every 10 seconds, and rediscovers immediately after either fails.

On the Mac, both `~/.omnigent/config.yaml` and `OMNIGENT_URL` continue to use:

```text
http://127.0.0.1:6767
```

They do not change during promotion. Existing Mac Neovim and CLI processes
therefore reconnect through the retargeted local tunnel without restarting.
CCO and FTW also keep the same loopback URL through the standby proxy, so
already-running devserver Neovim processes do not require a promotion restart.

## 6. Network behavior

The active server continues to bind only:

```text
127.0.0.1:6767
```

Do not bind uvicorn directly to `::`; its IPv6-only socket breaks IPv4
loopback and the existing Mac ET path.

The active hub alone runs `omnigent-prodnet.service`:

```text
[active hub prodnet IPv6]:6767 -> 127.0.0.1:6767
```

The inactive candidate alone runs:

```text
127.0.0.1:6767 -> [active hub prodnet IPv6]:6767
```

Peer devservers use that prodnet endpoint. The Mac uses an ET forward to the
active hub's loopback port. The Mac learns the target through the pull protocol
in Section 5.4, not through Manifold or a hub-to-laptop push. Promotion changes
the selected ET host; it does not introduce ServiceRouter, VPNLess, a VIP, or
a public endpoint.

Ordinary clients never need Persistent Storage credentials or filesystem
access. Hub-control helpers use it for snapshots and coordination; Mac and
devserver clients consume the resolved routing result through authenticated
local or remote helpers.

## 7. Snapshot design

### 7.1 Why Persistent Storage

Meta Persistent Storage is available from both devservers and uses a private,
user-scoped Manifold namespace. It avoids creating a custom Manifold bucket or
running a personal production service.

Use `~/persistent/private-30d`. Keep the default TTL because transcripts can
contain user data or source-derived content. Do not certify these backups as
`--no-user-data` merely to remove retention.

Never run live SQLite from ManifoldFS. Its semantics and performance are not
appropriate for SQLite locking, WAL, or shared-memory files. Only immutable,
completed backup archives belong there.

### 7.2 Archive format

Each generation contains:

```text
omnigent-state/
  manifest.json
  chat.db
  google-chat.sqlite3
  artifacts/
    ...
```

The manifest records:

- format version;
- generation ID;
- source host and active-hub epoch;
- creation timestamp;
- installed Omnigent and bridge versions;
- database checksums;
- a zero-count credential scan for account tokens and password hashes;
- artifact count and total bytes; and
- whether the snapshot was online or quiesced.

Restore compares the manifest's exact Omnigent and bridge versions with the
installed target versions before any Omnigent process opens the restored
database. This ordering matters because engine initialization may run Alembic
migrations. A mismatch blocks restore readiness rather than silently migrating
the only recovery copy.

Before archiving, the snapshotter inspects the staged `chat.db` and refuses to
publish it if `account_tokens` contains any invite/magic bearer secret or any
`users.password_hash` is populated. This personal deployment uses local
single-user auth, so either condition is unexpected and failing the backup is
safer than silently exporting authentication material. Durable host registry
rows and one-way host token hashes remain because sessions need them to
reconnect; `~/.omnigent/config.yaml`, raw host tokens, x509 material, and auth
caches are never included.

The compressed archive has a sibling SHA-256 sidecar. The sidecar is copied
only after the archive copy completes and is re-read successfully. Restore
ignores an archive without a valid sidecar.

### 7.3 Online snapshots

Every five minutes on the active hub:

1. Create a mode-`0700` local staging directory.
2. Use SQLite's online backup API for both databases.
3. Copy the artifact tree into staging.
4. Run `PRAGMA integrity_check` on both staged databases.
5. Create the manifest and inner checksums.
6. Build and checksum the archive locally.
7. Mount or remount Persistent Storage if necessary.
8. Copy the archive and then its completion sidecar.
9. Re-read and validate the destination.
10. Record backup health locally and remove staging.

Artifacts and rows cannot be captured in one cross-store transaction while
the service is live. This is acceptable for periodic recovery. Planned
handoffs use a quiesced snapshot.

Database backup before artifact copy is a required ordering invariant.
Artifacts are immutable and are published before database rows reference
them. Copying artifacts after fixing the database recovery point can therefore
include harmless unreferenced artifacts created later, but cannot omit an
artifact referenced by that database snapshot. If artifact garbage collection
is introduced later, it must be suspended or coordinated with snapshotting.

Keep the latest twelve five-minute snapshots and seven daily snapshots. The
exact cadence can be relaxed after measuring archive size and copy time.

The first implementation intentionally creates self-contained archives,
including the artifact tree. That is simpler to validate and restore than a
separate object pool whose unchanged objects can expire under the 30-day TTL.
Measure artifact size and upload duration in Phase 1. If full copies become a
real cost, move to an incremental content-addressed artifact pool with explicit
TTL refresh, manifest reachability checks, and garbage collection; do not add
that lifecycle machinery preemptively.

### 7.4 Quiesced snapshot

For a planned transfer:

1. Stop accepting new human input.
2. Query the session list and require every session to be `idle` or `failed`.
   If any session is `running` or `waiting`, restore normal ingress and require
   the operator to wait for or interrupt that turn before retrying.
3. Stop the Google Chat bridge.
4. Stop the periodic snapshot timer and wait for any active snapshot job to
   finish or cancel it before staging begins.
5. Stop the prodnet proxy.
6. Write and verify a new shared transition record that increments the epoch,
   names the source and target hubs, and has no active hub.
7. Verify the source hub's startup gate rejects the transition epoch.
8. Stop the Omnigent server.
9. Confirm the bridge, proxy, server, and timer are stopped and the health
   endpoint is closed.
10. Snapshot both databases and artifacts.
11. Verify SQLite integrity and all checksums locally.
12. Copy and re-verify the generation in Persistent Storage.
13. Leave the old hub's services stopped.

The transition record is written before the old server stops. During the
short interval between those operations, the existing source process may
still run, but neither source nor target can newly start: all startup gates
reject transition state. Once the source stops, no server can accept writes
until promotion finalizes the record for the target. This closes the reboot or
service-restart window that would otherwise allow the old hub to start again
during handoff.

This gives an exact recovery point for planned maintenance. A failed handoff
is resumed using its transition ID. If the transition was fenced before its
final generation was attached, rerunning promotion stops the source service
set again, creates and attaches that missing quiesced generation, then
continues. It may instead be explicitly aborted by validating the source state
and publishing a newer activation for the source. Never edit the transition
record by hand or reuse its epoch.

## 8. Operator command

The dotfiles-managed `omnigent-hub` command is the only supported ownership
control surface. `init.sh` installs a stable `~/bin/omnigent-hub` wrapper over
the locked service virtualenv.

### 8.1 `omnigent-hub status`

Report:

- preferred primary and standby;
- shared epoch and active hub;
- locally cached routing epoch on each reachable machine;
- server, proxy, bridge, and snapshot-timer state on both machines;
- newest valid snapshot and its age; and
- warnings for two active servers, two bridges, two snapshot timers, a client
  proxy on the serving hub, a missing standby proxy, stale routing, or stale
  backups.

Report exact installed Omnigent and bridge versions on CCO and FTW, the
versions in the newest snapshot manifest, and whether all three match. A
version mismatch makes the standby not promotion-ready.

For each candidate, also report:

- `~/.omnigent/config.yaml`'s `server:` value;
- generated `OMNIGENT_URL`;
- local routing epoch; and
- any Neovim process that is not using the stable loopback URL.

On the Mac, `omnigent-hub status` reports the remotely validated record and
which candidate supplied it. The `omnigent-tunnel` window separately shows the
current ET target and continuously verifies health through local port 6767.

### 8.2 `omnigent-hub backup --quiesced`

Stop ingress in the defined order, create the final generation, verify it, and
leave services stopped. Before stopping the server, it publishes the
no-active-hub transition fence described in Section 7.4. It must print the
transition ID, epoch, target hub, and exact generation ID used for the handoff.

### 8.3 `omnigent-hub promote ftw`

For planned maintenance:

1. Acquire the single-operator lock and confirm there is no unrelated
   transition in progress.
2. Compare exact Omnigent and bridge versions on source and target before
   disrupting the source; reject drift immediately.
3. Stop ingress, run `quiesce-check`, and publish a new transition epoch from
   CCO to FTW before stopping CCO, as described in Section 7.4. A failed
   quiescence check restores source services before returning an error.
4. Confirm CCO server, proxy, and bridge are stopped and cannot pass their
   startup gates; confirm the periodic snapshot timer is also stopped.
5. Create and verify CCO's final quiesced generation.
6. Explicitly stop FTW's hub service set, then restore that exact generation
   locally on FTW.
7. Before opening either restored database through Omnigent, require FTW's
   exact Omnigent and bridge versions to match the snapshot manifest and CCO;
   then validate checksums, SQLite integrity, schema compatibility, and
   important row counts.
8. Write FTW's local activation marker for the transition epoch and restored
   generation.
9. Finalize the shared record from transition state to FTW active, retaining
   that epoch and naming FTW's activation ID.
10. Stop FTW's loopback client proxy, reconcile its stable loopback config, and
   start FTW server plus prodnet proxy.
11. Reconcile CCO as standby: stop hub services, start its loopback client
    proxy to FTW, and restart `omnigent-host` only if its URL was stale.
12. Validate the FTW API locally and through CCO's loopback proxy.
13. Start the Google Chat bridge last.
14. Start the periodic snapshot timer only after FTW is active and healthy.
15. Verify existing Chat mappings before enabling inbound polling.
16. Let an awake Mac's `omnigent-tunnel` observe the new epoch and retarget
    local port 6767; an asleep Mac converges when `startup-windows` resumes it.

The operator lock is machine-local, not a distributed CAS. This personal
deployment assumes one human operator and must not run promotion, failback,
abort, or force-recovery commands concurrently from different machines.

For unexpected failure, require an explicit `--unexpected-failure` flag. The
command selects the newest valid snapshot, displays its age and the possible
loss window, and requires confirmation that CCO cannot still accept writes.
It leaves the Google Chat bridge stopped until its cursor and transcript are
reconciled, because the restored bridge database may predate an already
submitted phone instruction.

`omnigent-hub reconcile-gchat` performs that recovery explicitly:

1. Keep the bridge stopped and back up the restored bridge database.
2. Fetch Google Chat messages from an overlap window preceding the restored
   cursor.
3. Match each human message by its immutable Google Chat message resource
   name against restored inbound records and any exact source identity stored
   with durable Omnigent input.
4. Mark an exact previously submitted match as consumed without submitting it
   again. Accept only the bridge's known inbound states (`claimed`,
   `dispatching`, `submitted`, `ambiguous`, and `rejected`); an unknown restored
   state fails closed without advancing the cursor.
5. Classify a message with no exact durable identity as `AMBIGUOUS`; show its
   resource name, timestamp, text, target session, and transcript tail to the
   operator.
6. Default ambiguous messages to consumed, not replayed. Require an explicit
   operator action to resubmit one.
7. Advance the poll cursor only after every message through the selected
   boundary has a durable classification.
8. Restart the bridge and verify that the overlap poll submits nothing twice.

Text or timestamp similarity alone is not proof of prior delivery. An
instruction may have caused external side effects even when its transcript row
was lost with the newest unsnapshotted state. The recovery bias is therefore
at-most-once execution: possible omission is surfaced for manual action rather
than risking silent duplicate tool work.

### 8.4 `omnigent-hub failback cco`

Failback is not merely changing a hostname:

1. Quiesce FTW input.
2. Publish a new no-active-hub transition epoch from FTW to CCO.
3. Confirm FTW cannot pass its startup gate, then stop its services.
4. Stop its periodic snapshot timer and wait for any running snapshot job.
5. Create a final FTW generation.
6. Require exact version agreement, then restore and validate it on CCO.
7. Write CCO's activation marker and finalize the transition with CCO active.
8. Update routes and restart services in the same order, starting CCO's
   snapshot timer only after the server is healthy.
9. Leave FTW's hub services stopped.

This restores the preferred topology without discarding anything written
while FTW was active.

## 9. Failure cases

### 9.1 CCO reboots while FTW is active

CCO's hub services inspect `active-hub.json`, see that FTW owns the newer
epoch, and refuse to start. CCO starts only its execution-host service, which
connects to FTW.

### 9.2 Persistent Storage is temporarily unavailable

The currently running server continues. A new hub server cannot start and a
promotion cannot proceed. Snapshots fail visibly and `status` reports their
age. This favors temporary unavailability over split brain.

If the active hub must be restarted before Persistent Storage returns, use the
manual `force-start` procedure only after independently verifying the other
hub cannot accept writes. Do not make this fallback automatic merely because
the local machine is the preferred primary.

A normal cold boot exercises the bounded mount/remount retry in Section 5.2
and continues retrying once per minute. If the private mount needs a new
credential, opening the tracked Mac tunnel or running any remote
`omnigent-hub` command supplies an ephemeral delegated CAT. `force-start` is
reserved for a confirmed storage outage after independently stopping the
other hub; it is not a substitute for credential bootstrap.

### 9.3 FTW fails while it is temporarily active

Restore the newest FTW-generated snapshot to CCO and promote CCO. Any changes
newer than that snapshot may be lost. The same unexpected-recovery bridge
reconciliation applies.

### 9.4 Both machines are reachable but partitioned from each other

Do not automatically promote. One failed health check is not proof that the
active server is dead. The operator verifies the active hub through another
path and fences it before promotion.

### 9.5 The active-hub record disagrees with a running service

`status` reports a critical invariant violation. Do not start another server.
Determine which history accepted the latest writes, stop both hub service
sets, snapshot that history, and restore one authoritative generation. There
is no automatic merge.

## 10. Why not a distributed database?

### 10.1 DSQLite

DSQLite is Meta's managed SQLite-compatible service and is useful inspiration.
A personal free-pool database can be created without a team pool and with no
expiry by using `--ttl=0`.

It is not currently the simplest Omnigent backend:

- Omnigent uses SQLAlchemy and Alembic;
- DSQLite clients use HTTP/Hrana and `libsql`;
- no supported DSQLite SQLAlchemy dialect was found in fbsource;
- a private dialect must correctly support transactions, migrations,
  introspection, pooling, retries, and Omnigent's query behavior; and
- this would become a permanent personal fork or plugin to maintain.

DSQLite would improve database availability but would not distribute
Omnigent's in-process runner registry, SSE subscribers, pending elicitations,
Google Chat bridge state, or artifact store. It does not eliminate the need
for a single active server and recovery semantics.

### 10.2 Shared SQLite on Persistent Storage

Rejected. ManifoldFS is a backup destination, not a live POSIX filesystem for
SQLite locking and WAL.

### 10.3 Active/active Omnigent servers

Rejected. It would require a shared database, shared artifacts, a distributed
event/backplane layer, connection affinity, bridge leadership, approval
ownership, and reconciliation of in-flight turns. That is far beyond the
personal requirement.

### 10.4 Tupperware or another always-on service platform

This could remove devserver placement from the problem, but introduces
packaging, service identity, ownership, deployment, capacity, and operational
overhead. It is appropriate for a supported service, not this personal daemon.

### 10.5 Automatic failover

Rejected initially. False promotion creates two histories that cannot be
merged. Manual recovery of a personal system takes minutes and has a much
smaller correctness surface.

## 11. Implementation and deployment phases

### Phase 1: backup and restore (implemented and locally smoke-tested)

- Implement versioned archive creation with SQLite online backup.
- Implement validation and restore into a new directory.
- Add the five-minute active-hub timer.
- Test restore using a throwaway loopback port with the bridge disabled.
- Measure archive size, copy duration, and realistic RPO.
- Verify from FTW that a generation written by CCO can be read and validated
  after pulling dotfiles and running `init.sh` there.

This phase immediately reduces session-loss risk without changing topology.

### Phase 2: standby preparation (complete)

- Track primary and standby FQDNs in dotfiles.
- Install identical pinned Omnigent, bridge, and hub-controller versions on FTW.
- Make `status` and promotion preflight compare Omnigent, bridge, and
  hub-controller versions across both candidates. Compare the Omnigent and
  bridge versions with the newest snapshot manifest before a database is
  opened; controller code is not recovery data and therefore is not embedded
  in snapshots.
- Add shared active-hub record parsing and fail-closed service conditions.
- Add bounded Persistent Storage mount/remount and startup retries with
  ephemeral delegated-CAT bootstrap.
- Initialize the record with CCO active.
- Initialize CCO's matching local activation marker.
- Verify that FTW hub services refuse to start.
- Verify that a routine CCO service restart passes without changing its
  activation marker.

### Phase 3: operator workflow (implemented and unit/integration-tested)

- Implement `status`, `backup --quiesced`, `promote`, and `failback`.
- Implement transition-state fencing and resumable/abortable handoffs.
- Implement an explicit, guarded `force-start` recovery path for a coordination
  storage outage.
- Update devserver routing caches.
- Implement the read-only remote resolver and Mac pull/reconciliation loop;
  use it to select the Mac ET tunnel target and never require a Manifold mount
  or inbound push on macOS.
- Reconcile `config.yaml` and generated `OMNIGENT_URL` on promotion.
- Make the bridge start last and fail closed after stale recovery.
- Implement `reconcile-gchat` with exact-identity matching and explicit
  ambiguous-message disposition.
- Add dry-run output showing every host and generation affected.

### Phase 4: cross-hub rehearsal (complete)

1. Perform a planned CCO-to-FTW handoff.
2. Validate API, CLI, CodeCompanion, web UI, peer hosts, and Google Chat.
3. Reboot CCO and verify its server remains fenced while FTW is active.
4. Fail back with FTW's newer state.
5. Verify CCO is primary again and FTW is inactive.

The rehearsal completed on 2026-07-18. FTW served the API and Mac tunnel at
epoch 4, CCO's startup gate rejected all hub services while FTW was active, an
FTW-era marker appeared in an FTW-authored snapshot, and failback restored that
marker to CCO at epoch 5. Service ownership and routing converged without
warnings. The promotion also exposed a short-lived stale activation view on
CCO; the bounded post-activation refresh retry described in section 5.2 was
added from that evidence.

### Ongoing upgrades

An Omnigent, bridge, or hub-controller upgrade is incomplete until the same
pinned version is installed on both CCO and FTW and `omnigent-hub status`
reports them equal. Install the package on the inactive standby without
starting its hub services, upgrade CCO, verify normal operation, and produce a
snapshot whose manifest records the new Omnigent and bridge versions. Do not
leave version convergence until promotion time.

## 12. Acceptance criteria

1. CCO is the preferred and normal active hub.
2. FTW's server, prodnet, bridge, and snapshot timer are inactive during
   normal operation; its loopback client proxy is active.
3. Planned promotion transfers all committed Omnigent and bridge state with no
   loss.
4. CCO can be powered down while the API, history, and Google Chat operate from
   FTW.
5. A rebooted old primary cannot start a second writable server.
6. Failback transfers all FTW-era writes and returns service to CCO.
7. An unexpected failure can recover from a validated snapshot with a measured
   maximum loss window.
8. At most one server, prodnet proxy, bridge, and snapshot timer are active.
9. Active SQLite files remain on local disk.
10. No credentials or machine host identity enter the backup.
11. No Omnigent OSS changes are required.
12. FTW can read and validate a current snapshot produced by CCO, and CCO can
    read and validate one produced by FTW.
13. `omnigent-hub status` detects a stale CLI server URL, stale
    `OMNIGENT_URL`, stale routing epoch, or incorrect client-proxy ownership.
14. A source reboot or service restart during a handoff cannot start either
    hub while the shared record is in transition state.
15. A slow or stale Persistent Storage mount is retried without bypassing the
    fence; when a new private credential is required, an authenticated Mac or
    operator process supplies only an ephemeral delegated CAT.
16. Stale Google Chat recovery never automatically resubmits an instruction
    without exact durable evidence that it was not previously delivered.
17. Promotion is blocked before database open when source and target
    Omnigent, bridge, or hub-controller versions differ, or when the snapshot
    Omnigent/bridge versions differ from the target.
18. A quiesced handoff cannot race the periodic snapshot timer.
19. A Mac with no Persistent Storage access can discover either active hub
    through either reachable candidate and retarget its ET forward.
20. A sleeping Mac that misses a promotion converges after wake without a
    server-side push, source-control change, or Neovim restart.
21. Transition state, unavailable candidates, and conflicting same-epoch
    responses never make the Mac guess an active hub.
22. Running `init.sh` on a new execution devserver discovers the active hub,
    starts its execution host, and keeps its route current without granting
    that client Persistent Storage access.

## 13. Operational summary

Normal state:

```text
CCO active; FTW standby; snapshot age < 10 minutes
```

Before planned CCO maintenance:

```text
omnigent-hub status
omnigent-hub promote ftw --dry-run
omnigent-hub promote ftw --yes
```

After CCO returns:

```text
omnigent-hub status
omnigent-hub failback cco --yes
```

Installation on each machine is intentionally explicit:

```text
cd ~/dotfiles
sl pull --rebase
./init.sh
```

Tracked code, topology, policies, units, and scripts live in dotfiles.
Machine-specific host identity, databases, activation markers, routing caches,
generated runtime environment files, logs, mounts, and credentials remain
local. Running `init.sh` does not copy a live database from another machine;
the first promotion restores the selected validated snapshot through the
operator command.

## 14. Verification state

The dotfiles implementation currently has the following local evidence:

- 58 `omnigent-hub` tests cover record validation, fencing, force recovery,
  stale-mount refresh, service ordering, stable routing, delegated-CAT
  transport, credential exclusion, version drift, Google Chat reconciliation,
  interrupted-transition resumption, and Mac candidate/conflict resolution;
- an in-process two-hub integration test performs a real planned promotion,
  verifies both restored SQLite stores and artifacts, adds FTW-era state,
  performs a real failback, and verifies that newer state on CCO;
- 95 Google Chat bridge tests cover mirroring, inbound idempotency, exact source
  identity, and restart behavior;
- Ruff, formatting, strict mypy, Shellcheck, Bash syntax checks, and
  `systemd-analyze verify` pass for the changed surfaces; and
- a real Persistent Storage generation with zero account credentials has been
  checksum-validated, restored to a temporary directory, booted on an isolated
  loopback port, and queried successfully through `/health` and `/v1/sessions`.

The real-machine rehearsal additionally proved criteria 2-6, 12, and 19-20:
CCO promoted to FTW, the Mac retargeted through candidate discovery, CCO stayed
fenced while its standby proxy served the FTW API, FTW produced a 19-session
snapshot containing a new marker, and failback restored that marker to CCO.
The final epoch-5 status had no warnings and showed exactly one owner for each
hub-only service.

## 15. References

### Personal deployment

- `~/dotfiles/omnigent_config/topology.env`
- `~/dotfiles/systemd/omnigent-server.service`
- `~/dotfiles/systemd/omnigent-prodnet.service`
- `~/dotfiles/systemd/omnigent-client-proxy.service`
- `~/dotfiles/systemd/omnigent-host.service`
- `~/dotfiles/systemd/omnigent-google-chat.service`
- `~/dotfiles/systemd/omnigent-hub-reconcile.timer`
- `~/dotfiles/systemd/omnigent-snapshot.timer`
- `~/dotfiles/bin/omnigent-hub`
- `~/dotfiles/bin/omnigent-hub-reconcile`
- `~/dotfiles/bin/omnigent-onboard-check`
- `~/dotfiles/bin/omnigent-retire-legacy-standby`
- `~/dotfiles/bin/omnigent-server-url`
- `~/dotfiles/bin-macos/omnigent-tunnel`
- `~/dotfiles/services/omnigent-hub/README.md`
- `~/dotfiles/services/omnigent-google-chat/README.md`

### Meta infrastructure

- Persistent Storage:
  `https://www.internalfb.com/wiki/Development_Environment/Persistent_Storage/`
- Devserver local storage and nightly backup:
  `https://www.internalfb.com/wiki/Devservers/home-vs-local-directory/`
- Manifold getting started:
  `https://www.internalfb.com/wiki/Infra_Cloud/Storage_0/Manifold/Getting_Started/`
- DSQLite:
  `https://www.internalfb.com/intern/staticdocs/dsqlite/`

### Omnigent behavior

- Database engine configuration: `omnigent/db/utils.py`
- Host reconnect behavior: `omnigent/host/connect.py`
- Runner tunnel registry: `omnigent/runner/transports/ws_tunnel/registry.py`
- In-process SSE delivery: `omnigent/runtime/session_stream.py`
