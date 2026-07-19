# Omnigent hub administration

This package implements the personal CCO-primary, FTW-standby deployment in
[`docs/omnigent-availability-design.md`](../../docs/omnigent-availability-design.md).
It is installed by `~/dotfiles/init.sh`; use the stable `~/bin/omnigent-hub`
wrapper rather than invoking the virtualenv directly.

## Install

Pull `~/dotfiles` and run this on the Mac, CCO, and FTW:

```bash
~/dotfiles/init.sh
```

The installer pins Omnigent, synchronizes this package from `uv.lock`, installs
systemd user units on Linux, and starts the ownership reconciler on candidate
hubs. Current hub ownership is runtime state in private Persistent Storage and
is never committed to git.

`omnigent-hub status` reports reproducible source digests for both this hub
controller and the Google Chat bridge. Promotion fails before quiescing the
source unless both candidate hubs have identical controller, bridge, and
Omnigent versions.

On Linux, `init.sh` is also the onboarding reconciler. It refreshes the shared
record, retires pre-HA local servers and tmux host launchers on an inactive
candidate, starts only the services appropriate for the machine's role, and
waits for `omnigent-onboard-check` to verify loopback health, a bidirectional
execution-host RPC, fencing, and service ownership. Ordinary devservers
discover the active hub through the two configured candidates, materialize the
same local routing cache without mounting Persistent Storage, and maintain an
SSH local forward to the active hub. Re-running `init.sh` is safe.

The Mac is always a client: installation reconciles its Omnigent URL and ACP
configuration but never opens or migrates `~/.omnigent/chat.db`. Only the
active Linux hub seeds shared agent definitions into the authoritative
database. A legacy Mac database from the former local-server deployment may
remain on disk, but it is not part of the active topology.

A cold devserver cannot mint an unattended credential for private Persistent
Storage. The interactive `omnigent-hub` wrapper mints a short-lived delegated
CAT for its process tree and passes it over SSH/ET when needed, which lets a
candidate mount or refresh the namespace. No bearer credential is persisted
in dotfiles or on disk. systemd bypasses that wrapper, so startup stays
fail-closed until the Mac tunnel or another authenticated operator command
performs this bootstrap.

That interactive credential bootstrap is the one machine-local exception to
automatic onboarding. If `init.sh` reports that Persistent Storage could not
be mounted, establish an interactive Meta-authenticated shell on either hub
and run `omnigent-hub status`; then rerun `init.sh`. No hostname, token, or
runtime ownership file should be edited manually.

ManifoldFS may expose a partially cached mutable record across mounts. Record
reads remount and retry after a parse failure, and every handoff force-refreshes
the target before restore and the source after activation. The exact transition
or activation must match after refresh or the handoff remains fenced. The old
source's post-activation refresh has a bounded 30-second retry because distinct
mounts can briefly retain a valid but stale same-epoch view.

The same 60-second reconciler has role-specific discovery: candidates read hub
ownership from Persistent Storage, while ordinary devservers query both
candidates without a delegated credential. Both then reconcile a stable
loopback endpoint: the active owner serves it directly and every other Linux
host forwards it over SSH, restarting the execution host when activation
identity changes.

## Routine operations

```bash
# Read both candidates, service state, versions, routes, and backup freshness.
omnigent-hub status

# Validate the plan without changing ownership.
omnigent-hub promote ftw --dry-run

# Optional explicit preflight. Promotion also runs this after stopping ingress.
omnigent-hub quiesce-check

# Planned CCO -> FTW handoff and later failback.
omnigent-hub promote ftw --yes
omnigent-hub failback cco --yes

# Create a transition fence plus final quiesced recovery generation.
omnigent-hub backup --quiesced --yes

# Validate the newest archive by starting an isolated restored server.
omnigent-hub smoke-restore
```

For an unexpected CCO loss, independently confirm CCO is stopped before:

```bash
omnigent-hub promote ftw \
  --unexpected-failure \
  --source-confirmed-stopped \
  --yes
omnigent-hub reconcile-gchat --yes
```

Unexpected recovery restores the newest completed generation. It starts the
server and snapshot timer but deliberately leaves Google Chat stopped until
`reconcile-gchat` classifies phone inputs with an at-most-once bias.

## Recovery controls

```bash
# Resume normal local service state from the authoritative record.
omnigent-hub reconcile-services

# Return an incomplete planned handoff to its fenced source.
omnigent-hub abort-transition --yes

# Last resort while Persistent Storage is unavailable. Verify the other hub
# is stopped first; this creates an expiring local force lineage.
omnigent-hub force-start \
  --other-hub-confirmed-stopped \
  --reason 'coordination store unavailable' \
  --yes

# Publish that forced lineage after storage recovers, then resume snapshots.
omnigent-hub repair-force-start --yes
```

An existing force override never bypasses an authoritative storage read. It is
used only when that read fails, and a readable equal or newer shared epoch
retires the local override. Transition and standby reconciliation also remove
obsolete activation markers.

Logs are under `~/.local/state/omnigent-hub/` and the service-specific
`~/.local/state/omnigent-*/` directories. The coordination record and immutable
snapshot generations live under `~/persistent/private-30d/omnigent-ha/`.

## Verification

```bash
cd ~/dotfiles/services/omnigent-hub
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
```

The full suite includes an in-process two-hub promotion/failback integration
test. The real-machine rehearsal completed on 2026-07-18; repeat the design's
Phase 4 checklist after material changes to fencing, storage, or routing.
