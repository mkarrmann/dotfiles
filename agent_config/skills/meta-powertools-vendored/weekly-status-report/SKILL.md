---
name: weekly-status-report
display_name: Weekly Status Report
description: Generate comprehensive weekly status reports by aggregating diffs, tasks, and communication data. Use this skill when users want to create or review their weekly accomplishments, track progress, or prepare status updates for their team/manager.
tags:
  - productivity
  - status-report
  - meta-tools
  - weekly-report
allowed-tools: Bash
---

# Weekly Status Report

Generate comprehensive weekly status reports by aggregating data from diffs, tasks, and communications.

## When to Use

Use this skill when:
- "Generate my weekly status report"
- "What did I accomplish this week?"
- "Create a status update for my manager"
- "Show me my weekly progress"

## Overview

This skill collects and aggregates:
1. **Diffs** - All diffs created, updated, or landed by the user
2. **Tasks** - Tasks created or owned by the user
3. **Communications** - Workplace posts, GChat, etc.
4. **Documents** - Google Workspace (Docs, Sheets, Drive)
5. **External Tools** - JIRA tickets (when configured)

---

## Part 0: Optional Dependency Check

Some data sources require optional plugins or skills to be installed. Before collecting data, check for these dependencies and prompt the user to install them if missing.

### Required Plugins/Skills

| Dependency | Purpose | Install Command |
|---|---|---|
| `google-chat` plugin | GChat conversations, debug threads | `/plugin install google-chat` |
| `google-docs` skill | Reading Google Doc content | `claude-templates skill google-docs install` |

### Checking and Prompting Logic

**IMPORTANT:** Do NOT skip sections silently when a dependency is missing. Instead:

1. **Check** if the plugin/skill is available (e.g., check if `google-chat:gchat-search` skill exists in the available skills list, or if the `google_chat` MCP tool is available)
2. **If missing on first encounter**: Use `AskUserQuestion` to ask the user if they want to install it. Example:
   ```text
   "The google-chat plugin is not installed. It enables collecting GChat debug conversations,
    design discussions, and collaboration threads for the report. Would you like to install it?"
   Options: "Yes, install it" / "No, skip GChat sections"
   ```
3. **If the user chooses to install**: Run the install command (e.g., `/plugin install google-chat` or `claude-templates skill google-docs install`), then inform the user they may need to restart Claude Code for it to take effect. No preference is recorded — on next run, if the plugin/skill is now installed, the check won't trigger; if it's still missing (e.g., restart needed), it will re-prompt.
4. **If the user declines**: Record their preference by writing to `~/.claude/weekly-status-report-prefs.json`:
   ```json
   {
     "skip_google_chat": true,
     "skip_google_docs_skill": false,
     "output_to_file": true,
     "output_directory": "~/gdrive/AI/weekly-reports",
     "preferences_updated": "<YYYY-MM-DD>"
   }
   ```
5. **On subsequent runs**: Read `~/.claude/weekly-status-report-prefs.json` first. If a section is marked as skipped, skip it silently without asking again. If the prefs file doesn't exist, treat all sections as enabled and prompt for missing dependencies.

### Example Preference Check Flow

```python
# At the start of report generation:
prefs_file = "~/.claude/weekly-status-report-prefs.json"

# 1. Read existing preferences (if any)
prefs = read_file(prefs_file)  # Returns {} if file doesn't exist

# 2. Check google-chat plugin:
if not is_plugin_installed("google-chat"):
    if prefs.get("skip_google_chat"):
        # User previously declined — skip silently
        pass
    else:
        # First time — ask the user
        answer = AskUserQuestion("Install google-chat plugin for GChat data?")
        if answer == "Yes":
            run("/plugin install google-chat")
            # Plugin takes effect after restart — GChat data will be
            # available in future reports. No prefs update needed;
            # on next run the plugin will be installed and this block
            # won't be entered.
        else:
            prefs["skip_google_chat"] = True
            write_file(prefs_file, prefs)

# 3. Check google-docs skill:
if not is_skill_available("google-docs"):
    if prefs.get("skip_google_docs_skill"):
        # User previously declined — skip reading doc content silently
        # (still discover docs via knowledge_filtered_search, just don't read full content)
        pass
    else:
        answer = AskUserQuestion("Install google-docs skill for reading Google Doc content?")
        if answer == "Yes":
            run("claude-templates skill google-docs install")
            # Skill takes effect after restart
        else:
            prefs["skip_google_docs_skill"] = True
            write_file(prefs_file, prefs)

# 4. Check file output preference (see Part 11 for full details)
if "output_to_file" not in prefs:
    answer = AskUserQuestion("Save report to a markdown file?")
    if answer == "Yes":
        output_dir = AskUserQuestion("Where to save?")
        prefs["output_to_file"] = True
        prefs["output_directory"] = output_dir
    else:
        prefs["output_to_file"] = False
    write_file(prefs_file, prefs)
```

## Part 1: Collecting Diffs

