# GSD Task Operations Reference

Complete reference for task search, CRUD, relationships, tags, diffs, and team intake.

## Task Search (Recommended for Filtering)

Use `task search` for efficient server-side filtering with Power Search.
Search defaults to `--status OPEN` (showing only open tasks). Use `--status CLOSED` to search closed tasks.
Output uses the same paginated format as `task list` (`items` array with `pagination` metadata).

```bash
# Search by project (defaults to open tasks)
gsd task search --project-id <PROJECT_ID>

# Search by title text
gsd task search --project-id <PROJECT_ID> --title "API refactoring"

# Search by full text (title + description)
gsd task search --project-id <PROJECT_ID> --text "performance optimization"

# Search closed tasks
gsd task search --project-id <PROJECT_ID> --status CLOSED

# Filter by specific progress status (client-side post-filter)
gsd task search --project-id <PROJECT_ID> --progress IN_PROGRESS
gsd task search --project-id <PROJECT_ID> --progress BLOCKED

# Filter by priority
gsd task search --project-id <PROJECT_ID> --priority HIGH

# Filter by owner (unixname or user ID)
gsd task search --project-id <PROJECT_ID> --owner johndoe

# Filter by tags
gsd task search --project-id <PROJECT_ID> --tags urgent,backend

# Combine multiple filters
gsd task search \
  --project-id <PROJECT_ID> \
  --title "refactor" \
  --priority HIGH

# Default limit is 100 — for large projects (>100 tasks) always pass --limit 500.
# Truncation is silent: the API returns no warning and pagination metadata is
# unreliable (hasNextPage and total reflect only the returned count, not the real total).
gsd task search --project-id <PROJECT_ID> --limit 500
```

**Search options:**
- `--project-id`: Filter by GSD project ID
- `--section-id`: Filter by GSD section ID
- `--title`: Search text in task title
- `--text`: Search text in title and description
- `--owner`: Filter by owner (unixname or FBID)
- `--priority`: Filter by priority (UNKNOWN, UNBREAK_NOW, HIGH, MID, LOW, WISHLIST)
- `--status`: Server-side filter (OPEN or CLOSED). Defaults to OPEN if not specified.
- `--progress`: Client-side filter by progress status (NO_PROGRESS, BACKLOG, PLANNED, IN_PROGRESS, BLOCKED, CLOSED)
- `--tags`: Filter by tag names (comma-separated)
- `--limit`: Maximum results (default: 100). Cursor-based pagination is not supported — use a higher limit to fetch more results.

## Task List

```bash
# List all tasks in a project
gsd task list --project-id <PROJECT_ID>

# List tasks in a specific section
gsd task list --section-id <SECTION_ID>
```

> **Note:** Filtering with `--priority` or `--status` in `task list` is deprecated.
> Use `gsd task search` instead for better performance with server-side filtering.

## Task Get

Use `gsd task get` to retrieve a specific task by ID:

```bash
# Get basic task details
gsd task get <TASK_ID>

# Get task with subtasks expanded
gsd task get <TASK_ID> --include-subtasks

# Get task with parent info (if it's a subtask)
gsd task get <TASK_ID> --include-parent

# Get task with blocking relationships
gsd task get <TASK_ID> --include-blocking

# Get Flytrap bug report data (attachments, description, submission time)
gsd task get <TASK_ID> --include-flytrap

# Get task comments (actor, timestamp, content; default: last 100)
gsd task get <TASK_ID> --include-comments

# Get last 10 comments
gsd task get <TASK_ID> --include-comments 10

# Combine multiple flags
gsd task get <TASK_ID> --include-subtasks --include-parent --include-blocking

# Examples:
gsd task get T243726663 --include-subtasks
# Returns: subtasks.count, subtasks.nodes[] with each subtask's details

gsd task get T243726352 --include-parent
# Returns: subtask_parent with parent task info if this is a subtask

# Extract title and description
gsd task get T123456 | jq -r '{
  title: .data.node.task_title,
  description: .data.node.task_description_rte.rte_content_markdown,
  status: .data.node.task_progress_status
}'
```

