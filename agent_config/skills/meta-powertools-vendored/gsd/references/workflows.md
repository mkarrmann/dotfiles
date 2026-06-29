# GSD Workflows & Tips Reference

Common workflows, jq patterns, troubleshooting, and best practices.

## Common Workflows

### 1. Create a New Project with Tasks

```bash
# Create team (if needed)
gsd team create \
  --name "My Team" \
  --members <YOUR_USER_ID>

# Create project
gsd project create \
  --name "Q1 Goals" \
  --team-id <TEAM_ID>

# Create sections
gsd section create \
  --name "Backend" \
  --project-id <PROJECT_ID>

gsd section create \
  --name "Frontend" \
  --project-id <PROJECT_ID>

# Create tasks
gsd task create \
  --title "Setup API endpoints" \
  --section-id <BACKEND_SECTION_ID>

gsd task create \
  --title "Build UI components" \
  --section-id <FRONTEND_SECTION_ID>
```

### 2. Sprint Management

```bash
# List existing sprints to find IDs
gsd sprint list --team-id <TEAM_ID>

# Create sprint
gsd sprint create \
  --team-id <TEAM_ID> \
  --name "Sprint 1" \
  --start-date 2025-12-01 \
  --target-date 2025-12-14

# Get sprint details
gsd sprint get <SPRINT_ID>

# Add tasks to sprint
gsd task add-to-sprint <TASK_ID1> --sprint-ids <SPRINT_ID>

gsd task add-to-sprint <TASK_ID2> --sprint-ids <SPRINT_ID>

# Update sprint dates
gsd sprint update <SPRINT_ID> \
  --start-date 2025-12-08 \
  --target-date 2025-12-21

# List tasks to verify
gsd task list --project-id <PROJECT_ID>
```

### 3. Weekly Status Reporting

```bash
# List available templates
gsd status-report templates --team-id <TEAM_ID>

# Create draft from template
gsd status-report create-draft-from-template \
  --project-id <PROJECT_ID> \
  --template-id <TEMPLATE_ID> \
  --status ON_TRACK

# Update the draft with your content
gsd status-report update-draft <DRAFT_ID> \
  --owner-id <YOUR_USER_ID> \
  --status ON_TRACK \
  --description "# Weekly Update

## Completed
- **Feature X**: Shipped to production
- **Bug fixes**: Resolved 15 P1 issues

## In Progress
- **Feature Y**: 70% complete

## Blockers
- None"

# Publish when ready
gsd status-report publish <DRAFT_ID>
```

### 4. Add Existing Tasks to a Project or Section

Use `gsd task add-to-project` to associate pre-existing tasks with a project or section. This is the correct approach when tasks were created standalone (without `--project-id` or `--section-id`) or need to be added to an additional project.

```bash
# Add multiple tasks to a project
gsd task add-to-project \
  --task-ids T123456,T789012 \
  --project-id <PROJECT_ID>

# Add multiple tasks to a specific section (preferred — places them directly)
gsd task add-to-project \
  --task-ids T123456,T789012 \
  --section-id <SECTION_ID>

# Add tasks to a section by name (creates the section if it doesn't exist)
gsd task add-to-project \
  --task-ids T123456,T789012 \
  --project-id <PROJECT_ID> \
  --section-name "My Section"
```

> **Important:** `gsd task update` does NOT support `--project-id`, `--section-id`, or `--add-project` flags. Use `add-to-project` for associating tasks with projects, or `task move` for moving between projects/sections.

> **Permission:** You must be a member of the target GSD project. If you get an `AlwaysDenyRule` error, join the project via the GSD web UI first.

### 5. Finding and Filtering Tasks

```bash
# Search for tasks by title
gsd task search \
  --project-id <PROJECT_ID> \
  --title "API refactoring"

# Find high-priority open tasks
gsd task search \
  --project-id <PROJECT_ID> \
  --priority HIGH \
  --status OPEN

# Find tasks in progress
gsd task search \
  --project-id <PROJECT_ID> \
  --status OPEN \
  --progress IN_PROGRESS

# Find blocked tasks
gsd task search \
  --project-id <PROJECT_ID> \
  --status OPEN \
  --progress BLOCKED

# Find tasks by owner
gsd task search \
  --project-id <PROJECT_ID> \
  --owner johndoe \
  --status OPEN

# Count tasks by status using jq (from task list)
# Note: task list returns tasks grouped by section
gsd task list --project-id <PROJECT_ID> 2>/dev/null \
  | jq '[.data.node.sections.nodes[].tasks.nodes[] | .task_progress_status] | group_by(.) | map({status: .[0], count: length})'
```

## Tips and Best Practices

### Working with JSON Output