### Get All Diffs Created in Last 7 Days

```bash
meta graphql.query execute --query 'query($search: String!) {
  phabricator_diff_custom_search_query(args: {query: $search}) {
    phabricator_diffs(first: 100) {
      edges {
        node {
          number
          diff_title
          created_time
          committed_time
        }
      }
    }
  }
}' --variables '{"search": "{\"key\":\"AND\",\"children\":[{\"key\":\"EQUALS_ME\",\"field\":\"DIFF_AUTHOR_FBID\"},{\"key\":\"IS_WITHIN_LAST_7_DAYS\",\"field\":\"DIFF_CREATED_TIME\"}]}"}'
```

### Categorizing Diffs

- **Landed**: `committed_time` is not null
- **In Review**: `committed_time` is null

### Ensuring Complete Diff Coverage

**CRITICAL:** The GraphQL query above is the primary source of truth for diff collection since it filters by author FBID. However, if it fails (e.g., shell issues), the fallback `knowledge_filtered_search` with `keywords="<username>"` searches indexed content, NOT author metadata. Diffs with empty summaries or titles that don't mention the username will be missed.

**To avoid missing diffs, use multiple search strategies in parallel:**

1. **Primary**: GraphQL query with `EQUALS_ME` on `DIFF_AUTHOR_FBID` (most reliable)
2. **Fallback**: `knowledge_filtered_search` with `keywords="<username>"` and `doc_types=["DIFF"]`
3. **Cross-check**: Infer the user's project/block areas from their recent diffs (look at diff titles, file paths, and summaries from the primary query results), then run additional `knowledge_filtered_search` queries for each inferred project area, filtered by the reporting date range, and check if any returned diffs are authored by the user
4. **Verify authorship**: For diffs found via keyword search, always fetch diff details with `include_diff_author=true` to confirm the user is the author before including in the report
5. **Cross-reference from tasks**: When collecting tasks, check for diff IDs referenced in task descriptions (e.g., "Diff: D12345678" or "Related to D12345678"). These referenced diffs may be authored by the user but missed by keyword search. Fetch details with `include_diff_author=true` to verify and include if authored by the user

**Example cross-check for project-specific diffs:**
```python
# Infer project areas from the user's already-found diffs (titles, file paths)
# Then search by each inferred project/component area
for project_area in inferred_project_areas:
    knowledge_filtered_search(
        doc_types=["DIFF"],
        keywords=project_area,
        start_creation_time="<start_date>",
        end_creation_time="<end_date>"
    )
    # For each result, verify author matches user via get_phabricator_diff_details

# Also check diffs referenced in tasks
for task in collected_tasks:
    # Extract diff IDs like D12345678 from task description
    # Fetch diff details and verify authorship
    get_phabricator_diff_details(diff_number, include_diff_author=True)
```

### Getting Detailed Diff Information

**IMPORTANT:** Do NOT just copy diff titles. Fetch detailed info and provide 1-2 sentence descriptions explaining:
- What was actually changed/implemented
- Why the change was made
- Impact or benefit

Use `get_phabricator_diff_details` tool with `include_diff_summary=true` and `include_raw_diff=true`.

### Handling Diffs with Empty Summaries

**CRITICAL:** Some diffs have empty or minimal summaries (e.g., just "Title" or "compliance"). Do NOT skip these diffs or use their title as the description. Instead:

1. **Fetch the raw diff**: Use `get_phabricator_diff_details` with `include_raw_diff=true`
2. **Analyze the actual code changes** to understand:
   - **File paths**: Which module/component was changed (e.g., `src/` = source code, `verif/` = verification, `tests/` = tests)
   - **Change patterns**: What was added, removed, or modified
   - **Logic changes**: Read the diff hunks to understand the functional change
3. **Generate a meaningful description** based on the code analysis, not the empty summary

**Example:**
```text
Diff D12345678: "[ProjectX] Fix edge case"
Summary: (empty)

→ Fetch raw diff → see changes to state_machine.v (gating size==0),
  new test sequences in test_lib.svh

→ Generated description: "Fixed state machine to handle zero-size edge
   case that caused buffer underflow. Added two directed tests for
   boundary conditions."
```

---

## Part 2: Collecting Draft Commits (Local Sapling Repository)

### Overview

In addition to published diffs, collect draft commits that exist locally but haven't been published to Phabricator yet. These represent work-in-progress that should be included in status reports.

### Step 1: Check if Sapling Repository Exists

```bash
# Check if we're in a Sapling repo
sl root 2>/dev/null && echo "Sapling repo found" || echo "Not a Sapling repo"
```

### Step 2: Get Draft/Unpublished Commits

Draft commits are those that:
- Exist in the local repository
- Have NOT been published to Phabricator (no Differential Revision in commit message)
- Were created/modified in the last 7 days

```bash
# Get all local commits from the last 7 days
sl log -r "date(-7) and draft()" -T "{node|short}\t{desc|firstline}\t{date|age}\n"
```

