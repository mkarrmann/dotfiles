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

A cold devserver cannot mint an unattended credential for private Persistent
Storage. The interactive `omnigent-hub` wrapper mints a short-lived delegated
CAT for its process tree and passes it over SSH/ET when needed, which lets a
candidate mount or refresh the namespace. No bearer credential is persisted
in dotfiles or on disk. systemd bypasses that wrapper, so startup stays
fail-closed until the Mac tunnel or another authenticated operator command
performs this bootstrap.

## Routine operations

```bash
# Read both candidates, service state, versions, routes, and backup freshness.
omnigent-hub status

# Validate the plan without changing ownership.
omnigent-hub promote ftw --dry-run

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