**Response fields:**
- Basic: `id`, `prefixed_number`, `task_title`, `task_priority`, `task_progress_status`, `task_owner`, `num_subtasks`, `is_subtask_parent`
- With `--include-subtasks`: `subtasks.count`, `subtasks.nodes[]` (each with title, status, priority, owner)
- With `--include-parent`: `subtask_parent` (id, prefixed_number, title, status)
- With `--include-blocking`: `task_parents` (tasks blocking this), `task_children` (tasks this blocks)
- With `--include-flytrap`: `task_type_configuration.task_type_context` with Flytrap bug report fields (only populated for Flytrap-originated tasks):
  - `flytrap_bug_report_mv_id` — Bug report ID
  - `flytrap_bug_report_mv_description` — Bug description
  - `flytrap_bug_report_mv_report_submission_time` — Submission timestamp
  - `flytrap_bug_report_mv_attachments.nodes[]` — Manifold attachments (`id`, `name`, `cdn_url`, `mime_type`, `size`)
  - `flytrap_bug_agent_output` — OpsMate cached analysis
- With `--include-comments`: `intern_activity_comments.count`, `intern_activity_comments.nodes[]` (each with `id`, `created_time`, `activity_actor.{id, name}`, `rte_content.{rte_content_plain_text, rte_content_markdown}`)

**Important:** Task descriptions are stored in rich text format. Access them via:
- `.data.node.task_description_rte.rte_content_markdown` - Markdown formatted
- `.data.node.task_description_rte.rte_content_plain_text` - Plain text

**Note:** Always use `gsd task get` to read task details. Do NOT use `knowledge_load` or fetch task URLs directly - the CLI provides all necessary task information.

## Task Create

```bash
# Create task in a project (minimal)
gsd task create \
  --title "Task Title" \
  --project-id <PROJECT_ID>

# Create task with all options
gsd task create \
  --title "Task Title" \
  [--description "Description"] \
  [--project-id <PROJECT_ID>] \
  [--section-id <SECTION_ID>] \
  [--priority UNKNOWN|UNBREAK_NOW|HIGH|MID|LOW|WISHLIST] \
  [--size UNKNOWN|EXTRA_SMALL|SMALL|MEDIUM|LARGE|EXTRA_LARGE] \
  [--owner-id <USER_ID>] \
  [--owning-team-id <TEAM_NAME_OR_ID>]

# Create task directly in a section
gsd task create \
  --title "Task Title" \
  --section-id <SECTION_ID>

# Create task with owning team for Team Intake
gsd task create \
  --title "Task Title" \
  --project-id <PROJECT_ID> \
  --owning-team-id "Horizon Platform SDK"
```

**Notes:**
- If `--owner-id` is not specified, the task is assigned to the **current user** automatically
- Tags cannot be added during creation. Use `gsd task update --task-id <ID> --add-tags tag1,tag2` after creating the task

## Task Update

