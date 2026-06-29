---
name: gsd
description: The GSD (Get Stuff Done) CLI tool for managing Meta's internal project management system. Use this skill to create, update, query, and manage teams, themes, projects, sections, tasks, sprints, milestones, status reports, portfolio status reports, and OKRs via command line. Themes provide top-level organization for grouping related projects.
allowed-tools: Bash(buck2:*), Read, Grep, Glob
---

## Overview

The GSD CLI is a Rust-based command-line tool for interacting with Meta's GSD
(Get Stuff Done) project management system. It provides programmatic access to
all major GSD operations through the internal GraphQL API.

## IMPORTANT: Alias Conflict and Binary Path

**On many OD machines, oh-my-zsh's git plugin creates an alias `gsd='git svn dcommit'`** which shadows the actual GSD CLI.

Binary paths:
- **Mac**: `/opt/facebook/bin/gsd`
- **Devservers/Linux/OD**: `/usr/local/bin/gsd`
- **Windows**: `C:\ProgramData\chocolatey\bin\gsd.exe`

**Detection and workaround (Mac/Linux):**
```bash
# Check if aliased
type gsd

# Find actual binary
which gsd
ls -la /opt/facebook/bin/gsd /usr/local/bin/gsd 2>/dev/null

# Use full path to avoid alias conflicts (recommended)
/opt/facebook/bin/gsd <command>  # Mac
/usr/local/bin/gsd <command>     # Devservers

# Or unalias for session
unalias gsd 2>/dev/null
```

**Windows:** No alias conflict. `gsd` works directly if Chocolatey's bin directory is in `PATH` (it is by default). The full path `/c/ProgramData/chocolatey/bin/gsd.exe` also works from bash.

## Installation

```bash
devfeature install --persist gsd
```

## Core Concepts

### GSD Hierarchy

- **Portfolio** → **Team** → **Theme** (optional) → **Project** → **Section**
  (optional) → **Task**
- Teams are the primary organizational unit
- Projects belong to teams and can optionally be grouped by themes
- Sections subdivide projects (optional)
- Tasks are the work items

### Output Format

- All commands output JSON by default
- Use `jq` for filtering and formatting results
- Task IDs in API responses have two fields:
  - `id`: Infrastructure ID (e.g., "1567364077929152") - use this for mutations
  - `prefixed_number`: Human-readable (e.g., "T{number}") - for display only

### Valid Enum Values

| Field | Valid Values | Aliases |
|-------|-------------|---------|
| **Priority** | `UNKNOWN`, `UNBREAK_NOW`, `HIGH`, `MID`, `LOW`, `WISHLIST` | `UBN` → UNBREAK_NOW, `MEDIUM` → MID |
| **PlannablePriority** | `NONE`, `P0`, `P1`, `P2` | - (used for sections, projects, themes) |
| **Status** | `NO_PROGRESS`, `BACKLOG`, `PLANNED`, `IN_PROGRESS`, `BLOCKED`, `CLOSED` | - |
| **Size** | `UNKNOWN`, `EXTRA_SMALL`, `SMALL`, `MEDIUM`, `LARGE`, `EXTRA_LARGE` | `XS`, `S`, `M`, `L`, `XL` |

All values are case-insensitive (e.g., `high`, `HIGH`, and `High` all work).

## Parsing GSD URLs

GSD URLs follow the pattern `https://www.internalfb.com/gsd/<team_id>/<project_id>[/<page>]`. Extract the numeric path segments as `team_id` and `project_id` for use with CLI commands.

## User Identity

```bash
# Get your username
whoami

# Search for tasks you own (uses username)
gsd task search --project-id <PROJECT_ID> --owner $(whoami)
```

Most commands auto-default the owner to the current user (`task create`, `milestone create`, `status-report create-draft`). FBID lookup is only needed when assigning to someone else — see `references/workflows.md` for details.

## Reference Files

Read these files for detailed syntax, examples, and flags when working with specific entities:

