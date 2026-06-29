# Cross-agent plugin sync

`plugins.list` is the canonical set of `agent-market` plugins you want
installed on every agent (Claude Code, Codex, Devmate, Metacode).

## Workflow

- **After installing a new plugin** anywhere: run `sync save` then `sync apply`.
- **After uninstalling a plugin** anywhere: edit `plugins.list` to remove
  the line, then run `sync apply` (it won't auto-uninstall — see Notes).
- **On a fresh devserver**: dotsync2 ships `~/dotfiles/`; run `sync apply`
  once to populate every agent.
- **To audit drift**: `sync diff`.

The `agent-market` 2-hour systemd cron keeps installed-plugin *versions*
fresh on its own. You don't need to touch it.

## Notes

- `sync apply` is install-only. It does not uninstall "extra" plugins on
  any agent — that protects local experiments. If you really want to
  uninstall everywhere, do it manually with
  `agent-market plugin <name> uninstall --agent <agent>`.
- `plugin-hygiene` is intentionally excluded from `plugins.list` (kept
  Claude-local). Don't add it.
- The `meta-powertools` + `10x-data-scientist` bundle is intentionally
  excluded (~49k chars of skill descriptions). Its valuable MCPs are
  vendored at `plugins/custom-mcps/mcps/` and wired into Claude via
  `~/.claude/settings.json` `mcpServers`. To re-syndicate to other
  agents, copy those JSONs into the agent's native MCP config (e.g.
  `~/.codex/config.toml` `[mcp_servers.*]`).
- Some plugins won't be available on every agent surface; the apply step
  logs `(failed — X may not be available for Y)` and keeps going.
- dvsc/dm-core (Devmate) on-disk config dir is not yet documented. If
  Devmate installs go nowhere, ask in the `claude.templates.website`
  Workplace group.
