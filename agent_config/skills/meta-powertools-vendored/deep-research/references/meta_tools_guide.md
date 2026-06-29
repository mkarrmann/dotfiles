# Meta Tools Comprehensive Guide

This reference provides detailed documentation for Meta-specific development tools, optimized for research teammates.

## BigGrep - Code Search at Scale

### Tool Selection Matrix
| Pattern Type | Tool | Example | Use Case |
|-------------|------|---------|----------|
| Regex | `fbgr` | `fbgr "class.*Test" --limit 80` | Pattern matching, wildcards |
| Exact String | `fbgs` | `fbgs "exact_function" --limit 80` | Literal text search |
| Filename | `fbgf` | `fbgf "*.py" --limit 80` | File discovery |

### Essential Flags
- `--limit N` or `-n N`: Limit results (ALWAYS USE)
- `-f PATTERN` or `--files PATTERN`: Filter by file path
- `-l`: Show only filenames
- `-C N` or `--context N`: Show N lines of context

### Common Patterns
```bash
# Find Python test files in specific project
fbgf -f "nest/.*test.*\.py$" --limit 80

# Search for class definitions with context
fbgr "^class\s+\w+.*:" -C 5 --limit 80

# Find exact imports
fbgs "from nest.platform import" -l --limit 50

# Complex regex with file filter
fbgr -f "fbcode/nest/.*\.py$" "def\s+\w+_handler" --limit 30
```

### Handling "Too Many Files" Errors
When search scope exceeds 10,000 files:
```bash
# Option 1: Narrow the path
fbgr -f "nest/platform/.*\.py$" "pattern" --limit 80

# Option 2: Use Mercurial pipeline
hg files | grep "nest.*\.py$" | xargs grep -l "pattern"

# Option 3: Get file list first, then search
FILES=$(hg files | grep "pattern")
echo "$FILES" | xargs grep -n "search_term"
```

## Mercurial (sl/hg) - Version Control

### Essential Commands
```bash
# Navigation
sl                          # Visualize commit stack
sl goto <hash>             # Navigate to commit
sl goto remote/master      # Go to master

# History
sl log -r . --stat         # Current commit with file changes
sl reflog --all | head -20 # Recent position changes
sl show <hash>             # Full diff of commit

# File Operations
hg files                   # All tracked files
hg status -mar            # Modified, added, removed
hg status -u              # Untracked files
hg status -i              # Ignored files

# Diff Analysis
sl diff -r <old> -r <new> # Compare commits
sl diff --stat            # Summary of changes
```

### Working with Stacks
```bash
# View stack
sl

# Navigate stack
sl prev                    # Go to parent commit
sl next                    # Go to child commit
sl goto <hash>            # Go to specific commit

# Understand amendments
sl reflog --all | grep <hash>  # Track commit evolution
sl obslog <hash>               # Obsolescence history
```

## Buck2 - Build System

For comprehensive Buck2 workflows, build/test operations, and debugging, see: `fbcode/claude-templates/components/skills/buck/SKILL.md`

### Quick Reference for Research Tasks

```bash
# Find target ownership (most common research query)
buck2 uquery 'owner("fbcode/nest/platform/server.py")'

# List targets in BUCK file
buck2 uquery 'targets_in_buildfile(fbcode//nest/platform:BUCK)'

# For detailed Buck2 operations:
# - Building and testing workflows
# - Dependency analysis (deps/rdeps queries)
# - Debugging build failures
# - Buck macro usage and parameters
# → Read the buck skill at: fbcode/claude-templates/components/skills/buck/SKILL.md
```

## jf (Jellyfish) - Phabricator Integration

### Diff Investigation
```bash
# Basic operations
jf get D83568601                    # Download diff locally
jf export --diff D83568601          # Export raw diff
jf diff-properties D83568601        # Get metadata

# Comments and review
jf inlines D83568601                           # Show inline comments
jf inlines D83568601 --include-non-inline      # All comments
jf inlines D83568601 --include-resolved        # Include resolved

# Search and list
jf list --author username --limit 10
jf list --title "search terms"
jf list --status NEEDS_REVIEW

# Land checks
jf land --list -r .                 # Check land eligibility
```

