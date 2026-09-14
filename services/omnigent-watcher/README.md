# Omnigent watcher

Private sidecar that watches things on behalf of explicitly opted-in Omnigent
sessions and wakes them when something changes. It uses only the published
Omnigent REST surface and requires no Omnigent source changes and no policy
modules.

Subscription does not require an approval prompt. The operation is
session-scoped, idempotent, and reversible.

## When to use it

Prefer notifications and quiet mechanical observers over repeated model status
checks. Harness-native equivalents are encouraged: use a background command's
completion notification for a build or test, a native monitor for a file or job
within an open session, or a native event channel for an upstream webhook.
Choose based on the required feedback, lifetime, execution host, and latency;
the custom watcher is not mandatory merely because it is installed.

Omnigent's watcher adds persistent requests and pending notifications outside
the harness process, plus specialized Meta sources. For example, an external
export job can finish after the native process exits and wake the same usable
Omnigent conversation later. A submitted Meta stack can subscribe once to CI
failures, CI green, human comments, and AI-review findings without teaching a
generic monitor how to interpret each source. Use the custom integration by
default for Meta follow-up. A generic notification primitive does not establish equivalent
source coverage. Before substituting another integration, verify its Meta
event handling, stack coverage, and session lifetime against
[phabricator-diff-watch](../../agent_config/skills/phabricator-diff-watch/SKILL.md).

Mechanical probes use no model inference while unchanged, but commands, API
requests, setup, and notification handling are not free. A scheduled prompt
that repeatedly checks status still invokes the model. See the agent-facing
[waiting guidance](../../agent_config/skills/waiting-without-polling/SKILL.md)
for examples and native capability references.

## Two surfaces

The engine is shared; the interfaces are not, deliberately.

| | `diff_subscribe` / `diff_status` / `diff_unsubscribe` | `subscribe` / `status` / `unsubscribe` |
| --- | --- | --- |
| Subject | Phabricator diff IDs, named explicitly | any namespaced `<prefix>:<id>` |
| How it is read | built in | an argv the caller supplies |
| Events | the four diff kinds below | `changed` |
| Skill | `phabricator-diff-watch` | `watch-anything` |

The diff surface stays opinionated about diffs — it takes no source and no
command, and its events describe review and CI specifically, which is the
interface worth having for the common case. The generic surface assumes
nothing about its subject, at the cost of the caller having to say how to read
it.

Both subscription tools take the `session_id` they should wake, validate it
against the server, bind each watch synchronously, and record a `watch_requests`
row scoped by source. Nothing is harness-specific.

Binding takes a silent baseline; existing feedback does not itself cause a
wake. Read current feedback when subscribing. A diff whose requested event
kinds cannot all be read fails to bind, while successful siblings remain
subscribed. The reply names failures; those subjects receive no durable request
and need an explicit retry after the problem is resolved. Calls accept up to
20 subjects, and the checked-in server limit is 100 distinct subjects with
active or suspended subscriptions across all sessions and sources.

### Where it runs

The sidecar and its database run beside the Omnigent server that owns the
session. MCP tools are HTTP clients, using `OMNIGENT_URL` (default
`http://127.0.0.1:6767`) for both session validation and subscriptions.

| Environment | Worker and command execution |
| --- | --- |
| Desktop | Beside the local Omnigent server |
| Active work hub | Hub-owned systemd worker |
| Work Mac, other devservers, standby | On the active hub through the existing connection |

`omnigent_config/config.server.yaml` mounts `omnigent_watcher.http_api`
through `debug_router_modules` on server hosts. Work clients do not mount the
API or run a second worker. Command paths must exist on the server; a local
path on a work client does not address that client's filesystem remotely.

The native MCP registry enables `watch` on both profiles for Claude, Codex,
and Metacode. Omnigent's managed agents use the same MCP server. The generic
watching skills are global; Phabricator guidance remains Meta-workspace scoped.

They used to open the database by path instead, which meant "whichever machine
I am on". On the hub that was the real database; anywhere else it was an empty
file no sidecar would ever poll, so a watch was accepted and then never fired.
Nothing detected it, because the hub is where it was tested.

The cost is that the Omnigent server imports this package, so a schema change
needs both restarted. The alternative was a second forwarded port, duplicating
the tunnel-recovery logic that makes the existing forward reliable.

### Why identity is an argument

This integration does not receive the owning session through MCP: stdio `env`
and HTTP `headers` are fixed at deploy time, there is no session `_meta`
plumbing, and Omnigent's MCP pool shares one server process across sessions.
A tool could
therefore only learn its own session by scraping the harness's private bridge
directory, which existed for two harnesses, coupled this code to Omnigent's
internal directory names, and could match two bridges at once and bind a watch
to the wrong session.

Agents get the id from `sys_session_get_info` instead. A short-lived subagent
should pass `parent_session_id` when the parent owns follow-up. A durable child
may own its watch, but its later externally triggered turns do not necessarily
notify the parent automatically.

