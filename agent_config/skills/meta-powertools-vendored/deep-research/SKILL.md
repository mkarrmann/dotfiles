---
name: deep-research
description: Advanced multi-agent research system for Meta's codebase and infrastructure. This skill should be used when users need deep, comprehensive research across Meta's monorepo, requiring analysis of code patterns, diffs, build systems, or data infrastructure. Especially useful for complex questions requiring multiple perspectives, extensive codebase exploration, or investigation across multiple Meta systems.
---

# Meta Deep Research

## Overview

This skill enables systematic, multi-agent deep research specifically optimized for Meta's development environment, leveraging BigGrep, Mercurial, Buck2, Presto, and other Meta-specific tools.  it uses a **team-based orchestration model** where the leader (you) creates a coordinated research team, manages a shared task list, monitors agent progress, and actively steers the investigation based on emerging findings.

## Research Process Architecture

### Query Classification

Analyze the user's query and classify it into one of three categories:

#### 1. Depth-First Queries
When the problem requires multiple perspectives on the same issue:
- **Examples**: "what are the performance implications of this architecture?", "how did this system evolve over time?", "what are all the security considerations for this component?"
- **Approach**: Create **one task per identified perspective/angle**.  if you identify 7 angles, create 7 tasks.  don't artificially cap the number of tasks -- let the number of threads dictate the task count.  (agents are capped separately; see pool size guidelines below.)
- **Meta Context**: useful for analyzing design decisions, understanding system evolution through diffs, or evaluating technical debt

#### 2. Breadth-First Queries
When the problem can be broken into distinct, independent sub-questions:
- **Examples**: "compare all the services in this platform", "list all Buck targets and their dependencies", "find all usages of this API across teams"
- **Approach**: Create **one task per distinct component or sub-question**.  if reconnaissance reveals 15 services, create 15 tasks.  agents will work through them sequentially.
- **Meta Context**: ideal for cross-team analysis, dependency mapping, or API migration studies

#### 3. Straightforward Queries
When the problem is focused and well-defined:
- **Examples**: "what Buck target owns this file?", "find the diff that introduced this bug", "what's the Presto schema for this table?"
- **Approach**: 1-2 tasks, 1-2 agents.  skip team creation if truly trivial -- just use a single Task tool call.

## Pre-Research Reconnaissance

**CRITICAL: Before creating a team or spawning agents, verify you have enough context to formulate effective research tasks.**

If the user's request is ambiguous, underspecified, or covers a domain you lack context on, do NOT immediately dispatch agents with vague instructions.

### Step 1: Initial Reconnaissance
Perform a quick, lightweight exploration yourself (the orchestrator) to build baseline understanding:
- Run a few targeted searches (fbgs/fbgr/fbgf) to understand the landscape
- Read 1-2 key files to grasp the system's structure
- Check relevant BUCK files or directory layouts to map the terrain
- Use MCP knowledge search tools to find relevant internal documentation

This should be brief (3-5 tool calls max) -- just enough to understand what exists and what questions to ask.

### Step 2: Clarify with the User
Once you have baseline context, use `AskUserQuestion` to clarify what the user actually wants before committing to a full research plan.  for example:
- "I found 3 subsystems related to X.  which are you interested in: A, B, or C?"
- "this system has both a legacy and modern implementation.  should I research both or focus on the current one?"
- "I see this spans multiple teams.  do you want a cross-team analysis or focus on your team's usage?"

### When to Skip Reconnaissance
You can skip this step and go directly to team creation when:
- The user's query is already specific and well-scoped
- You already have sufficient context from the conversation history
- The query maps clearly to a known system or file path

### When Reconnaissance is Essential
Always do reconnaissance first when:
- The user uses vague terms ("how does X work?", "tell me about Y")
- The domain is unfamiliar and you can't confidently design research tasks
- The query could be interpreted in multiple ways
- You're unsure which tools or search terms will yield useful results

## Team-Based Orchestration

### Architecture Overview