### Version ID Extraction (for GraphQL)
```bash
# Get version ID from diff properties
jf diff-properties D83568601 | grep '"id"'
# Look for: "latest_phabricator_version":{"id":"VERSION_ID"}
```

## GraphQL - Advanced Queries

For comprehensive GraphQL query construction, schema exploration, and complex operations:
- General queries: `fbcode/claude-templates/components/skills/graphql-query/SKILL.md`
- Advanced search patterns: `fbcode/claude-templates/components/skills/graphql-powersearch/SKILL.md`

### Quick Reference (Use jf Commands First)

For most diff investigation tasks, prefer jf commands over GraphQL (see jf section above).

```bash
# Schema discovery (when needed for custom queries)
jf graphql --query '{
  xfb_graphiql_schema_explorer(
    input: { type_name: { type_name: "TypeName" } }
    schema_name: FACEBOOK_INTERNAL
  ) {
    ... on XFBGraphiQLSchemaExplorerTypename {
      typename
      fields { name description }
    }
  }
}'

# For detailed GraphQL operations:
# - Complex query construction
# - CI investigation workflows
# - Bulk diff analysis
# - Custom schema queries
# → Read the GraphQL skills at paths above
```

## MCP Meta Knowledge Tools

MCP (Model Context Protocol) tools provide direct access to Meta's internal knowledge base, documentation, and collaboration systems.

### Tool Selection Guide

