---
name: cogwheel-debug
description: Debug Cogwheel test failures by extracting experiment IDs, querying debug tools for experiment/trial details, analyzing TW logs, and tracing errors to source code. Use when investigating Cogwheel test failures, ServiceLab experiments, or Conveyor APPLICATION_FAILURE on cogwheel nodes.
---

## Prerequisites

This skill requires the `team-cogwheel-engineer` plugin for the Cogwheel debug MCP tools (`experiment_debug_info`, `trial_debug_info`, etc.). If these tools are not available, tell the user:

> Install the `team-cogwheel-engineer` plugin:
> ```
> claude-templates plugin team-cogwheel-engineer install
> ```
> Then restart your session and re-run `/cogwheel-debug`.

## Routing

Ask the user: automated debug-fix-rerun loop, or one-time diagnosis?

- **Loop:** invoke `/cogwheel-debug-loop --experiment-id E<ID>` and stop.
- **One-time (default):** proceed with the guide below.

---

Read the full debugging guide at `references/debug_guide.md` and follow it.
