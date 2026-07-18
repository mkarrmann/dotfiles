# Omnigent Google Chat bridge

This integration mirrors selected Omnigent sessions into one private internal
Google Chat space and forwards replies from one allowlisted human back to the
same sessions. It runs beside `omnigent-server`, opens no inbound listener, and
uses the sanctioned `meta google.chat.message` CLI for all Chat access.

## Safety model

- Omnigent remains the agent runtime and transcript authority.
- The reusable bridge defaults to explicit label opt-in. This personal
  deployment deliberately uses all-host discovery, with an explicit
  `omnigent.google_chat.enabled=false` escape hatch per session.
- Chat output is authored as Meta Bot. The daemon refuses to start until Phase
  0 has established a bot actor distinct from the controlling human.
- Every phone message is claimed durably before dispatch. An uncertain
  Omnigent delivery is marked ambiguous, never replayed automatically, and
  reported in its Chat thread after restart.
- Every mirrored message has a deterministic Google Chat request ID, so a
  retry cannot create a second message.
- The SQLite database is bound to one exact `spaces/...` resource and locked by
  one process.

## Prerequisites

1. A private internal Google Chat space containing only the intended users.
2. `meta google.chat.message send/list` access on the devserver.
3. A reachable Omnigent server and an online Omnigent host.
4. Python 3.11+ and `uv`.

Do not use a direct message. Meta Bot identity requires a named space.

## Deployment topology

The personal deployment is fully described in dotfiles:

- `~/dotfiles/omnigent_config/topology.env` names the one hub devserver that
  owns the Omnigent server, prod-network proxy, Google Chat bridge, transcript
  database, and bridge database.
- `~/dotfiles/omnigent_config/google-chat.env` contains the stable, non-secret
  Google Chat resource IDs and bridge policy.
- every devserver runs `omnigent-host.service` and resolves the central server
  through `omnigent-server-url`;
- the Mac reaches the same server through the hub ET tunnel and runs its local
  execution host through launchd; and
- the bridge runs only on the hub, but `host_scope=all` lets it mirror and
  recover sessions bound to any host registered with the single-user server.

Changing the hub is a tracked topology change, not a collection of local
edits. Update `topology.env`, move `~/.omnigent/chat.db`, artifacts, and the
Google Chat bridge database to the new hub, then rerun `init.sh` everywhere.

## Install

```bash
~/dotfiles/init.sh
```

On the configured hub, bootstrap atomically materializes the tracked policy as
`~/.config/omnigent-google-chat.env` with mode `0600`, adds the resolved local
server URL, installs locked Python dependencies, and enables the service. On
secondary devservers the same unit is installed but its hub `ExecCondition`
keeps it inactive. On macOS the bridge is not installed.

The generated env is not an override surface. Update the tracked policy and
rerun `init.sh`. The actor IDs are Google Chat `sender.name` values such as
`users/123...`, not display names. Omit `OMNIGENT_AUTH_EMAIL` for this local
single-user server; sending that header would select a different identity
instead of the reserved `local` owner.

## Phase 0

Before enabling the daemon, run the interactive transport probe:

```bash
cd ~/dotfiles/services/omnigent-google-chat
set -a; source ~/dotfiles/omnigent_config/google-chat.env; set +a
uv run omnigent-google-chat phase-zero
```

The probe uses synthetic text only. It:

1. posts an idempotent Meta Bot root;
2. posts an ordinary reply and a reply with a real self-mention;
3. asks you to verify locked-phone/background push notifications;
4. lists the thread through raw uncached space polling;
5. reports the observed bot/human actor identities and list latency; and
6. retries the root request ID so you can confirm there is only one root.

Reply to the probe from the phone when prompted. If Meta Bot is not a distinct
non-human sender or a mentioned reply does not notify the phone, do not run the
bridge. Otherwise update the reported actor IDs and validation gate in
`~/dotfiles/omnigent_config/google-chat.env`, then rerun `init.sh`:

```text
OMNIGENT_GCHAT_PHASE0_VALIDATED=true
```

## Run

```bash
cd ~/dotfiles/services/omnigent-google-chat
set -a; source ~/.config/omnigent-google-chat.env; set +a
uv run omnigent-google-chat run
```

The tracked personal policy uses `host-active` discovery with `host_scope=all`.
It mirrors recent sessions from every Mac/devserver host registered with the
central single-user server unless a session explicitly sets
`omnigent.google_chat.enabled=false`. The bridge creates one thread for an
existing session and never creates an Omnigent session.

For a narrower deployment, use `host_scope=configured`, set
`OMNIGENT_GCHAT_HOST_ID`, and add `omnigent.google_chat.enabled=true` to a
session through the normal Omnigent session update API or client defaults.

The tracked CodeCompanion config already resolves `host="auto"` to the local
machine, uses the current workspace, and stamps the discovery label. A generic
single-host setup can express the equivalent defaults as:

