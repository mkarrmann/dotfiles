# Cross-agent agent setup

`plugins.list` is the canonical set of `agent-market` plugins installed
on every agent (Claude Code, Codex, Devmate, Metacode). `drop-plugins.list`
is the inverse — plugins kept uninstalled everywhere. MCPs live under
`plugins/custom-mcps/mcps/` and vendored skills under
`skills/meta-powertools-vendored/`.

## What `~/dotfiles/init.sh` wires up automatically

Running `init.sh` (designed to be re-run; idempotent) handles:

Work plugins and internal MCPs below are enabled only in the `work` profile.
`bin/dotfiles-profile` performs the same detection for standalone `sync-mcps`
runs as for `init.sh`/`sync.sh`; see the repository README for overrides.
Desktop runs remove managed internal MCP registrations from existing configs
without removing unrelated personal servers.

1. **Symlinks** every `skills/*/SKILL.md` and every
   `skills/meta-powertools-vendored/*/SKILL.md` subdir into
   `~/.codex/skills/`, and — for Claude — into either
   `~/.claude/skills/` or `~/checkoutN/.claude/skills/` depending on
   `skills-global.list` (see "Skills" below).
2. **Generates** `~/.codex/config.toml` from
   `codex_config/config.template.toml` + `~/.codex/config.local.toml`.
   Work machines also merge `codex_config/config.work.toml`.
   These are parsed and merged recursively: local scalars and arrays replace
   shared values, and local table entries override matching shared entries.
   The generated file is replaced atomically; invalid TOML leaves it intact.
3. **MCPs** — calls `agent_config/sync-mcps all`, which writes the 7
   MCP definitions from `plugins/custom-mcps/mcps/*.json` into each
   agent's native config (Claude `~/.claude.json.mcpServers`, Codex
   `[mcp_servers.X]`, Metacode `opencode.json.mcp`). For Metacode it
   also adds the vendored-skills dir to `skills.paths` (Metacode loads
   skills from paths, not symlinks).
4. **Plugins** — calls `agent_config/bootstrap-plugins`, which uninstalls
   everything in `drop-plugins.list` from every agent, cleans orphan
   plugin caches under `~/.claude/plugins/cache/agent-market/` and
   `~/.codex/plugins/cache/claude-templates/`, then runs `sync apply`
   to install everything in `plugins.list` on every agent.

So: pull dotfiles → run `init.sh` → every devserver lines up.

### Codex config and local overrides

Use `sync.sh` to apply config edits, or run
`agent_config/sync-mcps codex --generate-config` for only the Codex config.
This requires Python 3.11+ (or `tomli` installed for an older Python).
`config.local.toml` is a dotfiles convention, consumed by this generator;
Codex reads the resulting `config.toml` and writes its runtime state there.
Examples live in `codex_config/config.local.example.toml`.

Generation combines the shared template and managed MCP definitions, then
applies local overrides. The work profile also loads `config.work.toml` and
registers internal MCPs; desktop generation retracts those managed servers.
It preserves existing `projects`, `tui`, `notice`,
`features`, `plugins`, and `hooks` tables and unmanaged MCP definitions;
explicit template/local entries take precedence. Put durable preference
overrides in `config.local.toml`, since edits to shared keys in the generated
file are replaced on the next sync. Local MCP overrides also survive a
standalone `sync-mcps codex` run.

For the retired symlink to `dotfiles/codex_config/config.toml`, generation
first backs up the original under the Codex home, migrates differing
preferences and trust entries into `config.local.toml`, and replaces the
symlink with a regular generated file. Existing local overrides win and are
also backed up. The old symlink target is left intact for archival.
Runtime UI state stays in the generated file, and managed MCP definitions
are refreshed from their canonical JSON sources.

Both Codex sync paths honor `CODEX_HOME`, except that an Omnigent native
session's temporary home maps back to `~/.codex`, as in `sync.sh`.

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

### Why `skillListingBudgetFraction` is set

Claude Code renders the skill listing under a byte budget:

    budget = skillListingBudgetFraction * context_tokens * 3

When the listing exceeds it, descriptions are **not** truncated — every
evictable one is dropped, leaving a bare name list with no trigger text,
which makes skills effectively undiscoverable. Claude Code's own bundled
skills are exempt from eviction and so consume the budget first.

The 1M window is selected only when the model id literally contains
`[1m]` (the check is `/\[1m\]/i`). Anything that strips that suffix —
Omnigent spawns `--model claude-opus-5` — falls back to 200k, where the
`0.01` default yields just 6,000 chars; the bundled skills alone overrun
that. `sync.sh` therefore sets `0.10` (60,000 chars at 200k) against a
measured ~39,500-char listing.

The fraction is a cap, not a reservation: raising it costs nothing by
itself, but the listing it permits is real per-turn context (~39.5KB,
~13k tokens). To shrink the listing itself, drop unused plugins via
`drop-plugins.list` (plugin skills ignore `skillOverrides`), then set
`skillOverrides: {"<name>": "user-invocable-only"}` for non-plugin
skills that never need advertising.

`skillListingMaxDescChars` is left at its 1536 default — the longest
description today is 1,135 chars, so nothing is truncated.

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
  comments. `sync-mcps` parses TOML and replaces managed MCP tables by
  name, so formatting and quoted table names do not affect synchronization.
- Some plugins won't install on every agent — `sync apply` logs
  `(failed — X may not be available for Y)` and keeps going.
- Devmate has no on-disk user config; it inherits from Claude via
  `DOTSYNC_DEVSERVER`. Plugin installs to `--agent devmate` go through
  `agent-market`'s devmate adapter.