**Alternative: Get commits without diff IDs:**
```bash
# Get commits that don't have "Differential Revision" in their message
sl log -r "date(-7) and not public()" -T "{node|short}\t{desc|firstline}\t{date|age}\t{desc}\n" | grep -v "Differential Revision"
```

**Get detailed commit information:**
```bash
# For each draft commit, get full description
sl log -r "<commit_hash>" -T "{desc}\n"
```

**Get actual code changes for analysis:**
```bash
# Get diff statistics for each commit
sl diff -c "<commit_hash>" --stat

# Get full diff for detailed analysis
sl diff -c "<commit_hash>"
```

### Step 3: Analyze Changes (Not Just Commit Messages)

**CRITICAL:** Do NOT rely solely on commit messages. Analyze the actual code changes to generate accurate descriptions.

**Workflow for each draft commit:**

1. **Get the diff:**
   ```bash
   sl diff -c "<commit_hash>" --stat
   ```

2. **Analyze changed files to understand:**
   - **File paths**: What module/component was changed?
   - **File types**: Is it test code, implementation, config, documentation?
   - **Change size**: Lines added/removed indicate scope
   - **Patterns**: Multiple files in same directory suggest feature work

3. **Read actual changes if needed:**
   ```bash
   # Get full diff for deeper analysis
   sl diff -c "<commit_hash>"
   ```

4. **Generate description based on:**
   - Commit message (WHAT the developer said)
   - Changed files (WHERE the changes are)
   - Diff content (WHAT actually changed)
   - Context from file paths (WHY - what feature/component)

**Example Analysis:**

```bash
$ sl diff -c abc1234 --stat
 arvr/silicon/rls/fcv/vap/node_marcher/vap_lvl_nm.sv     | 45 ++++++++++++++++++++++++++---
 arvr/projects/ferm/RefFunc/VAP/RefMGU/LevelNodeMarcher.cpp | 23 +++++++--------
 arvr/projects/ferm/RefFunc/VAP/unittest/Mgu4Test.cpp       | 12 ++++++++
 3 files changed, 65 insertions(+), 15 deletions(-)
```

**Inferred description:**
- Changed files in node_marcher (both RTL and reference model)
- Added test coverage (unittest file)
- Significant RTL changes (45 lines in .sv file)
→ "Enhanced node marcher RTL implementation with improved state handling and added corresponding reference model updates and test coverage"

### Step 4: Get Uncommitted Changes

Also include uncommitted changes that are staged or modified:

```bash
# Check for uncommitted changes
sl status

# Get diff of uncommitted changes
sl diff --stat
```

### Step 4: Categorizing Draft Work

**Draft Commits:**
- Commits that exist locally but haven't been published
- Include commit hash and description

**Uncommitted Changes:**
- Modified files not yet committed
- Provide summary of affected files/areas

### Integration into Report

Add draft commits to the Summary section with a special marker:

```markdown
## Summary

**[Project/Feature A]**

• [Description of published diff] (D123 - Landed)

• [Description of draft commit] (Draft: abc1234 - Not yet published)

• [Description of uncommitted changes] (WIP - Uncommitted)
```

### Example Commands Workflow

```bash
# 1. Check if in Sapling repo
if sl root >/dev/null 2>&1; then

  # 2. Get draft commits from last 7 days
  echo "=== Draft Commits ==="
  sl log -r "date(-7) and draft()" -T "{node|short}|{desc|firstline}|{date|shortdate}\n"

  # 3. Get uncommitted changes summary
  echo "=== Uncommitted Changes ==="
  sl status --no-status | head -20

  # 4. For detailed commit info
  for commit in $(sl log -r "date(-7) and draft()" -T "{node|short}\n"); do
    echo "--- Commit: $commit ---"
    sl log -r "$commit" -T "{desc}\n"
    sl diff -c "$commit" --stat
  done
fi
```

### Filtering Logic

**Include:**
- Commits with substantive code changes (not just test commits)
- Commits related to active projects
- Uncommitted changes in actively developed files

**Exclude:**
- Temporary/experimental commits marked with "WIP", "test", "tmp"
- Commits older than reporting period
- Backup commits

### Alternative: Using get_local_changes Tool

If the `get_local_changes` tool is available, use it for comprehensive information:

```python
# This tool provides:
# - Uncommitted changes with diffs
# - Recent commits in the stack
# - Commit details including descriptions
get_local_changes()
```

---

## Part 3: Collecting Tasks

**IMPORTANT:** Only collect tasks where:
- **Creator**: User created the task, OR
- **Owner**: User is the owner/assignee of the task

Do NOT include tasks where user is only subscribed, CC'd, or mentioned.

### Method 1: Knowledge Search (Primary)

**IMPORTANT:** Search with broader time range (30-60 days) since tasks created earlier may still be active/relevant.

```python
knowledge_search(
    doc_types=["TASK"],
    keywords="<username>",
    start_creation_time="<30-60 days ago>"
)
```

