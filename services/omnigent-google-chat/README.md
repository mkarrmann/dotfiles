# Omnigent Google Chat bridge

This integration mirrors selected Omnigent sessions into one private internal
Google Chat space and forwards replies from one allowlisted human back to the
same sessions. It runs beside `omnigent-server`, opens no inbound listener, and
uses the sanctioned `meta google.chat.message` CLI for all Chat access.

## Safety model

- Omnigent remains the agent runtime and transcript authority.
- Sessions are opt-in by default through the
  `omnigent.google_chat.enabled=true` label.
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

## Install

```bash
cd ~/dotfiles/services/omnigent-google-chat
uv sync --all-groups
cp .env.example ~/.config/omnigent-google-chat.env
chmod 600 ~/.config/omnigent-google-chat.env
```

Fill in the exact space, human actor, bot actor, mention unixname, host, and
Omnigent authentication settings. Omit `OMNIGENT_AUTH_EMAIL` for a local
single-user server; sending that header selects a different identity instead
of the server's reserved `local` owner. The actor IDs are Google Chat
`sender.name` values such as `users/123...`, not display names.

## Phase 0

Before enabling the daemon, run the interactive transport probe:

```bash
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
bridge. Otherwise set the reported actor IDs and:

```text
OMNIGENT_GCHAT_PHASE0_VALIDATED=true
```

## Run

```bash
uv run omnigent-google-chat run
```

Add `omnigent.google_chat.enabled=true` to a session's labels through the normal
Omnigent session update API or client label defaults. Label discovery also
requires the session's host to equal `OMNIGENT_GCHAT_HOST_ID`, so a label from
another machine cannot cross the bridge boundary. The bridge creates one thread
for that existing session. It never creates an Omnigent session.

For CodeCompanion, set the label and the same exact host in its Omnigent adapter
defaults:

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
and per-session overrides. `host` must match `OMNIGENT_GCHAT_HOST_ID`, and a
remote devserver requires an explicit absolute `workspace`.

The optional `host-active` discovery mode mirrors recent, non-archived sessions
on the configured host unless they explicitly carry
`omnigent.google_chat.enabled=false`. This is convenient but copies more data
to durable Chat history, so label mode is the default.

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

The dotfiles bootstrap installs `systemd/omnigent-google-chat.service` and
syncs this project's locked environment when the private configuration and
Meta CLI are present:

```bash
~/dotfiles/init.sh
tail -f ~/.omnigent/google-chat.log
```

The unit has no listening socket. It requires the existing Omnigent server and
host services to be healthy but retries transient unavailability itself. It
uses the host's provisioned x509 identity in place and keeps `/tmp` shared with
the interactive `meta` CLI so authentication caches have the same lifecycle.

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
state, but not phone message bodies or a transcript copy. Keep it and
`~/.config/omnigent-google-chat.env` mode `0600` in private directories.

## Development

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy --strict src tests
uv run pytest
```

Tests use a fake `meta` executable/runner and mocked Omnigent HTTP transport;
they do not post to Google Chat.
