# Controlled upgrade: schema 5 to 6

This procedure is for an installed dotfiles-managed deployment. It is a planned
operator operation, not a hot upgrade. Run each stage only after the previous
one succeeds. Keep the exact old checkout, including local changes, and its
configuration for rollback. Use the paths in `config.toml` if they differ from
the checked-in defaults below.

The watcher stops sampling and delivering during maintenance. The work hub's
API is briefly unavailable to all clients; mobile ingress and snapshots are
paused. Desktop shutdown ends connected local Omnigent sessions, including
idle ones: finish and save their work first. Do not run setup, reconciliation,
handoff, or promotion concurrently. These commands require the user's explicit
authorization before an agent executes them.

## 1. Stop the old processes before changing source

**Active work hub only:** pause the timer that otherwise restarts owned services.

```bash
systemctl --user stop omnigent-hub-reconcile.timer
systemctl --user show omnigent-hub-reconcile.service -p ActiveState --value
```

If the oneshot is still active, let it finish before proceeding. Then stop
ingress and require the existing quiescence check to succeed. Keep users from
starting new turns until the upgrade is finished.

```bash
~/dotfiles/bin/omnigent-hub services stop-ingress --json
~/dotfiles/bin/omnigent-hub quiesce-check --json
```

Only after that check succeeds:

```bash
~/dotfiles/bin/omnigent-hub services stop-server --json
```

The execution hosts and existing tunnels remain running. The paused reconciler
must remain stopped through migration. No ownership record is changed.

**Desktop Linux:** after finishing connected sessions:

```bash
systemctl --user stop omnigent-watcher.service omnigent-host.service
env OMNIGENT_URL=http://127.0.0.1:6767 ~/.local/bin/omnigent server stop
```

**Desktop macOS:** stop the dotfiles-managed watcher job, then the local server
and host daemon. `bootout` prevents its `KeepAlive` policy from restarting it.

```bash
launchctl bootout "gui/$(id -u)/com.mkarrmann.omnigent-watcher"
env OMNIGENT_URL=http://127.0.0.1:6767 ~/.local/bin/omnigent server stop
```

These macOS commands apply to the desktop profile, not a work Mac client. If a
service/job is already stopped, confirm that state before continuing; do not
ignore an unexplained stop failure.
On either desktop, `~/.local/bin/omnigent server status --json` must show
`"running": false` and `"daemon_attached": false` before updating source.

## 2. Back up the stopped watcher database

Use SQLite's backup API, not a copy of the main file that could omit WAL data.
If this deployment still uses `diff-watcher.sqlite3`, select that existing file
instead. Do not create a replacement empty database.

```bash
watcher_db="$HOME/.omnigent/watcher.sqlite3"
watcher_backup="$(mktemp -d "$HOME/.omnigent/watcher-v5-backup.XXXXXX")"
python3 - "$watcher_db" "$watcher_backup/watcher.sqlite3" <<'PY'
from pathlib import Path
import sqlite3
import sys

source, destination = map(Path, sys.argv[1:])
with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original:
    assert original.execute("PRAGMA user_version").fetchone()[0] == 5
    with sqlite3.connect(destination) as backup:
        original.backup(backup)
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
destination.chmod(0o600)
print(destination)
PY
cp -p "$HOME/.omnigent/config.yaml" "$watcher_backup/config.yaml"
```

Retain this directory and the matching old source. The private directory created
by `mktemp` contains the subscription/delivery state needed for rollback.

## 3. Install matching source and start the new API first

With both old processes stopped, update `~/dotfiles` to the reviewed schema-6
revision. Install its locked watcher dependencies and merge its server overlay:

```bash
cd ~/dotfiles/services/omnigent-watcher
uv sync --frozen --all-groups
~/dotfiles/bin/omnigent-config-ensure
```

Start only the server/host for this platform:

