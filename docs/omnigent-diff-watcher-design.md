# Omnigent diff watcher sidecar

**Status:** Implemented; delivery enabled. Multi-diff, the generic `watch_*`
surface, and explicit session identity shipped (v4 schema).

**Owner:** `mkarrmann`

## 1. Decision

Implement the Phabricator diff watcher entirely in `~/dotfiles`. Do not modify
or require a checkout of the Omnigent repository.

The integration uses only surfaces present in published Omnigent `0.5.1`:

- stdio MCP servers declared in agent YAML;
- `GET /v1/sessions/{id}`, to validate the session a tool was handed;
- `GET /v1/sessions/{id}/items`; and
- the existing internal `POST /v1/sessions/{id}/events` message route.

There is no Omnigent server plugin, and since the identity refactor no
server-side policy module either: session labels, the `capture_diff` policies,
and the session listing they required are gone (§4.2). The user-facing "plugin"
is a stateless MCP control surface plus a hub-local sidecar service.

## 2. Goals

- Let the agent responsible for a diff explicitly subscribe or unsubscribe.
- Bind the watch to a named, server-validated Omnigent session and to explicitly
  named diffs. Infer neither.
- Work from every harness Omnigent hosts, without harness-specific code.
- Wake the named session for new unresolved non-author review comments and new
  terminal CI failures on the latest version of any diff it watches.
- Cover a whole stack from one session, with a single wake naming every
  affected diff rather than one wake per diff.
- Baseline existing state so subscription never creates historical work.
- Batch autocorrelated updates for five minutes and send one concise message.
- Avoid polling dead sessions and terminal diffs, and slow polling with age.
- Persist source cursors, batches, retry state, and handled fingerprints.
- Survive sidecar/server restarts and active-hub handoff.
- Point the same engine at anything else a bounded command can report.
- Keep raw comments, logs, credentials, and source command output out of wake
  messages and durable watcher state.

## 3. Non-goals

- Changes to `~/repos/omnigent` or any published Omnigent package.
- Orchest task association.
- Webhooks or a new internal event service.
- Pending CI notifications.
- Author, draft, deleted, or resolved ordinary comment notifications.
- A general Omnigent plugin SDK.

## 4. Components

### 4.1 MCP tools

`services/omnigent-diff-watcher` installs a stdio MCP server exposing two
surfaces over one engine:

- `diff_watch_subscribe(session_id, diffs, events?)`
- `diff_watch_unsubscribe(session_id, diff?)`
- `diff_watch_status(session_id)`
- `watch_subscribe(session_id, subject, command, extract?, interval_seconds?, timeout_seconds?)`
- `watch_unsubscribe(session_id, subject?)`
- `watch_status(session_id)`

Claude, headless Codex, and dvsc agent YAML declare this server with the
inline `type: mcp` format. Polly and Debby are package-owned bundles;
`omnigent-agents-ensure` copies their installed versions to a temporary staging
directory and overlays the same MCP definition before registration, avoiding a
stale vendored fork. Native Codex is registered through
`codex_config/config.work.toml` and native Claude through
`agent_config/plugins/custom-mcps/mcps/diff-watch.json` -> `~/.claude.json`,
because a native session boots the vendor TUI and takes its tool surface from
that vendor's own config.

Nothing in the server is harness-specific: no flags, no bridge directories, no
environment probing. Every registration is the same bare command.

Registration location matters as much as registration: `~/.claude/settings.json`
accepts an `mcpServers` key and ignores it. Servers written there never reach
`claude mcp list`, no tool is advertised, and nothing reports the absence. The
user scope Claude Code reads is top-level `mcpServers` in `~/.claude.json`.

### 4.2 Session identity is an argument

Every tool takes the `session_id` it should wake. Agents obtain it from
Omnigent's own `sys_session_get_info`, which reports the calling session's id
(and `parent_session_id`, so a subagent can address a watch to a parent that
will outlive it).

This is asked for rather than discovered because **MCP carries no session
context in any transport**: stdio `env` and HTTP `headers` are fixed at deploy
time, there is no `_meta` plumbing on the call path, and Omnigent's MCP pool
shares one server process across every session using an agent. The only way a
tool could learn its own session was to scrape the harness's private bridge
directory -- `CODEX_HOME` for Codex, a `CLAUDE_CODE_SESSION_ID` match against
`state.json` for Claude. That worked for exactly two harnesses, coupled the tool
to Omnigent's internal directory layout, and could match two bridges at once and
silently bind a watch to the wrong session.

An explicit address costs one cheap tool call and works in every harness
Omnigent hosts.

The trade is that the address comes from the model rather than from the harness.
Every id is validated with `GET /v1/sessions/{id}` before anything is written,
so a typo, an unknown id, or a closed or archived session is refused at the
call. What validation cannot catch is a _deliberate_ wrong-but-live id, which
would wake a different session of the same user, on the same machine, from an
agent that already has that user's shell -- a nuisance, not an escalation.

Both surfaces then take the same route:

1. Validate the session.
2. Bind synchronously through the shared `DiffWatcher.subscribe`, which reads
   each subject once to establish a baseline. An unresolvable diff or an
   unrunnable command is therefore reported to the caller in the same turn
   rather than failing later in another process.
