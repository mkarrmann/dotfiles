# Tupperware TaskUpdateHistoryRecord Query Skill

## Overview

This skill provides the complete schema reference and query patterns for **TaskUpdateHistoryRecord** objects in Tupperware Universal Search. TaskUpdateHistoryRecord contains historical records of task updates, useful for understanding task lifecycle and state transitions.

### When to Use This Skill
- Investigating task lifecycle events
- Understanding task state transitions
- Debugging task update issues
- Auditing task changes over time
- Tracing task history

## Quick Reference

**Object Type Enum:** `8` (TaskUpdateHistoryRecord)

**Query Language:** See [universal-search-syntax.md](universal-search-syntax.md) for how to run queries (thriftdbg command syntax, single-line rule, quick-start examples).

### Common Query Patterns

#### Get Task Update History for a Task (Association)

```json
{"request":{"select":{"allFields":{}},"from":8,"where":{"assocFilter":{"assocObjectType":4,"assocObjectIds":["tsp_prn/myteam/my_service/0"]}},"jsonResponseFormat":{}}}
```

#### Get Task Update History by Time Range

> **Limitation:** TaskUpdateHistoryRecord only supports `EQ` (cmp:1) comparison for field filters. Range queries (GT, GE, LT, LE) are not supported. To filter by time, use `EQ` with a specific timestamp, or retrieve all records via association and filter client-side with `jq`.

Get all history for a task, then filter by time range client-side:

```json
{"request":{"select":{"allFields":{}},"from":8,"where":{"assocFilter":{"assocObjectType":4,"assocObjectIds":["tsp_prn/myteam/my_service/0"]}},"jsonResponseFormat":{}}}
```

Then pipe through: `jq '[.[] | select(.startTime >= 1700000000 and .endTime <= 1700100000)]'`

#### Get Task Update History with Specific Fields

```json
{"request":{"select":{"selectedJsonPaths":["$.startTime","$.endTime","$.agentState","$.exitCode","$.exitTrigger","$.hostName"]},"from":8,"where":{"assocFilter":{"assocObjectType":4,"assocObjectIds":["tsp_prn/myteam/my_service/0"]}},"jsonResponseFormat":{}}}
```

See universal-search-syntax.md for how to run these queries.

## Schema Reference