```bash
gsd task update <TASK_ID> \
  [--title "New Title"] \
  [--description "New Description"] \
  [--priority UNKNOWN|UNBREAK_NOW|HIGH|MID|LOW|WISHLIST] \
  [--status NO_PROGRESS|BACKLOG|PLANNED|IN_PROGRESS|BLOCKED|CLOSED] \
  [--size UNKNOWN|EXTRA_SMALL|SMALL|MEDIUM|LARGE|EXTRA_LARGE] \
  [--owner-id <USER_ID>] \
  [--remove-owner] \
  [--owning-team-id <TEAM_NAME_OR_ID>] \
  [--start-date YYYY-MM-DD] \
  [--target-date YYYY-MM-DD] \
  [--add-tags tag1,tag2] \
  [--remove-tags tag3] \
  [--add-blocking T123,M456] \
  [--add-blocked-by T789] \
  [--remove-blocking T123,M456] \
  [--remove-blocked-by T789] \
  [--add-subtask T111,T222] \
  [--remove-from-parent] \
  [--add-diffs D123,D456] \
  [--remove-diffs D789] \
  [--add-to-section <SECTION_ID>[,<SECTION_ID2>]] \
  [--remove-from-section <SECTION_ID>[,<SECTION_ID2>]] \
  [--add-to-project <PROJECT_ID>[,<PROJECT_ID2>]] \
  [--remove-from-project <PROJECT_ID>[,<PROJECT_ID2>]]

# Example: Update priority, status, and dates
gsd task update T000000000 \
  --priority HIGH \
  --status IN_PROGRESS \
  --size L \
  --start-date 2025-12-01 \
  --target-date 2025-12-31

# Example: Add blocking relationships (T123 blocks T456 and T789)
gsd task update T123 --add-blocking T456,T789

# Example: Mark task as blocked by another task
gsd task update T456 --add-blocked-by T123

# Example: Add subtasks to a parent task
gsd task update T123 --add-subtask T456,T457

# Example: Remove a task from its parent (make standalone)
gsd task update T456 --remove-from-parent

# Example: Manage tags
gsd task update T123 \
  --add-tags urgent,backend \
  --remove-tags low-priority

# Example: Link Phabricator diffs to a task
gsd task update T123 --add-diffs D91781174,D91234567

# Example: Remove diff links from a task
gsd task update T123 --remove-diffs D91781174

# Example: Assign task to a team for Team Intake
gsd task update T123 --owning-team-id "Horizon Platform SDK"

# Example: Remove task owner (set as up for grabs)
gsd task update T123 --remove-owner

# Example: Add task to a section
gsd task update T123 --add-to-section <SECTION_ID>

# Example: Remove task from a section
gsd task update T123 --remove-from-section <SECTION_ID>

# Example: Move task to a section and update status in one command
gsd task update T123 --add-to-section <SECTION_ID> --status IN_PROGRESS

# Example: Add task to a project
gsd task update T123 --add-to-project <PROJECT_ID>

# Example: Remove task from a project
gsd task update T123 --remove-from-project <PROJECT_ID>
```

> **Note:** The `--task-id` flag is also supported for backwards compatibility:
> `gsd task update --task-id T123 --status IN_PROGRESS`

## Task Move

```bash
# Move to a different section
gsd task move <TASK_ID> --to-section-id <SECTION_ID>

# Move to a different project
gsd task move <TASK_ID> --to-project-id <PROJECT_ID>
```

## Task Close

```bash
gsd task close <TASK_ID>
```

## Task Add Comment

```bash
# Add a comment to a task
gsd task add-comment <TASK_ID> --text 'Comment text here'

# Alternative: use --task-id flag
gsd task add-comment --task-id T123456 --text 'Comment text here'

# Multi-line or markdown comments (use heredoc)
gsd task add-comment T123456 --text "$(cat <<'EOF'
## Status Update
Fixed the `resetUiState` function.

Changes:
- Preserved counts during filter operations
- Added `shouldUpdateCounts=false` to callbacks
EOF
)"
```

**Important:** Use single quotes for text containing backticks, parentheses, or other shell-special characters. See the "Shell Escaping for Rich Text" section in SKILL.md for details.

## Add/Remove Task from Sprint

```bash
# Add to one sprint
gsd task add-to-sprint <TASK_ID> --sprint-ids <SPRINT_ID>

# Add to multiple sprints (comma-separated)
gsd task add-to-sprint <TASK_ID> --sprint-ids <SPRINT_ID1>,<SPRINT_ID2>

# Remove from sprint
gsd task remove-from-sprint <TASK_ID> --sprint-ids <SPRINT_ID>
```

## Add Task(s) to Project/Section

```bash
# Add multiple tasks to a project
gsd task add-to-project --task-ids T123456,T789012 --project-id <PROJECT_ID>

# Add multiple tasks to a section (takes precedence over --project-id)
gsd task add-to-project --task-ids T123456,T789012 --section-id <SECTION_ID>

# Add tasks to a section by name (creates the section if it doesn't exist)
gsd task add-to-project --task-ids T123456,T789012 --project-id <PROJECT_ID> --section-name "My Section"
```

## Task Relationships

### Blocking/Blocked-by