| Need | Tool | When to Use |
|------|------|-------------|
| Specific diff details | `mcp__plugin_meta_mux__get_phabricator_diff_details` | You have a diff number and need status, CI signals, reviewers |
| Load known resource | `mcp__plugin_meta_mux__knowledge_load` | You have a URL or reference ID (D#, T#, S#, P#, N#) |
| Search documentation | `mcp__plugin_meta_mux__knowledge_filtered_search` | Exploratory search across wiki, workplace, docs |

### mcp__plugin_meta_mux__get_phabricator_diff_details

Get information about a specific diff including CI failures, reviewers, and status.

```text
# Basic diff details
get_phabricator_diff_details(
    phabricator_diff_number="D83568601"
)

# Diff investigation with CI and review info
get_phabricator_diff_details(
    phabricator_diff_number="D83568601",
    include_diff_status=true,
    include_diff_summary=true,
    include_failing_ci_signals=true,
    include_diff_author=true,
    include_reviewers=true,
    include_stack_dependencies=true,
    include_test_plan=true
)

# For GRepo project investigations
get_phabricator_diff_details(
    phabricator_diff_number="D83568601",
    include_grepo_project_name=true,
    include_grepo_project_path=true,
    include_tags=true
)
```

**Available parameters** (all boolean, all optional):
- `include_diff_status` - diff status (Needs Review, Accepted, Closed, etc.)
- `include_diff_summary` - diff description/summary
- `include_diff_author` - author name and unixname
- `include_reviewers` - reviewer names and acceptance status
- `include_test_plan` - test plan content
- `include_raw_diff` - the actual diff content
- `include_failing_ci_signals` - failing CI signals
- `include_critical_ci_signals` - critical CI signals with error context
- `include_stack_dependencies` - parent and child diffs
- `include_tags` - tags/projects applied to the diff
- `include_ai_review_insights` - AI code review insights
- `include_grepo_project_name`, `include_grepo_project_path` - GRepo project info

**Common Use Cases**:
- Investigating CI failures on a diff
- Understanding diff dependencies (parent/child diffs in stack)
- Getting reviewer status and acceptance info
- Finding associated tags and test plans

### mcp__plugin_meta_mux__knowledge_load

Load content directly from Meta internal URLs or reference IDs.

```text
# Load diff content
knowledge_load(url="https://www.internalfb.com/diff/D63926261")

# Load task details
knowledge_load(url="https://www.internalfb.com/T216973509")

# Load SEV information
knowledge_load(url="https://www.internalfb.com/sevmanager/view/496098")

# Load paste content
knowledge_load(url="https://www.internalfb.com/phabricator/paste/view/P1745412")

# Load Bento notebook
knowledge_load(url="https://www.internalfb.com/intern/anp/view/?id=638684")

# Load Workchat thread with time filters
knowledge_load(
    url="https://www.internalfb.com/workchat/thread/12345",
    workchat_start_creation_time="2024-11-01T00:00:00Z",
    workchat_end_creation_time="2024-11-19T23:59:59Z"
)
```

**URL Patterns**:
- Diffs: `https://www.internalfb.com/diff/D[number]`
- Tasks: `https://www.internalfb.com/T[number]`
- SEVs: `https://www.internalfb.com/sevmanager/view/[number]`
- Pastes: `https://www.internalfb.com/phabricator/paste/view/P[number]`
- Notebooks: `https://www.internalfb.com/intern/anp/view/?id=[number]`

**Common Use Cases**:
- Loading specific referenced resources (T12345, D12345, etc.)
- Accessing paste content shared in discussions
- Reading SEV details for incident analysis
- Extracting notebook analysis results

### mcp__plugin_meta_mux__knowledge_filtered_search

Search Meta's internal knowledge base including wiki pages, workplace posts, internal docs, and more.

```text
# Natural language search
knowledge_filtered_search(
    natural_language_query="How does GraphQL authentication work at Meta?"
)

# Search with document type filter
knowledge_filtered_search(
    natural_language_query="Nest platform architecture documentation",
    doc_types=["GOOGLE_DOCUMENT", "WIKI_PAGE"]
)

# Search workplace posts for technical solutions
knowledge_filtered_search(
    keywords="Buck2 build failure",
    doc_types=["GROUP_POST"]
)

# Keyword-only search
knowledge_filtered_search(
    keywords="remote work policy"
)

# Time-filtered search
knowledge_filtered_search(
    natural_language_query="Recent updates to authentication system",
    start_creation_time="2024-10-01T00:00:00Z",
    end_creation_time="2024-11-19T23:59:59Z"
)

# Search specific workplace groups
knowledge_filtered_search(
    keywords="GraphQL best practices",
    workplace_group_ids=["123456789"]
)

# Search specific wiki paths
knowledge_filtered_search(
    natural_language_query="How to use Presto",
    wiki_subpaths=["Engineering/Data"]
)
```

**Document Types**:
- `DIFF` - Code diffs/changesets
- `GOOGLE_DOCUMENT` - Working docs, roadmaps, planning docs
- `GOOGLE_PRESENTATION` - Slide presentations
- `GOOGLE_SITE` - Google Sites / gsites
- `GOOGLE_SPREADSHEET` - Spreadsheets
- `GROUP_POST` - Workplace posts (technical solutions, discussions)
- `MEETING_NOTE` - Meeting notes
- `SEV` - SEV incident reports
- `STATIC_DOCS` - Static documentation sites
- `TASK` - Tasks
- `WIKI_PAGE` - Official technical wiki pages
- `WUT` - WUT (advanced knowledge search only)

**Common Use Cases**:
- Finding technical documentation and best practices
- Discovering team roadmaps and planning docs
- Searching for solutions to technical problems in workplace posts
- Looking up HR policies and benefits information
- Finding presentations and design docs

### MCP Tools Best Practices

1. **Prefer specific over search**
   - If you have a diff/task ID → Use `knowledge_load` or `get_phabricator_diff_details`
   - If you need to discover → Use `knowledge_filtered_search`

2. **Use appropriate filters**
   - Filter by `doc_types` to reduce noise
   - Use time filters for recent information
   - Specify workplace groups or wiki paths when known

3. **Combine with other tools**
   - Use `fbgs`/`fbgr` for code search
   - Use MCP tools for documentation and context
   - Use `jf` commands for diff operations

4. **Efficiency tips**
   - `get_phabricator_diff_details` is faster than GraphQL for single diff investigation
   - `knowledge_load` is faster than manual URL browsing
   - `knowledge_filtered_search` returns relevant results ranked by relevance

## Presto - Data Queries

### Query Template
```bash
presto infrastructure --execute "
  SELECT column1, column2, COUNT(*) as cnt
  FROM schema.table
  WHERE ds >= '<DATEID-7>'  -- REQUIRED partition filter
    AND other_conditions
  GROUP BY column1, column2
  ORDER BY cnt DESC
  LIMIT 100                  -- ALWAYS limit exploration
" --output-format ALIGNED
```

### Date Macros
- `<DATEID>`: Today
- `<DATEID-N>`: N days ago
- `<DATEID+N>`: N days future
- Format: yyyy-mm-dd

### Common Operations
```bash
# Schema discovery
presto infrastructure --execute "DESCRIBE schema.table"

# Sample data
presto infrastructure --execute "
  SELECT *
  FROM schema.table
  WHERE ds = '<DATEID-1>'
  LIMIT 10
"

# Count by partition
presto infrastructure --execute "
  SELECT ds, COUNT(*) as row_count
  FROM schema.table
  WHERE ds >= '<DATEID-7>'
  GROUP BY ds
  ORDER BY ds DESC
"
```

## Performance Optimization Tips

### Search Optimization
1. Always use `--limit` flags
2. Start with narrow searches, broaden if needed
3. Use file path filters when possible
4. Prefer exact string search over regex when applicable

### Query Optimization
1. Use most specific Buck query type (owner vs deps)
2. Cache query results when doing multiple operations
3. Batch related queries together

### Pipeline Optimization
1. Use parallel tool calls for independent operations
2. Chain commands efficiently with pipes
3. Exit early when objective is met

## Error Recovery Patterns

### Common Errors and Solutions

| Error | Solution |
|-------|----------|
| "Too many files" | Use narrower path filter or hg pipeline |
| "Buck target not found" | Use uquery to discover correct target |
| "Permission denied" | Check team ownership, try different approach |
| "Presto timeout" | Add more filters, reduce date range |
| "Diff not found" | Verify diff number, check if landed |
| "Need documentation" | Use knowledge_filtered_search MCP tool |
| "Task/SEV details needed" | Use knowledge_load MCP tool with ID/URL |

### Fallback Chains
```text
Primary → Fallback 1 → Fallback 2
fbgr → narrower fbgr → hg files | grep
buck2 build → buck2 uquery → check BUCK file
jf commands → get_phabricator_diff_details MCP → GraphQL → manual investigation
Presto query → smaller date range → sample subset
Manual wiki search → knowledge_filtered_search MCP → ask team directly
```

## Tool Limits Reference

| Tool | Limit Type | Value | Workaround |
|------|-----------|-------|------------|
| fbgr/fbgs | File scope | 10,000 files | Use path filters |
| fbgr/fbgs | Results | Use --limit flag | Always specify |
| Presto | Query time | ~30 seconds | Add filters |
| GraphQL | Complexity | Varies | Simplify query |
| Buck2 | Target depth | Recursive can be slow | Use specific paths |

## Best Practices Summary

1. **Always limit results** - Use --limit, LIMIT, first: N
2. **Start specific** - Narrow searches, broaden if needed
3. **Use correct tool** - fbgr for regex, fbgs for exact, MCP for docs/diffs
4. **Check ownership** - Buck targets own files
5. **Prefer simple** - MCP tools over manual browsing, jf over GraphQL
6. **Document paths** - Use file_path:line_number format
7. **Handle errors** - Have fallback strategies ready
8. **Track budget** - Count tool calls, stop before limit
9. **Know your MCP tools** - get_phabricator_diff_details for CI, knowledge_load for IDs, knowledge_filtered_search for discovery
