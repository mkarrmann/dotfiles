---
name: commit-search
description: Search through committed code changes across Meta repositories (fbsource, www, configerator, etc.) to find diffs containing specific code patterns. Use this skill when the user asks to search for code changes in commits, find diffs that modified specific code, locate who changed certain functionality, or search commit history for patterns. Returns diff IDs (D12345), titles, file paths, and repos.
---

# Commit Search

Search for diffs containing specific code changes across Meta repositories using the CommitSearch GraphQL API.

## Quick Start

Execute the GraphQL query below with appropriate variables to search commits and retrieve diff IDs:

```bash
jf graphql --query 'query CommitSearchQuery(
  $repos: [String!]!
  $line_needle: String
  $line_case_sensitive: Boolean
  $search_added: Boolean
  $search_removed: Boolean
  $line_regex: String
  $author_exact: String
  $path_include: String
  $path_exclude: String
  $count: Int
  $time_range: InternDateTimeRange
  $sev_filter: XFBCommitSearchSEVFilter
) {
  xfb_commitsearch_search(repos: $repos, client_id: "claude_code_skill") {
    with_datetime_range(range: $time_range) {
      with_content(
        line_needle: $line_needle
        line_case_sensitive: $line_case_sensitive
        search_added: $search_added
        search_removed: $search_removed
        line_regex: $line_regex
        author_exact: $author_exact
        path_include: $path_include
        path_exclude: $path_exclude
        sev_filter: $sev_filter
      ) {
        results(first: $count) {
          edges {
            node {
              commit_id
              repo
              path
              base_path
              modified_path
              phabricator_diff {
                number
                diff_title
              }
            }
          }
        }
      }
    }
  }
}' --variables '<JSON_VARIABLES>'
```

## Available Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `repos` | `String` | **Required.** Array of repos: `fbsource`, `www`, `configerator`, `instagram-server`, `whatsapp-server` |
| `line_needle` | `String` | Text to search for in code changes |
| `line_case_sensitive` | `Boolean` | Case-sensitive search (default: false) |
| `search_added` | `Boolean` | Search only in added lines |
| `search_removed` | `Boolean` | Search only in removed lines |
| `line_regex` | `String` | Regex pattern to search for |
| `author_exact` | `String` | Filter by exact author unixname |
| `path_include` | `String` | Filter to paths matching this pattern |
| `path_exclude` | `String` | Exclude paths matching this pattern |
| `count` | `Int` | Number of results to return (default: 10) |
| `time_range` | `InternDateTimeRange` | Date range filter |
| `sev_filter` | `XFBCommitSearchSEVFilter` | Filter for SEV-linked diffs. Values: `ATTACHED` (any SEV), `ATTACHED_PRIVACY_ONLY` (privacy/security/availability SEVs) |

## Search Strategies

Run multiple parallel searches with different strategies to maximize results:

### Search Added Lines
Find code that was introduced:
```json
{"repos": ["fbsource"], "line_needle": "search term", "search_added": true, "count": 15}
```

### Search Removed Lines
Find code that was deleted:
```json
{"repos": ["fbsource"], "line_needle": "search term", "search_removed": true, "count": 15}
```

### Search with Path Filter
Find changes in specific directories:
```json
{"repos": ["fbsource"], "line_needle": "search term", "path_include": "arvr/apps/hsr", "count": 15}
```

### Search by Author
Find changes by specific engineer:
```json
{"repos": ["fbsource"], "line_needle": "search term", "author_exact": "unixname", "count": 15}
```

### Regex Search
Find patterns using regex:
```json
{"repos": ["fbsource"], "line_regex": "function\\s+\\w+Test", "count": 15}
```

### Search for SEV-Linked Diffs
The API has native SEV filtering via the `sev_filter` parameter with two modes:

**Find diffs linked to any SEV:**
```json
{"repos": ["fbsource"], "line_needle": "search term", "sev_filter": "ATTACHED", "count": 15}
```

**Find diffs linked to privacy/security/availability SEVs only:**
```json
{"repos": ["fbsource"], "line_needle": "search term", "sev_filter": "ATTACHED_PRIVACY_ONLY", "count": 15}
```

`ATTACHED_PRIVACY_ONLY` filters to diffs whose linked SEV has a privacy, security, or availability impacted area tag. It does **not** cover other incident types (integrity, youth, AI, etc.).

**Note:** The SEV filter is a post-fetch filter — results are first fetched by content/path/author criteria, then checked for SEV association. Provide additional filters (path, author, time range) to keep result sets manageable.

## Workflow

1. **Parse User Request**: Identify search terms, repos, paths, and any filters
2. **Run Multiple Searches**: Execute 2-3 parallel searches with different strategies
3. **Deduplicate Results**: Group by diff number to avoid duplicates
4. **Format Output**: Present results with diff IDs, titles, paths, and repos

## Example

**User Request:** "Find diffs that added HSR unit tests"

**Execution:**
```bash
jf graphql --query '...' --variables '{"repos": ["fbsource"], "line_needle": "unit test", "search_added": true, "path_include": "hsr", "count": 10}'
```

**Output Format:**
```
Found 3 diffs matching "HSR unit tests":

1. D88164637 - [HSR] Add HSR-specific unit test rules reference to workflow
   Path: arvr/apps/worlds/.llms/rules/unit_test_writing_workflow.md
   Repo: fbsource

2. D77260049 - [hsr][scripting] Simplified networking API unit tests Docs
   Path: arvr/apps/hsr/experiments/simple-hsr/tests/README.md
   Repo: fbsource
```

## Supported Repositories

- `fbsource` - Main monorepo (fbcode, xplat, arvr, etc.)
- `www` - WWW/PHP codebase
- `configerator` - Configuration repo
- `instagram-server` - Instagram server code
- `whatsapp-server` - WhatsApp server code
- `opsfiles` - Operations files

## Tips

- Run multiple search strategies in parallel for comprehensive results
- Use `path_include` to narrow down searches to relevant directories
- Use `search_added: true` when looking for new code introductions
- Use `search_removed: true` when looking for deleted functionality
- Deduplicate by `phabricator_diff.number` since same diff may appear multiple times
- The `phabricator_diff` field may be null for commits without associated diffs

## Related Tools

- **diff-search skill** - Search for diffs by **metadata** (author, status, reviewers, title). Use when you need to find diffs by organizational criteria rather than code content.