| File | When to Read | Contents |
|------|-------------|----------|
| `references/tasks.md` | Task CRUD, search, filtering, relationships | task search/list/get/create/update/move/close, subtasks, blocking, tags, diffs, team intake, sprint assignment |
| `references/entities.md` | Team/theme/project/section/sprint/milestone ops | team list/get/create, theme CRUD + move-project, project CRUD, section CRUD, sprint CRUD + rename/delete/tasks, milestone create/delete/rename/edit-date/edit-owner/update-status/add-blockers/remove-blockers |
| `references/reporting.md` | Status reports, portfolio status reports, OKRs, diff search | status-report list/get/templates/create-draft/publish/delete, portfolio-status-report list/get/create, OKR get/get-projects/get-dependent-tasks/get-okrs/create-update/get-children/get-hierarchy/attach-project/detach-project/attach-tasks/detach-tasks, diff list |
| `references/workflows.md` | Multi-step workflows, jq patterns, troubleshooting | Project setup, sprint mgmt, status reporting, task filtering workflows, jq examples, date formats, markdown in reports, FBID lookup, error handling, troubleshooting |
| `references/graphql.md` | GraphQL operations not available via CLI | Custom field creation, milestone deletion (no 2FA), milestone blockers via GraphQL, FBID lookup, schema discovery |

## Command Quick Reference

### Team
| Operation | Command |
|-----------|---------|
| List teams | `gsd team list` |
| Get team | `gsd team get <TEAM_ID>` |
| Create team | `gsd team create --name "Name" --members <IDs>` |

### Theme
| Operation | Command |
|-----------|---------|
| List themes | `gsd theme list --team-id <ID>` |
| Get theme | `gsd theme get <ID> [--team-id <TEAM_ID>] [--include-archived]` |
| Create theme | `gsd theme create --name "Name" --team-id <ID>` |
| Update theme | `gsd theme update <ID> [--name] [--priority]` |
| Move project | `gsd theme move-project --project-id <ID> --theme-id <ID> --team-id <ID>` |
| Archive theme | `gsd theme archive <ID>` |
| Unarchive theme | `gsd theme unarchive <ID>` |

### Project
| Operation | Command |
|-----------|---------|
| List projects | `gsd project list [--team-id <ID>] [--filter owned\|shared\|contributed] [--owner <unixname\|fbid>]` |
| Get project | `gsd project get <PROJECT_ID>` |
| Create project | `gsd project create --name "Name" --team-id <ID>` |
| Update project | `gsd project update <ID> [--name] [--status] [--priority]` |
| Attach resource link | `gsd project attach-resource <ID> --url <URL> --name "Name"` |
| List resource links | `gsd project list-resources <ID>` |
| Detach resource link | `gsd project detach-resource <ID> --destination-id <NODE_ID>` or `--url <URL>` or `--name "Name"` |
| Add comment | `gsd project add-comment <ID> --text '...'` |
| Archive project | `gsd project archive <ID> [--close-tasks]` |
| Unarchive project | `gsd project unarchive <ID>` |

### Section
| Operation | Command |
|-----------|---------|
| List sections | `gsd section list --project-id <ID>` |
| Get section | `gsd section get <SECTION_ID>` |
| Create section | `gsd section create --name "Name" --project-id <ID>` |
| Update section | `gsd section update <ID> [--name] [--priority]` |
| Delete section | `gsd section delete <ID>` |

### Portfolio
| Operation | Command |
|-----------|---------|
| Get portfolio | `gsd portfolio get <PORTFOLIO_ID>` |
| List portfolios | `gsd portfolio list` |
| Update portfolio | `gsd portfolio update <ID> [--name] [--add-projects <IDs>] [--add-teams <IDs>] [--add-portfolios <IDs>] [--remove-projects <IDs>] [--remove-teams <IDs>] [--remove-portfolios <IDs>]` |

### Portfolio Status Report
| Operation | Command |
|-----------|---------|
| List | `gsd portfolio-status-report list --portfolio-id <ID> [--limit N]` |
| Get | `gsd portfolio-status-report get <REPORT_ID>` |
| Create | `gsd portfolio-status-report create --portfolio-id <ID> --status <STATUS> --description "..."` |

### Task
| Operation | Command |
|-----------|---------|
| **Search** | `gsd task search --project-id <ID> [--title] [--status] [--priority] [--owner] [--tags]` |
| List | `gsd task list --project-id <ID>` |
| Get | `gsd task get <ID> [--include-subtasks] [--include-parent] [--include-blocking] [--include-flytrap] [--text-description] [--include-comments [N]]` |
| Create | `gsd task create --title "Title" --project-id <ID> [--section-id] [--priority] [--size]` |
| Update | `gsd task update <ID> [--title] [--status] [--priority] [--add-tags] [--add-diffs] ...` |
| Move | `gsd task move <ID> --to-section-id <ID>` or `--to-project-id <ID>` |
| Close | `gsd task close <ID>` |
| Add to sprint | `gsd task add-to-sprint <ID> --sprint-ids <IDs>` |
| Remove from sprint | `gsd task remove-from-sprint <ID> --sprint-ids <IDs>` |
| Add comment | `gsd task add-comment <ID> --text '...'` |
| Add to project | `gsd task add-to-project --task-ids <IDs> --project-id <ID>` |
| List attachments | `gsd task list-attachments <ID> [--file-name "name"] [--file-name-prefix "prefix"] [--limit N]` |
| Get attachment | `gsd task get-attachment <ATTACHMENT_ID>` |
| Attach resource link | `gsd task attach-resource <ID> --url <URL> [--name "Name"]` |
| List resource links | `gsd task list-resources <ID>` |

