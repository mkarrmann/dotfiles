---
name: metamate-research
description: 'REQUIRED for Meta-internal concepts, frameworks, policies, acronyms, or terminology NOT in code or training data. Query Metamate INSTEAD OF grep for understanding — grep finds implementations, Metamate finds documentation and reasoning. NOT for finding code — use grep after.'
context: fork
allowed-tools: Bash(meta metamate.conversation query:*)
---

# Metamate Research Skill

Query Metamate, Meta's internal knowledge agent with access to wikis, Workplace posts, Google Docs, tasks, diffs, SEVs, and 60+ document types.

**IMPORTANT**: Metamate is an advanced AI agent that can personalize responses based on the user's context—not just a search engine. Provide structured context to get dramatically better answers.

## CRITICAL: Use This INSTEAD OF Grep/Search

**USE THIS FIRST** (before grep, file search, or code reading) when:

- You encounter ANY unfamiliar Meta-internal term, concept, or acronym
- You need to understand how a Meta framework or API works (Ent, XController, GK, Haste, etc.)
- You're researching internal policies, processes, or team practices
- You're debugging and need context beyond what's in the code
- You want to find internal documentation or Workplace discussions
- You're stuck or unsure what to do next on a Meta-specific task

**WHY**: Claude's training data does NOT include Meta internal documentation. Grepping code shows implementation, NOT reasoning or authoritative documentation.

## When to Use Grep/Read INSTEAD

- Finding specific code implementations (AFTER you understand the concept)
- Reading configuration files
- Searching for usages of a known function or class
- Local file operations

**Pattern**: Metamate to UNDERSTAND → grep to FIND implementations

## How to Use

```bash
meta metamate.conversation query --raw -p "$(cat <<'EOF'
[YOUR STRUCTURED PROMPT]
EOF
)"
```

**Why this command**: `meta metamate.conversation query` calls the Metamate web engine remotely via the `meta` CLI — no local www or Hack runtime required, so it works on any host with the `meta` CLI installed. `--raw` prints just the response text (good for piping or showing to the user). Pass the structured prompt to `-p` as a single quoted argument; a `cat <<'EOF' ... EOF` heredoc keeps multi-line prompts readable and avoids shell-escaping issues with backticks/quotes inside the prompt.

**Optional flags**:
- `--uuid <conversation-uuid>` — continue an existing conversation (single-turn is still the norm; use sparingly).
- `--agent <alias>` — route to a specific agent (e.g. `datamate`, `analytics-agent`). Omit for Metamate auto-routing.
- `-o json` — structured output if you need to parse fields rather than just the answer text.

For long-running queries, use a background subagent:

```
Task({
  subagent_type: "Bash",
  run_in_background: true,
  description: "Research [TOPIC]",
  prompt: "Run: meta metamate.conversation query --raw -p \"$(cat <<'EOF'\n[STRUCTURED PROMPT]\nEOF\n)\". Summarize key findings."
})
```

## CRITICAL: Structured Prompt Format

Use this format to help Metamate personalize its response:

```
## Context
[2-3 sentences: What problem are you solving? What have you tried? What's the goal?]

## Working Environment
- Repo: [www, fbsource, etc.]
- Recent files: [2-4 relevant PRE-EXISTING files you've been working on]

## Question
[Your specific question—be precise about what you need to know]

## Research Hints
To personalize your response, please also consider:
- My recent diffs and my team's recent diffs for relevant patterns
- My recent Workplace or Google Chat conversations for additional context
- Code examples in the files mentioned above
```

### Minimal Format (for simple lookups only)

```
Search the wiki, Workplace groups, Google Docs, and other internal sources for [QUESTION].
```

## Quick Example

```
## Context
I'm implementing a GraphQL mutation that writes to the UserSettings ent.
Getting EntPrivacyDeniedException on writes but reads work fine.
I've verified the viewer context is correct.

## Working Environment
- Repo: www
- Recent files: flib/user/settings/UserSettingsEntMutator.php, flib/site/graphql/mutations/UpdateUserSettingsMutation.php

## Question
What causes EntPrivacyDeniedException on writes but not reads? What privacy rules must be satisfied for ent mutations?

## Research Hints
To personalize your response, please also consider:
- My recent diffs and my team's diffs for ent mutation patterns
- My recent chats for discussions about this issue
```

## Limitations

- **Single-turn by default** - Treat each query as standalone; use `--uuid` to continue a prior conversation only when truly needed
- **Response time** - 1-2 minutes typical; long or complex prompts can take 10+ minutes
- **Access-based** - Only sees what the user has access to
- **Token limits** - Keep context concise; don't dump entire file contents

## Integration Pattern

When you encounter something unfamiliar at Meta:

1. **DO NOT grep first** for internal concepts
2. **DO NOT ask the user** to explain—look it up
3. **Construct a structured prompt** with context (see format above)
4. **Run `meta metamate.conversation query --raw -p "..."`**
5. **Summarize findings** back to conversation
6. **THEN grep** for specific implementations if needed

## References

- [context-guide.md](context-guide.md) - What to include in each section and research hints
- [prompt-examples.md](prompt-examples.md) - Example prompts for different scenarios

## Pairs with

- **[`dna`](../../plugins/dna/) (Data 'N' Analytics)** — for multi-step data + analytics workflows (metric RCA, pipeline / DQ investigations, lineage traces, ad-hoc queries). Install with `claude-templates plugin dna install`.
