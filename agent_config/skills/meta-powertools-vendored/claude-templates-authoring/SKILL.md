---
name: claude-templates-authoring
description: Guide for creating, modifying, and validating claude-templates components (plugins, skills, hooks, commands). Use when editing skills/plugins/hooks in fbcode/claude-templates/, adding new skills/plugins/hooks, configuring hooks, debugging hook loading issues, testing skill/plugin installations, or validating component structure before submission.
---

# Claude Templates Authoring

This skill provides guidance for creating, modifying, and validating components in the claude-templates repository.

## Choosing the Right Component Type

Each component type serves a different purpose. Choose the right tool for the job:

| Component | Use When |
|-----------|----------|
| **Skill** | Providing knowledge, workflows, or specialized guidance |
| **Command** | Creating a reusable slash command (e.g., `/brainstorm`) |
| **Hook** | Triggering actions on events (session start, tool use, etc.) |
| **Plugin** | Bundling **multiple** components together |

**ONLY** suggest a plugin when:
- Packaging **more than one** component (skills + commands, hooks + skills, etc.)
- User explicitly expects to add more components in the future

**ALWAYS** question the user if terminology seems mismatched:
- User says "plugin" but describes a single skill → Ask: "This sounds like a standalone skill. Do you need a plugin, or would a skill be simpler?"
- User says "skill" but describes event-triggered behavior → Ask: "This sounds like a hook. Should we create a hook instead?"
- User says "command" but describes complex workflows → Ask: "This might be better as a skill. Commands are for simple prompt expansions."

## Related Skills

When creating components, use the specialized creator skills for **WHAT** to build, and this skill for **HOW** to distribute:

| Skill | Purpose |
|-------|---------|
| **skill-creator** | **WHAT** - Guides skill content, structure, best practices, and testing |
| **hook-creator** | **WHAT** - Guides hook implementation, events, and debugging |
| **claude-templates-authoring** | **HOW** - Guides adding components to claude-templates for distribution |

If creating a skill and `skill-creator` is available, use it first to design the content. If creating a hook and `hook-creator` is available, use it first. Then use this skill to add the component to claude-templates.

## Quick Start

### Create a New Plugin

1. Create plugin directory in `fbcode/claude-templates/components/plugins/<plugin-name>/`
2. Create `.claude-plugin/plugin.json` with name, description, version, and component paths
3. Add required files: `README.md`, plus any skills/, commands/, hooks/, agents/
4. Test with `claude-templates plugin <name> install --dev`

**Note:** marketplace.json is auto-generated at build time from individual plugin.json files. You never need to edit it manually.

### Create a New Skill

1. Create skill directory in `fbcode/claude-templates/components/skills/<skill-name>/`
2. Create `SKILL.md` with YAML frontmatter (name, description)
3. Add to a plugin's `skills` array in its `.claude-plugin/plugin.json`, or publish standalone

## Component Types

| Type | Location | Purpose |
|------|----------|---------|
| **Plugin** | `plugins/<name>/` | Bundle of skills, commands, hooks, agents |
| **Skill** | `skills/<name>/` | Specialized knowledge and workflows |
| **Command** | `commands/<name>.md` | Slash command (e.g., `/brainstorm`) |
| **Hook** | `hooks/` | Scripts triggered by Claude Code events |
| **Agent** | `agents/<name>.md` | Custom agent type for Task tool |

## Plugin Structure

```
plugins/<plugin-name>/
├── README.md              (required - plugin documentation)
├── LICENSE                (optional - for open source)
├── skills/                (optional - skill subdirectories)
│   └── <skill-name>/
│       └── SKILL.md
├── commands/              (optional - slash command files)
│   └── <command>.md
├── hooks/                 (optional - hook scripts)
│   ├── session-start.sh
│   └── hooks.json         (ONLY if not using inline hooks)
├── agents/                (optional - custom agent definitions)
│   └── <agent>.md
├── scripts/               (optional - utility scripts)
└── mcp/                   (optional - MCP server configs)
```

## plugin.json Configuration

Every plugin needs a `.claude-plugin/plugin.json` file in its directory:

```
plugins/<plugin-name>/
├── .claude-plugin/
│   └── plugin.json     # Required - plugin metadata and configuration
├── README.md
└── ...
```