The id comes from the model, so it is validated with `GET /v1/sessions/{id}`
before anything is written. That catches a typo, an unknown id, and a closed or
archived session. It does not catch a deliberate wrong-but-live id, which would
wake another session of the same user on the same machine, from an agent that
already has that user's shell.

### Generic watches

A command watch runs its argv directly — never through a shell, so no part of
a subject or spec is interpreted as shell syntax — hashes the selected output,
and compares it with the latest value acknowledged by that subscription.
Commands run with the server worker's credentials and reduced environment,
which may differ from the subscribing harness's sandbox. This is trusted
command delegation; the argv is recorded in the database and readable through
`status`.

`A → B (delivered) → A` can produce another notification. This remains a
sampled state watcher, not an event log: transitions between polls are missed,
and `A → B → A` before delivery may be coalesced away. Use an event source
when every transition matters. Subscription takes a silent baseline, even
when a probe already prints `MATCH`; handle an already-satisfied condition
immediately. Unsubscribe once the task is done.

Subjects share one command specification across all sessions on a server.
Reusing a subject with a different command, extraction, interval, or timeout
is rejected. Use a new unique subject name when changing the specification.

Two behaviours differ from a diff watch:

- **The nominal interval is held constant** (`interval_seconds`, 30s–24h,
  default 60s, with ±10% scheduling jitter).
  The idle ladder that backs a quiet diff off to daily polling would defeat a
  watch whose whole job is catching a change promptly.
- **`extract` is usually necessary.** Without it the whole output is the value,
  so a timestamp or request id anywhere in it re-fires every poll.
  No regex match, or an optional capture group that did not participate, is
  a probe error: preserve the previous value and back off. A capture that
  actually matched an empty string remains a valid value.

## Events

| Event            | Fires when                                                  |
| ---------------- | ----------------------------------------------------------- |
| `review_comment` | A human's unresolved latest-version comment is new, changed, or observed reopening. |
| `ci_failure`     | A signal reports `FAILED`, as soon as it does.              |
| `ai_review`      | An actionable automated-review finding is new, changed, or observed reopening. |
| `ci_green`       | A version's run finishes with nothing failing.              |
| `changed`        | A generic watch observes a value different from its latest acknowledged value. |

Three things are deliberately true here, each of which was once false:

- **Failures are not held until the run settles.** A wide test selection nearly
  always has something pending, so gating on a settled aggregate hid failures
  for most of the run.
- **Automated reviewers get their own event.** They reach neither of the other
  feeds: the comment stream filters automated authors, and reviewers report at
  `WARNING`, which is not a CI failure. Two sources are read — signalview's
  `REVIEW_INSIGHTS` group (RADAR and friends) and `meta phabricator.diff
arctic`. Arctic findings the author already dismissed or addressed are
  skipped.
- **Completion is reported, not only breakage.** Without `ci_green` a session
  can learn that something broke but never that the work is done, so "is it
  green yet" stays a manual poll. It is scoped to green because a red run
  already reports itself through `ci_failure`.

A successful diff subscription follows the named diffs through amendments and
repeated feedback without renewal. An observed resolution followed by a
reopening is a new occurrence, even if acknowledgment of an earlier occurrence
arrives late. These sources report sampled current state, not every transition
or comment in the diff's history. The signalview reviewer query currently
reads up to 50 signals per group without pagination; wake counts are hints to
load current feedback, not an exhaustive review inventory.

## Architecture

- `omnigent-watch-mcp` exposes both surfaces through the agent's normal
  stdio MCP configuration, with no flags and no per-harness variants. Each tool
  validates its `session_id`, binds the watch, and writes a `watch_requests`
  row.
- The service re-binds `watch_requests` rows that have no live
  subscription, polls each active subject once, and stores cursors and batches
  in `~/.omnigent/watcher.sqlite3` on the server machine. The database path
  comes from `config.toml`, shared by the server API and worker.
- A source implements `domain.WatchSource`: it is handed a subject, an opaque
  cursor, and an optional spec, and returns a `PollResult`. Everything below
  that line — leasing, fingerprint diffing, batching, liveness, delivery — is
  source-neutral and never learns what the subject is.
- Delivery posts one concise message to the existing hidden
  `POST /v1/sessions/{id}/events` route. An attempted batch and its latest
  pending feedback are stored separately, so acknowledgment handles only the
  attempted occurrences. A stable batch marker is checked before every retry.

## Timing, delivery, and diagnostics

- **Variable latency.** The checked-in configuration batches for five minutes
  and spaces notifications by at least ten minutes per session. Generic polls
  have a configured minimum of 30 seconds, default 60 seconds. Diff polling
  adapts from one minute to one day of nominal delay as activity ages; pending
  CI requests one minute. Successful polls have ±10% jitter. Busy or unreachable
  sessions defer delivery further, as can a failed or partial refresh of any
  subject in a batch. These are scheduling settings, not delivery
  deadlines; continue other work or finish the subscribing turn.