Existing work schema-5 deployments use the same `omnigent-server.service` and
`omnigent-watcher.service` paths, so their managed symlinks need no replacement.
The renamed `config.server.yaml` is merged by `omnigent-config-ensure` above;
it is not a unit symlink. Check any locally customized units before proceeding.

| Environment | Stage configuration and start the matching API |
| --- | --- |
| Active work hub | `systemctl --user daemon-reload`, then `~/dotfiles/bin/omnigent-hub services start-core --json` |
| Desktop Linux | `~/dotfiles/bin/omnigent-desktop-ensure --stage`, then `systemctl --user daemon-reload`, then `systemctl --user start omnigent-host.service` |
| Desktop macOS | `~/dotfiles/bin/omnigent-desktop-ensure --stage`, then the command below |

```bash
env OMNIGENT_URL=http://127.0.0.1:6767 \
  PYTHONPATH="$HOME/dotfiles/omnigent_config/policy_modules:$HOME/dotfiles/services/omnigent-watcher/src${PYTHONPATH:+:$PYTHONPATH}" \
  ~/.local/bin/omnigent start --server '' --non-interactive
```

The macOS command explicitly selects the local server and supplies its router
import path. On every platform, require this check to succeed before starting
the worker; inspect startup logs if it fails:

```bash
curl --noproxy '*' --fail --silent --show-error http://127.0.0.1:6767/health
```

Keep agents from issuing watch operations until migration completes. The new
API may reject watch requests against the still-old database during this gap.

## 4. Start the matching worker and verify migration

| Environment | Start worker |
| --- | --- |
| Active work hub | `~/dotfiles/bin/omnigent-hub services start-watcher --json` |
| Desktop Linux | `systemctl --user start omnigent-watcher.service` |
| Desktop macOS | `launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.mkarrmann.omnigent-watcher.plist"` |

The new worker performs migration. Inspect its service log, then use the new
operator command, which reads the database without initializing or migrating it:

```bash
~/dotfiles/services/omnigent-watcher/.venv/bin/omnigent-watcher \
  --config ~/dotfiles/services/omnigent-watcher/config.toml status --json
```

Require `"status": "current"`, `"schema_version": 6`, and
`"expected_schema_version": 6`. This confirms storage version, not worker health;
also inspect `systemctl --user status omnigent-watcher.service` on Linux or
`launchctl print "gui/$(id -u)/com.mkarrmann.omnigent-watcher"` on desktop macOS.
Both Linux units log to `%S/omnigent-watcher/service.log` (normally
`~/.local/state/omnigent-watcher/service.log`); inspect the resolved path with
`systemctl --user show omnigent-watcher.service -p StandardOutput`.
The desktop macOS plist uses `~/.local/state/omnigent-watcher/service.log`.
Do not use `once` for validation: it migrates and runs another worker cycle.

On the work hub, restore normal ingress and the previously active reconciler:

```bash
~/dotfiles/bin/omnigent-hub services start-tail --json
systemctl --user start omnigent-hub-reconcile.timer
```

Work Macs and other clients only need the matching MCP runtime/configuration;
they do not own or migrate this database. Restart their MCP processes or use a
fresh native session after registration/tool changes. Any future hub owner
must have schema-6-capable source before restoring and opening a new snapshot;
update standby source before the next planned promotion.

## Rollback

Stop the new worker and API using the same platform procedure. Restore the
matching old source, locked dependencies, and saved server configuration, then
restore the pre-upgrade watcher database with SQLite's backup API while all
writers remain stopped. Do not simply place the old main file beside newer
`-wal`/`-shm` files, and do not point an old worker at schema 6. Start the old API
before the old worker and restore the paused work services.

Rollback loses subscriptions, cancellations, and delivery bookkeeping recorded
after the backup. Messages already accepted by Omnigent cannot be retracted,
so restored delivery state may produce duplicates. It does not restore or roll
back the separate Omnigent conversation database.
