# Diff Stack Review Skill

A comprehensive code review skill that uses parallel expert reviewers and deep cross-stack analysis to review diff stacks before landing. Discovers full stacks from a single diff number, deduplicates against existing reviewer feedback, validates findings against tests, and offers non-destructive `jf suggest` fixes.

## Overview

The diff-stack-review skill provides:

- **Phabricator-First Stack Discovery** — give it a single diff number and it discovers the full stack via API
- **8 Expert Reviewers** running in parallel (Clean Code, Security, Architecture, Design, Testing, Privacy, Performance, Data Modeling)
- **Deep Cross-Stack Analysis** — 5 procedural steps tracing data flow, shared file mutations, dependency ordering, test architecture, and emergent behavior
- **Reviewer Feedback Deduplication** — builds an index from existing Phabricator comments and hard-filters duplicates
- **Test Plan Critique** — first-class evaluation of test plans by the Testing reviewer
- **Targeted Source Code Research** — verifies findings against actual source code before reporting
- **Validation Pruning** — two-pass analysis with evidence-based drop/downgrade and test_status classification
- **Overall Rating** — 🔴 CRITICAL / 🟡 MAJOR / 🔵 MINOR / 🟢 NIT with clear thresholds
- **Prioritized Fix Plans** with severity-based ordering
- **Two Fix Modes** — `jf suggest` (non-destructive, reviewable on Phabricator) or `sl amend` (modify commits)
- **Pastry Report Saving** — save full report to Phabricator paste for sharing
- **Pre-submission Comparison** between Phabricator and local changes (sl amend mode)

## Installation

```bash
# Install for all projects (recommended)
claude-templates skill diff-stack-review install --scope user

# Install for current project only
claude-templates skill diff-stack-review install --scope project

# Install from development source (for testing changes)
claude-templates skill diff-stack-review install --dev
```

## Usage Examples

### Review a Specific Diff's Stack

```
> /diff-stack-review D12345678

Claude will:
1. Discover the full stack from D12345678 via Phabricator API
2. Fetch all diffs locally (if not already present)
3. Launch 8 expert reviewers in parallel
4. Perform deep cross-stack analysis
5. Deduplicate findings against existing reviewer comments
6. Validate findings against test code and test plans
7. Present prioritized findings with overall rating
8. Offer to apply fixes via jf suggest or sl amend
```

### Review Current Stack

```
> review my stack

Claude will discover and review the stack containing your current working copy commit.
```

### Review with Custom Revset

```
> /diff-stack-review ancestors(.)

Claude will review only the commits matching the provided revset.
```

### Comprehensive Pre-Landing Review

```
> do a comprehensive review of my stack before I land it

Claude will:
1. Analyze all diffs in your stack
2. Check for cross-diff issue resolution
3. Generate a detailed fix plan
4. Show Phabricator vs Local comparison before submission
```

### Focus on Specific Concerns

```
> review my stack with focus on security and performance

Claude will prioritize findings from Security and Performance reviewers.
```

## Workflow Phases

| Phase | Description |
|-------|-------------|
| **1. Discover & Fetch** | Discovers full stack from a diff number or revset, fetches context and reviewer comments |
| **2. Analyze** | Parallel expert review (8 reviewers) + cross-stack analysis for multi-diff stacks |
| **3. Validate & Report** | Targeted verification, validation pruning, prioritized findings with overall rating |
| **4. Act** | Choose: jf suggest, sl amend, specific fixes, comments, or acknowledge |

## Expert Reviewers

| Reviewer | Focus Area |
|----------|------------|
| **Clean Code** | Naming, readability, SOLID principles, code smells |
| **Security** | Vulnerabilities, injection, authentication, authorization |
| **Architecture** | Design patterns, separation of concerns, modularity |
| **Design** | API design, interfaces, abstractions, extensibility |
| **Testing** | Test coverage, test quality, edge cases, test plans |
| **Privacy** | PII handling, data retention, consent, logging |
| **Performance** | Complexity, resource usage, caching, N+1 queries |
| **Data Modeling** | Schema design, relationships, constraints, migrations |

## Overall Rating

| Rating | Threshold |
|--------|-----------|
| 🔴 **CRITICAL** | Any Critical findings, or 3+ Major findings |
| 🟡 **MAJOR** | No Critical but 1-2 Major findings |
| 🔵 **MINOR** | No Critical or Major, but 3+ Minor findings |
| 🟢 **NIT** | 0-2 Minor findings only |

## Severity Levels

| Severity | Description | Action |
|----------|-------------|--------|
| **CRITICAL** | Production failures, security holes, data loss risks | Must fix before landing |
| **MAJOR** | Significant bugs, performance issues, quality problems | Should fix before landing |
| **MINOR** | Code quality improvements, non-critical edge cases | Optional |

## Fix Modes

| Mode | Command | Effect |
|------|---------|--------|
| **jf suggest** (default) | `jf suggest --no-commit --diff D<num>` | Posts inline suggestion on Phabricator — non-destructive, reviewable |
| **sl amend** | `sl amend` | Modifies local commits in place — use when you want to fix and re-submit |
| **Post comments** | `jf action D<num> -m "..."` | Posts findings as comments without code changes |

## Commands Reference

### During Review

| Command | Action |
|---------|--------|
| `approve` / `proceed` | Start fix plan generation |
| `skip issue N` | Don't fix a specific issue |
| `show details for issue N` | Get more context on an issue |
| `rerun security review` | Re-run a specific reviewer |

### During Fixes

| Command | Action |
|---------|--------|
| `continue` | Proceed to next diff |
| `pause` | Stop and wait for manual review |
| `revert` | Undo all changes |
| `submit` / `land` | Submit the stack to Phabricator |

## Related Files

- `SKILL.md` - Main skill definition
- `references/expert-reviewers.md` - Reviewer prompts, severity criteria, test plan evaluation, feedback dedup
- `references/cross-stack-analysis.md` - Cross-stack patterns and per-diff analysis tables
- `references/jf-suggest-workflow.md` - Action menu, apply workflows, comment templates
- `references/plan-template.md` - Fix plan structure (sl amend mode)
- `references/comparison-template.md` - Pre-submission comparison format
- `references/revert-procedures.md` - Undo commands

## Tips

1. **Start with a diff number** — Phabricator-first discovery finds the full stack automatically
2. **Trust the dedup** — findings that duplicate existing reviewer comments are filtered out
3. **Check test coverage** — findings where existing tests already cover the scenario are likely false positives
4. **Use jf suggest** — it's non-destructive and lets reviewers see proposed fixes inline
5. **Save to pastry** — share the full report with your team via a Phabricator paste link
6. **Focus on critical issues first** — don't get overwhelmed by Minor severity items
7. **Trust cross-diff analysis** — if an issue is marked as "addressed in later diff", you don't need to fix it

## Author

Jacob Komarovski (jacobkom@meta.com)

## Contributing

To contribute improvements to this skill:

1. Edit files in `fbcode/claude-templates/components/skills/diff-stack-review/`
2. Test with `claude-templates skill diff-stack-review install --dev`
3. Submit a diff for review