```text
Orchestrator (you)
  │
  ├── TeamCreate("deep-research-{topic}")
  │     └── shared task list at ~/.claude/tasks/deep-research-{topic}/
  │
  ├── Spawn trust-gate-verifier (parallel, run_in_background=true)
  │     └── prompt: ~/.claude/skills/deep-research/subagents/trust_gate_verifier_prompt.md
  ├── Spawn claim-extractor (parallel, run_in_background=true)
  │     └── prompt: ~/.claude/skills/deep-research/subagents/claim_extractor_prompt.md
  ├── TaskCreate × N  (one per research thread)
  │     └── with addBlockedBy/addBlocks for phased dependencies
  │
  ├── Task(teammate, team_name=..., run_in_background=true) × pool_size
  │     ├── meta-codebase-explorer  (code search, Buck, jf, sl)
  │     ├── meta:code_search        (deep code pattern analysis)
  │     ├── knowledge_search        (internal docs, wiki, workplace)
  │     └── data-agent              (Presto, data infrastructure)
  │
  ├── Orchestrator monitors, redirects, creates follow-up tasks
  │     └── proactive communication: check-ins, cross-pollination, pivots
  │
  ├── Peer-to-peer: teammates can directly message each other
  │
  ├── Synthesis agent (general-purpose) integrates all findings
  │
  ├── shutdown_request to all teammates
  │
  └── TeamDelete (cleanup)
```

### Phase 1: Team Setup

After reconnaissance and query classification:

#### 1. Create the team
```text
TeamCreate(team_name="deep-research-{short-topic}", description="Research: {topic}")
```

#### 2. Create tasks with dependency ordering

Each task should have:
- A clear, single-sentence objective
- Explicit scope boundaries (what to explore AND what NOT to explore)
- Tool budget (20/40/60 calls)
- Expected output format

**Use task dependencies to enforce research phases.**  research often has natural ordering -- you need to map the landscape before deep-diving into subsystems.

```text
# Phase 1: Foundation tasks (no dependencies)
TaskCreate("Map the auth system directory structure and key entry points")  → task #1
TaskCreate("Find internal documentation on auth architecture")             → task #2

# Phase 2: Deep-dive tasks (depend on phase 1)
TaskCreate("Investigate token generation and JWT patterns")                → task #3
TaskCreate("Investigate session management and storage")                   → task #4
TaskCreate("Investigate RBAC and permission checks")                       → task #5
TaskUpdate(taskId="3", addBlockedBy=["1"])
TaskUpdate(taskId="4", addBlockedBy=["1"])
TaskUpdate(taskId="5", addBlockedBy=["1", "2"])

# Phase 3: Cross-cutting analysis (depends on phase 2)
TaskCreate("Cross-reference security patterns across all auth subsystems") → task #6
TaskUpdate(taskId="6", addBlockedBy=["3", "4", "5"])
```

Agents automatically respect dependencies -- they only pick up tasks whose blockers are all completed.  this eliminates the most common orchestration failure: agents starting analysis before the landscape is understood.

**When to use dependencies vs. flat tasks:**
- **Use dependencies** when findings from one task are prerequisites for another
- **Use flat tasks** when threads are genuinely independent (breadth-first queries)
- **Mix both** for depth-first queries: flat within a phase, dependencies between phases

#### 3. Select agent types per task

**Not all tasks need the same agent type.**  match the agent to the work:

| Task Nature | Agent Type | Capabilities |
|-------------|-----------|-------------|
| Code exploration, Buck targets, diffs | `meta-codebase-explorer` | fbgs, fbgr, fbgf, buck2, jf, sl, MCP tools |
| Deep code pattern analysis, API usage | `meta:code_search` | Optimized code search, pattern matching across monorepo |
| Internal docs, wiki, workplace posts | `knowledge_search:knowledge_search` | MCP knowledge tools, doc search |
| Presto queries, data analysis | `data-agent` | Data exploration, SQL queries |
| Historical code analysis at specific commits | `meta-codebase-explorer` with `isolation: "worktree"` | Isolated repo copy for checkout |

**Default**: `meta-codebase-explorer` covers most research tasks.  use specialized types when the task is clearly in their domain:
- Task is "find all internal documentation about X" → `knowledge_search:knowledge_search`
- Task is "query Presto for usage metrics" → `data-agent`
- Task is "compare code before and after D12345" → `meta-codebase-explorer` with `isolation: "worktree"`
- Everything else → `meta-codebase-explorer` or `meta:code_search`

#### 4. Spawn the agent pool

Pool size guidelines (agents, not tasks -- tasks can be unlimited, agents are the worker pool):
- **3-5 agents** for most research (even if there are 15 tasks -- agents pick up new tasks when done)
- **1-2 agents** for straightforward queries
- **Up to 8 agents** for extremely broad research with many independent threads
- **Hard cap: 10 agents** -- beyond this, diminishing returns and system strain

**Spawn all agents in a single message with `run_in_background: true`.**  this keeps the orchestrator non-blocking and responsive to incoming messages.

```text
# Spawn all in one message -- parallel launch
Task(
  name="researcher-1",
  team_name="deep-research-{topic}",
  subagent_type="meta-codebase-explorer",
  run_in_background=true,
  prompt="[teammate prompt — see references/subagent_template.md]"
)
Task(
  name="doc-researcher",
  team_name="deep-research-{topic}",
  subagent_type="knowledge_search:knowledge_search",
  run_in_background=true,
  prompt="[teammate prompt adapted for doc search tasks]"
)
# ... etc
```

