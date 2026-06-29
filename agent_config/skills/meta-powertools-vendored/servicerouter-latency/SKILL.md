---
name: servicerouter-latency
description: Comprehensive guide for debugging ServiceRouter latency issues. Auto-invoke when user mentions SR latency, RPC timeouts (RECV_TIMEOUT, QUEUE_TIMEOUT, TASK_EXPIRED), connection errors, or service slowness. Provides hypothesis formation and automated Scuba query validation.
allowed-tools: Bash(scuba:*)
hooks:
  PostToolUse:
    - matcher: "Skill"
      hooks:
        - type: command
          command: "python3 /usr/local/claude-templates-cli/components/helpers/track_plugin_usage.py --skill servicerouter-latency || true"
          async: true
          timeout: 5
  UserPromptSubmit:
    - hooks:
        - type: command
          command: "python3 /usr/local/claude-templates-cli/components/helpers/track_plugin_usage.py --skill servicerouter-latency || true"
          async: true
          timeout: 5
---

# ServiceRouter Latency Debugging Guide

This skill provides comprehensive knowledge and tools for investigating ServiceRouter (SR) latency issues. It combines hypothesis formation with automated Scuba query validation.

**Companion skill:** This skill uses Scuba queries extensively. Reference the `scuba` skill for detailed Scuba query syntax, functions, and best practices.

## Table of Contents