3. Only then write a `watch_requests` row, which carries `source`
   (`phabricator` or `command`) and is what re-binds the watch after a restart.

Source scoping on those rows is the only thing keeping `diff_watch_unsubscribe`
away from a generic watch, and vice versa.

Diffs are named explicitly; nothing is inferred. An earlier design scraped
`Differential Revision:` and `jf submit` result lines out of _every_ tool's
output into an `omnigent.diff.number` session label. That bound a live session
to a `D99999999` that appeared in a test fixture, and because the label was a
capped evict-oldest set, a false capture could displace a real diff. The agent
already has the ids in its own submit output.

The server installs no approval policy for these session-scoped, idempotent
operations. dvsc also uses ACP's `bypassPermissions` default so an ALLOW or
policy abstention does not fall through to a redundant client prompt; explicit
DENY or ASK policies continue to take precedence.

### 4.3 Watcher sidecar

`omnigent-diff-watcher.service` runs only on the active Omnigent hub. It:

1. Re-binds any `watch_requests` row that has no live subscription, every 15
   seconds. This is a _recovery_ pass, not the path a watch normally takes:
   the MCP tool binds on subscribe, so this exists to restore state after a
   restart and to pick up rows written while the sidecar was down. A bind that
   keeps failing backs off by doubling, 60s to a 6h cap, rather than retrying
   every cycle forever.
2. Polls each distinct subject once and fans results out to its subscribers.
3. Maintains adaptive deadlines, five-minute batches, liveness, and retirement.
4. Posts a single message when a batch is current and its session is idle.

The service database is `~/.omnigent/diff-watcher.sqlite3`, mode `0600`, WAL
enabled. It never writes Omnigent's database.

### 4.4 Hub controller

The existing hub controller starts/stops the watcher with the other active-hub
tail services. Quiesced handoff stops it before the final snapshot. Snapshot
format 2 includes `diff-watcher.sqlite3`, validates its checksum/schema summary,
and restores it on the promoted hub.

## 5. Subscription behavior

Subscribe succeeds only when:

- the call names at least one valid diff, or a namespaced subject and argv;
- the named session exists and is not archived or closed;
- the diff exists and is active;
- every selected source can establish a current baseline; and
- resource limits permit another active subject.

Repeated subscribe calls with the same preferences are idempotent. Adding a
new event type baselines only that type, so a pre-existing CI failure does not
wake a comments-only subscription that later enables CI.

One session may watch a stack. Several sessions may intentionally watch the
same diff; they share one external poll and receive separate batches.

Unsubscribe cancels the watch request, retires the durable subscription, and
cancels its open batch. Removing the last subscriber removes the subject from
the external poll schedule.

## 6. Source and event rules

The read-only source executes bounded, argv-only commands:

- `jf diff-properties D12345`
- `meta phabricator.diff comments ... --latest-version --skip-author
--unresolved-only --no-suggestions`
- a fixed `jf graphql` Signalview query for aggregate and failed signal IDs

Each command has a 30-second timeout and one-MiB output cap. The environment is
allowlisted. Errors retain only a category such as auth, timeout, rate limit,
unavailable, malformed, or missing.

A review event qualifies when a new or materially edited comment is current,
unresolved, human-authored, non-author, and on the latest diff version.

A CI failure event qualifies when the latest version reports a new stable
failure fingerprint. A CI-green event qualifies when the current version
finishes with no failures. Pending, skipped, and cancelled states do not
qualify. Automated-review findings use their dedicated event stream. A new diff
version invalidates the old version's pending events.

A generic watch's source is the argv its caller supplied, run directly rather
than through a shell, under the same allowlisted environment and output cap and
its own per-watch timeout (1-120 seconds, never more than its interval). Its one
`changed` event fires when the hash of the output -- after `extract`, if one was
given -- moves.

First observation is always a baseline and emits no event.

## 7. Polling and resource limits

Successful polling uses the diff's last meaningful activity:

| State                              |   Interval |
| ---------------------------------- | ---------: |
| CI active or activity under 1 hour |   1 minute |
| Idle 1-6 hours                     |  5 minutes |
| Idle 6-24 hours                    | 15 minutes |
| Idle 1-3 days                      |     1 hour |
| Idle 3-14 days                     |    6 hours |
| Idle over 14 days                  |   24 hours |

Deadlines receive deterministic plus/minus 10 percent jitter. Source failures
use a separate 1, 2, 5, 15, then 30 minute exponential sequence and never make
the diff appear older.

A generic watch does not use this ladder. Its interval is held constant, because
backing a quiet subject off to daily polling would defeat a watch whose whole job
is catching a change promptly.

Limits are 100 active subjects, two concurrent source polls, one open batch per
subscription, and one source poll lease per subject. Hitting a limit rejects new
work; it does not evict active subscriptions.

## 8. Correlation and revalidation

The first qualifying event fixes:

```text
flush_at = first_event_at + 5 minutes
```