Tasks can block other tasks, creating dependency chains. Use these to indicate that one task must be completed before another can proceed.

```bash
# Make T123 block T456 (T456 cannot proceed until T123 is done)
gsd task update T123 --add-blocking T456

# Mark T456 as blocked by T123 (equivalent to above, from the other side)
gsd task update T456 --add-blocked-by T123

# Add multiple blocking relationships at once
gsd task update T123 --add-blocking T456,T457,T458

# Remove blocking relationships
gsd task update T123 --remove-blocking T456
gsd task update T456 --remove-blocked-by T123

# Make T123 block milestone M456 (uses TimelineBlockers API automatically)
gsd task update T123 --add-blocking M456

# Remove milestone blocking
gsd task update T123 --remove-blocking M456
```

> **Note:** `--add-blocking` and `--remove-blocking` auto-detect milestones (M-prefixed IDs or FBIDs) and route to the TimelineBlockers API. `--add-blocked-by` and `--remove-blocked-by` remain task-only.

### Subtask Relationships

Tasks can have subtasks, creating a parent-child hierarchy. Subtasks cannot have their own subtasks (only one level deep).

```bash
# Add subtasks to a parent task
gsd task update T123 --add-subtask T456,T457

# Remove a task from its parent (make it standalone)
gsd task update T456 --remove-from-parent
```

**Note:** The `--remove-from-parent` flag is called on the **subtask** itself, not the parent.

## Task Tags

Tags help categorize and filter tasks. Tags are comma-separated strings.

**IMPORTANT: `--tags` is NOT supported in `gsd task create`.** You must use a two-step process:

```bash
# Step 1: Create the task
gsd task create \
  --title "My Task" \
  --project-id <PROJECT_ID>

# Step 2: Add tags via update
gsd task update T<TASK_NUMBER> \
  --add-tags tag1,tag2
```

**Special Tag: `taskscli-agent-mutation-enabled`**

Add this tag to tasks you want to update via CLI on OD machines. Without this tag, CLI updates may fail:

```bash
# Add the tag to enable CLI updates
gsd task update T123456 \
  --add-tags taskscli-agent-mutation-enabled
```

**Other tag operations:**

```bash
# Add tags to existing task
gsd task update T123 --add-tags urgent,needs-review

# Remove tags from task
gsd task update T123 --remove-tags low-priority

# Add and remove in same command
gsd task update T123 --add-tags urgent --remove-tags low-priority
```

## Task Diffs

Link Phabricator diffs (code reviews) to tasks to track implementation progress.

**IMPORTANT: Use `--add-diffs` (plural), NOT `--add-diff`:**

```bash
# Correct: --add-diffs (plural)
gsd task update T123 --add-diffs D91781174,D91234567

# Wrong: --add-diff (will error)
# gsd task update T123 --add-diffs D91781174

# Remove diff links from a task
gsd task update T123 --remove-diffs D91781174

# Combine with other updates
gsd task update T123 --status IN_PROGRESS --add-diffs D91781174

# Query diffs linked to a task (use task get with jq)
gsd task get T123 | jq '.data.node.linked_diffs'
```

**Note:** Linking diffs to tasks makes them visible in the GSD web UI under the task's "Diffs" tab, and in Phabricator under the diff's "Tasks" field.

## Team Intake (Owning Team)

Tasks can be assigned an "owning team" for Team Intake workflow. This makes tasks appear in the team's Intake inbox for triage, separate from individual task ownership.

**Key concepts:**
- **Owning Team**: Shared team ownership for triage and intake workflows
- **Owner**: Individual person responsible for the task
- **Team Intake Inbox**: Tasks with an owning team appear here for team triage

**Set owning team:**

```bash
# By team name (case-insensitive, spaces/underscores interchangeable)
gsd task update T123 --owning-team-id "Horizon Platform SDK"
gsd task update T123 --owning-team-id "horizon_platform_sdk"

# By team FBID
gsd task update T123 --owning-team-id 875258017650217

# When creating a task
gsd task create \
  --title "New intake task" \
  --project-id <PROJECT_ID> \
  --owning-team-id "My Team Name"
```

