---
name: acp-broker-cli
description: >-
  Use when invoking `acp-broker-cli` — the operator CLI for the `acp-broker`
  orchestration surface. Covers the connection model (UDS to a local
  broker, which itself may delegate to a `persistence-server` running on a
  different machine), the four subcommand groups (`agent`, `session`,
  `plugin`, `history`, `sqlite`), useful invocations, and known quirks.
  For interpreting the conversation events returned by `history query
  session-events`, see [[acp-broker-sessions]].
---

# acp-broker-cli

Universal CLI for the `acp-broker` orchestration surface
(`~/repos/acp-broker/crates/acp-broker-cli`).

## Binary location

```bash
~/repos/acp-broker/target/release/acp-broker-cli      # preferred
~/repos/acp-broker/target/debug/acp-broker-cli        # if release isn't built
```

Not on `$PATH` by default — invoke by full path or alias it.

## Connection model — important

The CLI **talks to a local `acp-broker` over a Unix domain socket**, not
directly to a persistence server. Socket path precedence:

1. `--socket <PATH>` flag
2. `$ACP_BROKER_SOCKET` env var
3. `${XDG_RUNTIME_DIR:-/tmp}/acp-broker.sock` (default — typically
   `/run/user/<uid>/acp-broker.sock` on Linux)

The broker that answers the UDS may delegate to a **`persistence-server`
on a different host** (reverse-tunneled via the user's
`nvs-tunnels`; see [[acp-broker-sessions]] for the full topology). The
CLI never speaks the server's wire directly — read commands ride the
broker's persistence plugin.

**Sanity check before running any command:**

```bash
ls -l "${XDG_RUNTIME_DIR:-/tmp}/acp-broker.sock"   # must exist
```

If absent: no broker is running on this host. Either start one
(`~/dotfiles/bin/acp-broker-up`) or fall through to direct TCP /
on-disk WAL access — see [[acp-broker-sessions]].

## Global flags

| Flag | Effect |
|------|--------|
| `-s, --socket <PATH>` | Override broker UDS path |
| `--json` | Machine-parseable JSON output (vs the default tab-separated human format) |
| `-h, --help` | Print help for the current subcommand |

`--help` is available at every level (`acp-broker-cli <group> <cmd>
--help`) and is the authoritative reference — prefer it over guessing
flag shapes.

## Subcommand groups

| Group | Purpose |
|-------|---------|
| `agent` | Manage broker-tracked agent subprocesses |
| `session` | Manage live ACP sessions across agents |
| `plugin` | Manage broker plugins |
| `history` | Query persisted history (events, lifecycle, saved sessions) |
| `sqlite` | SQLite-backed persistence plugin namespace (`meta.sqlite_persistence.*`) — cross-broker `list-brokers`, `set-metadata`, `resume` |

## Common invocations

### Read history for a session

See [[acp-broker-sessions]] for context on `bsid_*` ids and the event
shape.

```bash
CLI=~/repos/acp-broker/target/release/acp-broker-cli
BSID=bsid_81326f8a-66b8-4c33-9fe6-c146b683705b

# Stream every captured event for the session (one JSON per line)
$CLI history query session-events --session "$BSID"

# Same session log via the saved-session loader (different code path)
$CLI history query load "$BSID"

# List every saved session known to the broker
$CLI history query saved-sessions

# Agent lifecycle records (start/stop/...)
$CLI history query agent-lifecycle
```

### Enumerate brokers / sessions

```bash
$CLI sqlite list-brokers               # every broker registered with the server
$CLI session list                      # live sessions on THIS broker
$CLI agent list                        # tracked agent subprocesses
```

### Mutate session metadata

```bash
$CLI sqlite set-metadata --help        # name / tags / metadata bag on a saved session
```

### Resume / fork a session

```bash
$CLI sqlite resume --help              # cross-broker resume (reattach)
                                       # NB: same-broker only; see skill for cross-broker fork
```

## Output format

Default output is **tab-separated** human-readable rows. Add `--json` for
JSON. `history query session-events` and `history query load` emit
**one JSON object per event per line** regardless of `--json` — pipe
through `jq`:

```bash
$CLI history query session-events --session "$BSID" \
  | jq -r 'select(.method=="session/prompt") | "\(.seq)\t\(.ts)"'
```

## Quirks

- **`--limit` on `history query session-events` may not be honored.** A
  session can hold 15k+ events; always expect the full stream and
  bound with `head` / `jq` slicing.
- **`session.list` (v1) has no `broker_session_id` filter.** When
  looking up a specific `bsid_*`, list all and filter clientside.
- The `sqlite` subcommand only exposes `list-brokers`, `set-metadata`,
  `resume` — for arbitrary persistence-server methods (e.g.
  `session.load`, `read_session_events`), use `history query …` or
  drop down to direct TCP (see [[acp-broker-sessions]]).
- The CLI version must match the broker it dials — if the binary
  shipped with `~/repos/acp-broker` is much older than the running
  broker (or vice versa) you may hit wire-shape errors. Rebuild with
  `cargo build --release --bin acp-broker-cli` from
  `~/repos/acp-broker`.