Later events join without extending the deadline. Before delivery the sidecar
fetches authoritative current state and removes resolved comments, superseded
CI failures, and terminal diffs. Partial source failure defers the whole batch
while retaining only independently successful cursors.

Busy, waiting, approval-blocked, terminal-pending, or unreachable sessions are
not messaged. Their single open batch remains mergeable. Deliveries have a
ten-minute minimum separation.

The message contains counts and a stable batch marker, never source detail:

```text
[Diff watcher dwb_...] D12345 has 2 unresolved review comments and 1
current-version CI failure. Load the current diff review and CI state, address
actionable findings, and update the diff as needed.
```

The skill directs the awakened agent to fetch current truth with
`diff-comments` and `ci-signals` before editing.

## 9. Delivery and recovery

Published Omnigent `0.5.1` does not accept a client idempotency key on
`POST /events`. The sidecar therefore uses the batch ID as an in-band marker:

1. Persist the revalidated batch and summary.
2. Check recent session items for `[Diff watcher <batch-id>]`.
3. If present, mark the batch delivered without posting.
4. Otherwise POST the message to `/events`.
5. On a transport-uncertain result, recheck items before retrying.
6. On restart, repeat the same marker check for every delivering batch.

This prevents duplicates across normal retries and crash recovery. It cannot
provide a formal atomic exactly-once guarantee because the server has no
idempotency key: a server could accept a message, keep it invisible through all
verification attempts, and then accept a retry. The other unavoidable race is
a session becoming busy between the final status check and POST. Both windows
are narrow, observable, and inherent to the no-core-change constraint.

## 10. Retirement and suspension

Retire immediately when:

- the session unsubscribes;
- the session is deleted, archived, or closed;
- the diff is committed, abandoned, or reverted; or
- two authoritative observations classify the subject as missing.

Retire on age when a watch has delivered nothing for seven days
(`idle_retire_seconds`). The conditions above all need an *event* — a diff that
finishes, or a session that closes. Neither happens to a diff abandoned in
review, and Omnigent almost never closes a session, so without this a forgotten
watch polls forever: production reached 26 of 29 watches older than a week, the
oldest 24 days. Idleness is measured from the last delivery rather than from
creation, so an eight-day-old diff still producing review comments keeps its
watch and only a silent one ages out.

A retirement for cause also cancels the recorded `watch_requests` row, so the
recovery pass cannot resurrect the watch on its next cycle. For the age-out
this is not merely tidy but load-bearing: a re-bound subscription is created
fresh, so retiring without cancelling would age out and re-bind forever.

Runner loss alone is temporary. After 24 hours unreachable, suspend external
diff polling and probe only Omnigent liveness every six hours. Recovery pulls
the shared diff deadline forward for one current snapshot and creates at most
one current-state batch.

## 11. Configuration and rollout

`services/omnigent-diff-watcher/config.toml` is source-controlled with:

```toml
delivery_mode = "enabled"
delivery_session_allowlist = []
```

Rollout order:

The service was rolled out through log-only and allowlisted canary stages.
Current delivery is enabled without a session allowlist.

## 12. Verification

Standalone service:

```bash
cd ~/dotfiles/services/omnigent-diff-watcher
uv sync --frozen --all-groups
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy --strict src tests
uv run pytest -q
```

The suite covers source parsing and command bounds, baselines, edits,
resolution-before-flush, version invalidation, fixed correlation windows,
multiple subscribers, persistence/restart recovery, leases, adaptive polling,
partial failures, auth/rate-limit classification, lifecycle retirement,
suspension/recovery, event-type expansion, marker deduplication, REST event
shape, real stdio MCP negotiation including refusal of an unknown or closed
session, source-scoped unsubscribe, recovery re-binding and its backoff,
published Omnigent bundle parsing, and a full subscribe-to-single-wake flow.

Hub durability:

```bash
cd ~/dotfiles/services/omnigent-hub
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src tests
uv run pytest tests/test_snapshot.py tests/test_handoff_integration.py \
  tests/test_runtime.py -q
```

## 13. Acceptance criteria

- No watcher code or required change exists in `~/repos/omnigent`.
- Published Omnigent parses both MCP-enabled agent bundles.
- Every harness Omnigent hosts can register a watch; nothing in the MCP server
  is harness-specific.
- Subscription names its session and its subjects, infers neither, is validated
  against the server before anything is written, and is idempotent and
  reversible without a redundant approval prompt.
- An unresolvable diff or an unrunnable command fails the tool call rather than
  only the sidecar's log.
- Existing comments and failures never wake an agent.
- A correlated review/CI burst produces one concise message after five minutes.
- Resolution, supersession, or terminal state before flush prevents stale work.
- Busy/offline sessions receive no immediate steering message.
- Repeated polls, retries, and normal restart recovery do not duplicate a batch.
- Dead sessions, terminal diffs, and last-unsubscribe stop external polling.
- Source errors are bounded, redacted, observable, and never sent to agents.
- Watcher state survives active-hub snapshot, promotion, and failback.
- Automated unit, integration, full-flow, MCP, published-package compatibility,
  and hub handoff tests pass.
- Log-only and one-session live canaries pass before unrestricted delivery.
