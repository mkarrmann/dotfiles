---
name: tasks
display_name: Tasks
author: mhoyl
description: >
  Use when the user asks to create, list, search, update, close, tag, or inspect
  tasks, manage comments, or work with GSD projects, sections, milestones, goals,
  or smartfolios. Uses the Meta CLI (`meta tasks.*`) — covers ~20 entity types and ~90 commands. Pre-installed on devservers, on-demand instances, and laptops.
tags:
  - productivity
  - task-management
  - meta-tools
  - gsd
allowed-tools: Bash
---

# Meta CLI — Tasks Platform

Manage Meta internal tasks and the full GSD ecosystem. All commands live under the `meta tasks.*` namespace.

## Prerequisites

- `meta` is pre-installed on devservers, on-demand instances, and laptops (Mac/Windows). No installation needed.

## Quick Start — Common Commands

Common examples. Always run `--help` to discover available flags:

```bash
# List my open tasks
meta tasks.task list --owner-is-me --status-is=Open

# List someone else's open tasks
meta tasks.task list --owner-is=unixname --status-is=Open

# Get full details on a specific task
meta tasks.task describe --task=T256458061

# Create a task
meta tasks.task create --title="Do the thing" --owner=ayub --priority=MID

# Close a task with a comment
meta tasks.task update --task=T256458061 --close --comment="Done"

# Attach a file to a task (attachments go through a comment; use file:// prefix for local files)
meta tasks.comment create --task=T256458061 --text="See attached report" --attachment=file:///tmp/report.txt

# Add a comment
meta tasks.comment create --task=T256458061 --text="Update here"

# Delegate an existing task to an AI agent
meta tasks.task delegate --task=T256458061 --agent=claude --repo=fbsource

# Create a task and delegate it to an agent in one step
meta tasks.task create --title="Fix auth bug" --description="Detailed context..." --delegate-to-agent=devmate --context-artifact=D12345,T67890
```

## Command Pattern

**IMPORTANT:** The namespace is two levels: `tasks.<entity>`, not `tasks.<action>`. For example, use `meta tasks.task list`, NOT `meta tasks.list`.

```bash
meta tasks.<entity> <action> [--flags]

# Fallback if `meta` is not on PATH:
cd /data/sandcastle/boxes/fbsource/www && phpse prod MetaCLI tasks.<entity> <action> [--flags]
```

All commands support: `--output=json|table|yaml` (`-o`), `--verbose` (`-v`), `--help`.
List commands additionally support: `--limit=N` (`-l`), `--columns=col1,col2`.

**Always run `meta tasks.<entity> <action> --help` to discover flags before guessing.**

## Entity Types

| Entity | Actions | Description |
|--------|---------|-------------|
| `tasks.task` | archive, create, delegate, describe, history, list, merge, set-ubn, update | Core task CRUD — the primary entity for most operations |
| `tasks.task.tag` | ~~list, add, remove~~ | **Deprecated.** Use `tasks.task metadata` (tags in output) and `tasks.task update --add-tag=X` / `--remove-tag=X` instead |
| `tasks.task.project` | list, add, remove | Link/unlink tasks from GSD projects |
| `tasks.comment` | create, update, list, delete | Task comments |
| `tasks.gsd.project` | add-theme, create, delete, list, metadata, remove-theme, transfer, update | GSD Projects — planning containers |
| `tasks.gsd.task` | close, comment, create, dependency, diffs, list, metadata, move, subtask, update | GSD-aware task ops — richer than `tasks.task` (subtasks, diffs, deps, move) |
| `tasks.gsd.section` | create, update, list, delete | Sections within a GSD project |
| `tasks.gsd.milestone` | create, update, list, delete | Time-bound checkpoints |
| `tasks.gsd.team` | create, list, overview | GSD Teams |
| `tasks.gsd.theme` | create, update, list, delete | Strategic groupings across projects |
| `tasks.gsd.custom-field` | list, update | Project-specific metadata fields |
| `tasks.goal` | create, delete, linked-item, list, metadata, status-update, subgoals, update | Goals / OKRs |
| `tasks.goal.status-update` | create, update, list, delete, metadata, generate | Progress reports on goals |
| `tasks.smartfolio` | create, update, list, delete, metadata | Portfolio-level views |