- **Bounded lifetime.** Seven days without a session delivery retires a watch
  and cancels its request, measured from its baseline if nothing was delivered.
  This includes watches with deferred feedback. Any watcher delivery to the
  session advances the timer for its live subscriptions; repeating an active
  subscription does not. Diff completion also retires its watch, without a
  completion notification. Stored requests can survive a worker restart, but
  require the same usable Omnigent session.
- **Outages are not replayable event history.** A stopped worker samples
  nothing. Server/session unavailability blocks delivery and may suspend
  polling; there is no guarantee of capturing transitions through an outage.
- **Retries check acceptance, then current feedback.** Receipt lookup runs
  first, even when a session is busy. If no receipt is found, the worker
  refreshes the sources before retrying. An obsolete attempt is superseded
  under a new identifier; an unchanged attempt keeps its content and identifier.
  A first attempt confirmed not sent can become mutable again. Latest feedback
  remains queued independently, with its original scheduling deadlines.
  Notifications can still race source changes; read current state on a wake.
- **Cancellation cannot retract a message.** Cancelling or retiring a subject
  removes its queued feedback. An affected attempt with active siblings checks
  its receipt before replacing the message, preserving those siblings' feedback
  without needlessly repeating a confirmed delivery. An already accepted or
  in-flight message may still arrive.
- **Duplicate suppression is best effort.** Retry checks search the latest
  1,000 session items for the batch marker. This is not an exactly-once
  guarantee: a missing marker does not prove the earlier attempt was never
  accepted, and superseding or cancelling that attempt can repeat feedback.
  Schema 6 preserves existing subscriptions and baselines. A pending attempt
  migrated from an older schema has unknown occurrence history; its receipt
  cannot suppress a current Phabricator finding, which may therefore repeat
  once after migration.
- **Status reports recorded state, not worker health.** It distinguishes
  pending, active, suspended, and retired subscriptions and shows failure
  counts, schedules, result times, session delivery times, and both attempted
  and queued notifications when present. A recorded result may be a baseline
  or partial read. Last poll-attempt timestamps and
  per-watch error categories are not persisted. Probe errors back off without
  producing `changed` notifications.

Commands run on the server with a reduced daemon environment, not the agent's
interactive shell. Use server-visible absolute paths and credentials available
there. The default command timeout is 30 seconds, configurable from 1 to 120
seconds and no greater than the polling interval; output is bounded.

## Development

```bash
uv sync --frozen --all-groups
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy --strict src tests
```

The worker, Omnigent server API, and MCP process load this package separately.
Source edits take effect when the affected processes next restart: the worker
owns polling and delivery, while the server owns subscription and status
requests. Only the worker may migrate the database, so schema changes require
coordinated worker and server activation. Native sessions can also retain
their startup MCP registration and tool schemas; a fresh session may be needed
after those change. Source edits alone do not update already-loaded modules.

The operator `status` command reads stored counts and schema information without
initializing or migrating the database. It does not establish worker health.

`init.sh` installs the watcher runtime on both profiles and converges the
appropriate services. It affects running services; agents must obtain the
user's authorization for that live operation. A fresh agent session picks up
the MCP registration after setup. Restarting a session alone cannot fix a
missing runtime, worker, or server API. The install phase does not bootstrap the
watcher database. On work machines, a deferred or failed server refresh aborts
setup before further worker convergence; rerun when the server can be refreshed.

### Upgrading schema 5 to 6

Use a planned maintenance window and the [upgrade procedure](UPGRADING.md).
Hot upgrades and mixed worker/server versions are not supported. Stop the old
worker **before updating source or migrating**, stop the server after sessions
are quiescent, and take a SQLite-aware backup. Start the matching new server
before starting the new worker that migrates the database. Existing subscriptions
and baselines are preserved; read-only operator `status --json` must report
`status: current` and schema 6 afterward.

Only the active work hub or a standalone desktop owns this migration. Work
clients do not migrate a local database. Do not promote a hub during the upgrade;
a future owner needs matching code before opening a migrated snapshot. Rollback
requires the old code and its pre-upgrade database backup and loses watcher
changes made after that backup. These are operator actions requiring explicit
authorization, not steps an agent should execute as part of source verification.

The integration tests use temporary databases, test HTTP servers and fake
session endpoints. They do not register watches with the live server:

```bash
uv run pytest tests/integration/test_mcp_stdio.py tests/integration/test_command_watch_end_to_end.py
```

An explicitly authorized `once` runs a worker cycle against the configured
server, including database migration, polling, and possible delivery. It is
not a read-only health check and must not run alongside the managed worker:

```bash
uv run omnigent-watcher --config config.toml once --json
```