**View owning team:**

```bash
# Get task details (includes owning_team in response)
gsd task get T123 | jq '.data.node.owning_team'

# Returns: {"id": "875258017650217", "name": "Horizon Platform SDK"}
```

**Find available teams:**

```bash
# List all teams you have access to
gsd team list | jq '.data.tasks_gsd_teams.nodes[] | {id, name}'
```

**Note:** The `--owning-team-id` flag accepts either a team name or FBID. Team names are resolved case-insensitively, and spaces/underscores are treated equivalently.

## Task Attachments

### Attach a Resource Link (URL)

Attach a URL to a task as a resource link — the same thing the "Add link" option does in the task UI's Attach menu. This is for **links** (docs, diffs, dashboards, Workplace posts, wikis), not file uploads.

```bash
# Attach a link with an explicit display name
gsd task attach-resource T123456 --url "https://www.internalfb.com/intern/wiki/Example/" --name "Design Doc"

# Name is optional — defaults to the URL when omitted
gsd task attach-resource T123456 --url "https://docs.google.com/document/d/abc123/edit"

# --task-id flag form also works
gsd task attach-resource --task-id T123456 --url "https://fburl.com/abc" --name "Runbook"
```

**Options:**
- `--url` (required): the URL to link.
- `--name` (optional): display label for the link. Defaults to the URL if omitted.

**Notes:**
- Uses the contextual work graph linker (`xfb_contextual_work_graph_link_underlying_node_identifier_nodes`) — the same mutation that powers project resource links (`gsd project attach-resource`). The task FBID is the link origin.
- To link a **Phabricator diff** to a task, prefer `gsd task update <ID> --add-diffs D123` (structured diff link), not a raw URL.
- There is currently no CLI to **upload a file** attachment to a task (the `list-attachments`/`get-attachment` commands below are read-only).

### List Resource Links

List the URL resource links attached to a task (the counterpart to `attach-resource`). This reads the contextual work graph links and filters out work items (diffs, tasks, projects, sections) so only document/URL links remain — mirroring `gsd project list-resources`.

```bash
gsd task list-resources T123456
```

**Response** is a `resources` array, each entry with `id`, `type`, `name`, and `url`:

```bash
gsd task list-resources T123456 | jq '.data.resources[] | {name, url}'
```

**Note:** this lists URL **links** only. For uploaded **file** attachments use `list-attachments` (below). Use `list-resources` before `attach-resource` if you need to avoid adding a duplicate URL.

### List Attachments

List all attachments on a task:

```bash
# List all attachments on a task
gsd task list-attachments T123456

# Filter by exact file name
gsd task list-attachments T123456 --file-name "screenshot.png"

# Filter by file name prefix
gsd task list-attachments T123456 --file-name-prefix "error_log"

# Limit results
gsd task list-attachments T123456 --limit 5
```

**Response** includes attachment metadata: `id`, `file_name`, `mime_type`, `file_size`, `file_status`, `img_width`, `img_height`, `attachment_url`, `download_attachment_uri`, `thumbnail_url`, `created_at`.

**Parse with jq:**

```bash
# List attachment names and sizes
gsd task list-attachments T123456 | jq '.data.node.task_internal_attachments.nodes[] | {file_name, file_size, mime_type}'

# Get attachment count
gsd task list-attachments T123456 | jq '.data.node.task_internal_attachments.count'
```

### Get Single Attachment

Get metadata for a single attachment by its FBID:

```bash
gsd task get-attachment <ATTACHMENT_FBID>
```

**Response** includes full attachment metadata plus the parent task info (`id`, `task_number`, `prefixed_number`, `task_title`).

### Attachment Count in Task Get

The `gsd task get` response now includes attachment count:

```bash
gsd task get T123456 | jq '.data.node.task_internal_attachments.count'
```

**Notes:**
- Deleted attachments are automatically filtered out
- Attachments are sorted by category (images/videos first) then filename
- `attachment_url` and `download_attachment_uri` are CDN URLs for accessing the file
- `thumbnail_url` provides a thumbnail version (useful for images)
