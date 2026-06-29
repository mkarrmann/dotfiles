---
name: submitting-diffs
description: Use when submitting new diffs to Phabricator (not amending) - enforces complete workflow with `sl status` check, file analysis for intelligent prefix tags, full commit message structure (Context/Motivation/This diff/Test Plan), and approval. Triggers on submit, jf submit, publish diff, create commit, ready to submit
---

# Submitting Diffs to Phabricator

## Overview

Enforce complete, structured workflow for submitting new diffs to Phabricator. Always check status first, analyze files for intelligent tag detection, create properly formatted commit messages, and get user approval.

**This skill is for NEW diffs only.**

> **Tip (amend cycles).** When you amend an in-flight draft diff with substantive changes, also re-read the commit message body and check it still describes what the diff actually contains — fix any stale claims (removed sections, sections added later, factual statements that no longer hold) before resubmitting. Treat it as a standard step of the amend flow, not a final-polish task. **Don't include line counts or other stats the Phabricator UI already shows** (lines added/removed, file count, +/- per file, etc.); they get stale across amends and they duplicate information the reviewer can already see.

## Mandatory Workflow (Strict Order)

### 1. Check Status FIRST

**Always start here:**
```bash
sl status
```

This shows what's actually changing before you do anything else. Look for:
- Modified files (M)
- New files (?) - need `sl add`
- Deleted files (!) - need `sl rm`

### 2. Stage All Files

**For new files:**
```bash
sl add <file1> <file2> ...
```

**For deleted files:**
```bash
sl rm <file1> <file2> ...
```

### 3. Verify Build

```bash
arc f          # always: format the diff
```

If you touched code with a buck target (anything under `fbcode/`, `fbandroid/`, `fbobjc/`, etc.), also run the relevant `buck build` or `buck test` for the area you touched. For example:

- IG4A engineers: `buck build ig4a`
- A specific Rust crate: `buck build fbcode//path/to/crate:target`
- A Python module: `buck test fbcode//path/to:tests`

Skip if no buck target applies (docs-only, BUCK-file-only, prose-only changes).

#### Pre-Submit Polish Order (codegen -> format -> build gate -> self-review)

When the change generates code or touches Configerator, the single `arc f` above is not enough — run the finishing steps in this ORDER before you commit (or `sl amend` an existing diff). Order matters: out-of-order steps leave generated or unformatted code in the diff, or commit something that does not build.

1. **Codegen first** — regenerate any dependent/generated files the change affects, *before* formatting, so the regenerated output gets formatted too: `ent gencode` (Ent schema), `dsfmt` (Thrift/DSL), `conf build` (Configerator materialized configs). Skip only if the change generates nothing.
2. **Format after codegen** — `arc f`. Run it *after* codegen, never before — `arc f` before codegen leaves the regenerated files unformatted.
3. **Terminal build gate** — run the real validation build for the area you touched (`arc build`, or the relevant `buck build` / `buck test` from above) and let it pass. This is the terminal gate before commit/amend. For a change that includes Configerator, `conf build` only rematerializes configs — it is **not** the build gate; still run `arc build` so the full build validates.
4. **Self-review** — re-read the full change (`sl diff`) for leftover debug code and stale message claims; do more than one pass for larger changes.

Only after steps 1–4 pass do you create the commit (Step 7) or `sl amend` an existing diff, then submit as a draft.

### 4. Analyze Files for Tags

**Before creating commit message**, analyze changed files to derive prefix tags appropriate to your codebase area. Tag conventions vary by team; the principle is consistent: tags should make the diff scope obvious from the title.

#### Generic conventions

- `[bug-fix]` — fixing a bug
- `[EZ]` — trivial change
- `[BE]` — better engineering (refactoring, cleanup, etc.)

#### IG4A-specific examples (Instagram for Android)

IG4A engineers use a richer tag scheme. Apply only if your changes are in IG4A:

| Pattern | Tag | Example |
|---------|-----|---------|
| Files contain ViewModel/UiState/UseCase/Repository/ActionHandler | [MVVM] | `HomeViewModel.kt` |
| Files in `features/homecoming/**` | [Homecoming] | `features/homecoming/ui/` |
| Only refactoring, no logic changes | [BE] | Pure MVVM conversion |
| Files in other features | Feature name | `features/clips/` → maybe [Clips] |

**For IG4A diffs, always start with `[IG4A]`**, then add detected tags.

**Tag order:** `[IG4A][MVVM][Homecoming][BE]` or `[IG4A][bug-fix]`.

For non-IG4A diffs, derive prefix tags appropriate to your codebase (e.g. `[claude-templates][skill]`, `[orc][tg]`, `[infra][telemetry]`). When in doubt, look at recent diffs touching nearby files for the local convention.

### 5. Browse Phabricator Diff Templates