#### 5. Use plan mode for high-complexity tasks (optional)

For tasks with 60+ tool budget where a wrong approach wastes significant resources, spawn the agent with `mode: "plan"`:

```text
Task(
  name="researcher-complex",
  team_name="deep-research-{topic}",
  subagent_type="meta-codebase-explorer",
  mode="plan",
  run_in_background=true,
  prompt="[teammate prompt]"
)
```

The agent will propose its research strategy before executing.  you approve or redirect via `plan_approval_response`:

```text
# If strategy looks good:
SendMessage(type="plan_approval_response", request_id="{from request}",
  recipient="researcher-complex", approve=true)

# If strategy needs adjustment:
SendMessage(type="plan_approval_response", request_id="{from request}",
  recipient="researcher-complex", approve=false,
  content="Don't search the legacy codebase -- focus on fbcode/new/auth/ only")
```

This catches bad research approaches before any tool budget is spent.

### Phase 2: Active Orchestration

**This is what distinguishes team-based research from fire-and-forget subagents.**

As the orchestrator, you are NOT passive.  after spawning teammates, you enter an active monitoring loop.

#### Monitoring Incoming Messages
Teammates send you messages via `SendMessage` when they:
- Complete a task (with key findings summary)
- Discover something that changes the research direction
- Hit a blocker and need guidance
- Find information relevant to another teammate's task

These messages are delivered to you automatically.

#### Orchestrator Communication Protocol

**You MUST proactively communicate with agents, not just react to their messages.**

##### Proactive Communication (orchestrator-initiated)

| Trigger | Action | Example |
|---------|--------|---------|
| Agent A reports finding relevant to Agent B | **Cross-pollinate**: forward key context to B | "FYI: researcher-1 found that the auth module was migrated to fbcode/security/auth/ in D12345. This may affect your RBAC investigation." |
| Phase 1 tasks complete | **Unblock agents**: summarize phase 1 findings for phase 2 agents | "Phase 1 complete. Key entry point is fbcode/nest/auth/handler.py:85. Token storage uses Configerator. Focus your deep-dive on these paths." |
| Early finding changes the landscape | **Broadcast pivot** (sparingly) | "Key discovery: entire auth system uses shared library at fbcode/security/. All agents should search there, not fbcode/nest/auth/." |
| Agent appears stuck (idle for extended period, no messages) | **Check in**: ask for status | "researcher-3: checking in -- are you making progress on the rate limiting task, or are you blocked?" |
| You gain context from outside the team (user provides info, you do your own search) | **Share context** proactively | "The user clarified they only care about the REST API auth, not the GraphQL layer. Skip any GraphQL auth code." |

##### Reactive Communication (responding to agent messages)

| Signal from teammate | Orchestrator action |
|---------------------|-------------------|
| "Found the answer to thread X" | Mark task complete, message agent if follow-up tasks exist |
| "Thread X is a dead end" | Redirect agent to a new task or create a replacement task |
| "Discovered unexpected system Y" | Create new task for Y, assign to available agent |
| "Blocked on access/permissions" | Message agent with alternative approach or reassign |
| "Finding contradicts thread Z" | Message the agent working on Z to cross-reference |
| "No relevant code found" | Suggest alternative search terms, paths, or tools |
| Agent goes idle with no message | Check task list -- agent may have finished and is waiting for work |

##### Course Correction Actions

1. **Redirect an agent**: if a teammate reports "thread X is a dead end, the code was deleted in D12345", send them a message with a new direction:
   ```text
   SendMessage(type="message", recipient="researcher-2",
     content="Abandon current thread. Instead investigate Y, which was revealed by researcher-1's findings. Focus on...",
     summary="Redirecting to new thread Y")
   ```

2. **Create follow-up tasks**: if early findings reveal new threads worth investigating:
   ```text
   TaskCreate(content="Investigate Z (discovered by researcher-1)", ...)
   ```
   Available teammates will pick these up automatically.

3. **Broadcast a pivot**: if a finding fundamentally changes the research direction for everyone:
   ```text
   SendMessage(type="broadcast",
     content="Key discovery: the entire auth system was migrated in D12345. All research should focus on the new location at fbcode/new/path/...",
     summary="Research pivot - new auth location found")
   ```
   Use broadcasts sparingly -- only for findings that genuinely affect all agents.

4. **Merge or split tasks**: if a task turns out to be too broad, create sub-tasks.  if two tasks overlap, message one agent to stop and the other to absorb the scope.