Example `.claude-plugin/plugin.json`:

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "description": "Brief description of what the plugin does",
  "author": {
    "name": "Your Name",
    "email": "you@meta.com"
  },
  "homepage": "https://www.internalfb.com/code/fbsource/fbcode/claude-templates/components/plugins/my-plugin/README.md",
  "skills": ["./skills/"],
  "commands": ["./commands/"],
  "hooks": { ... },
  "keywords": ["keyword1", "keyword2"]
}
```

**Note:** marketplace.json is auto-generated from these plugin.json files at build time. You never edit marketplace.json directly.

See [marketplace-json-schema.md](references/marketplace-json-schema.md) for the complete field reference.

## Hook Configuration

### How Hooks Work

**CRITICAL**: Hooks are **copied to `~/.claude/settings.json` at install time**. They are NOT loaded dynamically from plugin directories at runtime.

```
Install Time:                          Runtime:
┌─────────────────────┐               ┌─────────────────────┐
│ plugin.json         │               │                     │
│ (inline hooks)      │──┐            │  Claude Code reads  │
│         OR          │  ├─► merge ─► │  ~/.claude/         │
│ hooks/hooks.json    │──┘            │  settings.json      │
└─────────────────────┘               └─────────────────────┘
```

**Implications:**
- Deleting hooks from settings.json removes them, even if the plugin is still "enabled"
- Reinstalling a plugin re-merges hooks to settings.json
- `${CLAUDE_PLUGIN_ROOT}` is expanded by Claude Code at runtime when executing the command

### Source Priority (Install Time)

When installing a plugin, this priority determines which source is used:

1. **If plugin.json has inline hooks** → Use those, **completely ignore** hooks.json
2. **If plugin.json has NO hooks** → Fall back to hooks/hooks.json

Both sources get merged to settings.json the same way - the priority just determines which source is read.

### Inline Hooks (Recommended)

Define hooks directly in plugin.json:

```json
{
  "name": "my-plugin",
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume|clear|compact",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PLUGIN_ROOT}/hooks/session-start.sh"
          }
        ]
      }
    ]
  }
}
```

For hook events, implementation details, and debugging, use the `hook-creator` skill.

## Testing Components

### CLI Subcommands

The `claude-templates` CLI has different subcommands for different component types. Use the correct one:

| Component Type | Install Command |
|---------------|-----------------|
| **Plugin** | `claude-templates plugin <name> install --dev` |
| **Skill** (standalone) | `claude-templates skill <name> install --dev` |

**Common mistake**: Using `plugin` to install a standalone skill or vice versa. Check where the component lives:
- `./plugins/<name>/` → use `plugin` subcommand
- `./skills/<name>/` → use `skill` subcommand

### Dev Installation

Install a component for development testing:

```bash
# For plugins (in ./plugins/ directory)
claude-templates plugin <name> install --dev

# For standalone skills (in ./skills/ directory)
claude-templates skill <name> install --dev
```

This installs from your local fbcode source, not the published marketplace.

### Testing Hooks

1. Install the plugin with `--dev`
2. Start a new Claude Code session
3. Check for hook output in the session
4. For debugging, add logging to your hook script:
   ```bash
   echo "Hook fired at $(date)" >> /tmp/hook-debug.log
   ```

### Cleanup

Remove dev-installed plugins and reset to production:

```bash
claude-templates dev-cleanup
```

## Debugging and Incremental Changes

When debugging or iterating on a skill/plugin installed from claude-templates, understand the two locations involved:

| Location | Purpose | Persistence |
|----------|---------|-------------|
| `~/.claude/` or `.claude/` | Installed version (what Claude uses) | **Temporary** - overwritten on reinstall |
| `fbcode/claude-templates/components/` | Source of truth | **Persistent** - survives reinstalls |

### Before Editing: Check the Source

**CRITICAL**: Before editing any skill or plugin, you **MUST** check if it's installed from claude-templates.

**How to check:**
```bash
# Look for the plugin in the claude-templates cache
ls ~/.claude/plugins/cache/claude-templates/<plugin-name>/
```

If the directory exists, the component is from claude-templates. **You MUST edit the source in `fbcode/claude-templates/components/`**, not in `~/.claude/` nor in `~/.claude-templates-dev/`.

**Why this matters:**
- Edits to `~/.claude/` and `~/.claude-templates-dev/` are temporary and will be overwritten on reinstall
- The source of truth is in fbcode - that's what gets published
- Other users won't see your fixes unless you update the source

### Quick Debugging (Temporary)

For rapid iteration during a debugging session, edit the installed version directly:

```bash
# Edit the installed skill
vim ~/.claude/plugins/cache/claude-templates/<plugin>/<version>/skills/<skill>/SKILL.md
```

**Warning**: These changes will be overwritten when the plugin is reinstalled. Use only for quick experiments.

### Persisting Changes (Required)

To make changes permanent:

1. **Apply changes to source**: Edit files in `fbcode/claude-templates/components/`
2. **Reinstall to test**: Run `claude-templates skill <name> install --dev`
3. **Repeat**: After every source change, reinstall before testing

**Note**: The `--dev` flag is required to install uncommitted skills. Without it, `claude-templates skill` only shows skills that have been landed.

```bash
# Edit source
vim fbcode/claude-templates/components/skills/<skill>/SKILL.md