Phabricator hosts live diff templates (https://www.internalfb.com/diff_templates) — the same ones the diff UI's template picker shows — owned by oncall rotations. They exist because each team has its own sense of what a good Summary and Test Plan look like; using them makes your diff land in the shape reviewers on that code expect. So before you write the message, browse the templates owned by the oncalls relevant to *this* change and pick the best-fitting one.

**5.1 Gather the relevant oncalls (union).** You want templates from any oncall connected to this change:
- Phabricator's canonical defaults: shortname `phabricator`.
- Your oncall: `meta oncall.rotation mine --output=json` → `.short_name`.
- The code-owner oncall(s) of the changed files — from the nearest enclosing `BUCK`/`TARGETS` `oncall("...")`, or the file's inline oncall annotation (`<<Oncalls('x')>>`, `# OnCall: x`, `@oncall x`).

**5.2 Resolve each shortname to its oncall id:**
```bash
meta oncall.rotation.short-name list --name=<shortname> --output=json
```
Take the **`id`** field (the "shortname FBID"). **Do not use the `rotation_id` field** that appears in the same output — `oncall_ids` below wants the shortname FBID, and passing `rotation_id` silently returns no team templates (no error to warn you).

**5.3 Browse the templates** for the two fields you write — SUMMARY and TEST_PLAN. Use one fixed query string and pass the ids + target as GraphQL **variables**, so you only swap the `--variables` blob between runs (no rebuilding the query each time):
```bash
QUERY='query($ids: [ID!], $target: PhabricatorDiffTemplateTargetType) {
  phabricator_diff_templates(oncall_ids: $ids, target: $target) {
    nodes { name content oncall { oncall_rotation { name } } }
  }
}'

# SUMMARY
meta graphql.query execute --output=json --query="$QUERY" \
  --variables='{"ids": ["<id1>","<id2>"], "target": "SUMMARY"}'

# TEST_PLAN — same query, just change the target
meta graphql.query execute --output=json --query="$QUERY" \
  --variables='{"ids": ["<id1>","<id2>"], "target": "TEST_PLAN"}'
```
Set `"ids"` to the full union from 5.2 (pass `"ids": null` to browse every team's templates). Each node has `name`, `content`, and its owning `oncall`. An empty `nodes` array just means those oncalls registered no template — that's normal, not an error.

**5.4 Pick template**:
1. *Classify the change* using the step-4 file analysis plus the diff itself: bug fix vs new feature vs refactor vs part of a diff stack.
2. *Match a template to that intent* by its name and content shape — e.g. a bug fix → a "Bug fix"-style template, new functionality → a "New Feature" one, a stacked diff → a "Stack" one. For TEST_PLAN, match by *how you actually verified*: a UI change → a before/after template, a repro flow → a step-by-step one, something trivial → the simplest one.
3. *Break ties by owner:* among templates that fit the intent, **prefer the team / code-owner template** over the generic Phabricator default.
4. *Still ambiguous* → take the most general fitting template and move on (placeholders make a slightly-off pick cheap to fix). *Nothing fits the change's nature, the lookup errored, or it returned nothing usable* → use the fallback skeleton in [`references/fallback-message-structure.md`](references/fallback-message-structure.md). Never block the submit on template fetching.

### 6. Create Commit Message

Use recommended tags based on file analysis. **Only ask user if unsure about tags.** Assemble the message from three parts — **TITLE**, **SUMMARY**, **TEST_PLAN** — that step 7 drops into the commit:

- **TITLE** — always the skill's own convention (no template covers it). Use this fixed format every time, with tags from your step-4 analysis:
  ```
  [Tag1][Tag2] one-line title summarizing the change and why
  ```
- **SUMMARY** — the chosen SUMMARY template's headings/placeholders (or the 5.4 fallback), filled with real content for this change.
- **TEST_PLAN** — the chosen TEST_PLAN template (or the 5.4 fallback), filled in. It goes under the `Test Plan:` section header — not a `**Test Plan:**` markdown subtitle.

**Proceed autonomously:** don't ask which template to use — pick, fill it with meaningful concise content, and move on.

**Critical:** show ALL of TITLE, SUMMARY, and TEST_PLAN — not just a TITLE.

### 7. Create Commit

Drop the **TITLE**, **SUMMARY**, and **TEST_PLAN** you assembled in step 6 into the commit:

```bash
sl commit -m "$(cat <<'EOF'
<TITLE>

<SUMMARY>

Test Plan: <TEST_PLAN>
EOF
)"
```

### 8. Add Metadata (Optional)

Before submitting, you can attach reviewers, tags, and tasks:

```bash
jf template --add-reviewers username1,username2
jf template --add-tags tag1,tag2
jf template --add-tasks TXXXXXXXX
```

Note: Phabricator parses the resulting `Reviewers:` line into actual reviewer assignments only if the names resolve. For project-style reviewers (oncalls, teams), the `#` prefix in `meta phabricator.diff add-reviewer --reviewer="#ProjectName"` is more reliable.

### 9. Offer Pre-Land Review (Optional)

Before submitting, offer to run `/pre-land-review` for a comprehensive quality check (CI signals, expert code review, bug detection, security analysis). If the user declines or wants to skip, proceed directly to submission.

### 10. Submit

```bash
jf submit --draft
```

**Always use --draft flag. No approval needed - submit directly.**

When ready to publish (after iteration / amends), use `jf publish`.

## Stacked Diffs

When creating diff on top of existing diff:

**Check parent first:**
```bash
sl fssl
```

**Copy from parent:**
- Prefix tags (keep consistent)
- Task ID
- Reviewers

Example (IG4A):
- Parent: `[IG4A][MVVM][Homecoming] Add UiState - T12345`
- Child: `[IG4A][MVVM][Homecoming] Add ViewModel - T12345`

## TodoWrite Integration

**Create todos for tracking:**

```json
[
  {"content": "Check sl status and stage files", "status": "pending"},
  {"content": "Run arc f and any relevant buck build/test", "status": "pending"},
  {"content": "Analyze files and detect prefix tags", "status": "pending"},
  {"content": "Explore Phabricator templates for Summary and Test Plan (union of oncalls)", "status": "pending"},
  {"content": "Create commit message with full structure", "status": "pending"},
  {"content": "Add reviewers, tags, and tasks via jf template", "status": "pending"},
  {"content": "Offer pre-land review (optional)", "status": "pending"},
  {"content": "Submit with jf submit --draft", "status": "pending"}
]
```

Mark each in_progress as you work, completed when done.

## File Analysis Example (IG4A)

This section illustrates tag derivation; the IG4A-specific tags only apply if your changes are under `fbandroid/java/instagram/`.

**Changed files:**
```
M fbandroid/java/instagram/features/homecoming/ui/HomeViewModel.kt
M fbandroid/java/instagram/features/homecoming/ui/HomeUiState.kt
A fbandroid/java/instagram/features/homecoming/domain/GetDataUseCase.kt
```

**Tag detection:**
- ViewModel + UiState + UseCase → [MVVM]
- All in features/homecoming → [Homecoming]
- New architecture (no logic change) → possibly [BE]

Use these detected tags directly. Only ask user if detection is unclear or ambiguous.

## Common Mistakes Table

| Mistake | Why It's Wrong | Fix |
|---------|----------------|-----|
| Skip `sl status` | Don't know what's changing | ALWAYS check status first |
| Forget `sl add` | New files not in commit | Check status, add all ? files |
| Skip build verification | Broken code goes to review | Run `arc f` always; run `buck build`/`buck test` for the area you touched |
| Title-only commit | Missing required sections | Use full template with all sections |
| Wrong tags | Inconsistent/unclear | Analyze files systematically, use recommended |
| No test plan | Reviewers don't know how to verify | Always include the `Test Plan:` line |
| Wrong Test Plan format | `**Test Plan:**` vs `Test Plan:` | Use `Test Plan:` as section header |

## Red Flags - STOP

If you catch yourself thinking:
- "Status check isn't necessary" → ❌ Check status first
- "I'll just use a title" → ❌ Use full structure
- "Build verification can wait" → ❌ Build (or format-only `arc f` if no buck target) now
- "I'm not sure about tags" → ❌ If unsure, ask user

**All of these mean: Follow the workflow. No shortcuts.**

## Rationalization Defenses

| Excuse | Reality |
|--------|---------|
| "We can skip arc f, looks formatted" | arc f catches issues. Takes 5 seconds. |
| "Build will pass, I tested manually" | Manual testing ≠ build verification. Run it (when there's a buck target). |
| "sl status is obvious" | Check anyway. Might miss new/deleted files. |
| "Title is enough for commit message" | Reviewers need a real Summary + Test Plan — the sections from your chosen Phabricator template, or the fallback structure. Use the full structure. |
| "I'll just use **Test Plan:**" | Wrong format. Use `Test Plan:` as section header. |
| "A line-count summary makes the description more concrete" | The Phabricator UI already shows lines added/removed, file count, and per-file breakdowns. Re-stating them in the description duplicates the UI and goes stale on the next amend. Describe what changed and why; let the diff stats describe how big. |

## When NOT to Use This Skill

**Don't use for:**
- Amending existing commits (separate workflow; remember to keep the commit message body in sync with the diff content across amend cycles)
- Updating diffs after review
- **Editing an already-submitted diff's metadata (title / summary / test plan) in place** — see below
- Rebasing or stacking operations

**Those have separate processes.**

### Editing an existing diff's title / summary / test plan in place

To change the title, summary, or test plan of a diff that is **already on Phabricator** (no new
revision, keep its reviewer comments), do NOT use this new-diff flow — and do NOT retitle by editing
the commit message with `sl metaedit -m` / `sl amend -m` as your update path. Editing the message that
way can detach the `Differential Revision:` line, so the next `jf submit` mints a brand-new D-number and
orphans the original diff's reviewer comments. Update the field(s) in place instead:

```bash
jf sync                                # pull any Phabricator-side metadata into the local commit first
jf template --override-title "..."     # and/or --override-summary "..." / --override-test-plan "..."
jf submit --update-fields              # pushes the staged metadata onto the SAME diff
```

`--update-fields` is required: a plain `jf submit` uploads code only and silently leaves the fields
stale (even on exit 0). Match the override flag to the field you're changing
(`--override-title` / `--override-summary` / `--override-test-plan`). See the
`creating-or-updating-diffs` skill for the full workflow, and its `meta phabricator.diff update`
in-place update (also the recovery path if a submit left the fields stale on the live diff).