### Sprint
| Operation | Command |
|-----------|---------|
| List sprints | `gsd sprint list --team-id <ID>` |
| Get sprint | `gsd sprint get <SPRINT_ID>` |
| List sprint tasks | `gsd sprint tasks <SPRINT_ID> [--limit N]` |
| Create sprint | `gsd sprint create --team-id <ID> --name "Name" --start-date --target-date` |
| Update sprint | `gsd sprint update <ID> [--name] [--start-date] [--target-date]` |
| Rename sprint | `gsd sprint rename <ID> --name "Name"` |
| Delete sprint | `gsd sprint delete <ID>` |

### Milestone
| Operation | Command |
|-----------|---------|
| Create | `gsd milestone create --plannable-id <ID> --name "Name" --date YYYY-MM-DD` |
| Delete | `gsd milestone delete <ID>` |
| Rename | `gsd milestone rename <ID> --name "Name"` |
| Edit date | `gsd milestone edit-date <ID> --date YYYY-MM-DD` |
| Edit owner | `gsd milestone edit-owner <ID> --owner-id <USER_ID>` |
| Update status | `gsd milestone update-status <ID> --status <STATUS>` |
| Add blockers | `gsd milestone add-blockers --milestone-id <ID> --blocker-ids T123,M456` |
| Remove blockers | `gsd milestone remove-blockers --milestone-id <ID> --blocker-ids T123,M456` |

### Status Report
| Operation | Command |
|-----------|---------|
| List | `gsd status-report list --project-id <ID>` |
| List drafts | `gsd status-report list-drafts --project-id <ID> [--limit N]` |
| Generate AI report | `gsd status-report generate-ai --reportable-id <ID> [--prompt "..."] [--agent NAME] [--changes-since-last-report]` |
| Get | `gsd status-report get <ID>` |
| Templates | `gsd status-report templates --team-id <ID>` |
| Create draft | `gsd status-report create-draft --project-id <ID> --status <STATUS> --description "..."` |
| Create from template | `gsd status-report create-draft-from-template --project-id <ID> --template-id <ID> --status <STATUS>` |
| Update draft | `gsd status-report update-draft <ID> --description "..."` |
| Publish | `gsd status-report publish <ID>` |
| Delete draft | `gsd status-report delete-draft <ID>` |

### OKR
| Operation | Command |
|-----------|---------|
| List OKRs | `gsd okr list --team-id <ID> --start YYYY-MM-DD --end YYYY-MM-DD` |
| Get | `gsd okr get <OKR_ID>` |
| Get projects | `gsd okr get-projects <OKR_ID>` |
| Get dependent tasks | `gsd okr get-dependent-tasks <OKR_ID>` |
| Get OKRs for project | `gsd okr get-okrs --project-id <ID>` |
| Create update | `gsd okr create-update <OKR_ID> --content "..."` |
| Get children | `gsd okr get-children --okr-id <ID>` |
| Get hierarchy | `gsd okr get-hierarchy --okr-id <ID> [--depth N]` |
| Attach project | `gsd okr attach-project <OKR_ID> --project-id <ID>` |
| Detach project | `gsd okr detach-project <OKR_ID> --project-id <ID>` |
| Attach tasks | `gsd okr attach-tasks <OKR_ID> --task-ids <IDs>` |
| Detach tasks | `gsd okr detach-tasks <OKR_ID> --task-ids <IDs>` |

### Diff
| Operation | Command |
|-----------|---------|
| List project diffs | `gsd diff list --project-id <ID>` |

## Critical Gotchas

1. **`id` vs `prefixed_number`**: Use `id` (infrastructure ID) for mutations, `prefixed_number` (T-number) for display only.

2. **Tags NOT in task create**: `--tags` is NOT supported in `gsd task create`. Use a two-step process: create task, then `gsd task update <ID> --add-tags tag1,tag2`.

3. **`--add-diffs` is plural**: Use `--add-diffs D123,D456`, NOT `--add-diff`. Similarly `--remove-diffs`.