**Filter Results** - Only include tasks where:
- User's username appears as author/creator
- User's username appears as owner/assignee
- Task content contains user's file paths (e.g., `/nfs/project/.../username/...`)

**Exclude** tasks where user is only subscribed, CC'd, mentioned in comments, or part of a team tag.

**For Weekly Report:** From filtered results, highlight tasks that were:
- Created this week
- Updated/worked on this week
- Closed this week

### Method 2: Tasks CLI (Alternative)

```bash
which tasks 2>/dev/null && echo "available" || echo "not available"
tasks --agent-enabled search --name $USER
```

### Method 3: GraphQL Query (Fallback - if knowledge_search fails)

First get the user's FBID:
```bash
meta graphql.query execute --query 'query { viewer { actor { id } } }'
```

Query tasks authored by user:
```bash
meta graphql.query execute --query 'query($fbid: ID!, $after_time: Int!) {
  tasks_custom_search_query(args: {query: "{\"key\":\"AND\",\"children\":[{\"key\":\"EQUALS\",\"field\":\"TASK_AUTHOR_FBID\",\"value\":\"" + $fbid + "\"},{\"key\":\"GREATER_THAN_OR_EQUALS\",\"field\":\"TASK_CREATED_TIME\",\"value\":\"" + $after_time + "\"}]}"}) {
    tasks(first: 100) {
      edges {
        node { id title status priority created_time updated_time }
      }
    }
  }
}' --variables '{"fbid": "<user_fbid>", "after_time": <unix_timestamp>}'
```

Query tasks assigned to user:
```bash
meta graphql.query execute --query 'query($fbid: ID!, $after_time: Int!) {
  tasks_custom_search_query(args: {query: "{\"key\":\"AND\",\"children\":[{\"key\":\"EQUALS\",\"field\":\"TASK_ASSIGNEE_FBID\",\"value\":\"" + $fbid + "\"},{\"key\":\"GREATER_THAN_OR_EQUALS\",\"field\":\"TASK_UPDATED_TIME\",\"value\":\"" + $after_time + "\"}]}"}) {
    tasks(first: 100) {
      edges {
        node { id title status priority created_time updated_time }
      }
    }
  }
}' --variables '{"fbid": "<user_fbid>", "after_time": <unix_timestamp>}'
```

---

## Part 3b: Parallel Processing

**IMPORTANT:** Use parallel threads to speed up data collection:

### Diff Collection (2-5 threads based on count)
Split diffs by area/category and fetch details in parallel:
- Group diffs by project/feature area from diff titles (e.g., `[ProjectA]`, `[FeatureB]`)
- Create one thread per group
- Run all threads simultaneously

### Task Collection (1 thread)
Run task search in parallel with diff collection.

### Example Parallel Execution
```python
# Launch all threads simultaneously using task tool with multiple subagents
task([
    {"title": "Diff details - Group 1", "prompt": "Get details for [Area1] diffs..."},
    {"title": "Diff details - Group 2", "prompt": "Get details for [Area2] diffs..."},
    {"title": "Diff details - Group 3", "prompt": "Get details for [Area3] diffs..."},
    {"title": "Diff details - Group 4", "prompt": "Get details for other diffs..."},
    {"title": "Tasks", "prompt": "Get tasks where user is creator/owner..."}
])
```

---

## Part 4: Workplace Posts

### Method 1: GraphQL Query (Recommended)

The `knowledge_search` approach with `keywords="<username>"` may return posts where the user is merely mentioned, not just posts authored by the user. For more accurate results, use the GraphQL endpoint directly.

First get the user's FBID:
```bash
meta graphql.query execute --query 'query { viewer { actor { id } } }'
```

Query Workplace posts authored by the user:
```bash
meta graphql.query execute --query 'query($author_id: ID!, $after_time: Int!) {
  workplace_posts_by_author(author_id: $author_id, first: 50) {
    edges {
      node {
        id
        message
        created_time
        permalink_url
        group {
          name
          id
        }
      }
    }
  }
}' --variables '{"author_id": "<user_fbid>", "after_time": <unix_timestamp_7_days_ago>}'
```

**Note:** If the above query fails, fall back to Method 2.

### Method 2: Knowledge Search (Fallback)

**Important Caveat:** This may return posts where the user is mentioned, not just authored. Filter results manually.

```python
knowledge_search(
    doc_types=["GROUP_POST"],
    keywords="<username>",
    start_creation_time="<7 days ago>",
    workplace_group_ids=[<group_id_1>, <group_id_2>]
)
```

**Post-Filter:** Only include posts where the author field matches the user's name/username.

### Extracting Action Items from Past Status Reports

**IMPORTANT:** When parsing workplace posts, look for previous status reports authored by the user. Check the "Future Plan" section of past reports for action items (AIs) that were planned.

**Workflow:**
1. Identify past status report posts (look for titles like "Weekly Status Report", "Status Update", etc.)
2. Extract items from the "Future Plan" section
3. Cross-reference with current week's diffs and tasks:
   - If the planned item was completed → include in Summary section
   - If the planned item was NOT implemented → carry forward to the new "Future Plan" section
