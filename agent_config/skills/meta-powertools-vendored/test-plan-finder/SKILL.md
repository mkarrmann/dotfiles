---
name: test-plan-finder
description: Write test plans for diffs by finding real examples from previous diffs that modified the same files. Use when the user asks to write a test plan, needs test plan examples, wants help filling out the Test Plan field in a commit message, asks "how should I test this change", says "what's a good test plan for this", wants to know what tests to run, or is preparing a diff for review and needs a test plan.
allowed-tools: Bash, Read
---

# Test Plan Finder

Find historical test plans from previous diffs using `meta codehub.file test-plans`.

Run `meta codehub.file test-plans --help` to learn the full CLI interface.

## Quick Start

```bash
meta codehub.file test-plans --path=<file_path> --limit=5 --min-words=15
```

## Workflow

1. Identify modified files (`sl status`)
2. Fetch test plans for the 2-3 most important files
3. Analyze patterns across examples (test commands, manual steps, edge cases)
4. Synthesize a test plan adapted to the current changes — don't just copy
5. Include concrete evidence: actual test output, pass/fail counts, commands run
