# Omnigent diff watcher

Private sidecar that watches Phabricator review comments and terminal CI
failures for explicitly opted-in Omnigent sessions. It uses the published
Omnigent `0.5.1` REST and policy surfaces and requires no Omnigent source
changes.

## Architecture

- `omnigent-diff-watch-mcp` exposes subscribe, unsubscribe, and status intent
  tools through the agent's normal stdio MCP configuration.
- `capture_diff.py` binds those tool results to the authenticated session by
  updating `omnigent.diff.watch`.
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