4. This ensures continuity and accountability across weekly reports

**Example:**
```markdown
## Future Plan (from last week's report)
• Implement feature X - Target: Jan 7
• Fix bug in module Y - Target: Jan 8

## This week's assessment:
• Feature X: Completed (D123456 - Landed) → Add to Summary
• Bug fix Y: Not done → Add to new Future Plan with updated target
```

### Google Chat Debug Conversations

**IMPORTANT:** This section requires the Google Chat plugin (`google-chat`). Follow the dependency check logic from Part 0:
- If the plugin is not installed and the user has **not** previously declined → prompt to install via `AskUserQuestion`
- If the user previously declined (check `~/.claude/weekly-status-report-prefs.json` for `skip_google_chat: true`) → skip this section silently
- If the user chooses to install → run `/plugin install google-chat` and inform them to restart Claude Code if needed

**PREFERRED:** Use the `google-chat` plugin skills (`gchat-search`, `gchat-summarize`) instead of calling the `google_chat` MCP tool directly. The skills provide a guided workflow with better error handling and output formatting.

Use the Google Chat plugin to collect debug conversations, technical discussions, and collaboration threads from the reporting period. This captures context that doesn't appear in diffs or tasks — design decisions, debug sessions, knowledge sharing, and cross-team coordination.

#### Step 1: Search for Relevant Conversations

Use the `google-chat:gchat-search` skill to search for conversations related to the user's active projects:

```text
# Invoke the gchat-search skill with project-related keywords
Skill: google-chat:gchat-search
Args: "<project_keyword_1> OR <project_keyword_2> OR <project_keyword_3>"
```

Infer search keywords from the user's diffs and tasks collected earlier (e.g., project names, feature names, component names).

#### Step 2: Summarize Key Spaces

For spaces with significant activity, use the `google-chat:gchat-summarize` skill:

```text
# Invoke the gchat-summarize skill for a specific space
Skill: google-chat:gchat-summarize
Args: "<space_id>"
```

#### Step 3: Direct MCP Tool Usage (Fallback)

If the skills are unavailable but the `google_chat` MCP tool exists, use it directly:

**3a. List available spaces:**

```python
google_chat(action="list_spaces", page_size=50)
```

This returns both named spaces and DM spaces. Categorize them:
- **Named spaces** (`space_type: "SPACE"` or `"GROUP_CHAT"`): Identified by `display_name` — project channels, team spaces, debug/oncall spaces, etc.
- **DM spaces** (`space_type: "DIRECT_MESSAGE"`): Have `display_name: null` — these require extra steps to identify (see Step 3d)

**3b. Collect messages from each relevant space:**

For each relevant space, fetch messages from the reporting period:

```python
google_chat(action="list_messages", space_id="<space_id>", page_size=50)
```

Paginate through results if needed using the `page_token` from previous responses.

**3c. Search for specific debug topics:**

Use the `gchat-search` skill or search by keywords related to the user's active projects:

```python
# Search for debug-related conversations
gchat_search(query="<project_name> debug", space_id="<space_id>")
gchat_search(query="<username> fix", space_id="<space_id>")
```

**3d. Summarize key threads:**

For threads with significant discussion (design decisions, debug sessions, incident response), use the `gchat-summarize` skill to extract key points:

```python
gchat_summarize(space_id="<space_id>")
```

Or get specific thread details:

```python
google_chat(action="get_message", space_id="<space_id>", message_id="<message_id>")
```

**3e. Find and read direct messages with collaborators:**

DM spaces from `list_spaces` have `display_name: null`, so you cannot identify who they are with from the listing alone. Use the following approach to discover and read relevant DMs:

**Infer collaborators from collected data:**
Extract collaborator names from the data already gathered in earlier steps:
- Diff reviewers (from `get_phabricator_diff_details` with `include_reviewers=true`)
- Task assignees and subscribers
- People mentioned in space conversations

**Find DM spaces by collaborator name:**
```python
# For each inferred collaborator, find the DM space
google_chat(action="find_dm_space_by_name", user_name="<collaborator_name>")
```

**Read messages from discovered DM spaces:**
```python
# Then list messages in that DM space
google_chat(action="list_messages", space_id="<dm_space_id>", page_size=30)
```

**Also process unknown DM spaces from Step 3a:**
For DM spaces returned by `list_spaces` that weren't matched to a known collaborator, fetch recent messages to check for relevant debug or design discussions:
```python
# For each unidentified DM space from list_spaces
google_chat(action="list_messages", space_id="<dm_space_id>", page_size=10)
# Check if messages are work-related and from the reporting period
```

#### Filtering and Categorizing GChat Activity

**Include in the report:**
- Debug sessions where the user helped investigate or resolve issues
- Design discussions where decisions were made
- Cross-team coordination threads (e.g., API changes, integration work)
- Knowledge sharing — answers the user provided to team questions
- Incident/oncall conversations the user participated in

