---
name: diff-stack-review
description: Comprehensive diff stack code review with parallel expert reviewers and cross-stack analysis. Discovers full stack from a single diff number via Phabricator API, reviews all diffs using 8 specialized perspectives, performs deep cross-stack analysis, deduplicates against existing reviewer feedback, validates findings against tests, and generates fix plans via jf suggest or sl amend. Pass --json [path] for non-interactive structured output (default /tmp/diff-stack-review-findings.json) — suppresses Phase 4 action menu and all Phabricator writes. Ideal for thorough review before landing a diff stack.
allowed-tools: Bash(sl:*), Bash(jf:*), Bash(arc:*), Bash(pastry*), Bash(timeout*), Read, Edit, Write, Grep, Glob
argument-hint: '[D<number> | revset] [--json [path]]'
hooks:
  UserPromptSubmit:
    - hooks:
        - type: command
          command: "python3 /usr/local/claude-templates-cli/components/helpers/track_plugin_usage.py --skill diff-stack-review || true"
          async: true
          timeout: 5
  PostToolUse:
    - matcher: "Skill"
      hooks:
        - type: command
          command: "python3 /usr/local/claude-templates-cli/components/helpers/track_plugin_usage.py --skill diff-stack-review || true"
          async: true
          timeout: 5
---

# Diff Stack Review

Comprehensive code review of a diff stack using parallel expert reviewers and deep cross-stack analysis, with prioritized fix plan generation.

## Reference Files

- **[expert-reviewers.md](./references/expert-reviewers.md)** - Reviewer types, prompts, severity criteria, test plan evaluation, feedback dedup
- **[cross-stack-analysis.md](./references/cross-stack-analysis.md)** - Cross-stack patterns, per-diff analysis pattern tables
- **[jf-suggest-workflow.md](./references/jf-suggest-workflow.md)** - Action menu, jf suggest workflow, comment templates
- **[plan-template.md](./references/plan-template.md)** - Fix plan structure (sl amend mode)
- **[comparison-template.md](./references/comparison-template.md)** - Phabricator vs local comparison
- **[revert-procedures.md](./references/revert-procedures.md)** - Undo/revert commands

## Context Window Strategy

| Phase | Execution | Why |
|-------|-----------|-----|
| 1: Discover & Fetch | **Subagent** | Mechanical API/CLI/fetch work — main agent only needs compiled results |
| 2: Analyze | **Subagents** (8 parallel) + **Main context** (cross-stack) | Expert reviews parallelized; cross-stack requires full picture |
| 3: Validate & Report | **Main context** | Judgment, synthesis, targeted verification |
| 4: Act | **Main context** | User interaction, apply operations |

## Phase 1: Discover & Fetch — SUBAGENT

**Launch as a single subagent.** The subagent discovers the stack, fetches all Phabricator data, and builds compiled indexes. The main agent receives structured results only.

