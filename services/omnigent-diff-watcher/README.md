# Omnigent diff watcher

Private sidecar that watches things on behalf of explicitly opted-in Omnigent
sessions and wakes them when something changes. It uses only the published
Omnigent REST surface and requires no Omnigent source changes and no policy
modules.

Subscription does not require an approval prompt. The operation is
session-scoped, idempotent, and reversible.

## Two surfaces

The engine is shared; the interfaces are not, deliberately.

| | `diff_watch_*` | `watch_*` |
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

The route is identical. Every tool takes the `session_id` it should wake,
validates it against the server, binds the watch synchronously, and records a
`watch_requests` row scoped by source. Nothing is harness-specific.

### Why identity is an argument

MCP carries no session context in any transport: stdio `env` and HTTP `headers`
are fixed at deploy time, there is no `_meta` plumbing, and Omnigent's MCP pool
shares one server process across every session using an agent. A tool could
therefore only learn its own session by scraping the harness's private bridge
directory, which existed for two harnesses, coupled this code to Omnigent's
internal directory names, and could match two bridges at once and bind a watch
to the wrong session.

Agents get the id from `sys_session_get_info` instead — one cheap call, every
harness. A subagent should pass that call's `parent_session_id`, or the wake is
delivered to a session that no longer exists.

The id comes from the model, so it is validated with `GET /v1/sessions/{id}`
before anything is written. That catches a typo, an unknown id, and a closed or
archived session. It does not catch a deliberate wrong-but-live id, which would
wake another session of the same user on the same machine, from an agent that
already has that user's shell.

### Generic watches

A command watch runs its argv directly — never through a shell, so no part of
a subject or spec is interpreted as shell syntax — hashes the output, and
raises a `changed` event when the hash moves. Storing an argv is not a
privilege escalation, since an agent that can subscribe can already run
commands, but it is a longer-lived one: the argv is recorded in the database
and readable through `watch_status`.

Two behaviours differ from a diff watch:

- **The interval is held constant** (`interval_seconds`, 30s–24h, default 60s).
  The idle ladder that backs a quiet diff off to daily polling would defeat a
  watch whose whole job is catching a change promptly.
- **`extract` is usually necessary.** Without it the whole output is the value,
  so a timestamp or request id anywhere in it re-fires every poll.

## Events

| Event            | Fires when                                                  |
| ---------------- | ----------------------------------------------------------- |
| `review_comment` | A human leaves an unresolved comment on the latest version. |
| `ci_failure`     | A signal reports `FAILED`, as soon as it does.              |
| `ai_review`      | An automated reviewer has an unresolved finding.            |
| `ci_green`       | A version's run finishes with nothing failing.              |
| `changed`        | A generic watch's command produces different output.        |

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

## Architecture

- `omnigent-diff-watch-mcp` exposes both surfaces through the agent's normal
  stdio MCP configuration, with no flags and no per-harness variants. Each tool
  validates its `session_id`, binds the watch, and writes a `watch_requests`
  row.
- The hub-only service re-binds `watch_requests` rows that have no live
  subscription, polls each active subject once, and stores cursors and batches
  in `~/.omnigent/diff-watcher.sqlite3`. Set
  `OMNIGENT_DIFF_WATCHER_DATABASE` to point both processes at another file.
- A source implements `domain.WatchSource`: it is handed a subject, an opaque
  cursor, and an optional spec, and returns a `PollResult`. Everything below
  that line — leasing, fingerprint diffing, batching, liveness, delivery — is
  source-neutral and never learns what the subject is.
- Delivery posts one concise message to the existing hidden
  `POST /v1/sessions/{id}/events` route. A stable batch marker is checked in
  session items before every retry.

The checked-in configuration starts in `log_only` mode. Enable delivery only
for an allowlisted canary session before enabling it generally.

## Development

```bash
uv sync --frozen --all-groups
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy --strict src tests
```

Two processes run this code, and they pick up changes at different moments.
The sidecar loads it once at start, so a change to the engine or a source needs
`systemctl --user restart omnigent-diff-watcher` — and only the sidecar may
migrate the schema, so that restart is also what applies a new schema version.
Schema v4 backfills a `watch_requests` row for every live subscription, so the
diff watches created under the old session-label scheme keep working across the
upgrade instead of silently lapsing.
The MCP server is spawned per agent session, so a change to the *tools* is not
visible to sessions already running; their tool schemas are whatever was on
disk when they started. Module-level edits need a fresh session, though the
lazily imported modules inside each tool are read at first call.

Run one reconciliation/poll cycle against the configured server:

```bash
uv run omnigent-diff-watcher --config config.toml once --json
```