**Exclude from the report:**
- Casual/social conversations
- Simple acknowledgments ("thanks", "got it", "lgtm")
- Automated bot messages
- Messages where the user was only passively CC'd

#### Integration into Report

Add GChat insights to the Summary section under relevant project areas or as a dedicated subsection:

```markdown
## Summary

**[Project/Feature A]**

• Implemented feature X (D123 - Landed)

• Debugged intermittent test failure in module Y — root cause identified via GChat discussion with [collaborator] as race condition in reset logic (GChat: [space_name])

**Key Discussions & Debug Sessions:**

• Collaborated with [team/person] on [topic] — resolved [outcome] (GChat: [space_name])

• Design discussion on [feature] — decided to use [approach] based on [rationale] (GChat: [space_name])
```

---

## Part 5: Thanks Received

### Fetching Thanks Using GraphQL

To include "Thanks" received by the user in the status report, use the GraphQL endpoint for thanks data.

First get the user's FBID:
```bash
meta graphql.query execute --query 'query { viewer { actor { id } } }'
```

Query thanks received by the user:
```bash
meta graphql.query execute --query 'query($recipient_id: ID!, $first: Int!) {
  thanks_received(recipient_id: $recipient_id, first: $first) {
    edges {
      node {
        id
        message
        created_time
        sender {
          name
          profile_picture_uri
        }
        skill {
          name
        }
      }
    }
  }
}' --variables '{"recipient_id": "<user_fbid>", "first": 20}'
```

**Alternative:** Use the graphql-query skill to discover the exact endpoint:
```
use https://www.internalfb.com/code/fbsource/fbcode/claude-templates/components/skills/graphql-query/SKILL.md
and find what's the graphql powering thanks like https://www.internalfb.com/careers/thanks
```

### Integrating Thanks into Report

Include a "Recognition" or "Thanks Received" section in the report:

```markdown
## Recognition Received

• **[Sender Name]**: "[Thanks message]" - [Skill/Category]

• **[Sender Name]**: "[Thanks message]" - [Skill/Category]
```

---

## Part 6: Calendar & Meetings

### Fetching Calendar Invites via GraphQL

To include key meetings attended during the reporting period:

```bash
meta graphql.query execute --query 'query($start_time: Int!, $end_time: Int!) {
  viewer {
    calendar_events(
      start_time: $start_time,
      end_time: $end_time,
      first: 100
    ) {
      edges {
        node {
          id
          title
          start_time
          end_time
          location
          attendees {
            name
            response_status
          }
          organizer {
            name
          }
        }
      }
    }
  }
}' --variables '{"start_time": <unix_timestamp_start>, "end_time": <unix_timestamp_end>}'
```

