# Omnigent diff watcher

Private sidecar that watches things on behalf of explicitly opted-in Omnigent
sessions and wakes them when something changes. It uses the published Omnigent
`0.5.1` REST and policy surfaces and requires no Omnigent source changes.

Subscription does not require an approval prompt. The operation is
session-scoped, idempotent, and reversible.

## Two surfaces

The engine is shared; the interfaces are not, deliberately.

| | `diff_watch_*` | `watch_*` |
| --- | --- | --- |
| Subject | validated Phabricator diff IDs | any namespaced `<prefix>:<id>` |
| How it is read | built in | an argv the caller supplies |
| Events | the four diff kinds below | `changed` |
| Declared through | `omnigent.diff.*` session labels | `watch_requests` table |
| Skill | `phabricator-diff-watch` | `watch-anything` |

The diff surface stays opinionated about diffs — it takes no source and no
command, and its events describe review and CI specifically, which is the
interface worth having for the common case. The generic surface assumes
nothing about its subject, at the cost of the caller having to say how to read
it.

They take different routes for a concrete reason: a diff watch rides session
labels, and a label value is capped at 256 characters, which an arbitrary argv
overruns. A generic watch is therefore written straight to the database by the
MCP tool and reconciled from `watch_requests`.

That route costs it reach. `diff_watch_*` never learns its own session -- it
returns an intent string and a server-side policy, which does know the session,
writes the label -- so it works from any harness. A generic watch writes the
row itself and must therefore identify the session, which only a native
harness's bridge directory allows; a streamed SDK session gets no session id in
its MCP environment. So `watch_*` is **native-only**, it says so when called
from anywhere else, and the agent specs (which launch this server with no
`--native` flag) deliberately do not advertise it.

Lifting that would mean either Omnigent passing a session id to stdio MCP
servers, or a server-side policy writing `watch_requests` the way
`capture_diff` writes labels -- the latter is the same shape as the diff route
and would work today, at the cost of a second policy module.

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

- `omnigent-diff-watch-mcp` exposes subscribe, unsubscribe, and status intent
  tools through the agent's normal stdio MCP configuration. A native harness
  launches it with `--native-codex` or `--native-claude`, which sends each
  result through the session's authenticated local Omnigent policy endpoint
  and returns the authoritative policy response; the streamed SDK harnesses
  get that rewrite for free and need no flag.
- `capture_diff.py` binds those tool results to the authenticated session by
  updating `omnigent.diff.watch`.
- `diff_watch_subscribe` accepts explicit `diffs` for an existing diff or stack;
  diffs submitted by the current session continue to associate automatically.
- The hub-only service reconciles session labels through `GET /v1/sessions`
  and generic watches from `watch_requests`, polls each active subject once,
  and stores cursors/batches in `~/.omnigent/diff-watcher.sqlite3`.
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
The MCP server is spawned per agent session, so a change to the *tools* is not
visible to sessions already running; their tool schemas are whatever was on
disk when they started. Module-level edits need a fresh session, though the
lazily imported modules inside each tool are read at first call.

Run one reconciliation/poll cycle against the configured server:

```bash
uv run omnigent-diff-watcher --config config.toml once --json
```
