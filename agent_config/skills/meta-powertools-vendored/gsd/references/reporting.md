# GSD Reporting Operations Reference

Complete reference for status reports, OKR operations, and diff search.

## Status Report Operations

### List Status Reports

```bash
gsd status-report list \
  --project-id <PROJECT_ID> \
  [--limit <N>]
```

### Get Status Report Details

```bash
gsd status-report get <REPORT_ID>
```

### List Available Templates

```bash
gsd status-report templates --team-id <TEAM_ID>
```

### Create Draft Status Report

```bash
gsd status-report create-draft \
  --project-id <PROJECT_ID> \
  --status ON_TRACK|AT_RISK|OFF_TRACK|COMPLETED \
  --description "Report content (supports Markdown)" \
  [--owner-id <USER_ID>] \
  [--start-date YYYY-MM-DD] \
  [--target-date YYYY-MM-DD] \
  [--priority P0|P1|P2|NONE]

# Markdown is auto-detected and converted to Lexical JSON
# Supported: # Headings, **bold**, *italic*, [links](url), lists
```

### Create Draft from Template

```bash
gsd status-report create-draft-from-template \
  --project-id <PROJECT_ID> \
  --template-id <TEMPLATE_ID> \
  --status ON_TRACK
```

### Update Draft

```bash
gsd status-report update-draft <DRAFT_ID> \
  --owner-id <USER_ID> \
  --status <STATUS> \
  --description "Updated content"
```

### Publish Draft

```bash
gsd status-report publish <DRAFT_ID>
```

### Delete Draft

```bash
gsd status-report delete-draft <DRAFT_ID>
```

> **Note:** The `--report-id` and `--draft-id` flags are also supported for backwards compatibility.

## Portfolio Status Report Operations

Portfolio status reports are published directly (no draft/publish flow like project reports).
They support `portfolio_status`, `modified_by`, and `description` fields.

### List Portfolio Status Reports

```bash
gsd portfolio-status-report list \
  --portfolio-id <PORTFOLIO_ID> \
  [--limit <N>]
```

### Get Portfolio Status Report

```bash
gsd portfolio-status-report get <REPORT_ID>
```

### Create Portfolio Status Report

```bash
gsd portfolio-status-report create \
  --portfolio-id <PORTFOLIO_ID> \
  --status ON_TRACK|AT_RISK|OFF_TRACK|COMPLETED \
  --description "Report content (supports Markdown)"

# Markdown is auto-detected and converted to Lexical JSON
# Supported: # Headings, **bold**, *italic*, [links](url), lists, tables
```

### Create Portfolio Status Report from AI-Generated Content

```bash
# Use with output from generate-ai to preserve native Lexical formatting
gsd portfolio-status-report create-from-ai \
  --portfolio-id <PORTFOLIO_ID> \
  --status ON_TRACK|AT_RISK|OFF_TRACK|COMPLETED \
  --description '<Lexical JSON from generate-ai>'
```

This command passes the description through as-is (no Markdown-to-Lexical conversion), preserving native formatting like task chips, @mentions, and colored status indicators produced by `generate-ai`.

> **Note:** Unlike project status reports, portfolio status reports do not support `start_date`, `target_date`, or `plannable_priority` fields.

## OKR Operations

### List OKRs by Team

```bash
# List OKRs for a team/board within a date range
gsd okr list --team-id <TEAM_ID> --start YYYY-MM-DD --end YYYY-MM-DD

# Example: List OKRs for H1 2026
gsd okr list --team-id 651483574721512 --start 2026-01-01 --end 2026-06-30

# Include archived OKRs
gsd okr list --team-id 651483574721512 --start 2026-01-01 --end 2026-12-31 --show-archived

# Extract objective titles
gsd okr list --team-id <TEAM_ID> --start 2026-01-01 --end 2026-12-31 \
  | jq '.data.xfb_okr_status_page_query[] | {title, number, type, overall_status}'
```

