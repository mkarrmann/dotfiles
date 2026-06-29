# Diff Search

Search and filter Phabricator diffs using natural language. Powered by `meta phabricator.diff list`.

## What You Can Ask

- "Show me my open diffs"
- "Find diffs by alice from the last 7 days"
- "What diffs landed in fbcode/foo/ this month?"
- "Show diffs reviewed by the agentic-web project that need review"
- "List all committed diffs from my org in the last 30 days"
- "Find diffs with 'qr code' in the title"

## Key Features

| Feature | Description |
|---------|-------------|
| **Author & Org** | Filter by author, your own diffs, or an entire org's diffs (recursive reports) |
| **Status** | Needs Review, Accepted, Committed, Waiting For Author, and more |
| **Time Ranges** | Absolute dates, relative periods (last 24h/7d/30d), or custom ranges |
| **Reviewers** | Filter by reviewer (person or project/group) |
| **File Paths** | Find diffs that modified specific files or directories |
| **Tags** | Filter by Phabricator tags |
| **Text Search** | Search in diff titles, summaries, or test plans |
| **Flexible Output** | Table, JSON, CSV, or YAML; customizable columns and sorting |

## Example Prompts

**My review queue:**
> "Show diffs where I'm a reviewer that need my action"

**Team activity:**
> "List diffs authored by recursive reports of manager_unixname in the last 7 days"

**File archaeology:**
> "Find diffs that modified fbcode/some/path/ and landed after 2025-06-01"

**Status tracking:**
> "Show my diffs that are accepted but not yet landed"