5. **Terminate a wayward agent**: if an agent is going off-track and messages haven't corrected it, shut it down and spawn a replacement:
   ```text
   SendMessage(type="shutdown_request", recipient="researcher-3",
     content="Research direction is not productive, shutting down")
   ```

#### Peer-to-Peer Cross-Referencing

Teammates can communicate directly with each other for faster cross-referencing, without routing through the orchestrator.

**How it works:**
1. Each teammate reads the team config to discover peers:
   ```text
   Read ~/.claude/teams/deep-research-{topic}/config.json
   ```
   This contains a `members` array with each teammate's `name`.

2. When a teammate finds something relevant to another teammate's task, they message them directly:
   ```text
   SendMessage(type="message", recipient="researcher-2",
     content="FYI: The RBAC module you're investigating was migrated in D12345. New location: fbcode/security/rbac/",
     summary="RBAC migration info")
   ```

3. The orchestrator gets visibility via idle notification peer DM summaries (included automatically).

**When teammates should use peer-to-peer:**
- Sharing a specific finding directly relevant to another teammate's current task
- Alerting a peer about a dead end they're about to hit
- Cross-referencing conflicting findings between two threads

**When teammates should still go through the orchestrator:**
- Direction-changing discoveries that affect the whole research
- Blockers that need orchestrator intervention
- Anything requiring new tasks or task reassignment

#### What Good Orchestration Looks Like

- **Proactive**: don't wait for all agents to finish.  act on early findings.  cross-pollinate between agents.
- **Selective**: not every message requires intervention.  if an agent is on track, let it work.
- **Decisive**: if a research direction is clearly wrong, redirect immediately.  don't let agents waste tool calls.
- **Additive**: use early findings to create better follow-up tasks than you could have designed initially.
- **Communicative**: share context agents wouldn't otherwise have -- user clarifications, your own reconnaissance findings, cross-agent insights.

### Phase 3: Synthesis

When all tasks are marked complete (or the orchestrator determines enough data has been collected):

#### Option A: Orchestrator Synthesis (for smaller research, < 8 tasks)

1. **Review all completed task findings** from the task list and agent messages
2. **Identify consensus** -- findings confirmed by multiple agents
3. **Note conflicts** -- where agents found contradictory information
4. **Prioritize by source quality** -- recent code > old code, actual behavior > comments
5. **Cross-reference with Meta documentation** using MCP tools if needed

#### Option B: Dedicated Synthesis Agent (for larger research, 8+ tasks)

For research with many findings, spawn a dedicated synthesis agent to integrate results.  this keeps the orchestrator's context clean and produces a higher-quality report.

```text
Task(
  name="synthesizer",
  team_name="deep-research-{topic}",
  subagent_type="general-purpose",
  prompt="You are the synthesis agent for team 'deep-research-{topic}'.

Your job is to produce the final research report by integrating all findings.

## Steps:
1. Read the task list (TaskList) to see all completed tasks
2. Read each completed task's details (TaskGet) for the full findings
3. Review all messages in the conversation for additional context
4. Identify consensus, conflicts, and gaps across all findings
5. Write the final report to /tmp/meta_research_report_$(date +%s).md
6. Message the orchestrator with the report path and a brief summary

## Report Structure:
[See references/reporting.md for the full report template]

## Quality Requirements:
- Cross-reference findings from different agents -- note where they agree/disagree
- Prioritize: recent code > old code, actual behavior > comments
- All claims must have file_path:line_number evidence
- Flag areas needing further investigation
- Include Meta-specific context (team ownership, related diffs, platforms)"
)
```

The synthesis agent has access to the shared task list and can read all completed task descriptions, giving it full visibility into every agent's findings.

### Phase 3.5: Trust Gate Verification (MANDATORY, ENFORCEMENT)

**CRITICAL: Every actionable claim must carry a machine-verified tag and the report
ships only if ≥ 95% of claims verify.** This phase replaces the prior "verify each
ref" instruction with automated, fail-closed enforcement.

See `~/.claude/skills/deep-research/references/finding_schema.md` for the structured
Finding JSON every research subagent emits. See
`~/.claude/skills/deep-research/subagents/trust_gate_verifier_prompt.md` for the
verification subagent (auto-spawned at TeamCreate). See
`~/.claude/skills/deep-research/subagents/claim_extractor_prompt.md` for Phase 1.5
extraction of free-text agent output (also auto-spawned).