4. **`--remove-from-parent` on the subtask**: Call on the subtask itself, NOT the parent task.

5. **`taskscli-agent-mutation-enabled` tag**: Required on tasks for CLI updates on OD machines.

6. **`task search` over `task list`**: Use `task search` for filtering — `task list` filtering by `--priority`/`--status` is deprecated (client-side, slow). Search defaults to `--status OPEN` if not specified; use `--status CLOSED` to include closed tasks. Output uses the same paginated format as `task list` (`items` array with `pagination` metadata).

7. **Owner auto-defaults**: `task create`, `milestone create`, and `status-report create-draft` auto-assign to current user. No need to look up your FBID.

8. **`--plannable-id` for milestones**: Use `--plannable-id` (not `--project-id`) when creating milestones.

9. **Milestone updates via subcommands**: No single `milestone update` — use `rename`, `edit-date`, `edit-owner`, `update-status` separately.

10. **Suppress ODS errors**: Use `2>/dev/null` when piping to jq to suppress ODS connection errors to stderr.

11. **`task update` cannot change project/section**: To add existing tasks to a project or section, use `gsd task add-to-project`. To move between projects/sections, use `gsd task move`. Do NOT try flags like `--add-project` or `--section-id` on `task update` — they don't exist and will produce confusing errors.

12. **GSD project permission errors (`AlwaysDenyRule`)**: If a GraphQL error contains `EntTasksGSDProjectPermissionPolicy:AlwaysDenyRule`, the current user is **not a member** of the target GSD project. The fix is to join the project via the GSD web UI (`https://www.internalfb.com/gsd/<team_id>/<project_id>/list`), then retry. This affects `task create --project-id/--section-id`, `task add-to-project`, and `task move`.

13. **`project list --owner`**: Use `--owner <unixname|fbid>` to list another user's projects. This queries the target user's owned/shared/contributed projects directly — no need to iterate through team themes. When `--owner` is provided, `--team-id`, `--theme-id`, `--limit`, and `--all` are ignored. The `--filter` flag still works to narrow by relationship type.

14. **Archive vs COMPLETED status**: `gsd project archive` sets `is_closed: true` (hides from default views). `gsd project update --status COMPLETED` only changes the progress status indicator without archiving. Use `archive` to hide projects/themes from the active view.

## Shell Escaping for Rich Text

Comments, descriptions, and status reports often contain markdown, backticks, parentheses, and special characters. Use these patterns to avoid shell interpretation issues:

**Single quotes** prevent all shell interpretation (recommended for simple text):
```bash
gsd task add-comment T123 --text 'Fixed bug in CosmoConsolePanel.js by changing resetUiState()'
```

**Heredoc** for multi-line or complex markdown:
```bash
gsd task add-comment T123 --text "$(cat <<'EOF'
## Summary
Fixed the `resetUiState` function in `CosmoConsolePanel.js`.

Changes:
- Preserved counts during filter operations
- Added `shouldUpdateCounts=false` to callbacks
EOF
)"
```

**Avoid double quotes with backticks** — the shell interprets them as command substitution:
```bash
# WRONG - shell tries to execute `resetUiState` as a command
gsd task add-comment T123 --text "Fixed `resetUiState` function"

# RIGHT - single quotes prevent interpretation
gsd task add-comment T123 --text 'Fixed `resetUiState` function'
```

This applies to all text flags: `--text`, `--description`, and `--title`.

## Limitations

### Not Supported via CLI

- **Project delete**: No GraphQL mutation exists (web UI only)
- **Theme delete**: Not supported (use web UI)
- **Custom fields**: Not implemented

### Performance Considerations

- **Use `task search` for filtering**: Server-side Power Search filtering is much faster than client-side
- **`task list` filtering is deprecated**: `--priority` and `--status` in `task list` fetch all tasks then filter client-side
- Large projects may have slow query times with `task list`
- Use pagination where available (`--limit` parameter in search)

## Additional Resources

- **GSD Web UI**: https://www.internalfb.com/gsd/
- **GSD Automation with Claude Code Guide**: https://www.internalfb.com/wiki/RL_AR/Experiences/Foundational_Experiences/FX_Telemetry_Team/AI4P/GSD_Automation_with_Claude_Code/
- **Source Code**: `fbcode/tools/gsd/`
- **Implementation Plan**: `fbcode/tools/gsd/PLAN.md`
- **Phase Summaries**: `fbcode/tools/gsd/PHASE*_SUMMARY.md`
- **GSD Wiki**: https://www.internalfb.com/wiki/GSD/
