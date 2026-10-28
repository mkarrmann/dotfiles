---
name: omnigent-subagents
description: >-
  Use before spawning a subagent, reviewer, second opinion, cross-model check
  (Codex, Claude, GPT), or parallel worker, and before shelling out to
  `codex exec` / `claude -p`. Default to an Omnigent child session
  (sys_session_create): durable, visible in the session tree, any harness and
  model, follow-ups in the same session, a wake notice on completion. Covers
  choosing the agent and model, writing the brief, collecting results, and
  the read-only caveat for reviewers. Trigger keywords: subagent, sub-agent,
  spawn an agent, codex review, have codex review, second opinion, reviewer,
  delegate, parallel agents, sys_session_create.
---

# Subagents through Omnigent

## Default: an Omnigent child session

When delegating work to another agent, launch an Omnigent child session with
`sys_session_create` rather than shelling out to a harness CLI or reaching for a
bespoke review framework. A child session:

- runs any registered harness (Codex, Claude, Muse, …) with the model and
  effort you name, so cross-model work needs no special tooling;
- is server-owned and durable: its whole transcript is queryable by id and
  visible to the user in the session tree while it runs;
- wakes you when it finishes, so there is nothing to poll;
- takes follow-up questions in the same context via `sys_session_send`, so a
  disputed finding can be checked without re-briefing;
- is covered by the harness idle watchdog, so a wedged turn fails visibly
  instead of hanging.

**Native subagent tools stay fine for quick in-context helpers** — a scoped
lookup, a fork that inherits the conversation, parallel file reads. Omnigent's
own tool description says the same. Reach for a child session when the work is
substantial, should use a different model, may need follow-up, or the user may
want to watch it.

**Other approaches are allowed** when the user asks for one, or when there is a
concrete reason to believe a more bespoke tool fits better (for example, a
framework built for exactly that kind of review). Say which you chose and why.

**Never run a harness CLI as a backgrounded shell command for long work**
(`codex exec "…" &`, `claude -p` in a background task). A background shell's
stdin is an open pipe that a CLI may block on forever, nothing enforces a
timeout, and nobody can see the run stall. If a raw CLI is truly unavoidable,
redirect stdin (`< prompt.md` or `< /dev/null`), wrap it in `timeout`, and
confirm it has started producing output before moving on.

## How

1. **Check availability.** The `sys_*` tools exist only in an Omnigent-hosted
   session. Without them, say so and use the harness's native subagent tool;
   do not fall back to a backgrounded CLI.
2. **Pick the agent.** `sys_agent_list` → a `builtins` row's `agent_id`. Skip
   rows with `available_on_host: false`. Usual picks: `codex` or `claude` for
   one agent; `debby` when you want two models to debate a question.
3. **Pick the model and effort.** Always pass both; an omitted model is not the
   harness default. Read the live catalog with `~/bin/omnigent-models` (Codex)
   or `~/bin/omnigent-models claude`; see [[omnigent-models]]. Adversarial or
   reasoning-heavy review: the top of the accepted effort ladder.
4. **Write a self-contained brief.** The child knows only what you write:
   - the goal, and the decisions already made (so it does not re-litigate them);
   - what is out of scope and what you have already ruled out;
   - where to look: paths, commits, exported patches — references, not pasted
     file contents;
   - the specific questions you want answered;
   - the output format (e.g. prioritized findings with severity, file:line,
     failure scenario, suggested fix, cited code; uncertainty marked as such);
   - for reviewers: **"read-only: do not edit files, commit, or change the
     checkout."**
   Hand it the code, not your conclusion, when you want an independent read.
5. **Launch.** `sys_session_create(agent_id=…, model=…, reasoning_effort=…,
   title=…, message=<brief>)`. Launch independent children in one message so
   they run in parallel. Then continue other work or end the turn.
6. **Collect.** When the runtime posts `[System: sub-agent … finished …]`, call
   `sys_read_inbox`. `sys_session_get_history` / `sys_session_get_info` read a
   child directly; [[omnigent-sessions]] covers debugging a hung or failed one.
   Never report a child's result before it arrives.
7. **Follow up, then relay.** Push back on doubtful findings with
   `sys_session_send` to the same child. The user does not see the child's
   report unless they open it, so relay what matters, with its evidence.

## Caveats

- **The builtin `codex` and `claude` agents run unsandboxed in your working
  directory**, with full write access. Read-only for a reviewer comes from the
  brief, not from the agent.
- **A child shares your checkout.** Do not `sl goto`, rebase, amend, or switch
  branches while a child is reading it; it will read the wrong files. Export
  patches first if you need to keep working in the tree.
- **Cost scales with children.** Each child is a full session. Spawn as many as
  the task needs, not one per idea.

## Related

- [[omnigent-models]]: model ids and effort ladders per harness.
- [[omnigent-sessions]]: reading, listing and debugging child sessions.
- [[waiting-without-polling]]: why you wait on the wake notice instead of
  checking status.