# Reinstall (REQUIRED after every change)
claude-templates skill <skill> install --dev

# Test in new session
claude
```

**Critical**: You MUST reinstall after every change to the source. The installed version does not auto-update.

### Common Mistake

Editing the source but forgetting to reinstall, then wondering why changes aren't reflected. Always reinstall:

```bash
claude-templates skill <name> install --dev
```

## Common Mistakes

### Redundant hooks.json

If plugin.json has inline hooks, any hooks/hooks.json is ignored. Delete redundant files:

```bash
rm plugins/<name>/hooks/hooks.json
```

### Missing Plugin Source

The plugin must have a `.claude-plugin/plugin.json` file with correct paths:

```json
"skills": ["./skills/"]   // Correct - relative to plugin directory
```

### Hook Script Not Executable

Make hook scripts executable:

```bash
chmod +x plugins/<name>/hooks/*.sh
```

### Invalid JSON

Always validate after editing plugin.json:

```bash
python3 -m json.tool plugins/<name>/.claude-plugin/plugin.json > /dev/null
```

## Validating Components

Use this skill to validate that a component is correct before submission.

### Validation Checklist

| Component | Validation Steps |
|-----------|-----------------|
| **Plugin** | README.md exists, plugin.json valid, component paths correct |
| **Skill** | SKILL.md has valid frontmatter (name, description), description under 1024 chars |
| **Hook** | Script is executable, uses `${CLAUDE_PLUGIN_ROOT}`, inline hooks preferred |
| **Command** | Has frontmatter with description, uses `$ARGUMENTS` for input |

### Quick Validation Commands

```bash
# Validate plugin.json syntax
python3 -m json.tool plugins/<name>/.claude-plugin/plugin.json > /dev/null

# Check hook scripts are executable
ls -la plugins/<name>/hooks/*.sh

# Test plugin installation
claude-templates plugin <name> install --dev

# Validate skill with skill-creator (if available)
python3 /home/mkarrmann/.claude/agent-market/skills/skill-creator/scripts/package_skill.py <path-to-skill>
```

### What to Verify Before Submission

1. **Structure**: All required files present (README.md for plugins, SKILL.md for skills, plugin.json for plugins)
2. **Schema**: plugin.json follows schema, no trailing commas
3. **Functionality**: `--dev` install works, hooks fire, skills load
4. **Content**: Description is clear, documentation is complete

## Adding Usage Instrumentation

After creating your skill or plugin, add usage instrumentation so adoption and usage patterns can be tracked in the `claude_templates_plugin_simple_metrics` Scuba table.

The tracking script lives at `/usr/local/claude-templates-cli/components/helpers/track_plugin_usage.py`. It runs async with a 5-second timeout, so it never blocks the user.

### Pattern A: Plugin Instrumentation

Add a `hooks` section to your `.claude-plugin/plugin.json`:

```json
{
  "name": "my-plugin",
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "mcp__plugin_my.plugin_.*|Skill|Task",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /usr/local/claude-templates-cli/components/helpers/track_plugin_usage.py --plugin my-plugin || true",
            "async": true,
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

Adjust the `matcher` regex to match tool names your plugin uses. The `|| true` ensures hook failures never break the user's workflow.

### Pattern B: Standalone Skill Instrumentation

Add a `hooks` section to your `SKILL.md` frontmatter:

```yaml
---
name: my-skill
description: ...
hooks:
  UserPromptSubmit:
    - hooks:
        - type: command
          command: "python3 /usr/local/claude-templates-cli/components/helpers/track_plugin_usage.py --skill my-skill || true"
          async: true
          timeout: 5
  PostToolUse:
    - matcher: "Skill"
      hooks:
        - type: command
          command: "python3 /usr/local/claude-templates-cli/components/helpers/track_plugin_usage.py --skill my-skill || true"
          async: true
          timeout: 5
---
```

The `UserPromptSubmit` hook fires when the skill is auto-triggered (via `apply_to_user_prompt`). The `PostToolUse` hook fires when the skill is invoked explicitly via the Skill tool.

### Viewing Metrics

Query the `claude_templates_plugin_simple_metrics` Scuba table to see usage data for your skill or plugin.

## Workflow Summary

1. **Create** plugin directory with `.claude-plugin/plugin.json` and required files
2. **Configure** hooks inline in plugin.json (recommended)
3. **Instrument** with usage tracking hooks (see above)
4. **Test** with `claude-templates plugin <name> install --dev`
5. **Verify** hooks fire in new session
6. **Validate** using the checklist above
7. **Submit** using the `10x-engineer:sharing-skills` skill for diff workflow
8. **Cleanup** with `claude-templates dev-cleanup` when done testing

## Additional Resources

- [Plugin Structure Reference](references/plugin-structure.md)
- [marketplace.json Schema](references/marketplace-json-schema.md)