The team ID can be found in the OKR board URL: `https://www.internalfb.com/okr/board/<TEAM_ID>`.
Returns objectives with their child KRs, including owner, status, and progress.

### Get OKR Details

```bash
gsd okr get <OKR_ID>
```

### Get Projects Linked to OKR

```bash
# Includes status updates from the last 90 days
gsd okr get-projects <OKR_ID>

# Extract latest status update
gsd okr get-projects <OKR_ID> \
  | jq -r '.data.okr.last_x_days_of_status_updates_in_order[-1]'
```

### Get Dependent Tasks

```bash
# Get all tasks associated with an OKR Key Result
gsd okr get-dependent-tasks <OKR_ID>

# Returns: task id, name, target_date, owner, priority, status, url, count

# Filter high priority tasks
gsd okr get-dependent-tasks <OKR_ID> \
  | jq '.data.okr.dependent_tasks.nodes | map(select(.task_priority == "HIGH"))'

# Get task count
gsd okr get-dependent-tasks <OKR_ID> \
  | jq '.data.okr.dependent_tasks.count'
```

### Get OKRs Linked to Project

```bash
gsd okr get-okrs --project-id <PROJECT_ID>
```

### Create OKR Status Update

```bash
gsd okr create-update <OKR_ID> \
  --content "## December Update

## Highlights
- Completed feature X
- Launched to 50% rollout

## Lowlights
- Delayed by infrastructure issues"
```

### Get Child OKRs (Sub-KRs)

```bash
# List immediate child OKRs of a given OKR
gsd okr get-children --okr-id <OKR_ID>

# Example:
gsd okr get-children --okr-id 222971

# Returns: id, title, number, overall_status for each child
```

### Get OKR Hierarchy

```bash
# Show hierarchy with default depth (2 levels of children)
gsd okr get-hierarchy --okr-id <OKR_ID>

# Show hierarchy with custom depth (max: 3)
gsd okr get-hierarchy --okr-id <OKR_ID> --depth 1

# Example:
gsd okr get-hierarchy --okr-id 222971 --depth 2

# Returns: objective, parents, current KR details, and nested children
# up to the specified depth
```

### Attach Project to OKR

```bash
# Attach a project to an OKR key result
gsd okr attach-project <OKR_ID> --project-id <PROJECT_ID>

# Example:
gsd okr attach-project 225178 --project-id 836927812500651

# Using --okr-id flag
gsd okr attach-project --okr-id 225178 --project-id 836927812500651
```

### Detach Project from OKR

```bash
# Detach a project from an OKR key result
gsd okr detach-project <OKR_ID> --project-id <PROJECT_ID>

# Example:
gsd okr detach-project 225178 --project-id 836927812500651

# Using --okr-id flag
gsd okr detach-project --okr-id 225178 --project-id 836927812500651
```

### Attach Tasks to OKR

```bash
# Attach tasks to an OKR key result
gsd okr attach-tasks <OKR_ID> --task-ids T123456,T789012

# Task IDs can be with or without T prefix
gsd okr attach-tasks 225178 --task-ids 252323955,252323943

# Using --okr-id flag
gsd okr attach-tasks --okr-id 225178 --task-ids T252323955
```

### Detach Tasks from OKR

```bash
# Detach tasks from an OKR key result
gsd okr detach-tasks <OKR_ID> --task-ids T123456,T789012

# Example:
gsd okr detach-tasks 225178 --task-ids T252323955

# Using --okr-id flag
gsd okr detach-tasks --okr-id 225178 --task-ids 252323955,252323943
```

**Note:** Task IDs are automatically resolved from task numbers (e.g., T252323955) to internal FBIDs. You can pass either format.

> **Note:** The `--okr-id` flag is also supported for backwards compatibility.

## Diff Search

**Find diffs associated with a project:**

```bash
gsd diff list --project-id <PROJECT_ID>

# Returns a URL to the diff search page
# Approximately 2.5M diffs are linked to GSD projects
```
