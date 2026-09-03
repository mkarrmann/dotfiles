# Omnigent diff watcher

Private sidecar that watches Phabricator diffs for explicitly opted-in
Omnigent sessions. It uses the published Omnigent `0.5.1` REST and policy
surfaces and requires no Omnigent source changes.

Subscription does not require an approval prompt. The operation is
session-scoped, idempotent, reversible with `diff_watch_unsubscribe`, and can
only attach validated Phabricator diff IDs.

## Events

| Event | Fires when |
| --- | --- |
| `review_comment` | A human leaves an unresolved comment on the latest version. |
| `ci_failure` | A signal reports `FAILED`, as soon as it does. |
| `ai_review` | An automated reviewer has an unresolved finding. |
| `ci_green` | A version's run finishes with nothing failing. |

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
- The hub-only service reconciles session labels through `GET /v1/sessions`,
  polls each active diff once, and stores cursors/batches in
  `~/.omnigent/diff-watcher.sqlite3`.
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

Run one reconciliation/poll cycle against the configured server:

```bash
uv run omnigent-diff-watcher --config config.toml once --json
```
