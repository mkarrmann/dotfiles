# Cross-agent agent setup

`plugins.list` is the canonical set of `agent-market` plugins installed
on every agent (Claude Code, Codex, Devmate, Metacode). `drop-plugins.list`
is the inverse — plugins kept uninstalled everywhere. MCPs live under
`plugins/custom-mcps/mcps/` and vendored skills under
`skills/meta-powertools-vendored/`.

## What `~/dotfiles/init.sh` wires up automatically

Running `init.sh` (designed to be re-run; idempotent) handles:

1. **Symlinks** every `skills/*/SKILL.md` and every
   `skills/meta-powertools-vendored/*/SKILL.md` subdir into
   `~/.claude/skills/` and `~/.codex/skills/`.
2. **Generates** `~/.codex/config.toml` from
   `codex_config/config.template.toml` + `~/.codex/config.local.toml`.
3. **MCPs** — calls `agent_config/sync-mcps all`, which writes the 7
   MCP definitions from `plugins/custom-mcps/mcps/*.json` into each
   agent's native config (Claude `settings.json.mcpServers`, Codex
   `[mcp_servers.X]`, Metacode `opencode.json.mcp`). For Metacode it
   also adds the vendored-skills dir to `skills.paths` (Metacode loads
   skills from paths, not symlinks).
4. **Plugins** — calls `agent_config/bootstrap-plugins`, which uninstalls
   everything in `drop-plugins.list` from every agent, cleans orphan
   plugin caches under `~/.claude/plugins/cache/agent-market/` and
   `~/.codex/plugins/cache/claude-templates/`, then runs `sync apply`
   to install everything in `plugins.list` on every agent.

So: pull dotfiles → run `init.sh` → every devserver lines up.

## Day-to-day workflow

- **Installed a new plugin** anywhere: run `sync save` then `sync apply`.
- **Decided to drop a plugin**: edit `plugins.list` to remove the line
  AND add the name to `drop-plugins.list` (so it doesn't sneak back in
  via `sync save` and gets actively uninstalled on next bootstrap).
- **Audit drift across agents**: `sync diff`.

The `agent-market` 2-hour systemd cron keeps installed-plugin *versions*
fresh on its own — no manual step.

## Notes

- `sync apply` is install-only (won't auto-uninstall extras — that's
  what `bootstrap-plugins` + `drop-plugins.list` are for).
- `meta-powertools` and `10x-data-scientist` are intentionally dropped
  (~49k chars of skill descriptions). The valuable MCPs are vendored
  at `plugins/custom-mcps/mcps/` and rewired by `sync-mcps`. The
  valuable skills are vendored at `skills/meta-powertools-vendored/`
  and symlinked by `init.sh`. See that dir's `TODO.md` for the
  staleness problem.
- Codex re-serializes `~/.codex/config.toml` at runtime and strips
  comments — that's why `sync-mcps` identifies its managed blocks by
  table *name* (`[mcp_servers.<known-name>]`) rather than by a marker
  comment.
- Some plugins won't install on every agent — `sync apply` logs
  `(failed — X may not be available for Y)` and keeps going.
- Devmate has no on-disk user config; it inherits from Claude via
  `DOTSYNC_DEVSERVER`. Plugin installs to `--agent devmate` go through
  `agent-market`'s devmate adapter.