See the full [Language Reference](#language-reference) below for complete field documentation.

### Key Fields (Indexed/Searchable)

| JSONPath | Type | Description |
|----------|------|-------------|
| `$.startTime` | i32 | Unix timestamp (seconds) when task instance started |
| `$.endTime` | i32 | Unix timestamp (seconds) when task instance stopped |

> Only `startTime` and `endTime` are currently indexed. Other fields can be selected but not filtered on.

### TaskUpdateHistoryRecord Structure Overview

```yaml
TaskUpdateHistoryRecord:
  startTime: i32 [Indexed] — Unix timestamp when task instance started
  endTime: i32 [Indexed] — Unix timestamp when task instance stopped
  agentState: TaskState enum — Agent-reported state at capture time
  exitCode: i32 — Process exit code (0 = success)
  signalCode: i32 — Signal that terminated the process
  exitMessage: string — Human-readable exit reason
  hostName: string — Hostname where task ran
  hostIp: string — IP address of host
  canaryId: string — Canary deployment identifier
  packages: list<Package> — Deployed fbpackages
  fullUUID: string — Full container instance UUID
  exitTrigger: ContainerExitTrigger enum — What triggered the exit
  killContext: TaskKillContext enum — Why the container was killed
```

### Access Pattern

Records are accessed via **association with a Task** (assocObjectType `4`). There is no direct `taskId` or `jobId` field to filter on.

For CompareOp values, see [universal-search-syntax.md](universal-search-syntax.md#compareop-values).

## Discover Available Fields

Use the `help()` API with `objectType:8` and `format:2` to discover available fields. See universal-search-syntax.md for the runnable command.

---

# Language Reference


# Tupperware TaskUpdateHistoryRecord — Language Reference (Agent Prompt)

This document describes the **TaskUpdateHistoryRecord** resource as defined in the Tupperware Universal Search system. It is intended to be used as an agent prompt so that an LLM can answer questions about task update history records, their schema, fields, and lifecycle.

---

## 1. Overview

A **TaskUpdateHistoryRecord** represents a single historical event record for a Tupperware Task, tracking one lifecycle period from start to stop. Each record captures when the task instance started and ended, the exit state, host information, packages in use, and exit context. Records are stored per-task and sorted descending by `startTime`.

In Universal Search, the TaskUpdateHistoryRecord object (`ObjectType.TaskUpdateHistoryRecord`, value `8`) is the `Tupperware.TaskUpdateHistoryRecord` struct from `tupperware/api/if/Tupperware.thrift`.

**TaskUpdateHistoryRecord identity format:** Records are accessed via association with a Task.

### Schema Diagram

The diagram below is the **authoritative schema reference** for the TaskUpdateHistoryRecord resource. All fields, types, `[Deprecated]` and `[Indexed]` annotations are shown here. Sections 2–3 provide **field-level documentation only** (behavioral details, caveats, code examples) — they do not repeat the schema.

> **Legend:** `[Indexed]` = field is searchable via `jsonPathFilter`.
> `[Deprecated]` = field is deprecated.

```yaml
Tupperware.thrift::TaskUpdateHistoryRecord:
  startTime: i32 [Indexed]
  endTime: i32 [Indexed]
  agentState: TaskState enum
  exitCode: i32
  signalCode: i32
  exitMessage: string
  hostName: string
  hostIp: string
  canaryId: string
  packages: list<Package>
    name: string
    version: i32
    ephemeralPackageId: string
    isRPM: bool
    fetchTimeoutInSec: i32
    installPrefix: string
    isAutoUpdate: bool
    installTimeoutInSec: i32
    rpms: map<string, string>
    tag: string
    twInternalReadOnly: bool
    uuid: string
    preCompressedSizeBytes: i64
    isCaf: bool
    isLazyCaf: bool
  fullUUID: string
  exitTrigger: ContainerExitTrigger enum
  killContext: TaskKillContext enum
```

---

## 2. Field Reference

Defined in `tupperware/api/if/Tupperware.thrift`. See the [Schema Diagram](#schema-diagram) for the full type hierarchy and annotations.

### `startTime` *(i32)* **[Indexed]**

Unix timestamp (seconds) when this task instance started running.

---

### `endTime` *(i32)* **[Indexed]**

Unix timestamp (seconds) when this task instance stopped. A value of `0` or `-1` may indicate the task is still running.

---

### `agentState` *(TaskState enum)*

The agent-reported state of the task at the time this record was captured.

Common final states include `TASK_STATE_COMPLETED` (clean exit, code 0), `TASK_STATE_ABORTED` (non-zero exit), `TASK_STATE_KILLED_BY_SIGNAL` (signal kill), and `TASK_STATE_FORCED_SHUT_DOWN` (SIGKILL).

---

### `exitCode` *(i32)*

Process exit code. `0` indicates successful completion; non-zero values indicate errors. Common values:
- `0` — success
- `1` — general error
- `137` — killed by SIGKILL (128 + 9)

---

### `signalCode` *(i32)*

Signal that terminated the process, if applicable. Common values:
- `9` — SIGKILL
- `15` — SIGTERM
- `0` — not killed by signal

---

### `exitMessage` *(string)*

Human-readable message describing the exit reason. May be empty for clean exits.

---

### `hostName` *(string)*

The hostname of the machine where this task instance ran.

---

### `hostIp` *(string)*

The IP address of the host where this task instance ran.

---

### `canaryId` *(string)*

If the task was part of a canary deployment during this lifecycle period, contains the canary identifier. Empty string if not a canary.

---

### `packages` *(list\<Package\>)*

The list of fbpackages that were deployed to this task instance. Each Package contains:

- **`name`** *(string)*: The fbpackage name.
- **`version`** *(i32)*: The fbpackage version number. Subject to ongoing deprecation — UUID will become the sole identifier.
- **`uuid`** *(string)*: The unique package identifier as reported by fbpkg. A `name:UUID` pair uniquely identifies a package.
- **`tag`** *(string)*: The tag being tracked (requires `isAutoUpdate`).
- **`isRPM`** *(bool)*: True if the package contains RPMs to install.
- **`isCaf`** / **`isLazyCaf`** *(bool)*: Whether the package is backed by CAF or Lazy CAF.
- **`preCompressedSizeBytes`** *(i64)*: Pre-compressed size in bytes, used to determine fetch timeout.

---

### `fullUUID` *(string)*

The full UUID of the container instance. This is the authoritative container identifier.

---

### `exitTrigger` *(ContainerExitTrigger enum)*

What triggered the container exit. This is a typedef for `AgentService.ExitTrigger`.

---

### `killContext` *(optional, TaskKillContext enum)*

Why the container was killed, from the agent's perspective. This is a typedef for `AgentService.KillContext`. Only set when the container was actively killed (as opposed to exiting on its own).

---

## 3. Indexed (Searchable) Fields & Supported Operations

Fields annotated `[Indexed]` in the schema above are searchable via `jsonPathFilter`. Only `startTime` and `endTime` are currently indexed.

### Refreshing the indexed-fields list

Use the `help()` API with `objectType:8` and `format:2` to discover searchable fields. See universal-search-syntax.md for the runnable command.

### Supported Association Types

TaskUpdateHistoryRecords support association-based queries with:

| Association Type | Description |
|-----------------|-------------|
| `Task` | Find history records belonging to a specific task |

For CompareOp values and query syntax, see [universal-search-syntax.md](universal-search-syntax.md).

---

## 4. References

- **Tupperware.thrift**: `fbcode/tupperware/api/if/Tupperware.thrift`
- **Common.thrift**: `fbcode/tupperware/api/if/Common.thrift`
- **common.thrift**: `fbcode/tupperware/if/common.thrift`
- **AgentService.thrift**: `fbcode/tupperware/if/AgentService.thrift`
- **ResourceSearch.thrift**: `fbcode/tupperware/universal_search/if/ResourceSearch.thrift`
- **Job Language Reference**: https://www.internalfb.com/wiki/Infra_Cloud/Service_Hosting/Tupperware/Tupperware_Reference/LanguageReference/Job