**Reference:** See paste [P2043406971](https://www.internalfb.com/intern/paste/P2043406971) for additional calendar query patterns.

### Filtering Interesting Meetings

Not all meetings are worth mentioning. Focus on:
- 1:1s with manager or skip-level
- Project sync meetings
- Design reviews
- Cross-functional discussions
- Meetings where key decisions were made

### Meeting Template in Report

```markdown
**Key Meetings & Discussions:**

• **[Meeting Title]** with [Attendees]: [Key outcomes/decisions]

• **[Design Review]**: [What was reviewed and outcome]
```

---

## Part 7: Google Workspace Integration

### Google Docs Activity

**IMPORTANT:** Reading Google Doc content requires the `google-docs` skill. Follow the dependency check logic from Part 0:
- If the skill is not available and the user has **not** previously declined → prompt to install/enable
- If the user previously declined (check `~/.claude/weekly-status-report-prefs.json` for `skip_google_docs_skill: true`) → skip reading doc content (still discover docs via `knowledge_filtered_search`, just don't read their full content)

**Step 1: Discover Recent Google Docs**

Use `knowledge_filtered_search` to find Google Docs authored by or mentioning the user:

```python
knowledge_filtered_search(
    doc_types=["GOOGLE_DOCUMENT", "GOOGLE_SPREADSHEET", "GOOGLE_PRESENTATION"],
    keywords="<username>",
    start_creation_time="<7 days ago>",
    end_creation_time="<today>"
)
```

**Step 2: Read Document Content**

**IMPORTANT:** Use the `google-docs` skill (NOT MCP `knowledge_load`) to read Google Doc content. The `google-docs` skill provides better formatting, handles authentication properly, and supports multiple backends (gdocs CLI, Python script, MCP tool).

```text
# Invoke the google-docs skill to read a document
Skill: google-docs
Args: "read <document_url>"
```

**Step 3: Categorize Documents**

For each document found, categorize it as:
- **Authored**: User created or is primary author — include in Summary under relevant project area
- **Contributed**: User commented or edited — mention as collaboration activity
- **Meeting Notes**: Auto-generated from Zoom/calendar — include in Key Meetings section
- **Design Docs**: Strategic/technical documents — highlight as key deliverables

**Step 4: Extract Key Information**

For each relevant document, extract:
- What the document is about (1-2 sentences)
- Key decisions or insights documented
- Action items or next steps
- Who else was involved

**Integration into Report:**
```markdown
**[Project/Feature A]**

• Authored "Vision Document Title" defining the strategic direction for [area] (Google Doc)

• Participated in design review for [feature] — decided on [approach] (Google Doc: [title])
```

### GChat Conversations

**PREFERRED:** Use the `google-chat` plugin skills as described in Part 4 above.

**Knowledge Search Alternative (if GChat plugin unavailable):**
```python
knowledge_search(
    doc_types=["MEETING_NOTE"],
    keywords="<topic_or_project>",
    start_creation_time="<7 days ago>"
)
```

---

## Part 8: JIRA Integration

> **Status:** 🚧 PLACEHOLDER

```python
# PLACEHOLDER: JIRA API
jira_get_activity(
    user="<username>",
    start_date="<7 days ago>",
    projects=["PROJ1", "PROJ2"],
    activity_types=["assigned", "created", "commented", "resolved"]
)
```

**Manual JQL:**
```
assignee = currentUser() AND updated >= -7d ORDER BY updated DESC
```

---

## Part 9: Report Formatting

### CRITICAL: Description Quality

1. **DO NOT** copy diff titles verbatim
2. **DO** provide 1-2 sentence descriptions
3. **DO** group by project/feature area
4. **DO NOT** expand acronyms - Use acronyms as-is unless the diff or task explicitly provides the expanded form in its description or context

### CRITICAL: Blending Tasks into Summary

**Tasks should NOT have a separate section.** Instead, blend tasks into the Summary section as bullets based on context:

- If a task is related to a project area, include it as a bullet under that area
- If a task is a blocker, include it in the "What's Blocked" section
- If a task was closed this week, mention it alongside related diffs
- Reference the task ID (e.g., T123456) inline with the description

**Example:**
```markdown
**[Project Area]**

• Implemented feature X with configuration updates (D123 - Landed)

• Investigating issue where component Y fails under condition Z (T456789 - Open, related to D124)

• Fixed regression in module W (D125 - Landed, closes T456780)
```

### CRITICAL: Line Spacing

Each bullet MUST be on its own line with blank lines between:

**CORRECT:**
```markdown
**[Project Area]**

• First change description with context (D123 - Landed)

• Second change description explaining what and why (D124 - In Review)

• Bug investigation for specific issue (T456 - Open)
```

**INCORRECT:**
```markdown
**[Project Area]** • First change (D123 - Landed) • Second change (D124)
```

### Report Template

```markdown
# Weekly Status Report
**Period:** [Start Date] - [End Date]
**Author:** [Username]
**Status:** [On Track / At Risk / Off Track]

## Highlight of the Week
• **[Achievement 1]**: [Brief description and impact]
• **[Achievement 2]**: [Brief description and impact]
• Landed X diffs, with Y in review
## What's Blocked

• **T123456: [Task Title]**
  - Blocker: [Description]
  - Waiting on: [Person/Team]
## Risks

• **[Risk 1]**: [Description]
  - Mitigation: [How to address]
## Summary

**[Project/Feature A]**
• [Detailed description of diff change] (D123 - Landed)
• [Detailed description of related task/issue being investigated] (T456 - Open)
• [Detailed description of diff change that closes a task] (D124 - Landed, closes T789)
**[Project/Feature B]**
• [Detailed description of change] (D125 - Landed)
• [Task update or progress] (T012 - In Progress)
**Key Meetings & Discussions:**
• [Meeting 1]: [Key outcomes]
**Workplace/Communication Highlights:**
• Posted announcement about [topic]
## Future Plan

• [Planned item 1] - Target: [Date]
• [Planned item 2] - Target: [Date]
• [Carried forward from last week: Item not completed] - Target: [New Date]
## Learnings
• **Technical**: [Insight]
• **Process**: [Observation]
```

---

## Part 10: Biweekly & Monthly Reporting

> **Status:** 🚧 PLACEHOLDER

### Biweekly Adjustments

Replace `IS_WITHIN_LAST_7_DAYS` with `IS_WITHIN_LAST_14_DAYS`:

```bash
--variables '{"search": "{\"key\":\"AND\",\"children\":[{\"key\":\"EQUALS_ME\",\"field\":\"DIFF_AUTHOR_FBID\"},{\"key\":\"IS_WITHIN_LAST_14_DAYS\",\"field\":\"DIFF_CREATED_TIME\"}]}"}'
```

### Biweekly Template

```markdown
# Biweekly Status Report
**Period:** [Start] - [End] (2 weeks)

## Highlights (Week 1: [Date Range])

• [Key achievement]

## Highlights (Week 2: [Date Range])

• [Key achievement]

## Summary by Project

**[Project A]**

• Week 1: [Summary]

• Week 2: [Summary]

• Total: X diffs landed, Y tasks completed
```

### Monthly Template

```markdown
# Monthly Status Report
**Period:** [Month Year]

## Executive Summary

• **Key Milestone 1**: [Description]

• **Metrics**: Landed X diffs, Closed Z tasks

## Weekly Breakdown

### Week 1 ([Date Range])
• [Activities]

### Week 2 ([Date Range])
• [Activities]

### Week 3 ([Date Range])
• [Activities]

### Week 4 ([Date Range])
• [Activities]

## Project Summary

**[Project A]**

• **Goal**: [Planned]

• **Achieved**: [Accomplished]

• **Remaining**: [Left]

## Learnings & Retrospective

• **What went well**: [Observations]

• **What could improve**: [Areas]
```

---

## Part 11: File Output

After generating the report, offer to save it as a markdown file. This supports cron job automation and historical tracking.

### Checking Saved Preference

Read `~/.claude/weekly-status-report-prefs.json` for existing output settings:

```python
prefs = read_file("~/.claude/weekly-status-report-prefs.json")

if "output_to_file" in prefs:
    # Preference already set — use it
    if prefs["output_to_file"]:
        output_dir = prefs["output_directory"]
        save_report(output_dir)
else:
    # First time — ask the user
    AskUserQuestion(...)
```

### Prompting the User (First Time Only)

If `output_to_file` is not set in the prefs file, use `AskUserQuestion`:

```text
"Would you like to save the report as a markdown file?
 This is useful for historical tracking and cron job automation."

Options:
  "Yes, save to file"
  "No, display only"
```

If the user chooses "Yes", ask for the output directory:

```text
"Where should reports be saved? Enter a directory path."

Options:
  "~/gdrive/AI/weekly-reports (Recommended — syncs via Google Drive)"
  "~/worknotes/weekly-reports"
  "Custom path"
```

Save both preferences to `~/.claude/weekly-status-report-prefs.json`:

```json
{
  "output_to_file": true,
  "output_directory": "~/gdrive/AI/weekly-reports",
  "preferences_updated": "<YYYY-MM-DD>"
}
```

If the user chooses "No", record that as well:

```json
{
  "output_to_file": false,
  "preferences_updated": "<YYYY-MM-DD>"
}
```

### File Naming Convention

Use the reporting period end date as the filename:

```text
<output_directory>/weekly-status-<YYYY-MM-DD>.md
```

Example: `~/gdrive/AI/weekly-reports/weekly-status-2026-02-24.md`

For biweekly/monthly reports:
```text
<output_directory>/biweekly-status-<YYYY-MM-DD>.md
<output_directory>/monthly-status-<YYYY-MM>.md
```

### Writing the File

```python
import os
from datetime import date

output_dir = os.path.expanduser(prefs["output_directory"])
os.makedirs(output_dir, exist_ok=True)

# Use the reporting period end date, NOT today's date
filename = f"weekly-status-{report_end_date.isoformat()}.md"
filepath = os.path.join(output_dir, filename)

write_file(filepath, report_content)
print(f"Report saved to: {filepath}")
```

### Cron Job Support

When running as a cron job (e.g., via `10x-engineer:overnight` or a scheduled task), the file output preference allows fully unattended report generation:

1. All preferences are pre-configured in `~/.claude/weekly-status-report-prefs.json`
2. No `AskUserQuestion` prompts are needed — all settings are read from the prefs file
3. The report is written to the configured directory automatically
4. If the output directory is on Google Drive (e.g., `~/gdrive/AI/weekly-reports`), the report syncs automatically

**Example cron invocation:**
```bash
# Generate weekly status report every Monday at 9am
0 9 * * 1 claude -p "Generate my weekly status report" --allowedTools 'Bash(*)' 2>&1 >> /tmp/weekly-report.log
```

---

## Best Practices

1. **Run early in the week**: Monday morning for previous week
2. **Add context**: Explain impact, not just what changed
3. **Describe, don't title**: Meaningful descriptions for diffs
4. **Highlight blockers**: Make visible for escalation
5. **Include next steps**: Set expectations
6. **Keep concise**: Focus on high-impact items
7. **Do NOT expand acronyms**: Use acronyms as-is from diff/task titles
8. **Group related work**: Combine related diffs/tasks under same project area
9. **Blend tasks into summary**: No separate task section - integrate contextually

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| No diffs returned | Verify authorship and date range |
| Tasks CLI not working | Use knowledge_search fallback |
| Only 1 task found | Use broader time range (30-60 days) |
| Calendar unavailable | Use knowledge_search for meeting_note |
| GChat plugin not installed | Prompt user to install via Part 0 dependency check |
| GChat access denied | Check plugin installation; use manual summary as fallback |
| Google Docs skill unavailable | Prompt user to install; fall back to knowledge_filtered_search summaries |
| User previously declined a plugin | Check `~/.claude/weekly-status-report-prefs.json`; skip silently |
| Want to re-enable a declined plugin | Delete or edit `~/.claude/weekly-status-report-prefs.json` |
