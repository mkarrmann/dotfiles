# Deep Research — Complete Orchestration Example

## Table of Contents

- [Depth-First Query: "How does the auth system work in nest?"](#depth-first-query-how-does-the-auth-system-work-in-nest)
- [Anti-Patterns to Avoid](#anti-patterns-to-avoid)
- [Always](#always)
- [Advanced Orchestration: Worktree Isolation](#advanced-orchestration-worktree-isolation)
- [Advanced Orchestration: Resume for Multi-Session Research](#advanced-orchestration-resume-for-multi-session-research)

## Depth-First Query: "How does the auth system work in nest?"

Here's the full flow for a depth-first query like "how does the auth system work in nest?":

```text
1. Reconnaissance (3-5 tool calls):
   - fbgf "nest/.*auth.*" --limit 20
   - Read key auth file to understand structure
   - AskUserQuestion if scope is ambiguous

2. TeamCreate(team_name="deep-research-nest-auth")

3. TaskCreate with dependency ordering:
   Phase 1 (foundation):
   - #1: "Map auth system directory structure and identify key entry points"
   - #2: "Find internal documentation on nest auth architecture"

   Phase 2 (deep-dive, blocked by phase 1):
   - #3: "Investigate token generation patterns" (blockedBy: [1])
   - #4: "Investigate session management and storage" (blockedBy: [1])
   - #5: "Investigate RBAC and permission checks" (blockedBy: [1, 2])
   - #6: "Investigate rate limiting and abuse prevention" (blockedBy: [1])

   Phase 3 (cross-cutting, blocked by phase 2):
   - #7: "Cross-reference security patterns across subsystems" (blockedBy: [3, 4, 5, 6])

4. Spawn mixed agent pool (all with run_in_background=true):
   - Task(name="researcher-1", subagent_type="meta-codebase-explorer", ...)
   - Task(name="doc-researcher", subagent_type="knowledge_search:knowledge_search", ...)
   - Task(name="researcher-2", subagent_type="meta-codebase-explorer", ...)
   - Task(name="researcher-3", subagent_type="meta:code_search", ...)

5. Active orchestration loop:
   - researcher-1 completes task #1: "Auth entry point is fbcode/nest/auth/handler.py:85"
     → Orchestrator messages researcher-2: "Phase 1 key finding: entry point at handler.py:85.
        Token storage uses Configerator. This context should help your deep-dive."
     → Tasks #3, #4, #6 are now unblocked -- agents pick them up automatically

   - doc-researcher completes task #2: "Found design doc at wiki/Nest/Auth/Architecture"
     → Task #5 is now unblocked (was waiting on both #1 and #2)
     → Orchestrator cross-pollinates: messages researcher-3 with doc findings

   - researcher-3 messages: "RBAC is delegated to fbcode/security/rbac/"
     → Orchestrator creates task #8: "Investigate shared RBAC library" (blockedBy: [5])
     → researcher-3 directly messages researcher-2 (peer-to-peer):
        "FYI: RBAC uses shared library, not nest-local code"

   - researcher-2 messages: "Blocked -- rate limiting in private configerator namespace"
     → Orchestrator messages: "Try knowledge_filtered_search for rate limiting docs instead"

6. All tasks complete → Spawn synthesis agent (general-purpose):
   - Reads all task details and agent messages
   - Produces final report at /tmp/meta_research_report_*.md

7. Shutdown all teammates, TeamDelete, deliver report via pingme
```

## Anti-Patterns to Avoid

### Never:
- Use `find` at fbsource root (use fbgf instead)
- Spawn agents without first identifying distinct threads to investigate
- Merge unrelated threads into a single task (dilutes focus)
- Use GraphQL before trying jf commands
- Query Presto without partition filters
- Ignore Buck file ownership rules
- Spawn more than 10 teammate agents (diminishing returns, system strain)
- Be a passive orchestrator -- monitor and steer your team
- Use only one agent type when tasks span code, docs, and data (match agent to task)
- Create all tasks as flat/independent when there are natural phase dependencies
- Let the orchestrator do synthesis for large research (8+ tasks) -- spawn a synthesis agent
- Treat code found in diffs as landed production code without checking diff status
- Include recommendations in the report without first verifying the underlying code claims against the current codebase
- Report code patterns by inference from similar systems -- always read the actual file

## Always

- Spawn all agents with `run_in_background=true` to keep them invisible and the orchestrator responsive
- Add `--limit` flags to search commands (use `--limit 80` for teammate searches)
- Check `sl` for current commit context
- Verify Buck targets with uquery before building
- Use proper search tool for pattern type (fbgr/fbgs/fbgf)
- Monitor teammate messages and intervene when needed
- Proactively cross-pollinate findings between agents
- Create follow-up tasks when early findings reveal new threads
- Use task dependencies for phased research
- Shut down teammates and delete the team when research is complete

## Advanced Orchestration: Worktree Isolation

When a task requires examining code at a specific point in time (before/after a diff landed, at a historical commit), use `isolation: "worktree"`:

```text
Task(
  name="historian",
  team_name="deep-research-{topic}",
  subagent_type="meta-codebase-explorer",
  isolation="worktree",
  run_in_background=true,
  prompt="You are investigating how the auth system looked BEFORE D12345 landed.
  Run: sl goto <parent-of-D12345-commit>
  Then investigate the auth system at that point in time.
  Compare with current state documented by other teammates."
)
```

The worktree gives the agent an isolated copy of the repo it can freely navigate (checkout different commits, etc.) without affecting other agents or the main working directory.  the worktree is auto-cleaned if no changes are made.

**Use cases:**
- Comparing system before/after a major refactor
- Investigating what code looked like when a bug was introduced
- Analyzing evolution of a system across multiple commits

## Advanced Orchestration: Resume for Multi-Session Research

Complex research that spans sessions can leverage the `resume` parameter to continue where agents left off.

**Before shutting down (end of session):**
1. Note the agent IDs returned by each Task tool call
2. Keep incomplete tasks in the task list (don't delete the team)
3. Inform the user which tasks are complete and which are pending

**In a follow-up session:**
```text
# Resume a specific agent with its full prior context
Task(
  resume="agent-id-from-previous-session",
  prompt="Continue your research from where you left off. Check the task list for remaining work."
)
```

The resumed agent retains its full conversation history and can pick up exactly where it stopped.

**When to use resume vs. fresh start:**
- **Resume** when agents were mid-task and have valuable accumulated context
- **Fresh start** when the research direction has changed significantly since last session
- **Hybrid**: resume agents on track, spawn fresh agents for new threads discovered between sessions
