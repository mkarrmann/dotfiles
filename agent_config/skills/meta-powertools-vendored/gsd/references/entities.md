# GSD Entity Operations Reference

Complete reference for team, theme, project, section, sprint, and milestone operations.

## Team Operations

### List Teams

```bash
gsd team list
```

### Get Team Details

```bash
gsd team get <TEAM_ID>
```

### Create Team

```bash
gsd team create \
  --name "My Team Name" \
  --members <USER_ID1>,<USER_ID2> \
  [--parent-team-id <PARENT_ID>] \
  [--privacy-owner-id <OWNER_ID>]

# Example:
gsd team create \
  --name "AI Infrastructure" \
  --members 100003752440036
```

## Theme Operations

Themes are optional organizational units that group related projects within a team.

**Supported operations:** list, get, create, update, move-project, archive/unarchive
**Not supported via CLI:** delete (use web UI at https://www.internalfb.com/gsd/)

### List Themes

```bash
gsd theme list --team-id <TEAM_ID>
```

### Get Theme Details

Includes projects in theme. By default, only open/active projects are returned.
Use `--include-archived` to also include archived/closed projects.

The `--team-id` flag is optional. If omitted, the team is auto-resolved from the
theme. If a wrong `--team-id` is provided, it is auto-corrected with a warning.

```bash
gsd theme get <THEME_ID>

# With explicit team ID
gsd theme get <THEME_ID> --team-id <TEAM_ID>

# Include archived/closed projects
gsd theme get <THEME_ID> --team-id <TEAM_ID> --include-archived
```

Each project node includes an `is_closed` boolean field indicating whether
the project is archived.

### Create Theme

```bash
gsd theme create \
  --name "Theme Name" \
  --team-id <TEAM_ID> \
  [--owner-id <USER_ID>] \
  [--priority NONE|P0|P1|P2]

# Example:
gsd theme create \
  --name "Q1 AI Infrastructure" \
  --team-id 1986023765263111
```

### Update Theme

```bash
gsd theme update <THEME_ID> \
  [--name "New Name"] \
  [--owner-id <USER_ID>] \
  [--priority NONE|P0|P1|P2]

# Example:
gsd theme update 123456789 \
  --name "Q2 AI Infrastructure" \
  --priority P1
```

> **Note:** The `--theme-id` flag is also supported for backwards compatibility.

### Move Project to Theme

```bash
gsd theme move-project \
  --project-id <PROJECT_ID> \
  --theme-id <THEME_ID> \
  --team-id <TEAM_ID>

# Example:
gsd theme move-project \
  --project-id 620992314315401 \
  --theme-id 123456789 \
  --team-id 1986023765263111
```

### Archive / Unarchive Theme

```bash
# Archive a theme
gsd theme archive <THEME_ID>

# Unarchive a theme
gsd theme unarchive <THEME_ID>

# Using --theme-id flag
gsd theme archive --theme-id <THEME_ID>
```

**IMPORTANT:** All sub-projects within a theme must be archived first before the theme can be archived via the web UI. When archiving a theme programmatically, archive all sub-projects first (see "Archive / Unarchive Project" below), then archive the theme.

### Bulk Archive Theme and Sub-Projects

To archive a theme and all its sub-projects in one workflow:

```bash
# Step 1: Get sub-project IDs
THEME_ID="<THEME_ID>"
PROJECT_IDS=$(gsd theme get "$THEME_ID" --team-id <TEAM_ID> 2>/dev/null \
  | jq -r '.data.node.projects.nodes[].id')

# Step 2: Archive each sub-project
for pid in $PROJECT_IDS; do
  gsd project archive "$pid"
done

# Step 3: Archive the theme
gsd theme archive "$THEME_ID"
```

## Project Operations

### List Projects

```bash
# List all projects
gsd project list

# Filter by ownership
gsd project list --filter owned
gsd project list --filter shared
gsd project list --filter contributed

# List projects in a specific team
gsd project list --team-id <TEAM_ID>

# List another user's projects (by unixname or FBID)
gsd project list --owner <unixname>
gsd project list --owner <unixname> --filter owned

# Combine filters
gsd project list --team-id <TEAM_ID> --filter owned
```

**Note:** When `--owner` is provided, `--team-id`, `--theme-id`, `--limit`, and `--all` are ignored. The `--filter` flag still works to narrow results by relationship type (owned, shared, contributed).

### Get Project Details

```bash
gsd project get <PROJECT_ID>
```

### Create Project

```bash
gsd project create \
  --name "Project Name" \
  --team-id <TEAM_ID> \
  [--theme-id <THEME_ID>] \
  [--owner-id <USER_ID>] \
  [--priority NONE|P0|P1|P2] \
  [--start-date YYYY-MM-DD] \
  [--target-date YYYY-MM-DD] \
  [--status ON_TRACK|AT_RISK|OFF_TRACK|COMPLETED]

# Example:
gsd project create \
  --name "Q1 Infrastructure Goals" \
  --team-id 1986023765263111
```

### Update Project

```bash
gsd project update <PROJECT_ID> \
  [--name "New Name"] \
  [--owner-id <USER_ID>] \
  [--priority NONE|P0|P1|P2] \
  [--start-date YYYY-MM-DD] \
  [--target-date YYYY-MM-DD] \
  [--status ON_TRACK|AT_RISK|OFF_TRACK|COMPLETED]

# Example:
gsd project update 620992314315401 \
  --status ON_TRACK \
  --priority P1 \
  --target-date 2025-03-31
```

> **Note:** The `--project-id` flag is also supported for backwards compatibility.

### Archive / Unarchive Project

```bash
# Archive a project (keeps tasks open)
gsd project archive <PROJECT_ID>

# Archive a project AND close all its tasks
gsd project archive <PROJECT_ID> --close-tasks

# Unarchive a project (reopen)
gsd project unarchive <PROJECT_ID>

# Using --project-id flag
gsd project archive --project-id <PROJECT_ID>
```

> **Note:** Archiving sets `is_closed: true` which hides the project from default views. This is different from setting status to COMPLETED via `gsd project update --status COMPLETED`, which only changes the progress indicator.

### Attach Resource Link

Attach a link to a project's Resources tab.

```bash
gsd project attach-resource <PROJECT_ID> --url <URL> --name "Display Name"
```

### List Resource Links

List all resource links attached to a project's Resources tab.

```bash
gsd project list-resources <PROJECT_ID>
```

### Detach Resource Link

Remove a resource link from a project's Resources tab. You can specify the resource by destination node ID, URL, or name.

```bash
# By destination node ID (from list-resources output)
gsd project detach-resource <PROJECT_ID> --destination-id <RESOURCE_NODE_ID>

# By URL (looks up the destination ID automatically)
gsd project detach-resource <PROJECT_ID> --url <URL>

# By name (looks up the destination ID automatically)
gsd project detach-resource <PROJECT_ID> --name "Display Name"
```

## Section Operations

### Get Section Details

```bash
gsd section get <SECTION_ID>
```

### List Sections

```bash
gsd section list --project-id <PROJECT_ID>
```

### Create Section

```bash
gsd section create \
  --name "Section Name" \
  --project-id <PROJECT_ID> \
  [--owner-id <USER_ID>] \
  [--priority NONE|P0|P1|P2]

# Example:
gsd section create \
  --name "Backend Work" \
  --project-id 620992314315401
```

### Update Section

```bash
gsd section update <SECTION_ID> \
  [--name "New Name"] \
  [--owner-id <USER_ID>] \
  [--priority NONE|P0|P1|P2]

# Example:
gsd section update 826632136983241 \
  --name "Backend Infrastructure" \
  --priority P1
```

> **Note:** The `--section-id` flag is also supported for backwards compatibility.

### Delete Section

```bash
gsd section delete <SECTION_ID>

# Example:
gsd section delete 826632136983241
```

> **Note:** This soft-deletes the section. Tasks in the section are not deleted.

## Sprint Operations

### List Sprints

```bash
# List all sprints
gsd sprint list --team-id <TEAM_ID>

# Limit number of results
gsd sprint list --team-id <TEAM_ID> -n 5

# Example:
gsd sprint list --team-id 885056174163172
```

### Get Sprint Details

```bash
gsd sprint get <SPRINT_ID>
```

### Create Sprint

```bash
gsd sprint create \
  --team-id <TEAM_ID> \
  --name "Sprint Name" \
  --start-date YYYY-MM-DD \
  --target-date YYYY-MM-DD

# Example:
gsd sprint create \
  --team-id 1986023765263111 \
  --name "Q1 Sprint 1" \
  --start-date 2025-01-01 \
  --target-date 2025-01-14
```

### Update Sprint

```bash
# Update dates
gsd sprint update <SPRINT_ID> \
  --start-date YYYY-MM-DD \
  --target-date YYYY-MM-DD

# Update name only
gsd sprint update <SPRINT_ID> --name "New Sprint Name"

# Update name and dates together
gsd sprint update <SPRINT_ID> \
  --name "Sprint 5" \
  --start-date 2025-03-01 \
  --target-date 2025-03-14

# Using --sprint-id flag
gsd sprint update --sprint-id <SPRINT_ID> --name "Sprint 5"
```

### Rename Sprint

```bash
gsd sprint rename <SPRINT_ID> --name "New Sprint Name"
```

> **Note:** `sprint rename` still works but `sprint update --name` is preferred as it can also change dates in the same command.

### Delete Sprint

```bash
gsd sprint delete <SPRINT_ID>
```

> **Note:** The `--sprint-id` flag is also supported for backwards compatibility.

### List Sprint Tasks

List all tasks assigned to a sprint. Returns a paginated task list.

```bash
gsd sprint tasks <SPRINT_ID> [--limit N]

# Using --sprint-id flag
gsd sprint tasks --sprint-id <SPRINT_ID>

# Limit results (default: 100)
gsd sprint tasks <SPRINT_ID> --limit 50

# Example:
gsd sprint tasks 2084187562382822
```

**Response fields per task:** `id`, `task_number`, `prefixed_number`, `task_title`, `task_priority`, `task_progress_status`, `task_owner` (id, name), `start_date`, `target_date`

**Pagination:** Response includes `pagination` object with `count`, `hasNextPage`, `endCursor`, and `total` fields.

## Milestone Operations

### Create Milestone

**IMPORTANT:** Use `--plannable-id` (not `--project-id`) to specify the project or section:

```bash
gsd milestone create \
  --plannable-id <PROJECT_OR_SECTION_ID> \
  --name "Milestone Name" \
  --date YYYY-MM-DD \
  [--owner-id <USER_ID>]

# Example:
gsd milestone create \
  --plannable-id 620992314315401 \
  --name "Q1 Complete" \
  --date 2025-03-31
```

### Delete Milestone

```bash
gsd milestone delete <MILESTONE_ID>
```

> **Note:** The `--milestone-id` flag is also supported for backwards compatibility.

> **Tip:** `gsd milestone delete` prompts for 2FA, which can't be satisfied from automated contexts. If you need to delete milestones from a script, use the GraphQL mutation `delete_tasks_gsd_milestone` instead — it does not require 2FA. See `references/graphql.md`.

### Update Milestone

Milestones are updated via separate subcommands (not a single `update` command):

```bash
# Rename a milestone
gsd milestone rename <MILESTONE_ID> --name "New Name"

# Edit milestone date
gsd milestone edit-date <MILESTONE_ID> --date YYYY-MM-DD

# Edit milestone owner
gsd milestone edit-owner <MILESTONE_ID> --owner-id <USER_ID>

# Update milestone status
gsd milestone update-status <MILESTONE_ID> --status <STATUS>

# Add blockers to a milestone (tasks, milestones, or projects)
gsd milestone add-blockers --milestone-id <MILESTONE_ID> --blocker-ids T123,M456

# Remove blockers from a milestone
gsd milestone remove-blockers --milestone-id <MILESTONE_ID> --blocker-ids T123,M456
```

> **Alternative:** Blocker add/remove are also exposed as GraphQL mutations (`xfb_upsert_blocker_tasks_object_with_timeline_blockers` / `xfb_remove_blocker_tasks_object_with_timeline_blockers`). Useful when you already have FBIDs in hand and want a single mutation. See `references/graphql.md`.

## Custom Field Operations

Custom fields on GSD deliverables (tasks, projects, sections) come in four kinds: dropdown, numeric, text, and people. The CLI handles **value** updates; field **definitions** are GraphQL-only.

### Update / List Custom Field Values

```bash
# Update a custom field value on a task
meta tasks.gsd.custom-field update \
  --task-number=T12345 \
  --field-name="Target Milestone" \
  --value="M0"

# List custom fields currently set on a task
meta tasks.gsd.custom-field list --task-number=T12345
```

### Create Custom Field Definitions (GraphQL only)

`meta tasks.gsd.custom-field` cannot create new field definitions. Use the corresponding GraphQL mutation per field type:

- Dropdown: `xfb_create_tasks_gsd_deliverable_custom_dropdown_field`
- Numeric: `xfb_create_tasks_gsd_deliverable_custom_numeric_field`
- Text: `xfb_create_tasks_gsd_deliverable_custom_text_field`
- People: `xfb_create_tasks_gsd_deliverable_custom_people_field`

See `references/graphql.md` for input shapes and color enum values.

> **Limitation:** Custom field **deletion** via API (CLI or GraphQL) is broken — server returns an exception. Delete via the GSD web UI instead.
