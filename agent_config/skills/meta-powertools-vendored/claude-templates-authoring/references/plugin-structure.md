# Plugin Structure Reference

This document provides detailed information about plugin directory structure and required files.

## Directory Layout

```
plugins/<plugin-name>/
├── README.md              (required)
├── LICENSE                (optional)
├── RELEASE-NOTES.md       (optional)
├── skills/                (optional)
│   ├── <skill-1>/
│   │   ├── SKILL.md
│   │   ├── references/
│   │   └── scripts/
│   └── <skill-2>/
│       └── SKILL.md
├── commands/              (optional)
│   ├── <command-1>.md
│   └── <command-2>.md
├── hooks/                 (optional)
│   ├── session-start.sh
│   ├── pre-tool-use.py
│   └── hooks.json         (fallback only - prefer inline in marketplace.json)
├── agents/                (optional)
│   └── <agent-name>.md
├── scripts/               (optional)
│   └── utility-script.py
└── mcp/                   (optional)
    └── server-config.json
```

## Required Files

### README.md

Every plugin must have a README.md with:

1. **Title and description** - What the plugin does
2. **Installation instructions** - How to install via marketplace
3. **Usage examples** - How to use the plugin's features
4. **Component list** - Skills, commands, hooks included
5. **Author and contact** - Who maintains it

Example structure:

```markdown
# My Plugin

Description of what this plugin provides.

## Installation

Install from the Meta marketplace:

\`\`\`bash
/plugin install my-plugin
\`\`\`

## Features

- **skill-name**: Description of skill
- **/command-name**: Description of command

## Usage

Examples of how to use the plugin...

## Author

Your Name (you@meta.com)
```

## Optional Components

### Skills Directory

Skills provide specialized knowledge and workflows:

```
skills/
└── my-skill/
    ├── SKILL.md           (required - skill definition)
    ├── references/        (optional - documentation)
    │   └── schema.md
    ├── scripts/           (optional - executable code)
    │   └── helper.py
    └── assets/            (optional - templates, images)
        └── template.html
```

### Commands Directory

Commands define slash commands that expand to prompts:

```
commands/
├── brainstorm.md          # Creates /plugin-name:brainstorm
└── analyze.md             # Creates /plugin-name:analyze
```

Command file format:

```markdown
---
description: Brief description shown in /help
arguments: [optional_arg_name]
---

The prompt that this command expands to.
Use $ARGUMENTS to reference user input.
```

### Hooks Directory

Hooks contain scripts triggered by Claude Code events:

```
hooks/
├── session-start.sh       # Runs on session start
├── pre-tool-use.py        # Runs before tool calls
└── hooks.json             # ONLY if not using inline hooks
```

**Important**: If you define hooks inline in marketplace.json, the hooks.json file is ignored.

### Agents Directory

Agents define custom agent types for the Task tool:

```
agents/
└── code-reviewer.md
```

Agent file format:

```markdown
---
name: code-reviewer
description: Reviews code for bugs and style issues
tools: [Read, Grep, Glob]
---

System prompt for the agent...
```

### Scripts Directory

Utility scripts used by skills or hooks:

```
scripts/
├── analyze.py
└── format.sh
```

### MCP Directory

MCP server configurations:

```
mcp/
└── my-server.json
```

## File Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| Plugin directory | lowercase-with-hyphens | `source-control-at-meta` |
| Skill directory | lowercase-with-hyphens | `test-driven-development` |
| Command file | lowercase-with-hyphens.md | `write-plan.md` |
| Hook script | lowercase-with-hyphens.sh/.py | `session-start.sh` |
| Agent file | lowercase-with-hyphens.md | `code-reviewer.md` |

## Version Control

Plugins in claude-templates are version controlled:

1. Source lives in `fbcode/claude-templates/components/plugins/`
2. Changes require a Phabricator diff
3. Plugin version in marketplace.json should be updated for significant changes
4. Use semantic versioning: `MAJOR.MINOR.PATCH`
