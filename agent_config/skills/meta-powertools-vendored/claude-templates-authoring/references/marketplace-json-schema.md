# marketplace.json Schema Reference

This document provides a complete reference for the marketplace.json plugin entry schema.

## File Location

```
fbcode/claude-templates/components/.claude-plugin/marketplace.json
```

## Top-Level Structure

```json
{
  "name": "claude-templates",
  "owner": {
    "name": "Claude Templates",
    "url": "https://fb.workplace.com/groups/claude.code.users"
  },
  "metadata": {
    "description": "Ready-to-use Claude Code templates",
    "version": "1.0.0"
  },
  "plugins": [
    { ... },
    { ... }
  ]
}
```

## Plugin Entry Schema

### Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Unique plugin identifier (lowercase-with-hyphens) |
| `source` | string | Path to plugin directory relative to marketplace.json |
| `version` | string | Semantic version (e.g., "1.0.0") |
| `description` | string | Brief description of plugin functionality |
| `author` | object | Author information |

### Author Object

```json
"author": {
  "name": "Your Name",
  "email": "you@meta.com"
}
```

### Optional Fields

| Field | Type | Description |
|-------|------|-------------|
| `homepage` | string | URL to plugin documentation |
| `repository` | string | URL to source repository (for open source) |
| `license` | string | License identifier (e.g., "MIT") |
| `strict` | boolean | Whether to enforce strict mode (default: true) |
| `keywords` | array | Search keywords for plugin discovery |
| `skills` | array | Paths to skill directories or files |
| `commands` | array | Paths to command files |
| `hooks` | object | Inline hook definitions |
| `agents` | array | Paths to agent definition files |
| `mcpServers` | array | Paths to MCP server configurations |

## Complete Example

```json
{
  "name": "my-plugin",
  "source": "./plugins/my-plugin",
  "version": "1.0.0",
  "description": "Complete description of what the plugin does, including key features and use cases",
  "author": {
    "name": "Your Name",
    "email": "you@meta.com"
  },
  "homepage": "https://www.internalfb.com/code/fbsource/fbcode/claude-templates/components/plugins/my-plugin/README.md",
  "repository": "https://github.com/user/repo",
  "license": "MIT",
  "strict": false,
  "skills": [
    "./skills/"
  ],
  "commands": [
    "./commands/"
  ],
  "agents": [
    "./agents/my-agent.md"
  ],
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
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/cleanup.py"
          }
        ]
      }
    ]
  },
  "mcpServers": [
    "./mcp/server.json"
  ],
  "keywords": [
    "keyword1",
    "keyword2",
    "keyword3"
  ]
}
```

## Field Details

### name

- Must be unique across all plugins
- Use lowercase letters, numbers, and hyphens only
- Should be descriptive but concise
- Examples: `source-control-at-meta`, `10x-engineer`, `claude-billing`

### source

- Path relative to the marketplace.json location
- Usually `./plugins/<plugin-name>`
- Can be `./` for plugins at the root level
- Must point to a directory containing plugin files

### version

- Follow semantic versioning: `MAJOR.MINOR.PATCH`
- Increment MAJOR for breaking changes
- Increment MINOR for new features
- Increment PATCH for bug fixes

### description

- Should explain what the plugin does
- Include key features and use cases
- Used for plugin discovery and search
- Keep under 500 characters for display purposes

### strict

- When `true` (default): Plugin operates in strict mode
- When `false`: Relaxed validation and permissions
- Set to `false` for plugins that need broader access

### skills

- Array of paths to skill directories or SKILL.md files
- Use `"./skills/"` to include all skills in the skills directory
- Use `"./skills/specific-skill/SKILL.md"` for individual skills

### commands

- Array of paths to command markdown files
- Use `"./commands/"` to include all commands in the commands directory
- Commands create slash commands: `/plugin-name:command-name`

### hooks

- Object defining inline hooks by event type
- Preferred over hooks/hooks.json file
- See [hooks-guide.md](hooks-guide.md) for detailed hook configuration

### agents

- Array of paths to agent definition files
- Agents become available as subagent types in the Task tool

### keywords

- Array of strings for plugin discovery
- Include relevant terms users might search for
- Good keywords: feature names, technologies, team names
- Keep to 5-15 relevant keywords

## Validation

Always validate after editing:

```bash
python3 -m json.tool components/.claude-plugin/marketplace.json > /dev/null && echo "Valid JSON"
```

## Adding a New Plugin Entry

1. Find the `plugins` array in marketplace.json
2. Add your entry at the end of the array (before the closing `]`)
3. Ensure proper comma placement (comma after previous entry, none after yours)
4. Validate JSON syntax
5. Test with `claude-templates plugin <name> install --dev`

## Common Mistakes

### Missing Comma

```json
// Wrong - missing comma before new entry
    }
    {
      "name": "new-plugin"

// Correct
    },
    {
      "name": "new-plugin"
```

### Invalid Source Path

```json
// Wrong - source doesn't exist
"source": "./plugin/my-plugin"

// Correct
"source": "./plugins/my-plugin"
```

### Trailing Comma

```json
// Wrong - trailing comma in array
"keywords": [
  "keyword1",
  "keyword2",
]

// Correct
"keywords": [
  "keyword1",
  "keyword2"
]
```
