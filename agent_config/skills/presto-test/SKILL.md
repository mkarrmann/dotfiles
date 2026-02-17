---
name: presto-test
description: Use when running validation tests against a deployed Presto cluster — CLI spot-checks, verifier (regression), goshadow (query replay), BEEST (querybank), or shadow A/B (performance comparison). Requires a deployed cluster — see presto-deploy.
---

# Presto Test

## Overview

Post-deployment validation tools for Presto clusters.

**Prerequisites:** `feature install warehouse` (provides `presto`, `pt`, `goshadow`, `presto-shadow`)

**Key script:** `~/.claude/skills/presto-test/presto-test`

**Related skills:**
- `presto-build` — Building Presto from source
- `presto-deploy` — Deploying to a cluster (required before testing)

## Test Selection Guide

| Test type | Use when | Duration |
|-----------|----------|----------|
| CLI spot-check | Quick sanity: "does it run a query?" | seconds |
| Verifier | Regression: compare results vs control cluster | minutes |
| goshadow | Replay real queries from production | minutes–hours |
| BEEST | Run curated querybank suites | minutes |
| Shadow A/B | Performance comparison under load | hours |

## Quick Reference

| Task | Command |
|------|---------|
| Interactive CLI | `presto-test cli -c <cluster>` |
| Spot-check query | `presto-test cli -c <cluster> -e "SELECT ..."` |
| Verifier (default) | `presto-test verifier -c <cluster>` |
| Verifier (explicit control) | `presto-test verifier -c <cluster> --control <ctl> --suite <suite>` |
| goshadow (paste) | `presto-test goshadow -c <cluster> -p <paste_id>` |
| goshadow (logs) | `presto-test goshadow -c <cluster> --mode logs --env <src> --start "..." --end "..."` |
| goshadow (live) | `presto-test goshadow -c <cluster> --mode live --env <src>` |
| BEEST (suite) | `presto-test beest -c <cluster> --suite <suite>` |
| BEEST (specific IDs) | `presto-test beest -c <cluster> --ids 115315,127316` |
| Shadow A/B | `presto-test shadow -c <cluster> --query-file <file> --tag <tag>` |

## CLI Spot-Check

Quick connectivity and query testing.

```bash
# Interactive shell
presto-test cli -c <cluster>

# Run a specific query
presto-test cli -c <cluster> -e "SELECT count(*) FROM hive.default.<table>"
```

## Verifier (Regression Suite)

Runs curated queries and compares results against a control cluster.

```bash
# Default control cluster
presto-test verifier -c <cluster>

# Explicit control cluster and test suite
presto-test verifier -c <cluster> --control atn1_batch1 --suite atn1_default
```

Results at: search "presto verifier results" on internal tools.

## goshadow (Query Replay)

Replays real production queries against a test cluster.

```bash
# Replay specific queries from a Paste
presto-test goshadow -c <cluster> -p <paste_id>

# Replay historical production traffic
presto-test goshadow -c <cluster> --mode logs --env <source_cluster> \
    --start "2026-01-14 14:00" --end "2026-01-14 16:00"

# Replay live traffic
presto-test goshadow -c <cluster> --mode live --env <source_cluster>
```

The script automatically adds `--run-as-current-user` and `--max-concurrent-query 200`.

## BEEST (Querybank)

Runs curated querybank test suites.

```bash
# Run a test suite
presto-test beest -c <cluster> --suite <suite_name>

# Run specific test cases by ID
presto-test beest -c <cluster> --ids 115315,127316
```

The script automatically sets `--namespace beest_ftw`.

## Shadow A/B (Performance Comparison)

Compares query performance between clusters.

```bash
presto-test shadow -c <cluster> --query-file /tmp/queries.sql --tag <unique_tag>
```

Default: 200 replayer threads. Override with `--threads <n>`.

Analyze results via Bento notebook N150290.

## Common Issues

| Problem | Fix |
|---------|-----|
| Cluster unreachable during validation | Check deployment; use `--skip-validation` to bypass |
| goshadow auth errors | Script adds `--run-as-current-user` automatically |
| Verifier: no control cluster | Specify `--control <cluster>` explicitly |
| `presto --smc` connection refused | Cluster may still be restarting; check TW job health |
| BEEST namespace errors | Script uses `beest_ftw` namespace by default |