**When to use `tasks.gsd.task` vs `tasks.task`:** Use `tasks.gsd.task` when working within a GSD project context — it supports subtask management, diffs, dependencies, and moving between sections. Use `tasks.task` for general task operations outside GSD.

## Enum Values

- **Priority:** `UNBREAK_NOW`, `HIGH`, `MID`, `LOW`, `WISHLIST`
- **Progress:** `NO_PROGRESS`, `BACKLOG`, `PLANNED`, `IN_PROGRESS`, `BLOCKED`, `CLOSED`
- **Size:** `EXTRA_SMALL`, `SMALL`, `MEDIUM`, `LARGE`, `EXTRA_LARGE`

## Behavioral Rules

1. **Always use `-o json`** when processing results programmatically.
2. **Always set `-l` (limit)** on list commands — the default is 10, which silently truncates.
3. **Task numbers accept both formats** — `T12345` and `12345`.
4. **Use `--help` first** — run `meta tasks.<entity> <action> --help` before constructing commands.
5. **Prefer `describe` over `list`** for single task lookups — richer detail.
6. **Run multiple fast calls in parallel** — commands complete in ~1-2s each.

7. **Attach files via `tasks.comment create --attachment`** — `tasks.task create`/`update` have NO attachment flag. To attach a file, post a comment: `meta tasks.comment create --task=T123 --text="..." --attachment=file:///path/to/file.png`. The `file://` prefix tells the thin client to read the file locally and forward it to the server; bare paths (without `file://`) only work with `meta --local`. Multiple files: `--attachment=file:///tmp/a.png,file:///tmp/b.png`. Files are uploaded and embedded inline as images in the comment.

8. **`{F}` references do NOT work in Tasks** — Phabricator file handles (`{F1234567}`) only render in Phabricator (diffs, pastes). They appear as raw text in task descriptions and comments. Always use `tasks.comment create --attachment` for task file uploads.

9. **Wrap task IDs in markdown links in user-facing output** — when emitting any task ID returned or referenced by a command (`create`, `describe`, `update`, list rows, narrative cross-references), wrap it as a markdown link so the user can click through to the task instead of copy/pasting the number. Format:

   ```
   [T<NUMBER>](https://www.internalfb.com/tasks/?t=<NUMBER>)
   ```

   Example: a freshly-created task with ID `T274244840` should be presented as `[T274244840](https://www.internalfb.com/tasks/?t=274244840)`, never bare `T274244840`. Applies to single-task contexts (create/describe/update output), each row of `list` output, and inline narrative cross-references the first time a task is mentioned.

10. **Bulk mutations require an exact reviewed set first.** Before closing, merging, tagging, or reassigning many tasks, list candidate IDs with the exact mutation filters, report the count and representative titles, and mutate only that captured set. If a stateful command fails, describe the task before retrying.

11. **Close recovered generated tasks with evidence.** If automation-created tasks are no longer actionable, close with `--close --outcome=already_fixed` and comment with recovery evidence and any root-cause task/post link. If there is a surviving root-cause task, merge into it instead.

## Deduplicating Tasks

When you want to deduplicate tasks, **merge — never just close.** Closing a duplicate marks it as "done" with no trail. Merging closes the source as "duplicate" and transfers its metadata (tags, subscribers, subtasks) to the surviving task.

```bash
# Merge duplicate into the real task
meta tasks.task merge --task=T<duplicate> --into=T<real>

# If the duplicate has subtasks that conflict, skip subtask transfer:
meta tasks.task merge --task=T<duplicate> --into=T<real> --no-subtasks --no-children
```

## Don'ts

- Do NOT use the Python `tasks` CLI, `gsd` CLI, or `tsk` CLI — use `meta tasks.*` instead.
- Do NOT omit `-l` on list commands — results silently truncate at 10.
- Do NOT guess flag names — always check `--help` first.
- Do NOT use MCP `knowledge_load` or `knowledge_filtered_search` for task lookups that `meta tasks.task describe` can handle.
- Do NOT close duplicate tasks with `--progress=CLOSED` — use `meta tasks.task merge` instead.
