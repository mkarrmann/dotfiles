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
   `~/.codex/skills/`, and — for Claude — into either
   `~/.claude/skills/` or `~/checkoutN/.claude/skills/` depending on
   `skills-global.list` (see "Skills" below).
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

## Skills: scoping and the listing budget

Claude Code and Omnigent both walk the ancestor `.claude/skills/` chain
upward from cwd, so a skill need not live in `~/.claude/skills` to be
found. `sync.sh` uses that to scope by workspace:

- Names in `skills-global.list` → `~/.claude/skills/`, advertised
  everywhere including non-Meta trees such as `~/repos/*`.
- Everything else, including all of `skills/meta-powertools-vendored/`,
  → `~/checkoutN/.claude/skills/`, advertised only while cwd is inside a
  Meta checkout. Workspace roots are detected as any `$HOME/*` directory
  containing an `fbsource/` or `configerator/` checkout, so a relocated
  or extra checkout is picked up without editing the script.

Keep the global list short: each entry costs context in every unrelated
session.

Skills belonging to a specific fbsource subtree (Sapphire, Presto, …)
are deliberately **not** linked here. They ship in that subtree's own
`.claude/skills/`, and the harness finds them from inside it; hoisting
them to global advertised them in every unrelated tree and pinned them
to one checkout.

### Frontmatter is mandatory

Every `SKILL.md` needs YAML frontmatter with both `name:` and
`description:`. Claude Code derives a missing `name` from the directory
name, but Omnigent rejects the skill and only warns on stderr, so it
silently loads in one harness and not the other. `sync.sh` validates
this and reports `SKILL PROBLEMS` on stderr, alongside `SHADOWED`
entries where a real file at the destination is masking the dotfiles
copy.

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
  and symlinked by `init.sh` (checkout-scoped — see "Skills" above).
  See that dir's `TODO.md` for the staleness problem.
- Codex re-serializes `~/.codex/config.toml` at runtime and strips
  comments — that's why `sync-mcps` identifies its managed blocks by
  table *name* (`[mcp_servers.<known-name>]`) rather than by a marker
  comment.
- Some plugins won't install on every agent — `sync apply` logs
  `(failed — X may not be available for Y)` and keeps going.
- Devmate has no on-disk user config; it inherits from Claude via
  `DOTSYNC_DEVSERVER`. Plugin installs to `--agent devmate` go through
  `agent-market`'s devmate adapter.
