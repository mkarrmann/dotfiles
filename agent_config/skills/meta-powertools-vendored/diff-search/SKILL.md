---
name: diff-search
description: 'Search Phabricator diffs by author, status, date, reviewers, files, or org. Supports bulk fetch and relative time filters.'
allowed-tools: Bash(meta phabricator.diff list*)
---

# Diff Search

Search Phabricator diffs using `meta phabricator.diff list`.

## Examples

```bash
meta phabricator.diff list --author-is-me --status-is="Needs Review"
meta phabricator.diff list --author-is=USERNAME --time-created-is-after=2025-01-01
meta phabricator.diff list --author-is-a-recursive-report-of-any-of=manager_unixname --time-created-is-within-last-7-days
meta phabricator.diff list --reviewers-include-any-of=reviewer_unixname --status-is="Needs Review"
meta phabricator.diff list --filepaths-affected-has-any-of-the-words=path/to/file --committed-time-is-after=2025-06-01
meta phabricator.diff list --tags-include-any-of=tag-name --time-created-is-within-last-30-days
meta phabricator.diff list --author-is-me --status-is=Accepted --columns=number,title,status,created,committed --output=json
```

## Key Options

| Flag | Description |
|------|-------------|
| `--author-is=UNIXNAME` | Filter by author |
| `--author-is-me` | Filter by current user |
| `--author-is-a-recursive-report-of-any-of=UNIXNAME` | All diffs from an org |
| `--status-is=STATUS` | Status filter (comma-separated) |
| `--status-is-not=STATUS` | Exclude statuses |
| `--time-created-is-after=DATE` | Created after date |
| `--time-created-is-before=DATE` | Created before date |
| `--time-created-is-within-last-24h` | Created in last 24 hours |
| `--time-created-is-within-last-7-days` | Created in last 7 days |
| `--time-created-is-within-last-30-days` | Created in last 30 days |
| `--committed-time-is-after=DATE` | Landed after date |
| `--committed-time-is-before=DATE` | Landed before date |
| `--reviewers-include-me` | Diffs where I'm a reviewer |
| `--reviewers-include-any-of=NAME` | Filter by reviewer (unixname or project) |
| `--review-stage-is="Action Required"` | Diffs needing my review action |
| `--tags-include-any-of=NAME` | Filter by tag |
| `--filepaths-affected-has-any-of-the-words=PATH` | Filter by affected file path |
| `--title-has-the-phrase=TEXT` | Search in title |
| `--title-has-any-of-the-words=TEXT` | Title contains any word |
| `-l N`, `--limit=N` | Max results |
| `--columns=COLS` | Columns to show |
| `-o json` | JSON output |
| `--sort-by=FIELD` | Sort by: created, updated, committed, number, line_count |
| `--sort-direction=DIR` | Sort: asc or desc (default: desc) |

## Valid Statuses

ONLY these exact values work for `--status-is`. Any other value (including `Open`, `Closed`, `Draft`, `Landed`, `Needs Revision`, `LAND_RECENTLY_FAILED`) will fail:

| Status | Meaning |
|--------|---------|
| `Needs Review` | Awaiting reviewer action |
| `Waiting For Author` | Reviewer requested changes |
| `Accepted` | Approved, ready to land |
| `Committed` | Already landed |
| `Abandoned` | Author closed without landing |
| `Changes Planned` | Author plans to update |
| `Unpublished` | Draft, not yet submitted for review |
| `Reverted` | Was landed then reverted |

Common mistakes: `Closed` is not valid (use `Abandoned`). `Needs Revision` is not valid (use `Waiting For Author`). `Open` is not valid (use `--include-only-open` flag instead). `Landed` is not valid (use `Committed`).

## Available Columns

Default: `number`, `title`, `status`, `author`, `created`

Additional: `repository`, `branch`, `updated`, `committed`, `published`, `line_count`, `comment_count`, `summary`, `file_count`, `url`

## Notes

- Date values accept ISO 8601 dates (e.g. `2025-01-01`) or timestamps
- Relative time filters: `--time-created-is-newer-than="-2 days"`, `--time-created-is-older-than="-4 weeks"`
- Use `--output=json` for machine-readable output
- Use `--no-truncate` to show full values in table output
- Run `meta phabricator.diff list --help` for the complete list of filters
- To search by **code content** (what was added/removed), use the **commit-search** skill instead