Use `jq` to extract specific fields. Always use `2>/dev/null` to suppress ODS connection errors when piping to jq:

```bash
# Get project name and URL
gsd project get <PROJECT_ID> 2>/dev/null \
  | jq '{name: .data.node.name, url: .data.node.gsd_url}'

# Extract task IDs (tasks are nested under sections)
gsd task list --project-id <PROJECT_ID> 2>/dev/null \
  | jq -r '.data.node.sections.nodes[].tasks.nodes[].prefixed_number'

# Get team members
gsd team get <TEAM_ID> 2>/dev/null \
  | jq -r '.data.node.team_members.nodes[].name'
```

### Date Formats

- All dates use `YYYY-MM-DD` format (e.g., "2025-12-31")
- CLI automatically converts to Unix timestamps for GraphQL
- Sprint dates are stored as Unix timestamps internally

### Markdown Support in Status Reports

The CLI automatically detects Markdown syntax and converts to Lexical JSON:

```bash
# This Markdown:
--description "# Progress Update

**Completed:**
- Implemented feature X
- Added **bold** and *italic* formatting

**Next:**
1. Test all features
2. Update documentation"

# Gets converted to proper Lexical JSON for the web UI
```

Supported Markdown:

- Headings: `# H1`, `## H2`, `### H3`
- Bold: `**text**`
- Italic: `*text*`
- Links: `[text](url)`
- Lists: `- bullet` or `1. numbered`
- Inline code: `` `code` ``

### ID Formats

- Team IDs: Numeric strings (e.g., "1986023765263111")
- Project IDs: Numeric strings (e.g., "620992314315401")
- Section IDs: Numeric strings (e.g., "826632136983241")
- Task IDs: Use the `id` field (e.g., "1567364077929152"), not the `prefixed_number`
- OKR IDs: Short form accepted (e.g., "204969" from URL)

### FBID Lookup (When Needed)

For most operations, **you don't need to look up your FBID** because:
- `gsd task create`, `gsd milestone create`, and `gsd status-report create-draft` automatically default to the current user as owner
- `gsd task search --owner` accepts your username directly
- `gsd project list --owner` accepts a unixname or numeric FBID to list another user's projects

**FBID is only needed when assigning to someone else:**

```bash
# Get your username
whoami

# Get another user's FBID from a task they own
gsd task get <TASK_ID_THEY_OWN> 2>/dev/null \
  | jq -r '.data.node.task_owner.id' \
  | base64 -d \
  | sed 's/intern_user://'

# Or use jf graphql to get your own ID if needed
USER_ID=$(jf graphql --query 'query { me { id } }' | jq -r '.me.id') && \
gsd task update T123456 \
  --owner-id $USER_ID
```

### Error Handling

Common errors:

- "Missing required field": Check GraphQL mutation parameters
- "Entity not found": Verify ID is correct
- "Invalid date format": Use YYYY-MM-DD format
- "invalid value 'X' for '--priority'": Use valid enum values (see SKILL.md Quick Reference)
- "invalid value 'X' for '--status'": Use valid enum values (see SKILL.md Quick Reference)
- "invalid value 'X' for '--size'": Use valid enum values (see SKILL.md Quick Reference)
- VPN required: Ensure connected to Meta VPN
- `EntTasksGSDProjectPermissionPolicy:AlwaysDenyRule`: The current user is **not a member** of the target GSD project. Join the project in the GSD web UI first, then retry. See "Permission denied when adding tasks to a project" below.

## Troubleshooting

**Issue: "GraphQL authentication failed"**

- Ensure you're connected to Meta VPN
- Check CAT token is valid
- Verify you have GSD access

**Issue: "Task not found in section after move"**

- Wait a few seconds for GraphQL cache
- Re-query the section
- Check task wasn't moved to different section

**Issue: "Status report doesn't render correctly"**

- Ensure Markdown syntax is valid
- Check Lexical JSON structure in response
- Verify no unclosed formatting tags

**Issue: Permission denied when adding tasks to a project**

- Error contains `EntTasksGSDProjectPermissionPolicy:AlwaysDenyRule`
- **Cause:** The current user is not a member of the target GSD project. GSD projects restrict mutations (create task in project, add-to-project, move) to project members.
- **Fix:** Open the project in the GSD web UI at `https://www.internalfb.com/gsd/<team_id>/<project_id>/list`, join the project, then retry the CLI command.
- This affects: `gsd task create --project-id/--section-id`, `gsd task add-to-project`, `gsd task move`

**Issue: "Can't find recently closed tasks"**

- Use helper script:
  `fbcode/tools/gsd/examples/find_recently_closed_tasks.sh <project_id> <days>`
- GraphQL doesn't expose closure timestamps efficiently
- Script combines GSD CLI + tasks CLI for filtering