```lua
require("codecompanion").setup({
  adapters = {
    omnigent = {
      extend = {
        omnigent = {
          defaults = {
            host = "host_REPLACE_ME",
            workspace = "/absolute/workspace/on/the/devserver",
            labels = {
              ["omnigent.google_chat.enabled"] = "true",
            },
          },
          opts = {
            background_updates = true,
            stream_reconnect = true,
          },
        },
      },
    },
  },
  interactions = {
    chat = { adapter = "omnigent" },
  },
})
```

Omnigent labels are string-valued, so use `"true"`, not a Lua boolean.
CodeCompanion's Omnigent session runtime merges these defaults into
`POST /v1/sessions`; its existing tests cover static labels, label functions,
and per-session overrides. In configured scope, `host` must match
`OMNIGENT_GCHAT_HOST_ID`. In all scope, it must resolve to the actual execution
host bound to the session. A remote devserver requires an absolute workspace.

The library default remains label-based and single-host. The tracked personal
policy deliberately chooses all-host `host-active` behavior so CLI and
CodeCompanion sessions started on any of these machines appear on the phone.

## Phone commands

Send these as standalone messages inside a mapped thread:

- `!status` posts the current Omnigent status without prompting the agent.
- `!stop` interrupts the active Omnigent turn.
- `!detach` permanently disables input and output for the mapping.

Unknown commands, attachments, oversized input, other actors, other spaces,
and unmapped threads are rejected without contacting Omnigent. Tool approvals
remain in an Omnigent UI.

## Mirroring and notifications

Concise mode mirrors completed assistant messages, user input from other
clients, and actionable status. It excludes reasoning, token deltas, tools,
terminal output, commands, logs, file contents, and diffs. `status-only` mode
copies no transcript content.

Waiting/blocked, approval-needed, and failure notices mention the configured
user. Completion mentions are controlled by
`OMNIGENT_GCHAT_MENTION_ON_COMPLETION`. Routine output is not mentioned. Only
the first chunk of one logical notification carries a mention.

Detaching stops future copies; it does not erase existing Chat history.

## systemd user service

The dotfiles bootstrap installs `systemd/omnigent-google-chat.service`,
generates its private runtime environment on the hub, and syncs this project's
locked environment when the Meta CLI is present:

```bash
~/dotfiles/init.sh
tail -f ~/.omnigent/google-chat.log
```

The unit has no listening socket. It requires the existing Omnigent server and
host services to be healthy but retries transient unavailability itself. It
uses the host's automatically provisioned x509 identity in place and never
copies certificate material. `/tmp` remains shared with the interactive
`meta` CLI so authentication caches have the same lifecycle.

## Local state contract

The remaining local files are runtime identity/state, not missing setup:

- `~/.config/omnigent-google-chat.env` is generated from tracked policy on the
  hub and replaced on every bootstrap; never edit it directly.
- `~/.omnigent/config.yaml` is owned by Omnigent. Its `host.host_id` is a
  unique per-machine registration and must not be copied between machines.
  `omnigent-dvsc-ensure` reconciles the tracked server URL and ACP agent entry
  while preserving that identity.
- `~/.omnigent/chat.db` and `artifacts/` are authoritative server data on the
  hub. `google-chat.sqlite3` is the bridge's delivery/dedup state. Logs and
  caches are also hub-local runtime data.

A fresh devserver only needs the dotfiles bootstrap: it receives central
routing, starts its execution host, obtains its own host ID, and becomes
eligible for all-host Google Chat discovery. The Mac receives the same routing
through the ET tunnel and preserves its own host ID through launchd.

## Recovery

- Restarting the bridge does not duplicate roots, items, or phone input.
- A Google Chat list failure leaves the poll cursor unchanged.
- A stale `dispatching` input becomes `ambiguous` on startup, is not resent,
  and produces one idempotent warning in the mapped Chat thread.
- Status notifications use a persistent per-session transition generation, so
  an ID-less `session.status` event remains deduplicated across restarts while
  later `running -> idle/failed` cycles can notify again.
- Meta CLI calls log their action and elapsed time when slow without logging
  authored message text. The 90-second execution bound accommodates the
  observed periodic credential refresh, which can take about 60 seconds. A
  failed poll never advances the cursor and retries after bounded backoff.
- A disconnected SSE mirror reopens the stream and repairs from durable
  `/items` before continuing.
- Changing the configured space fails startup. Use a new database or perform an
  explicit migration after reviewing existing mappings.

The database contains resource identities, hashes, cursors, and delivery
state, but not phone message bodies or a transcript copy. Keep it in the
owner-only `~/.omnigent` runtime directory.

## Development

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy --strict src tests
uv run pytest
```

Tests use a fake `meta` executable/runner and mocked Omnigent HTTP transport;
they do not post to Google Chat.