**Subagent prompt:**
> Discover the diff stack for `<user_input>`. Fetch Phabricator details for each diff. Return: (1) ordered list of `(commit_hash, diff_number, title)` tuples, (2) file overlap map `{file -> [diff_numbers]}` with coupled files, (3) single-diff or multi-diff flag, (4) per-diff summaries and test plans, (5) reviewer feedback index `{file:line_range -> feedback_summary}`, (6) diff author unixname and whether current user is the author, (7) review chunk assignments (groups of ≤5 diffs based on file overlap), (8) original_commit hash (the user's position before fetching). Follow the instructions below.

### Stack Discovery

**If the user provided a diff number** (e.g., `D12345` or `review D12345`):

1. **Record the user's current position** so it can be restored after the review:
   ```bash
   sl log -r . -T '{node|short}\n'
   ```
   Save this as `original_commit`.

2. **Discover the full stack via Phabricator.**

   ```text
   get_phabricator_diff_details(D12345, include_stack_dependencies: true, include_diff_summary: true)
   ```

   Follow parents up to root, children down to all leaves. Build the complete diff list.

3. **Fetch the stack locally and navigate to it.** `sl goto` pulls the diff from Phabricator if not already local:
   ```bash
   sl goto D<top_of_stack>
   ```

4. **Map commits to diffs.** Use `sl log` on the fetched stack (NOT `desc()` — it scans the entire repo and is extremely slow on fbsource):
   ```bash
   sl log -r 'stack()' -T '{node|short} {separate(" ", phabricatordiffid)} {desc|firstline}\n'
   ```
   If `phabricatordiffid` is missing, parse from the `Differential Revision:` line in the commit message.

5. **Reconcile.** Phabricator is authoritative for stack membership. If a diff can't be fetched locally, review using only the Phabricator raw diff and note this.

**IMPORTANT:** NEVER use `desc()` revset — it scans every commit in the repo and will hang for hours on fbsource.

**If the user provided a custom revset** (e.g., `ancestors(.)` or `.:`):
```bash
sl log -r '<user_revset>' -T '{node|short} {separate(" ", phabricatordiffid)} {desc|firstline}\n'
```

**If no argument was provided:**
```bash
sl log -r 'stack()' -T '{node|short} {separate(" ", phabricatordiffid)} {desc|firstline}\n'
```

**IMPORTANT:** Do NOT use bare `draft()` — it returns ALL drafts in the repo. Do NOT use `desc()` — it scans the entire repo and hangs on large repos.

Parse output into `(commit_hash, diff_number, title)` tuples. If a commit lacks a `phabricatordiffid`, parse from the `Differential Revision:` line:
```bash
sl log -r '<revset>' -T '{node|short} {desc}\n' | grep 'Differential Revision:' | grep -oP 'D\d{8,}'
```
Always anchor to `Differential Revision:` — commit messages often reference other diffs.

**Build file overlap map** from `sl diff --change <hash> --stat` for each commit. Files touched by 2+ diffs are "coupled."

**Chunk assignment (stacks of 6+ diffs).** Partition diffs into review chunks of ≤5 using the file overlap map:
1. Build a graph: nodes = diffs, edges = shared files. Find connected components.
2. Each component ≤5 diffs = one chunk. Components >5 diffs: split at weakest coupling (fewest shared files).
3. Orphan diffs (no shared files) are batched together, ≤5 per chunk.

For stacks of ≤5 diffs, use a single chunk containing all diffs.

### Context Fetching

For each diff in the stack:

1. **Fetch Phabricator details:**
   ```text
   get_phabricator_diff_details(D<number>, include_raw_diff: true, include_diff_summary: true, include_test_plan: true, include_diff_author: true)
   ```

2. **Fetch local diff** if it may differ from Phabricator (local amendments since last submit):
   ```bash
   sl diff --change <commit_hash>
   ```
   Skip if diffs were just fetched via `jf get` and haven't been locally amended.

3. **Build reviewer feedback index** from Phabricator inline comments and supplementary `jf inlines`:
   ```bash
   jf inlines --latest -r <commit_hash> --skip-author 2>&1
   ```
   Create a lookup of `{file:line_range -> feedback_summary}` from all reviewer comments (human, AI, lint bots). Any finding that overlaps with this index MUST be dropped — do not rephrase, "confirm", or re-report existing feedback.

4. **Determine authorship.** Compare diff author against current user — this controls action menu options in Phase 4.

### Subagent Returns

The main agent prints the stack overview and proceeds to Phase 2:
```text
STACK REVIEW — Analyzing N diffs
═════════════════════════════════
  D12345 (abc1234) — First change
  D12346 (def5678) — Second change

Coupled files:
  src/Foo.php -> D12345, D12348

Review chunks:
  Chunk 1: D12345, D12346, D12348 (coupled via src/Foo.php)
  Chunk 2: D12347, D12349 (independent)
```

## Phase 2: Analyze

### Parallel Expert Review — SUBAGENTS

**MANDATORY: Review ALL diffs in the stack.** The user provided a single diff to identify the stack — that does NOT mean "review only that diff." Every diff in every chunk must be reviewed. Do not skip, scope down, or "focus" on a subset — not even by "workstream." This is the entire purpose of the skill.

For stacks with multiple chunks, launch 8 reviewers **per chunk** — each reviewer only receives the diffs in its chunk. All reviewers across all chunks are launched in a **single parallel task tool invocation**. Do NOT use `run_in_background` — the main agent MUST wait for all reviewer results before proceeding to cross-stack analysis. Each reviewer returns structured findings in the Finding Format below.

Reviewers may be paired into subagents (e.g., "Clean Code + Architecture") to reduce subagent count — minimum 4 subagents per chunk. Do NOT collapse all 8 perspectives into a single "comprehensive" reviewer — that produces a shallow review.

For single-chunk stacks (≤5 diffs), this is simply 8 reviewers (or 4 paired subagents) for all diffs.

**Reviewers**: Clean Code, Security, Architecture, Design, Testing, Privacy, Performance, Data Modeling, Calibrated Reviewer

The Testing reviewer also evaluates test plans — see [expert-reviewers.md](./references/expert-reviewers.md) for all prompt templates, severity criteria, test plan evaluation dimensions, and dedup rules.

**Calibrated Reviewer** (`subagent_type: "general-purpose"`, `run_in_background: true`):
In addition to the domain expert reviewers, launch a team-calibrated reviewer that applies checks derived from real review patterns. For each finding: file, line, severity (blocking/nit/clarify), and a concrete fix suggestion.

Checks to apply:
1. **Verify API Calls** — new function calls match actual signatures (blocking if mismatch)
2. **Verify Type/Enum Assumptions** — search for counterexamples when code assumes a type always maps to one value (blocking if found)
3. **Boolean Logic & Branch Correctness** — both branches correct, no missing continue/return (blocking if wrong)
4. **Search for Downstream Consumers** — callers handle changed return types (blocking if broken)
5. **Code Duplication / Reuse Existing Utils** — check for missed utilities (nit)
6. **JustKnobs & Killswitches** — hardcoded limits should be JKs, new services need killswitches (blocking for services)
7. **Error Handling** — external calls need try/catch, no silently swallowed exceptions (blocking if swallowed)
8. **Diff Scope & AI Creep** — all changes relate to diff title (nit)
9. **AI Code Provenance** — new code follows existing patterns in the directory (question)
10. **Production Safety** — caching changes need fallback, risky changes need GK (blocking)
11. **Logging & Observability** — service calls need logging, error paths need ope() (nit/blocking)
12. **GK Gating** — new user-facing features need a GK (blocking)
13. **Don't Silently Fallback** — invalid input should error, not fallback (blocking)

### Cross-Stack Analysis — MAIN CONTEXT

Skip for single-diff reviews. For multi-diff stacks, this is the unique value of stack review — patterns that ONLY emerge from seeing multiple diffs together.

Follow the 5 steps in [cross-stack-analysis.md](./references/cross-stack-analysis.md):
1. Trace data flow across diffs
2. Trace shared file mutations
3. Check dependency ordering
4. Evaluate test architecture across the stack
5. Check for emergent behavior

### Finding Format

For each issue, record:
- **diff**: Diff number (e.g., D12345)
- **file**: File path
- **line**: Line number
- **severity**: Critical / Major / Minor
- **category**: Pattern name (e.g., "N+1 Query", "API Contract Drift")
- **problem**: 1-2 sentences max — what's wrong and why it matters. No hedging.
- **fix**: 1 sentence or code snippet. Omit if obvious from the problem.

## Phase 3: Validate & Report — MAIN CONTEXT

### Targeted Verification

Skip this step if all Critical/Major findings are self-evident from the diff content (e.g., missing error handling, no tests, duplicated code). Only verify findings that **assume behavior of code outside the diff** (e.g., "the API returns X", "callers expect Y").

When verification is needed, launch a **single subagent** with ALL findings that need verification in one batch. The subagent returns a verdict per finding: confirmed, invalidated, or downgrade with a 1-2 sentence evidence summary.

The subagent should:
1. **Read the relevant source code.** If a finding says "the API returns X," read the actual handler.
2. **Check codebase patterns.** If the codebase consistently doesn't handle a case, the finding may be against conventions — downgrade or drop.
3. **Read existing tests outside the diff.** The broader test suite may already cover the scenario.

### Validation

Validate all findings in a **single pass** (not one-by-one with individual tool calls). For each finding, apply these filters:

1. **Already reported?** Check the reviewer feedback index. Drop if already flagged by any reviewer or lint bot.
2. **Actually a problem?** Is it handled elsewhere in the diff, stack, or codebase?
3. **Severity correct?** Would a senior engineer flag this at this severity? When in doubt, downgrade.
4. **Test status?** Classify:
   - `tested_and_undermines`: Test covers this scenario — **drop** unless the test is flawed.
   - `tested_but_incomplete`: Partial coverage — **keep** but downgrade one level.
   - `tested_and_confirms`: Test reveals the same problem — **keep**.
   - `untested`: No coverage — **keep**. Absence strengthens the finding.
   - `test_plan_claims_covered`: Plan says covered but no test code exists — **keep** with note.

**Actions:** Drop false positives, reviewer duplicates, and test-undermined findings. Downgrade inflated severity. Upgrade where context reveals worse impact. Assign final sequential IDs to survivors.

### Report

```text
STACK REVIEW REPORT
═══════════════════
Stack: N diffs (D111, D222, ...)
Coupled files: src/Foo.php -> D111, D333

Overall: 🔴 CRITICAL / 🟡 MAJOR / 🔵 MINOR / 🟢 NIT
Issues: X Critical, Y Major, Z Minor

═══ CRITICAL ═══

[1] D111 | src/Foo.php:42 | Missing Error Handling
    Query result used without null check. Add guard before line 44.

═══ MAJOR ═══

[2] D222 | src/Bar.php:15 | N+1 Query
    Loop issues one query per user. Batch with WHERE IN clause.
    Tests: tested_but_incomplete (BarTest::testGetUsers, only 2 users).

═══ MINOR ═══

[3] D333 | src/Baz.php:88 | Magic Number
    Extract 86400 to a named constant.

═══ TEST PLAN ASSESSMENT ═══

D111: Weak — test plan says "ran locally" but doesn't specify scenarios.
D222: Adequate — test plan names specific test classes and scenarios.
D333: Missing — no test plan provided.
```

**Overall assessment thresholds:**
- 🔴 **CRITICAL**: Any Critical findings, or 3+ Major findings
- 🟡 **MAJOR**: No Critical but 1-2 Major findings
- 🔵 **MINOR**: No Critical or Major, but 3+ Minor findings
- 🟢 **NIT**: 0-2 Minor findings only

The **Test Plan Assessment** section is mandatory. For each diff, include the Testing reviewer's quality classification (Strong / Adequate / Weak / Missing) with a one-line rationale.

For issues in earlier diffs addressed by later diffs: mention for awareness but exclude from fix plan.

## Phase 4 (alternative): JSON Output Mode (`--json`)

When `$ARGUMENTS` contains the token `--json` (optionally followed by an output path), produce structured JSON output **instead of** running the interactive Phase 4 action menu. This is the non-interactive, machine-readable mode used by downstream skills and FaaS pipelines that need the findings as data, not as a Phabricator interaction.

### Trigger and arguments

- `--json` alone — write to the default path `/tmp/diff-stack-review-findings.json`.
- `--json <path>` — write to `<path>` (absolute, or a relative path that starts with `./`/`../` or ends in `.json` — see the disambiguation rule below; a bare token like `out` is treated as the diff/revset, not a path).

**Token-level detection** (not substring): split `$ARGUMENTS` on whitespace and check whether any token is **exactly** `--json`. Tokens like `--jsonx`, `--no-json`, or a path containing the literal substring `--json` MUST NOT trigger JSON mode.

If a `--json` token is present, strip it before parsing the remainder for the diff number or revset. Argument order does not matter — `D12345 --json` and `--json D12345` are equivalent.

**Path vs diff disambiguation.** The token immediately following `--json` is treated as the output PATH only if it (a) starts with `/`, `./`, or `../`, or (b) ends with the `.json` extension. Otherwise that token is treated as the diff/revset and the default output path is used. Examples:
- `--json D12345` → no path token; diff is `D12345`; write to `/tmp/diff-stack-review-findings.json`.
- `--json /tmp/x.json D12345` → path is `/tmp/x.json`; diff is `D12345`.
- `--json out.json D12345` → path is `out.json` (has `.json` extension); diff is `D12345`.
- `D12345 --json` → no path token; diff is `D12345`; default path.

### What JSON mode suppresses

In JSON mode, you MUST NOT do any of the following:

- Print the Phase 3 markdown report (no `STACK REVIEW REPORT` block, no emoji headers, no `TEST PLAN ASSESSMENT` section). The only stdout/chat output is the single one-line write confirmation at the end of the Write step.
- Post inline comments via `meta phabricator.diff inline-comment`.
- Post a top-level summary comment via `meta phabricator.diff comment`.
- Save the report to a paste via `pastry`.
- Run `jf suggest`, `jf action`, or any apply loop.
- Run `sl amend` or any other commit-mutating command.
- Present the action menu, ask the user to pick an option, or prompt for input.

JSON mode is data-only. The only required side effect is writing the JSON file — downstream callers read the file from disk regardless of the working copy's current commit, so no `sl goto` restore is performed.

### Output schema

The JSON file is the post-Phase-3 findings plus test plan assessments, using the **same vocabulary** as the markdown report — `severity` and `quality` are Capitalized, and the per-finding fields are exactly those defined in the Finding Format. The schema is intentionally general-purpose; downstream callers map it into their own schemas.

```json
{
  "overall": "CRITICAL",
  "stack": ["D111", "D222"],
  "findings": [
    {
      "id": 1,
      "diff": "D111",
      "file": "src/Foo.php",
      "line": 42,
      "severity": "Critical",
      "category": "Missing Error Handling",
      "problem": "Query result used without null check; will throw if the row is missing.",
      "fix": "Add a null guard before line 44."
    },
    {
      "id": 2,
      "diff": "D222",
      "file": "src/Bar.php",
      "line": 15,
      "severity": "Major",
      "category": "N+1 Query",
      "problem": "Loop issues one query per user.",
      "fix": null
    }
  ],
  "test_plan_assessments": [
    {"diff": "D111", "quality": "Weak", "rationale": "Says 'ran locally' with no scenario detail."},
    {"diff": "D222", "quality": "Adequate", "rationale": "Names specific test classes and scenarios."}
  ]
}
```

**Field definitions:**

| Field | Type | Source / Notes |
|-------|------|----------------|
| `overall` | `"CRITICAL"` \| `"MAJOR"` \| `"MINOR"` \| `"NIT"` | Phase 3 Overall assessment, using the same thresholds. |
| `stack` | array of `"D<digits>"` | Ordered list of diff numbers in the stack (Phase 1 output). |
| `findings[].id` | int | Sequential ID assigned in Phase 3 validation. **Contract: IDs are 1-based, contiguous, and unique across the findings array** — they match the markdown report's `[1]`, `[2]`, `[3]` numbering. |
| `findings[].diff` | `"D<digits>"` | Diff that owns the finding. Non-empty after trim. |
| `findings[].file` | string | File path as shown in the diff changeset. Non-empty after trim. |
| `findings[].line` | int (1-based, > 0) | New-side line number. Always a whole integer strictly greater than zero — never `0`, `-1`, `42.5`, or `"42"`. |
| `findings[].severity` | `"Critical"` \| `"Major"` \| `"Minor"` | Final post-validation severity. |
| `findings[].category` | string | Pattern name from the Finding Format. Non-empty after trim. |
| `findings[].problem` | string | 1-2 sentence problem statement. Non-empty after trim. |
| `findings[].fix` | string \| `null` | 1 sentence or code snippet, or JSON `null` when the Finding Format omits it. **The `fix` key is ALWAYS present in every finding object** (never absent — emit explicit `null` instead of dropping the key). |
| `test_plan_assessments[]` | array (possibly empty `[]`) | **The `test_plan_assessments` key is ALWAYS present at the top level** with an array value — never absent and never `null`. If the Testing reviewer was skipped or the stack is empty, emit `[]`. |
| `test_plan_assessments[].diff` | `"D<digits>"` | Diff that owns this test plan assessment. Non-empty after trim. |
| `test_plan_assessments[].quality` | `"Strong"` \| `"Adequate"` \| `"Weak"` \| `"Missing"` | Testing reviewer classification. |
| `test_plan_assessments[].rationale` | string | One-line rationale matching the markdown TEST PLAN ASSESSMENT line. Non-empty after trim. |

The JSON contains **every** survivor of Phase 3 validation (no severity floor, no truncation). Downstream callers apply their own filters.

### Write step

Before invoking `Write`, **walk the mapped object and assert every contract** above. If any finding violates an invariant — in particular, but not limited to: `line` not a whole int > 0; `diff`/`file`/`category`/`problem` empty after trim; `severity` outside the three allowed strings — abort with a non-zero error message naming the offending finding — do NOT silently drop it (downstream callers have no way to distinguish a silent drop from a genuine "no findings" result). Then use the `Write` tool to emit the file in one shot, print the one-line confirmation, and exit. Do NOT continue to the interactive Phase 4.

```text
diff-stack-review: wrote JSON output to /tmp/diff-stack-review-findings.json
  (N findings, M test plan assessments, overall: CRITICAL)
```

### Example invocations

| Invocation | Output path |
|------------|-------------|
| `/diff-stack-review D12345 --json` | `/tmp/diff-stack-review-findings.json` |
| `/diff-stack-review D12345 --json /tmp/my-review.json` | `/tmp/my-review.json` |
| `/diff-stack-review --json D12345` | `/tmp/diff-stack-review-findings.json` |

## Phase 4: Act

**Skip this entire phase if JSON Output Mode is active** — i.e. split `$ARGUMENTS` on whitespace and skip Phase 4 if (and only if) some whitespace-separated token is **exactly** `--json` (the same token-level detection the JSON Output Mode section defines; substrings like `--jsonx`, `--no-json`, or a path that merely contains `--json` MUST NOT trigger it). JSON Output Mode (above) replaces Phase 4 and is the sole output path in that case.

Present the action menu and execute the user's choice. See [jf-suggest-workflow.md](./references/jf-suggest-workflow.md) for the action menu, apply workflows, and comment templates. See [plan-template.md](./references/plan-template.md) for sl amend workflow. See [revert-procedures.md](./references/revert-procedures.md) for undo commands.

If assessment is 🟢 **NIT**, skip the menu: "Stack looks clean. No critical or major issues found. Ship it."

The action menu **always** includes: (1) save to paste, (2) apply fixes via jf suggest, and (3) suggest improved test plans as comments (when any test plan is Weak or Missing). Additional contextual options (sl amend, post comments, etc.) may be added.

After presenting the report, offer to save to a Phabricator paste:
```bash
echo '<full report text>' | pastry -t "diff-stack-review: <stack summary>" --md
```

User can request revert anytime — see [revert-procedures.md](./references/revert-procedures.md).

After all actions are complete (or if no action is taken), restore the user's original position:
```bash
sl goto <original_commit>
```

### Posting Findings as Inline Comments

When the user chooses "post findings as comments," use `meta phabricator.diff` to post **inline comments on exact lines** — not a single top-level comment with line references in brackets.

**Comment conciseness (CRITICAL):** Minor/nit comments: max 280 chars. Major/critical comments: max 450 chars (to allow short code fixes). Write in a natural human voice — like a colleague, not a tool. No structured headers, no bullet lists, no multi-paragraph explanations. See [jf-suggest-workflow.md](./references/jf-suggest-workflow.md) for examples.

1. **Top-level summary** (one terse line):
   ```bash
   meta phabricator.diff comment -n D<number> -m "diff-stack-review: <verdict> | <N> critical, <N> major, <N> minor | <paste_link>"
   ```

2. **Each finding as an inline comment:**
   ```bash
   meta phabricator.diff inline-comment \
     -n D<number> \
     -f "<file_path>" \
     -l <line_number> \
     -i <intent> \
     -m "[diff-stack-review] <severity> — <category>

<1-2 sentence problem + fix>"
   ```
   - Map severity to intent: Critical/Major → `blocking`, Minor → `nit`
   - Use the exact file path from the diff — use `meta phabricator.diff files -n D<number>` to discover correct paths
   - Valid intents: `blocking`, `nit`, `aside`, `clarify`, `context`, `preexisting`, `code-style`

3. **Supporting commands:**

   | Command | Purpose |
   |---------|---------|
   | `meta phabricator.diff files -n D<number>` | List file paths available in the diff changeset |
   | `meta phabricator.diff comments -n D<number>` | List existing comments |
   | `meta phabricator.diff delete-comment -n D<number> -c <comment_id>` | Delete a specific comment |
   | `meta phabricator.diff resolve-comments -n D<number> --comment-id=<id>` | Resolve a specific comment |
   | `meta phabricator.diff reply-comment -n D<number> -c <comment_id> -m "reply"` | Reply to a comment |

## Rules

### DO
- **Review ALL diffs in the stack — never skip or scope down to a subset**
- Run all expert reviews in parallel (single task tool call)
- Discover full stack from Phabricator when diff number is provided
- Check existing reviewer feedback before flagging issues
- Verify Critical and Major findings against actual source code
- Wait for explicit user approval before applying fixes
- Show Phabricator vs Local comparison before submission (sl amend mode)
- Post findings as inline comments on exact lines (use `meta phabricator.diff inline-comment`)
- Use `blocking` intent for Critical/Major findings, `nit` for Minor

### DO NOT
- **Use `run_in_background` for ANY subagent in ANY phase** — all subagents must be synchronous
- Submit without explicit user permission
- Duplicate feedback that reviewers or lint bots already gave
- Fix issues already addressed in later diffs
- Ignore test evidence that contradicts your findings
- Use bare `draft()` — always scope to a single connected stack
- Inflate severity — when in doubt, downgrade
- Skip diffs or "focus" on a subset — the diff number identifies the stack, it does not limit the review scope
- Post all findings in a single top-level comment with line numbers in brackets

## Submission (sl amend mode only)

**DO NOT SUBMIT WITHOUT USER SAYING "submit" OR "land"**

```bash
jf submit --draft --publish-when-ready -r "BASE::"
```