- [Quick Reference](#quick-reference)
- [When to Use](#when-to-use)
- [Auto-execution Guidelines](#auto-execution-guidelines)
- [Critical: ServiceRouter Scuba Sampling](#critical-servicerouter-scuba-sampling)
  - [Latency Column Units](#latency-column-units)
- [Investigation Modes](#investigation-modes)
- [Part 1: Hypothesis Formation](#part-1-hypothesis-formation)
  - [Latency Hierarchy](#latency-hierarchy)
  - [Debugging Workflow](#debugging-workflow)
  - [Debugging Server Slowness](#debugging-server-slowness)
  - [Debugging Client Slowness](#debugging-client-slowness)
  - [Common Error Types](#common-error-types-and-debugging)
  - [Retries](#retries)
  - [Load Balancing](#load-balancing)
  - [Connection Pooling](#connection-pooling)
- [Part 2: Validation with Scuba Queries](#part-2-validation-with-scuba-queries)
  - [Phase 1: Initial Triage](#phase-1-initial-triage)
  - [Phase 2A: Retry Analysis](#phase-2a-retry-analysis)
  - [Phase 2B: Server Analysis](#phase-2b-server-analysis)
  - [Phase 2C: Network Analysis](#phase-2c-network-analysis)
  - [Phase 2D: Client Analysis](#phase-2d-client-analysis)
  - [Phase 2E: Connection Analysis](#phase-2e-connection-analysis)
  - [Phase 3: Load Imbalance Analysis](#phase-3-load-imbalance-analysis)
  - [Phase 4: Downstream Dependency Analysis](#phase-4-downstream-dependency-analysis)
  - [Phase 5: Sharded / Multi-Tenant Backend Analysis](#phase-5-sharded--multi-tenant-backend-analysis)
- [Scuba Queries Reference](#scuba-queries-reference)
- [ODS Counters Reference](#ods-counters-reference)
- [Quick Debugging Checklist](#quick-debugging-checklist)
- [References](#references)

---

## Quick Reference

**For Experienced Users:**

**⚠️ SAMPLING:** SR Scuba uses sampling. Use `SUM(sampleRatio)` for request counts, not `COUNT(*)`.

**⚠️ UNITS:** The `latency` column is in **milliseconds (ms)**. Columns ending in `_us` are microseconds.

1. **Open SR Scuba:** `bunnylol srs <service_name>`
2. **Check latency breakdown:** Compare Overall vs Attempt vs Channel latency
3. **Retries?** Look at `Request Attempt ID` column (>1 = retried)
4. **Server issue?** Check `Server Application Handler Latency` and `Server Process Delay Ms`
5. **Client issue?** Check EventBase Scheduling Latency
6. **Network?** Check `Round Trip Time` and `Cross Region` columns
7. **Regional?** Use `src_region_raw` / `dst_region_raw` for region breakdown (NOT `src_region`/`dst_region` — those are often NULL)
8. **Connections?** Check `reuse` rate and `Connection Age`
9. **Downstream slow?** Query with `source_service = '<your_service>'` to find what it calls

**Common Scuba Commands (see `scuba` skill for full syntax):**

```bash
# Latency breakdown by time (note: SUM(sampleRatio) for request count)
scuba -e "SELECT time:truncate(time, 300),
          AVG(latency) AS avg_overall_ms,
          AVG(attempt_latency_ms) AS avg_attempt_ms,
          SUM(sampleRatio) AS requests
          FROM service_router
          WHERE time >= now()-3600 AND service = 'your_service' AND ex != 1
          GROUP BY time:truncate(time, 300) ORDER BY time:truncate(time, 300)" --format sparse

# Error breakdown
scuba -e "SELECT error_reason, SUM(sampleRatio) AS requests FROM service_router
          WHERE time >= now()-3600 AND service = 'your_service' AND error_reason IS NOT NULL
          GROUP BY error_reason ORDER BY requests:desc LIMIT 10" --format sparse
```

---

## When to Use

**AUTOMATICALLY invoke this skill when:**
- User mentions ServiceRouter, SR, or RPC latency issues
- Investigating timeout errors (RECV_TIMEOUT, QUEUE_TIMEOUT, TASK_EXPIRED)
- Debugging connection errors (CONNECT_TIMEOUT, CONNECT_RESET)
- User reports service slowness or response time degradation
- Questions about SR client or server performance
- Troubleshooting load balancing or traffic distribution issues

**DO NOT invoke for:**
- General Thrift questions not related to latency
- SMC/tier configuration without latency symptoms
- SR configuration questions without debugging context

---

## Auto-execution Guidelines

**The following commands are read-only and can be run without user confirmation when relevant:**

- `scuba -e "SELECT ... FROM service_router ..."` - All SR latency diagnostic queries
- `scuba -e "SELECT ... FROM thrift_request_events ..."` - Server-side latency queries
- `scuba -e "SELECT ... FROM thrift_connection_events ..."` - Connection event queries
- `bunnylol srs <service_name>` - Open SR Scuba UI for a service

**Reference the `scuba` skill for:**
- Scuba query syntax and best practices
- Time filtering (ALWAYS required: `time >= now()-<seconds>`)
- Output formats (`--format sparse` recommended)
- Available SQL functions and aggregations
- Common query patterns and troubleshooting

---

## Critical: ServiceRouter Scuba Sampling

**⚠️ IMPORTANT:** ServiceRouter Scuba uses sampling. Each row represents ONE SAMPLE, not one request.

### The `sampleRatio` Column

The `sampleRatio` column indicates how many actual requests each sample represents. At high traffic volumes, one sample might represent 100+ requests.

### Correct Query Patterns

**Request counts:**
```sql
-- ❌ WRONG - counts samples, not requests
COUNT(*) AS requests

-- ✅ CORRECT - sums the weight of each sample
SUM(sampleRatio) AS requests
```

**Error rates:**
```sql
-- ❌ WRONG - unweighted
AVG(error) * 100 AS error_pct

-- ✅ CORRECT - weighted by request volume
SUM(error * sampleRatio) / SUM(sampleRatio) * 100 AS error_pct
```

**Counting specific conditions (e.g., retries, specific errors):**
```sql
-- ❌ WRONG
SUM(IF(request_attempt_id > 1, 1, 0)) AS retried_requests

-- ✅ CORRECT
SUM(IF(request_attempt_id > 1, sampleRatio, 0)) AS retried_requests
```

**Latency metrics (no weighting needed):**
```sql
-- ✅ CORRECT - just use AVG or percentiles directly
AVG(latency) AS avg_latency
APPROX_PERCENTILE(latency, 0.99) AS p99_latency
```

### Why This Matters

Ignoring sampling leads to:
- **Underestimating request volume** by 10-100x during high traffic
- **Incorrect error rates** that don't reflect actual user impact

### Latency Column Units

ServiceRouter Scuba has multiple latency columns with different units:

| Column | Unit | Notes |
|--------|------|-------|
| `latency` | **milliseconds (ms)** | Overall request latency |
| `attempt_latency_ms` | milliseconds (ms) | Per-attempt latency |
| `channel_latency_ms` | milliseconds (ms) | Network channel time |
| `server_application_handler_latency_ms` | milliseconds (ms) | Server processing |
| `evb_scheduling_latency_us` | **microseconds (µs)** | EventBase scheduling |
| `selection_latency_us` | microseconds (µs) | Host selection time |
| `connect_latency_us` | microseconds (µs) | Connection establishment |
| `tcp_connect_latency_us` | microseconds (µs) | TCP handshake |

**Tip:** Columns ending in `_ms` are milliseconds; columns ending in `_us` are microseconds. The generic `latency` column is in **milliseconds**.

---

## Investigation Modes

### Interactive Mode (default)
- Pauses at decision points to ask clarifying questions
- Allows user to guide the investigation direction
- Best for: complex issues, unfamiliar services

### Automated Mode
- Runs through the full investigation without pausing
- Follows all promising leads and reports all findings
- Makes reasonable assumptions at decision points (documents assumptions made)
- Best for: quick triage, familiar services, comprehensive reports

---

## Part 1: Hypothesis Formation

### Overview

Latency in ServiceRouter is the delay between a client making a request and receiving a response. ServiceRouter breaks down overall latency into multiple components representing smaller steps in request processing.

**Key principle:** Start from high-level latencies and drill down into finer-grained components until you identify the root cause.

### Latency Hierarchy

```
Overall Latency
└── Attempt Latency (per-retry)
    ├── Client Latency (SR overhead)
    │   ├── Eventbase Scheduling Latency
    │   ├── Selection Latency
    │   │   ├── Selector Latency
    │   │   └── Health Check Latency
    │   ├── Load Balancing Latency
    │   │   └── K Avg Load Polling Latency
    │   └── Connection Total Latency
    │       ├── TCP Connect Latency
    │       └── Security Latency
    └── Channel Latency
        ├── Channel Request Send Latency
        └── Channel Response Wait Latency
            └── Server Processing Latency
                ├── Request Queuing Latency
                ├── Request Processing Latency
                ├── Response Queuing Latency
                └── Response Write Latency
```

### Debugging Workflow

#### Step 1: Check Overall Latency vs Attempt Latency

If **Overall Latency** increased but **Attempt Latency** is stable:
- Indicates increased retryable errors causing retries
- Check `Request Attempt ID` column - values > 1 indicate retries
- Filter `Retry Ex=1` to see intermediate retry samples
- Debug using [Debugging Errors wiki](https://www.internalfb.com/intern/wiki/ServiceRouter/User_Guide/Debugging/DebuggingErrors/)

If **Attempt Latency** increased → proceed to Step 2

#### Step 2: Check Channel Latency

If **Channel Latency** increased:
- Usually caused by server-side slowness → check Server Processing Latency (Step 3)
- Check for increased x-region traffic ("Cross Region" column in SR Scuba)
- Check compression settings changes ("Request Compression", "Response Compression" columns)
- For large payloads, consider ZSTD compression

If **Channel Latency** is stable but **Attempt Latency** increased → check Client Latency (Step 4)

#### Step 3: Debug Server Processing Latency

Check these metrics in order:

1. **Request Queuing Latency** (`Server Process Delay Ms` in SR Scuba)
   - High values indicate server CPU saturation or request spikes
   - Note: No queuing latency for 'eb' mode servers

2. **Request Processing Latency** (`Server Application Handler Latency` in SR Scuba)
   - Time in Thrift AsyncProcessor (deserialize + process + serialize)

3. **Response Write Latency** - Significant if response payload is large

**Data sources:**
- SR Scuba: `bunnylol srs <service_name>`
- Server Scuba: `thrift_request_events`
- ODS: `thrift.process_delay`, `thrift.process_time`

#### Step 4: Debug Client Latency (SR Overhead)

Check in this order:

1. **Eventbase Scheduling Latency** - High values indicate client CPU saturation
2. **Selection Latency** - Health checks or SMC lookups taking long
3. **Load Balancing Latency** - Load polling overhead
4. **Connection Total Latency** - New connection establishment

If none explain the issue, use [Internal Tracing](https://www.internalfb.com/intern/wiki/ServiceRouter/User_Guide/Debugging/Internal_Tracing/) as last resort.

---

### Debugging Server Slowness

When the server is running slow, clients may see RECV_TIMEOUT, QUEUE_TIMEOUT, or TASK_EXPIRED errors.

#### First: Check for Package Push

**Always check this first.** Many regressions come from new package pushes.

SR Scuba shows `fbpkg version` on both client and server - cross-check if regression aligns with push time.

#### Blocking Calls / Inefficient Handler

Check if server handler is:
- Executing blocking calls
- Performing I/O operations
- Running synchronous operations
- Contending for locks

**Metrics to check:**
- `process_delay_us` and `process_latency_us` in thrift_request_events Scuba
- `thrift.process_delay`, `thrift.process_time`, `thrift.queued_requests`, `thrift.queuelag` in ODS

**Tools to identify code inefficiency:**
1. **Thrift Dogpile** (recommended) - Triggered during server issues, stacktraces uploaded to `thrift_dogpiles` Scuba
2. **Strobelight On/Off CPU** - `bunnylol strobelight`
3. **QuickStack** - `bunnylol quickstack`

#### CPU Throttled by TW

Check ODS counter `tw.cpu.throttled` - measures percentage of time task's CPU was throttled due to hitting allocated limit.

#### Undersized CPU Thread Pool

Symptoms: Queue timeouts, requests piling up.

**Check if undersized:**
- `thrift_server_events` Scuba for thread count vs available cores
- ODS formula: https://fburl.com/ods/38fjgtqm (% unused logical cores)
- `dyno_thread_stats` Scuba for thread-level CPU metrics

**Fix:** Don't set CPU thread pool count explicitly - use default (max hyperthreads).

#### Large Response Payload

If server sends large responses:
- Check `write_delay_latency_us` and `write_latency_us` in thrift_request_events
- Check `thrift.write_delay` and `thrift.write_time` ODS counters

---

### Debugging Client Slowness

When the client is slow, you'll see high Eventbase Scheduling Latency and potentially RECV_TIMEOUT errors.

#### First: Check for Package Push

Same as server - check if regression aligns with package push time using SR Scuba fbpkg version columns.

#### Client CPU Exhausted

Check standard ODS metrics:
- `system.cpu-busy-pcts`, `system.cpu-util-pct`
- `tw.container.cpu-util-pct`, `tw.cpu.throttled`, `tw.cpu.reservation_pct`

SR Scuba also logs client CPU utilization and PSI.

#### SR IO Thread Overloaded

SR allocates 4 (default) internal IO threads. Check **SR EventBase Scheduling Latency** in SR Scuba.

**What is EventBase Scheduling Latency?**
Time from request placed in queue to picked up by event base thread.

**Common causes of high scheduling latency:**
1. **High CPU utilization** (most common) - slows context switching
2. **Request spike** - too many tasks dispatched to queue
3. **Insufficient SR IO threads** - check actual count in SR Scuba or dyno_thread_stats
4. **Blocked SR IO threads** (rare) - check for futex/mutex in strobelight profiles

**Tuning SR IO threads:**
- GFLAG `sr2_event_base_pool_size` - absolute number
- GFLAG `sr2_event_base_pool_size_cpu_multiplier` - fraction of cores (preferred)

#### TW CPU Throttling

Check `tw.cpu.throttled` ODS counter.

#### User Code on SR IO Threads (rare)

Applies to deprecated `getSRClientUnique` API without executor - user callbacks run on SR IO threads.

---

### Common Error Types and Debugging

#### RECV_TIMEOUT

**What it means:** Client waited too long for server response.

**RECV_TIMEOUT = network_padding + processing_timeout**

**Debugging:**
1. Check actual timeout value in error message (`after {X}ms`)
2. Verify `processing_timeout` is set correctly (recommend 1.5x p99 processing time)
3. Check for code overrides (takes precedence over config)
4. Check if server is slow → see Server Slowness section
5. Check if client is slow → see Client Slowness section

#### QUEUE_TIMEOUT (APP_QUEUE_TIMEOUT)

**What it means:** Request waited too long in server queue before processing.

**Debugging:**
1. Check queue timeout value in `thrift_request_events` Scuba
2. Default is 100ms for all C++ and Python Thrift services
3. Check if server is slow → see Server Slowness section
4. Check for request spike or load imbalance → see Load Imbalance section

#### TASK_EXPIRED (APP_TASKEXPIRED)

**What it means:** Overall request time (queue + processing) too long.

Task timeout = 1.1 × client timeout (timeout sharing mechanism).

**Note:** Usually you see RECV_TIMEOUT before TASK_EXPIRED. If seeing TASK_EXPIRED, check if SR IO thread is overloaded.

#### HOST_OVERLOAD / APP_OVERLOAD

**What it means:** Server concurrency limit breached.

**Debugging:** See https://www.internalfb.com/intern/staticdocs/thrift/docs/fb/troubleshooting/overload/

#### CONNECT_TIMEOUT

**What it means:** Taking too long to establish connection (TCP + TLS).

99% of time caused by TLS handshake timeout.

**Debugging:**

Server-side issues:
- Code push lining up with errors?
- Server overloaded? Check CPU, `thrift.socket_success_process_time`, `thrift.pending_connections`
- Check ThriftIO threads with Strobelight

Client-side issues:
- Creating too many connections? Check connection reuse rate
- Client busy/slow? Check SR_TLSConnection thread pool eventbase busy metrics

Network issues:
- Firewall issues for offnet services?
- High retransmits? Check `system.net.tcp.rxmits_per_s`

#### CONNECT_RESET (Connection Refused)

**What it means:** Server not accepting TCP connections.

**Quick test:** `nc {ip} {port} -v`

**Common causes:**
- Wrong port registered
- TW Spec misconfiguration
- TW/SMC update issue - stale host:port info

#### Connection Closed Errors (RECV_EOF, RECV_UNKNOWN, CONNECT_UNKNOWN)

**What it means:** Existing connection abnormally closed.

**Check:** `thrift_connection_events` Scuba for connection-closed events.

**Common causes:**
- Server protection mechanisms (ingress/egress memory limit, socket queue timeout)
- Server unhealthy (crashing, OOMing)
- ACL issues
- Server out of FDs

---

### Retries

#### How Retries Work

- SR automatically retries on certain failures
- Retries hit different hosts than original request
- Intermediate retries hidden in Scuba by default (filter `Retry Ex=1` to see)
- `Request Attempt ID` > 1 indicates retries occurred

#### Errors Eligible for Retries

**Retriable:**
- Connection errors: CONNECT_TIMEOUT, CONNECT_RESET, etc. (2 retries default)
- Overload errors: HOST_OVERLOAD, APP_OVERLOAD, APP_QUEUE_TIMEOUT (2 retries default)
- I/O errors: RECV_EOF, RECV_UNKNOWN, etc.
- App-specific errors if configured in Routing Product

**Not retriable:**
- Throttling errors: THROTTLING_*, BLACKHOLE
- Client-problem errors: UNKNOWN_METHOD, REQUEST_PARSING, APP_TASKEXPIRED

#### Auto-Disable Retry on Meltdown

SR tracks per-service error rate. If > 10% (configurable), retries disabled to let service recover.

Check `Retry Throttled` column in Scuba.

---

### Load Balancing

#### How It Works

1. **Selection** - Client talks to a subset of hosts
2. **Load Balance** - Pick-K algorithm selects from subset based on load

#### Load Balancing Policies

- **Auto** (default) - SR decides based on conditions
- **Load Polling** - Actively polls load via getStatus calls
- **Cached Load** - Uses cached load from previous responses
- **Random** - Randomly picks (poor performance)

Check `Dispatch Type Used` in SR Scuba.

#### Load Balancing Latency

Only reported when load polling is applied.

**K Avg Load Polling Latency** increased:
- Load poll calls (getStatus) taking longer
- Same code path as health checks

**Load polling frequency increased:**
- Per-host QPS dropped
- Selection size increased
- Traffic shifts/splits

#### Load Imbalance Debugging

**Signs of imbalance:**
- Some hosts have much higher load than others
- Check ODS `thrift.received_requests.rate.60` grouped by task

**Common causes:**
1. **Selection size too small** - Not all hosts represented
2. **Hardware heterogeneity** - Different machine types with different capacities
3. **Sharding issues** - Hot shards

**Fix selection size:**
- Default is 10, may be too low if few clients and many servers
- Use https://fburl.com/scuba/service_router/rgjbajih to check client/server counts
- Increase selection size until load balances

---

### Connection Pooling

SR caches TCP connections for reuse to avoid costly handshakes.

#### Key Configs

- **connectionPoolMaxSize** - Max idle connections in pool (NOT total connections)
- **connection_keepalive** - How long idle connection stays in pool (default 30s)

#### Connection Reuse Rate

Should be close to 1. Low reuse indicates:
- Multiple threads talking to same host simultaneously
- Bursty traffic
- Large selection size causing connections to expire

Check `reuse` column in SR Scuba.

#### Debugging Connection Issues

**Connection Total Latency** increased:
- Check connection reuse rate
- Check TCP Connect Latency and Security Latency separately
- Consider tuning connection pooling if high x-region traffic

---

### Internal Tracing

Use as **last resort** when other metrics don't explain latency.

#### Enabling Tracing

Already enabled for small subset. To increase:
1. **Routing Product** → Logging → Advanced Logging Options
   - Trace Slow Requests (threshold-based)
   - Trace Overall Timeouts
   - Trace All Requests (expensive, not recommended)

2. **ScubaSampler config** - Set `logTraceSampleRatio`

#### Reading Traces

Traces in `Internal Trace` column (`vtrace`) in SR Scuba.

Format:
```
<epoch_time_in_seconds> START
<us_from_start> (+<us_from_previous>) <action> <location>
```

Actions: ENTER, LEAVE, MARK, TIMEOUT

---

## Part 2: Validation with Scuba Queries

> **Note:** This section uses Scuba queries extensively. Reference the `scuba` skill for:
> - Query syntax and CLI usage (`scuba -e "SELECT ..."`)
> - Time filtering (mandatory: `WHERE time >= now()-<seconds>`)
> - Output formats (`--format sparse` for tables, `--format csv` for data)
> - Available functions (`APPROX_PERCENTILE`, `STRFTIME`, etc.)
> - Troubleshooting common query issues

### Investigation Inputs

Before starting validation, gather:
1. **Service name** - The SR service/tier name (e.g., `my_service.foo`)
2. **Issue timeframe** - When the problem started/was observed
3. **Symptom** - Latency increase, errors, timeouts, etc.

### Time Window Calculation

```
investigation_start = issue_start - buffer_hours
investigation_end = issue_end OR now()

Buffer guidelines:
- Issue duration < 1 hour  → buffer = 1 hour
- Issue duration 1-6 hours → buffer = 2 hours
- Issue duration > 6 hours → buffer = 3 hours
```

Always capture time before the issue to see the transition from healthy to degraded state.

---

### Phase 1: Initial Triage

Run initial diagnostic query to get latency breakdown:

```bash
# Query: Latency breakdown by component (sampleRatio for request counts only)
scuba query service_router \
  --start "${START_TIME}" \
  --end "${END_TIME}" \
  --columns "time:truncate(time, 300)" \
  --columns "avg(latency):Overall" \
  --columns "avg(attempt_latency_ms):Attempt" \
  --columns "avg(channel_latency_ms):Channel" \
  --columns "avg(server_application_handler_latency_ms):ServerProcessing" \
  --columns "avg(server_process_delay_ms):ServerQueue" \
  --columns "avg(evb_scheduling_latency_us) / 1000:ClientEvb" \
  --columns "sum(sampleRatio):Requests" \
  --columns "sum(error * sampleRatio) / sum(sampleRatio) * 100:ErrorPct" \
  --filter "service = '${SERVICE_NAME}'" \
  --filter "ex != 1" \
  --filter "shadow != 1" \
  --group-by "time:truncate(time, 300)" \
  --order-by "time:truncate(time, 300)" \
  --format table
```

**Analysis decision tree:**

```
IF Overall Latency increased but Attempt Latency stable:
  → Retries are occurring (Phase 2A: Retry Analysis)

IF Attempt Latency increased:
  IF Channel Latency increased:
    IF ServerProcessing or ServerQueue increased:
      → Server-side issue (Phase 2B: Server Analysis)
    ELSE:
      → Network or payload issue (Phase 2C: Network Analysis)
  ELSE (Client latency increased):
    → Client-side issue (Phase 2D: Client Analysis)
```

---

### Phase 2A: Retry Analysis

When Overall > Attempt, retries are inflating overall latency.

```bash
# Query: Retry breakdown (sampleRatio for counts)
scuba query service_router \
  --start "${START_TIME}" \
  --end "${END_TIME}" \
  --columns "time:truncate(time, 300)" \
  --columns "avg(request_attempt_id):AvgAttempts" \
  --columns "sum(sampleRatio):Requests" \
  --columns "sum(if(request_attempt_id > 1, sampleRatio, 0)):RetriedRequests" \
  --filter "service = '${SERVICE_NAME}'" \
  --filter "ex != 1" \
  --group-by "time:truncate(time, 300)" \
  --order-by "time:truncate(time, 300)"
```

```bash
# Query: What errors are causing retries?
scuba query service_router \
  --start "${START_TIME}" \
  --end "${END_TIME}" \
  --columns "error_reason:ErrorReason" \
  --columns "sum(sampleRatio):Count" \
  --filter "service = '${SERVICE_NAME}'" \
  --filter "retry_ex = 1" \
  --group-by "error_reason" \
  --order-by "Count:desc" \
  --limit 10
```

**Decision point:** Based on error types found, branch to appropriate error debugging:
- Connection errors → Phase 2E: Connection Analysis
- Overload errors → Phase 2B: Server Analysis
- Timeout errors → Check both server and client

---

### Phase 2B: Server Analysis

When server processing or queuing latency increased.

```bash
# Query: Server-side latency breakdown
scuba query service_router \
  --start "${START_TIME}" \
  --end "${END_TIME}" \
  --columns "time:truncate(time, 300)" \
  --columns "avg(server_application_handler_latency_ms):Processing" \
  --columns "avg(server_process_delay_ms):Queuing" \
  --columns "sum(sampleRatio):Requests" \
  --filter "service = '${SERVICE_NAME}'" \
  --filter "ex != 1" \
  --group-by "time:truncate(time, 300)" \
  --order-by "time:truncate(time, 300)"
```

```bash
# Query: Check for traffic spike by source (sampleRatio for counts)
scuba query service_router \
  --start "${START_TIME}" \
  --end "${END_TIME}" \
  --columns "time:truncate(time, 300)" \
  --columns "source_service:Source" \
  --columns "sum(sampleRatio):Requests" \
  --filter "service = '${SERVICE_NAME}'" \
  --filter "ex != 1" \
  --group-by "time:truncate(time, 300)" "source_service" \
  --order-by "Requests:desc" \
  --limit 20
```

```bash
# Query: Check for deployment correlation (sampleRatio for counts)
scuba query service_router \
  --start "${START_TIME}" \
  --end "${END_TIME}" \
  --columns "time:truncate(time, 300)" \
  --columns "dst_fbpkg_version:ServerVersion" \
  --columns "avg(server_application_handler_latency_ms):Processing" \
  --columns "sum(sampleRatio):Requests" \
  --filter "service = '${SERVICE_NAME}'" \
  --filter "ex != 1" \
  --group-by "time:truncate(time, 300)" "dst_fbpkg_version" \
  --order-by "time:truncate(time, 300)"
```

**Interactive mode - Ask user if unclear:**
- "Traffic increased from {source_service}. Is this expected? Should I investigate rate limiting or capacity?"
- "New server version {version} correlates with latency increase. Should I investigate the deployment?"

**Server-side next steps:**
1. Check thrift_request_events for detailed server metrics
2. Check ODS for `thrift.process_delay`, `thrift.process_time`
3. Check for CPU throttling via `tw.cpu.throttled`
4. Recommend Strobelight/Dogpile profiling if handler is slow

---

### Phase 2C: Network Analysis

When Channel Latency increased but server processing looks normal.

```bash
# Query: Network and payload analysis (sampleRatio for counts)
scuba query service_router \
  --start "${START_TIME}" \
  --end "${END_TIME}" \
  --columns "time:truncate(time, 300)" \
  --columns "avg(channel_latency_ms):Channel" \
  --columns "avg(round_trip_time_us) / 1000:RTT_ms" \
  --columns "avg(request_size):ReqSize" \
  --columns "avg(response_size):RespSize" \
  --columns "avg(cross_region) * 100:CrossRegionPct" \
  --filter "service = '${SERVICE_NAME}'" \
  --filter "ex != 1" \
  --group-by "time:truncate(time, 300)" \
  --order-by "time:truncate(time, 300)"
```

```bash
# Query: Cross-region breakdown (sampleRatio for counts)
scuba query service_router \
  --start "${START_TIME}" \
  --end "${END_TIME}" \
  --columns "src_region_raw:SrcRegion" \
  --columns "dst_region_raw:DstRegion" \
  --columns "avg(channel_latency_ms):Channel" \
  --columns "sum(sampleRatio):Requests" \
  --filter "service = '${SERVICE_NAME}'" \
  --filter "ex != 1" \
  --group-by "src_region_raw" "dst_region_raw" \
  --order-by "Requests:desc" \
  --limit 20
```

**Analysis:**
- RTT increased → Network issue, check with nestigations group
- Response size increased → Payload bloat, check for regression
- Cross-region % increased → Traffic routing change, check CSLB/locality settings

---

### Phase 2D: Client Analysis

When client-side latency (EventBase scheduling, selection, connection) increased.

```bash
# Query: Client-side latency breakdown
scuba query service_router \
  --start "${START_TIME}" \
  --end "${END_TIME}" \
  --columns "time:truncate(time, 300)" \
  --columns "avg(evb_scheduling_latency_us) / 1000:EvbScheduling_ms" \
  --columns "avg(selection_latency_us) / 1000:Selection_ms" \
  --columns "avg(lb_latency_us) / 1000:LoadBalance_ms" \
  --columns "avg(connection_total_latency_us) / 1000:Connection_ms" \
  --filter "service = '${SERVICE_NAME}'" \
  --filter "ex != 1" \
  --group-by "time:truncate(time, 300)" \
  --order-by "time:truncate(time, 300)"
```

```bash
# Query: Check client deployment (sampleRatio for counts)
scuba query service_router \
  --start "${START_TIME}" \
  --end "${END_TIME}" \
  --columns "time:truncate(time, 300)" \
  --columns "src_fbpkg_version:ClientVersion" \
  --columns "avg(evb_scheduling_latency_us) / 1000:EvbScheduling_ms" \
  --columns "sum(sampleRatio):Requests" \
  --filter "service = '${SERVICE_NAME}'" \
  --filter "ex != 1" \
  --group-by "time:truncate(time, 300)" "src_fbpkg_version" \
  --order-by "time:truncate(time, 300)"
```

**Analysis:**
- EvbScheduling high → Client CPU exhausted or SR IO threads overloaded
- Selection high → Health check issues or SMC tier updates
- LoadBalance high → Load polling issues
- Connection high → New connections being established frequently

---

### Phase 2E: Connection Analysis

When seeing connection-related errors or high connection latency.

```bash
# Query: Connection metrics (sampleRatio for counts)
scuba query service_router \
  --start "${START_TIME}" \
  --end "${END_TIME}" \
  --columns "time:truncate(time, 300)" \
  --columns "avg(reuse):ReuseRate" \
  --columns "avg(tcp_connect_latency_us) / 1000:TCPConnect_ms" \
  --columns "avg(security_latency_us) / 1000:TLS_ms" \
  --columns "avg(connection_total_latency_us) / 1000:Total_ms" \
  --filter "service = '${SERVICE_NAME}'" \
  --filter "ex != 1" \
  --group-by "time:truncate(time, 300)" \
  --order-by "time:truncate(time, 300)"
```

```bash
# Query: Connection errors (sampleRatio for counts)
scuba query service_router \
  --start "${START_TIME}" \
  --end "${END_TIME}" \
  --columns "error_reason:Error" \
  --columns "error_reason_what:Details" \
  --columns "sum(sampleRatio):Count" \
  --filter "service = '${SERVICE_NAME}'" \
  --filter "error_reason in ('CONNECT_TIMEOUT', 'CONNECT_RESET', 'CONNECT_UNKNOWN')" \
  --group-by "error_reason" "error_reason_what" \
  --order-by "Count:desc" \
  --limit 20
```

**Analysis:**
- Reuse rate dropped → Connection churn, check selection size and keepalive
- TLS latency increased → Server or client CPU issues during handshake
- CONNECT_TIMEOUT → Server overloaded or network issues

---

### Phase 3: Load Imbalance Analysis

Run if suspecting uneven load distribution.

```bash
# Query: Load distribution by destination host (sampleRatio for counts)
scuba query service_router \
  --start "${START_TIME}" \
  --end "${END_TIME}" \
  --columns "dst_tw_task_id:Host" \
  --columns "sum(sampleRatio):Requests" \
  --columns "avg(server_application_handler_latency_ms):Processing" \
  --columns "sum(error * sampleRatio) / sum(sampleRatio) * 100:ErrorPct" \
  --filter "service = '${SERVICE_NAME}'" \
  --filter "ex != 1" \
  --group-by "dst_tw_task_id" \
  --order-by "Requests:desc" \
  --limit 30
```

```bash
# Query: Load balancing policy used (sampleRatio for counts)
scuba query service_router \
  --start "${START_TIME}" \
  --end "${END_TIME}" \
  --columns "dispatch_type_used:LBPolicy" \
  --columns "sum(sampleRatio):Count" \
  --columns "avg(lb_latency_us) / 1000:LBLatency_ms" \
  --filter "service = '${SERVICE_NAME}'" \
  --filter "ex != 1" \
  --group-by "dispatch_type_used" \
  --order-by "Count:desc"
```

**Analysis:**
- Large variance in requests per host → Selection size may be too small
- Some hosts have much higher error rates → Check health checks and markdown
- Load polling not being used → May need fresher load information

---

### Phase 4: Downstream Dependency Analysis

**CRITICAL:** When a service experiences latency increases, check if its downstream dependencies are slow. A service's latency often reflects the latency of services it calls.

**Concept:**
- Your service (e.g., `service_A`) calls downstream services
- If a downstream service is slow, `service_A` will appear slow to its callers
- Use `source_service` to find what services your target service is calling

```bash
# Query: Find downstream services called by your service (sampleRatio for counts)
scuba -e "
SELECT
  service AS downstream_service,
  SUM(sampleRatio) AS requests,
  AVG(latency) AS avg_latency_ms,
  APPROX_PERCENTILE(latency, 0.99) AS p99_latency_ms,
  SUM(error * sampleRatio) / SUM(sampleRatio) * 100 AS error_pct
FROM service_router
WHERE time >= ${START_TIME} AND time <= ${END_TIME}
  AND source_service = '${SERVICE_NAME}'
  AND ex != 1
GROUP BY service
ORDER BY requests DESC
LIMIT 20
" --format sparse
```

```bash
# Query: Downstream latency timeline
scuba -e "
SELECT
  STRFTIME(time / 300 * 300, '%Y-%m-%d %H:%M') AS time_bucket,
  service AS downstream,
  SUM(sampleRatio) AS requests,
  AVG(latency) AS avg_latency_ms,
  SUM(error * sampleRatio) / SUM(sampleRatio) * 100 AS error_pct
FROM service_router
WHERE time >= ${START_TIME} AND time <= ${END_TIME}
  AND source_service = '${SERVICE_NAME}'
  AND ex != 1
GROUP BY STRFTIME(time / 300 * 300, '%Y-%m-%d %H:%M'), service
HAVING SUM(sampleRatio) > 10
ORDER BY time_bucket, requests DESC
" --format sparse
```

```bash
# Query: Compare upstream vs downstream latency correlation
# If downstream latency increased BEFORE your service's latency, downstream is the cause
scuba -e "
SELECT
  STRFTIME(time / 300 * 300, '%Y-%m-%d %H:%M') AS time_bucket,
  'upstream' AS direction,
  AVG(latency) AS avg_latency_ms,
  SUM(sampleRatio) AS requests
FROM service_router
WHERE time >= ${START_TIME} AND time <= ${END_TIME}
  AND service = '${SERVICE_NAME}'
  AND ex != 1
GROUP BY STRFTIME(time / 300 * 300, '%Y-%m-%d %H:%M')
ORDER BY time_bucket
" --format sparse
```

**Analysis patterns:**

1. **Downstream slowdown causing upstream issues:**
   - Downstream latency increased BEFORE or AT THE SAME TIME as your service
   - Your service's `Server Application Handler Latency` increased (waiting on downstream)
   - Fix: Address the downstream service first

2. **Your service is the bottleneck:**
   - Downstream latencies are stable
   - Your service's `Server Process Delay` or `Server Application Handler Latency` increased independently
   - Fix: Focus on your service's capacity/performance

3. **Cascading failure:**
   - Downstream timeouts cause your service to retry or block
   - Your service becomes slow, causing YOUR upstream callers to timeout
   - Fix: Add circuit breakers, reduce downstream timeouts

**Interactive mode - Ask user:**
- "Downstream service {X} shows {Y}ms p99 latency (up from baseline). Should I investigate this service?"
- "Multiple downstream services are slow. Which should I prioritize: {A} (highest traffic) or {B} (highest error rate)?"

**Next step:** If a downstream service is identified as slow, check if it is a sharded or multi-tenant backend (e.g., laser, memcache, tao) → proceed to Phase 5.

---

### Phase 5: Sharded / Multi-Tenant Backend Analysis

**CRITICAL:** Some backend services (e.g., `laser/leaf`, `memcache`, `tao`) are **sharded or multi-tenant** — a single `service` column value represents many independent datasets, tenants, or shards. Aggregating by `service` alone hides which specific tenant or operation type is causing latency.

**When to use this phase:**
- A downstream service identified in Phase 4 is a known sharded backend (laser, memcache, tao, etc.)
- Standard SR breakdown columns (`routing_tier`, `dst_cluster`, `dst_tw_task_id`, etc.) return NULL
- Latency varies wildly within the same service, suggesting different workloads

**Key columns for multi-tenant breakdown:**

| Column | Description | When to use |
|--------|-------------|-------------|
| `tenant_id` | Identifies the specific dataset/shard/tenant within a service | **Primary** — most reliable for sharded backends like laser |
| `method` | The RPC method being called (e.g., `getKnn` vs `multiget`) | Distinguishes operation types with very different latency profiles |
| `routing_tier` | Routing tier name (may be NULL for some services) | Try first, fall back to `tenant_id` if NULL |

#### Step 1: Break down by method

Different RPC methods can have vastly different latency characteristics. A single service may handle both fast key-value lookups and slow vector searches.

```bash
# Query: Latency by RPC method (sampleRatio for counts)
scuba -e "
SELECT
  method AS Method,
  SUM(sampleRatio) AS requests,
  AVG(latency) AS avg_latency_ms,
  APPROX_PERCENTILE(latency, 0.99) AS p99_latency_ms
FROM service_router
WHERE time >= ${START_TIME} AND time <= ${END_TIME}
  AND service = '${DOWNSTREAM_SERVICE}'
  AND ex != 1
GROUP BY method
ORDER BY requests DESC
LIMIT 20
" --format sparse
```

#### Step 2: Break down by tenant_id

For sharded backends, `tenant_id` identifies the specific dataset or shard. This is often the most important breakdown.

```bash
# Query: Latency by tenant (sampleRatio for counts)
scuba -e "
SELECT
  tenant_id AS Tenant,
  SUM(sampleRatio) AS requests,
  AVG(latency) AS avg_latency_ms,
  APPROX_PERCENTILE(latency, 0.99) AS p99_latency_ms
FROM service_router
WHERE time >= ${START_TIME} AND time <= ${END_TIME}
  AND service = '${DOWNSTREAM_SERVICE}'
  AND ex != 1
GROUP BY tenant_id
ORDER BY requests DESC
LIMIT 30
" --format sparse
```

#### Step 3: Try routing_tier (may be NULL)

Some services populate `routing_tier` with tier names. Check this first — if all NULL, use `tenant_id` instead.

```bash
# Query: Check routing_tier availability
scuba -e "
SELECT
  routing_tier AS RoutingTarget,
  SUM(sampleRatio) AS requests,
  AVG(latency) AS avg_latency_ms
FROM service_router
WHERE time >= ${START_TIME} AND time <= ${END_TIME}
  AND service = '${DOWNSTREAM_SERVICE}'
  AND ex != 1
GROUP BY routing_tier
ORDER BY requests DESC
LIMIT 30
" --format sparse
```

#### Step 4: Timeline for the affected tenant/method

Once a slow tenant or method is identified, get a time-bucketed view to confirm correlation with the overall issue.

```bash
# Query: Tenant latency timeline (sampleRatio for counts)
scuba -e "
SELECT
  STRFTIME(time / 300 * 300, '%Y-%m-%d %H:%M') AS time_bucket,
  SUM(sampleRatio) AS requests,
  AVG(latency) AS avg_latency_ms,
  APPROX_PERCENTILE(latency, 0.99) AS p99_latency_ms
FROM service_router
WHERE time >= ${START_TIME} AND time <= ${END_TIME}
  AND service = '${DOWNSTREAM_SERVICE}'
  AND tenant_id = '${TENANT_ID}'
  AND ex != 1
GROUP BY STRFTIME(time / 300 * 300, '%Y-%m-%d %H:%M')
ORDER BY time_bucket
" --format sparse
```

**Analysis patterns:**

1. **Single tenant regression:**
   - One tenant shows latency spike while others are stable
   - Could indicate: dataset growth, hot shard, backend capacity issue for that tenant
   - Fix: Investigate the specific tenant/dataset — check for data growth, rebalancing, or backend issues

2. **Single method regression:**
   - One RPC method (e.g., `getKnn`) shows regression while others (e.g., `multiget`) are stable
   - Could indicate: algorithmic regression, index corruption, or capacity issue for that operation type
   - Fix: Focus on the specific method's backend infrastructure

3. **All tenants affected:**
   - Broad latency increase across all tenants → likely a service-wide issue (capacity, deployment, network)
   - Proceed with standard Phase 2B/2C analysis for the downstream service

**Common sharded backends at Meta:**
- **laser/leaf**: Key-value and KNN vector search. Use `tenant_id` for dataset, `method` for operation type (`getKnn`, `multiget`, etc.)
- **memcache**: Distributed cache. Use `tenant_id` for cache pool
- **tao**: Graph store. Use `method` for operation type

---

### Error Deep-Dive Queries

#### RECV_TIMEOUT Analysis

```bash
# Query: RECV_TIMEOUT analysis (sampleRatio for counts)
scuba query service_router \
  --start "${START_TIME}" \
  --end "${END_TIME}" \
  --columns "time:truncate(time, 300)" \
  --columns "sum(sampleRatio):Total" \
  --columns "sum(if(error_reason = 'RECV_TIMEOUT', sampleRatio, 0)):RecvTimeouts" \
  --columns "avg(if(error_reason = 'RECV_TIMEOUT', latency, null)):TimeoutLatency" \
  --filter "service = '${SERVICE_NAME}'" \
  --filter "ex != 1" \
  --group-by "time:truncate(time, 300)" \
  --order-by "time:truncate(time, 300)"
```

#### Overload Error Analysis

```bash
# Query: Overload error analysis (sampleRatio for counts)
scuba query service_router \
  --start "${START_TIME}" \
  --end "${END_TIME}" \
  --columns "error_reason:Error" \
  --columns "dst_tw_task_id:Host" \
  --columns "sum(sampleRatio):Count" \
  --filter "service = '${SERVICE_NAME}'" \
  --filter "error_reason in ('HOST_OVERLOAD', 'APP_OVERLOAD', 'APP_QUEUE_TIMEOUT')" \
  --group-by "error_reason" "dst_tw_task_id" \
  --order-by "Count:desc" \
  --limit 20
```

---

## Output Format

After investigation, provide:

1. **Summary:** One-line description of root cause
2. **Evidence:** Key metrics that support conclusion
3. **Timeline:** When issue started and any correlating events
4. **Recommendation:** Specific actions to resolve
5. **Queries used:** Links to Scuba queries for user to explore further

Example:
```
## Summary
Server processing latency increased due to traffic spike from source_service.bar

## Evidence
- Server processing latency: 15ms → 45ms (3x increase)
- Traffic from source_service.bar: 1000 QPS → 3500 QPS (3.5x increase)
- Server queue depth increased proportionally

## Timeline
- 14:00 - Baseline (15ms processing)
- 14:23 - Traffic spike begins from source_service.bar
- 14:25 - Processing latency begins increasing
- 14:30 - Latency stabilizes at 45ms

## Recommendations
1. Contact source_service.bar owners about traffic increase
2. Consider rate limiting source_service.bar
3. If traffic is expected, scale server capacity

## Scuba Queries
- Latency breakdown: [link]
- Traffic by source: [link]
```

---

## Scuba Queries Reference

### ServiceRouter Client-Side (service_router)

```
bunnylol srs <your_service_name>
```

**Key columns:**
| Column | Description |
|--------|-------------|
| Overall Latency | Total client-observed latency |
| Attempt Latency | Per-attempt latency |
| Channel Latency | Thrift channel latency |
| Server Application Handler Latency | Server processing time |
| Server Process Delay Ms | Server queuing time |
| Round Trip Time | Network RTT |
| Request Size / Response Size | Payload sizes |
| Cross Region | Is this x-region traffic? |
| src_region_raw / dst_region_raw | Source/destination region short codes (e.g., `rva`, `hil`, `cln`). **Use these for regional analysis** — the `src_region`/`dst_region` columns are often NULL |
| src_region_xjoin / dst_region_xjoin | Source/destination region long names (e.g., `virginia`, `hillsboro`). Alternative to `_raw` columns |
| Connection Age | Age of connection used |
| reuse | Connection reuse (1=reused, 0=new) |
| Request Attempt ID | Attempt number (>1 = retried) |
| Error Reason | Why request failed |
| Error Reason What | Detailed error message |
| Dispatch Type Used | Load balancing policy used |
| method | RPC method name (e.g., `getKnn`, `multiget`) |
| tenant_id | Dataset/shard/tenant identifier (essential for sharded backends like laser) |
| routing_tier | Routing tier name (may be NULL for some services) |

**Default filters:**
- `ex!=1` - Excludes extra samples (retries)
- `shadow!=1` - Excludes shadow traffic

**To see retries:** Filter `Retry Ex=1` and remove `Ex!=1`

### Thrift Server-Side (thrift_request_events)

Example: https://fburl.com/scuba/thrift_request_events/z678gasz

**Key columns:**
| Column | Description |
|--------|-------------|
| Process Delay | Request queuing latency |
| Process Latency | Request processing latency |
| Write Delay | Response queuing latency |
| Write Latency | Response write latency |

### Connection Events (thrift_connection_events)

https://fburl.com/scuba/thrift_connection_events/7mo27b8e - Connection closed events

---

## ODS Counters Reference

### ServiceRouter OBC Counters (per-service)

| Counter | Description |
|---------|-------------|
| SR.latency_ms.avg.60 | Average latency |
| SR.latency_ms.p99.60 | P99 latency |
| SR.error.avg.60 | Error rate (0-1) |
| SR.fatal.avg.60 | Fatal error rate (0-1) |
| SR.num_requests.sum.60 | Total requests |
| SR.num_errors.sum.60 | Total errors |

Per-method variants: `SR.<method>.latency_ms.avg.60`, etc.

### Thrift Server FB303 Counters

| Counter | Description |
|---------|-------------|
| thrift.process_delay | Request queuing time |
| thrift.process_time | Request processing time |
| thrift.write_delay | Response queuing time (not collected by default) |
| thrift.write_time | Response write time (not collected by default) |
| thrift.queued_requests | Requests in queue |
| thrift.queuelag | Queue lag |
| thrift.server_load | Active + queued requests |
| thrift.received_requests | Requests received |

### Client Health Counters

| Counter | Description |
|---------|-------------|
| tw.cpu.throttled | CPU throttling percentage |
| system.cpu-util-pct | System CPU utilization |

---

## Quick Debugging Checklist

1. **Package push?** Check if regression aligns with deployment
2. **What increased?** Overall vs Attempt vs Channel vs Client latency
3. **Server or client?** Check Server Process Delay and EventBase Scheduling Latency
4. **Network?** Check Round Trip Time and Cross Region columns
5. **Regional?** Use `src_region_raw`/`dst_region_raw` (NOT `src_region`/`dst_region` — often NULL)
6. **Connections?** Check reuse rate, Connection Age, connect latency
7. **Errors?** Check Error Reason and Error Reason What columns
8. **Retries?** Check Request Attempt ID column
9. **Load balance?** Check Dispatch Type Used and host load distribution
10. **Downstream slow?** Query `source_service = '<your_service>'` to check downstream dependencies
11. **Sharded backend?** For services like laser, memcache, tao — group by `tenant_id` and `method` to find the affected tenant/operation
12. **Sampling?** Use `SUM(sampleRatio)` for request counts and error rates
13. **Units?** `latency` is in ms; columns ending in `_us` are microseconds

---

## References

- [Debugging Latency Wiki](https://www.internalfb.com/wiki/ServiceRouter/User_Guide/Debugging/Debugging_Latency/)
- [Debugging Errors Wiki](https://www.internalfb.com/intern/wiki/ServiceRouter/User_Guide/Debugging/DebuggingErrors/)
- [Debugging Server Slowness](https://www.internalfb.com/intern/wiki/ServiceRouter/User_Guide/Debugging/Debugging_Server_Slowness/)
- [Debugging Client Slowness](https://www.internalfb.com/intern/wiki/ServiceRouter/User_Guide/Debugging/Debugging_Client_Slowness/)
- [Load Balancing Wiki](https://www.internalfb.com/intern/wiki/ServiceRouter/Overview/LoadBalancing/)
- [Load Balancing Tuning](https://www.internalfb.com/wiki/ServiceRouter/User_Guide/Debugging/Debugging_Guidance_on_Tuning_Load_Balancing/)
- [Connection Pooling Wiki](https://www.internalfb.com/intern/wiki/ServiceRouter/Overview/Connection_Pooling/)
- [Retries Wiki](https://www.internalfb.com/intern/wiki/ServiceRouter/Overview/Fault_Tolerance/Retries/)
- [SR Monitoring Wiki](https://www.internalfb.com/intern/wiki/ServiceRouter/Overview/Monitoring/)
- [Internal Tracing Wiki](https://www.internalfb.com/intern/wiki/ServiceRouter/User_Guide/Debugging/Internal_Tracing/)
- [Thrift FB303 Counters](https://www.internalfb.com/intern/staticdocs/thrift/docs/fb/troubleshooting/fb303-counters/)