At synthesis time:
1. Synthesis agent writes draft report to /tmp/meta_research_report_<ts>.draft.md
2. Synthesis agent invokes:
   ```bash
   bash ~/.claude/skills/deep-research/references/scripts/trust_gate.sh \
     /tmp/meta_research_report_<ts>.draft.md
   ```
3. The script reads ~/.claude/teams/<team>/verification.jsonl, joins against
   [F-N] anchors in the draft, computes verified_pct, writes
   /tmp/gate_decision_<basename>.json.
4. Decision matrix:
   - verified_pct ≥ 0.95 → PASS (proceed to Google Doc with Trust Banner)
   - 0.85 ≤ verified_pct < 0.95 → SOFT BLOCK (auto-retry up to 2x; demote
     unverifiable claims to "Tentative Findings (Unverified)" appendix)
   - verified_pct < 0.85 → HARD BLOCK (pingme; user must opt in via --force-deliver)
   - ANY [HALLUCINATED] ref → HARD BLOCK regardless of percent
5. On PASS, render footnotes:
   ```bash
   python3 ~/.claude/skills/deep-research/references/scripts/render_footnotes.py \
     /tmp/meta_research_report_<ts>.draft.md \
     > /tmp/meta_research_report_<ts>.gdoc-ready.md
   ```
6. Inject the Trust Banner at top of report (see references/color_palette.md for
   the HEX-pinned palette — PASS=blue, NEVER green).

**Color HARD RULE**: All status chips use blue / yellow / orange / red / gray.
ZERO green. Use colorblind-accessible palette. Auto-fail any output containing green.

**No-auto-publish**: Trust Gate does NOT change the existing delivery surface.
Reports remain local markdown + Google Doc only. HTML wiki/Pixelcloud uploads are
OPT-IN via explicit user flag.

### Phase 4: Cleanup

1. Send `shutdown_request` to all remaining teammates
2. Call `TeamDelete` to clean up team and task files
3. Deliver the report (see [references/reporting.md](references/reporting.md))

## Meta-Specific Tool Strategies

For the full Meta tooling reference (BigGrep flags and patterns, Mercurial workflows, Buck2 query usage, Presto safe-querying rules, jf/GraphQL/MCP diff investigation, MCP knowledge search), see [references/meta_tools_guide.md](references/meta_tools_guide.md).

Quick reminders for orchestrator-level instructions to teammates:
- **Code search**: `fbgr` (regex), `fbgs` (exact), `fbgf` (filenames). Always pass `--limit 80`.
- **Mercurial**: prefer `sl` over `hg`. Use `jf diff-properties D#####` for diff investigation.
- **Buck2**: `buck2 uquery` for ownership; full workflows in `fbcode/claude-templates/components/skills/buck/SKILL.md`.
- **Presto**: ALWAYS include `WHERE ds >= '<DATEID-7>'` partition filters; default to `infrastructure` namespace; use `LIMIT 100` for exploration.
- **Diff investigation**: prefer `jf get`/`jf inlines`/`jf land --list` over GraphQL. Use MCP tools (`get_phabricator_diff_details`, `knowledge_load`, `knowledge_filtered_search`) for direct access.

## Teammate Prompt Template

The full teammate prompt template (with communication protocol, peer-to-peer messaging, task claiming, shutdown protocol) lives in [references/subagent_template.md](references/subagent_template.md). Every teammate prompt must include the role, mission, communication protocol, and shutdown handling defined there.

**Key requirements every teammate prompt MUST include:**
- Read `fbcode/claude-templates/components/skills/deep-research/references/subagent_template.md` and `references/meta_tools_guide.md` first
- Use SendMessage for ALL communication (plain text output is invisible to team)
- Verify code references by reading actual files (don't trust pattern-matching from other systems)
- Distinguish landed code (diff "Closed") from draft/abandoned diffs
- Tool budget per task: 20 (simple) / 40 (medium) / 60 (high; hard limit 80)

## Synthesis & Reporting

For the report structure, delivery flow (Google Doc + pingme + pastry fallback), and resource budgets, see [references/reporting.md](references/reporting.md).

## Complete Orchestration Example, Anti-Patterns, Worktree & Resume

For the full end-to-end example walkthrough (depth-first nest auth research), the Anti-Patterns / Always lists, and the advanced features (Worktree Isolation, Resume for Multi-Session Research), see [references/orchestration_example.md](references/orchestration_example.md).

## Resources

This skill includes the following bundled resources:

### references/
- `meta_tools_guide.md` - Detailed Meta tooling reference for teammates
- `subagent_template.md` - Full template for teammate instructions and protocol
- `reporting.md` - Report structure, delivery flow, resource budgets
- `orchestration_example.md` - End-to-end example, anti-patterns, advanced features (worktree, resume)
