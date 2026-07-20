# Omnigent diff watcher sidecar

**Status:** Implemented; staged in `log_only`

**Owner:** `mkarrmann`

## 1. Decision

Implement the Phabricator diff watcher entirely in `~/dotfiles`. Do not modify
or require a checkout of the Omnigent repository.

The integration uses only surfaces present in published Omnigent `0.5.1`:

- stdio MCP servers declared in agent YAML;
- server-side function policies and session label writes;
- `GET /v1/sessions` and `GET /v1/sessions/{id}`;
- `GET /v1/sessions/{id}/items`; and
- the existing internal `POST /v1/sessions/{id}/events` message route.

There is no Omnigent server plugin. The user-facing "plugin" is a stateless MCP
control surface plus a hub-local sidecar service.

## 2. Goals

- Let the agent responsible for a diff explicitly subscribe or unsubscribe.
- Bind opt-in to the authenticated Omnigent session without accepting a session
  ID or diff ID from the model.
- Wake that same session for new unresolved non-author review comments and new
  terminal CI failures on its latest diff version.
- Baseline existing state so subscription never creates historical work.
- Batch autocorrelated updates for five minutes and send one concise message.
- Avoid polling dead sessions and terminal diffs, and slow polling with age.
- Persist source cursors, batches, retry state, and handled fingerprints.
- Survive sidecar/server restarts and active-hub handoff.
- Keep raw comments, logs, credentials, and source command output out of wake
  messages and durable watcher state.

## 3. Non-goals

- Changes to `~/repos/omnigent` or any published Omnigent package.
- Orchest task association.
- Webhooks or a new internal event service.
- Green or pending CI notifications.
- Author, automated, draft, deleted, or resolved comment notifications.
- Multiple diffs owned by one session in the first version.
- A general Omnigent plugin SDK.

## 4. Components

### 4.1 MCP intent tools

`services/omnigent-diff-watcher` installs a stdio MCP server exposing:

- `diff_watch_subscribe(events?)`
- `diff_watch_unsubscribe()`
- `diff_watch_status()`

Claude and Codex agent YAML declare this server with the existing inline
`type: mcp` format. The tools are deliberately stateless and accept no session
or diff identity. Omnigent namespaces them under the `diff_watch` server.

### 4.2 Policy-bound preferences

`omnigent_config/policy_modules/capture_diff.py` recognizes the namespaced tool
result. The policy engine already knows which session produced that result and
has its current labels, so it writes:

```text
omnigent.diff.watch=ci_failure,review_comment
```

or:

```text
omnigent.diff.watch=off
```

The existing `omnigent.diff.number=D12345` label supplies the diff. Subscribe
requires an ASK approval policy. Status and unsubscribe do not.

This is the trusted identity boundary: the model cannot subscribe another
session because it never supplies a session ID.

### 4.3 Watcher sidecar

`omnigent-diff-watcher.service` runs only on the active Omnigent hub. It:

1. Lists sessions every 15 seconds.
2. Reconciles diff/watch labels into its SQLite subscription table.
3. Polls each distinct diff once and fans results out to its subscribers.
4. Maintains adaptive deadlines, five-minute batches, liveness, and retirement.
5. Posts a single message when a batch is current and its session is idle.

The service database is `~/.omnigent/diff-watcher.sqlite3`, mode `0600`, WAL
enabled. It never writes Omnigent's database.

### 4.4 Hub controller

The existing hub controller starts/stops the watcher with the other active-hub
tail services. Quiesced handoff stops it before the final snapshot. Snapshot
format 2 includes `diff-watcher.sqlite3`, validates its checksum/schema summary,
and restores it on the promoted hub.

## 5. Subscription behavior

Subscribe succeeds only when:

- the current session already has a valid `omnigent.diff.number` label;
- the session is not archived or closed;
- the diff exists and is active;
- every selected source can establish a current baseline; and
- resource limits permit another active diff.

Repeated subscribe calls with the same preferences are idempotent. Adding a
new event type baselines only that type, so a pre-existing CI failure does not
wake a comments-only subscription that later enables CI.

One session may watch one diff. Several sessions may intentionally watch the
same diff; they share one external poll and receive separate batches.

Unsubscribe writes `off`, retires the durable subscription, and cancels its
open batch. Removing the last subscriber removes the diff from the external
poll schedule.

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

A CI event qualifies only when the latest version reaches terminal failure
with a new stable failure fingerprint. Pending, green, skipped, and cancelled
states do not qualify. A new diff version invalidates the old version's pending
CI failures.

First observation is always a baseline and emits no event.

## 7. Polling and resource limits

Successful polling uses the diff's last meaningful activity:

| State | Interval |
|---|---:|
| CI active or activity under 1 hour | 1 minute |
| Idle 1-6 hours | 5 minutes |
| Idle 6-24 hours | 15 minutes |
| Idle 1-3 days | 1 hour |
| Idle 3-14 days | 6 hours |
| Idle over 14 days | 24 hours |

Deadlines receive deterministic plus/minus 10 percent jitter. Source failures
use a separate 1, 2, 5, 15, then 30 minute exponential sequence and never make
the diff appear older.

Limits are 100 active diffs, two concurrent source polls, one open batch per
subscription, and one source poll lease per diff. Hitting a limit rejects new
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

- the preference becomes `off`;
- the session is deleted, archived, or closed;
- the diff is committed, abandoned, or reverted; or
- two authoritative observations classify the diff as missing.

Runner loss alone is temporary. After 24 hours unreachable, suspend external
diff polling and probe only Omnigent liveness every six hours. Recovery pulls
the shared diff deadline forward for one current snapshot and creates at most
one current-state batch.

## 11. Configuration and rollout

`services/omnigent-diff-watcher/config.toml` is source-controlled and starts
with:

```toml
delivery_mode = "log_only"
delivery_session_allowlist = []
```

Rollout order:

1. Install/sync the service environment on both hub candidates and execution
   hosts through `init.sh`.
2. Verify the MCP server and published Omnigent agent parsing.
3. Start the sidecar in `log_only`; confirm empty/expected subscriptions.
4. Subscribe one canary session and verify the baseline is quiet.
5. Observe one comment/CI burst as one `would deliver` batch.
6. Set `delivery_mode = "enabled"` with only that session allowlisted.
7. Verify one wake, restart recovery, source failure, unsubscribe, and terminal
   retirement.
8. Remove the allowlist only after those canary checks pass.

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
suspension/recovery, preference expansion, marker deduplication, REST event
shape, real stdio MCP negotiation, published Omnigent bundle parsing, and a
full label-to-single-wake flow.

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
- Subscription is explicit, approved, session-bound, and idempotent.
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
