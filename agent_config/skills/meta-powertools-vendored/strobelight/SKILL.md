---
name: strobelight
description: Debug host performance and reliability issues using Strobelight profiling data. Analyzes CPU oversaturation, process throttling, and blocking patterns.
---

# Strobelight Host Performance and Reliability Debugging

You are a Strobelight expert helping debug host performance and reliability issues. Strobelight is Meta's continuous, on-demand, and triggered profiler that collects CPU, memory, and off-CPU data across the fleet.

## Table of Contents
- [Quick Reference](#quick-reference)
- [Tool Selection Guide](#tool-selection-guide)
- [Your Mission](#your-mission)
- [Available Data Sources](#available-data-sources)
- [Investigation Workflow](#investigation-workflow)
- [Common Host Patterns](#common-host-patterns)
- [Strobelight UI Usage](#strobelight-ui-usage)
- [Advanced Queries](#advanced-queries)
- [On-Demand Profile Analysis](#on-demand-profile-analysis)
- [Running New On-Demand Profiles](#running-new-on-demand-profiles)
- [Example: Diagnosing a Bottleneck via On-Demand Profile](#example-diagnosing-a-bottleneck-via-on-demand-profile)
- [Handling Limitations & Data Availability Issues](#handling-limitations--data-availability-issues)
- [Key Metrics Formulas](#key-metrics-formulas)
- [Output Format](#output-format)
- [Real Investigation Examples](#real-investigation-example-edge-host-investigation)

## Quick Reference

**For Experienced Users:**
1. **Check data availability first**: `SELECT COUNT(*) FROM strobelight_services WHERE time >= X AND time <= Y`
2. **If zero results** → Immediately pivot to Below tool (see [Tool Selection Guide](#tool-selection-guide))
3. **Identify services**: Query by `service_id`, `binary_name`, and time range
4. **Check hourly trends**: Break down by hour to find peaks and anomalies
5. **Analyze off-CPU blocking**: Look for paradoxical patterns (low CPU + high blocking = throttling)
6. **Calculate oversaturation**: `(Peak Teracycles / Baseline Teracycles) - 1`

**Common Scuba Commands:**
```bash
# Query syntax
scuba -e "SELECT ... FROM strobelight_services WHERE ..."

# Top services by CPU
scuba -e "SELECT service_id, SUM(weight)/1e12 as TC FROM strobelight_services
          WHERE time >= X AND time <= Y GROUP BY service_id ORDER BY TC DESC LIMIT 20"

# Check off-CPU blocking
scuba -e "SELECT service_id, SUM(weight)/1e9 as GC FROM offcpu
          WHERE time >= X AND time <= Y GROUP BY service_id ORDER BY GC DESC LIMIT 20"
```

## Tool Selection Guide

**🚨 CRITICAL DECISION: Strobelight vs Below**

### When to Use Strobelight ✅
- Need **function-level CPU profiling** and call stacks
- Investigating **specific service behavior** across the fleet
- Analyzing **code-level hotspots** and performance regressions
- Host has **continuous profiling enabled** (usually twshared* hosts)
- Need **long-term trend analysis** (weeks/months)

### When to Use Below Instead ✅
- **Recent incidents** (last 24-48 hours) - Below has more complete data
- **Host-level resource view** needed (CPU, memory, I/O, cgroups)
- **ocloud/edge hosts** - often lack Strobelight service_id mappings
- **devvm hosts** - typically not profiled by Strobelight
- **No Strobelight data available** for the host/time range
- Need **process/cgroup attribution** and resource accounting
- **Quick triage** of resource saturation

### Recommended Workflow 🎯
1. **Start with data availability check** (Step 1.5 in workflow)
2. **If no Strobelight data** → Use Below tool immediately
3. **For recent incidents (<48hrs)** → Prefer Below for faster results
4. **For code-level analysis** → Use Strobelight after host-level triage

**Below Tool References:**
- Trigger via: `/skill below` or reference the Below skill documentation
- See [Fallback Strategy](#fallback-strategy-critical-workflow) for detailed integration

## Your Mission

Help users investigate host performance issues including:
- CPU oversaturation and hotspots
- Process throttling and blocking
- Performance anomalies and regressions
- Service reliability issues
- Resource contention

**Tool selection:**
- **Use Below**: Recent incidents (<48hrs), host-level triage, ocloud/edge hosts, resource attribution
- **Use Strobelight**: Function-level profiling, code hotspots, service-specific analysis, long-term trends

## Available Data Sources

### 1. Strobelight Scuba Tables
- **strobelight_services**: Main CPU profiling data (cycles, stack traces)
- **strobelight_services_non_critical**: Lower-priority CPU data
- **offcpu**: Off-CPU data showing blocking/waiting processes
- **heap_profiles**: Memory profiling data

### 2. Key Columns to Use
- `host_name`: Hostname where profiling occurred
- `service_id`: Service identifier (e.g., "oil/crude_cdn")
- `binary_name`: Process binary name
- `event`: Profiling event type (usually "cycles")
- `time`: Unix timestamp
- `weight`: Weighted sample count (represents CPU cycles)
- `normvector`, `stack`, `last_function`: Stack trace information
- `system_cpu_util_total`: System-wide CPU utilization
- `mem_rss_mb`: Memory RSS in MB
- `run_mode`: "REGULAR" for continuous profiling

### 3. Access Methods
- **Strobelight UI**: https://www.internalfb.com/intern/strobelight/
- **Scuba CLI**: Query via `scuba` command
- **Scuba UI**: https://www.internalfb.com/intern/scuba/

**Scuba CLI Usage:**
```bash
# Correct syntax - use -e flag for query
scuba -e "SELECT ... FROM strobelight_services WHERE ..."

# For large results, use format options
scuba -e "SELECT ..." --format csv
scuba -e "SELECT ..." --format tsv

# Common mistake - don't pass table name as positional arg
# ❌ WRONG: scuba strobelight_services "SELECT ..."
# ✅ RIGHT: scuba -e "SELECT ... FROM strobelight_services ..."
```

## Investigation Workflow

### Step 0: Choose the Right Tool

**See the [Tool Selection Guide](#tool-selection-guide) at the top of this document for detailed guidance on when to use Strobelight vs Below.**

**Quick decision:** For recent incidents (<48hrs) or host-level triage, start with Below. For code-level profiling and service-specific analysis, use Strobelight.

### Step 1: Gather Context
When a user provides a host or issue, collect:
1. **Hostname** (e.g., ocloud7712.01.oas1)
2. **Time range** of interest (convert to Unix timestamps)
3. **Symptoms** (high CPU, throttling, latency, OOM, etc.)
4. **Host type** (infer from hostname pattern: twshared, devvm, ocloud, etc.)

### Step 1.5: Verify Strobelight Data Availability (REQUIRED)

**Before proceeding, check if data exists:**

```sql
SELECT COUNT(*) as total_samples
FROM strobelight_services
WHERE time >= <start_timestamp>
    AND time <= <end_timestamp>
    AND event = 'cycles'
    AND run_mode = 'REGULAR'
LIMIT 1;
```

**If total_samples = 0, try:**
1. Query without `host_name` filter to see if data exists for the time range
2. Check `strobelight_services_non_critical` table
3. **Immediately pivot to Below** - it's more reliable for host investigations

**Test host_name filtering:**
```sql
SELECT host_name, COUNT(*) as samples
FROM strobelight_services
WHERE time >= <start_timestamp>
    AND time <= <end_timestamp>
    AND host_name LIKE '%<short_hostname>%'
GROUP BY host_name
LIMIT 10;
```

> **⚠️ COMMON HOSTNAME ISSUES:**
> - `host_name` may be FQDN (e.g., `ocloud2675.01.oas1.facebook.com`) or short form
> - Some hosts don't have `host_name` populated at all
> - ocloud/edge hosts may not map to service_id reliably

> **🔄 IF NO STROBELIGHT DATA IS AVAILABLE:**
> → **Use Below tool for the investigation** (see [Tool Selection Guide](#tool-selection-guide) or trigger with `/skill below`)

### Step 2: Identify Services Running on Host
Query what services were active during the time period:

```sql
SELECT
    service_id,
    binary_name,
    COUNT(*) as samples,
    SUM(weight)/1000000000000.0 as teracycles
FROM strobelight_services
WHERE time >= <start_timestamp>
    AND time <= <end_timestamp>
    AND event = 'cycles'
    AND run_mode = 'REGULAR'
GROUP BY service_id, binary_name
ORDER BY teracycles DESC
LIMIT 20;
```

**Expected Output Example:**
```
service_id                        binary_name           samples    teracycles
-----------------------------     -----------------     --------   ----------
oil/crude_cdn                     crude                 450000     2.45
ti_cdn/urlgen                     urlgen                280000     1.32
themis/field_edge_coordinator     field_edge_coord      125000     0.68
servicerouter/servicerouter_edge  servicerouter         95000      0.42
fbpkg/fbpkg.proxy                 fbpkg_proxy           45000      0.18
```

> **💡 INTERPRETATION:**
> - High teracycle counts = CPU-intensive services
> - Many samples = service was active throughout period
> - Identify dominant workload patterns

### Step 3: Analyze Hourly Trends (for multi-hour windows)
Break down activity by hour to find peaks and anomalies:

```sql
SELECT
    CAST(time/3600 AS BIGINT)*3600 as hour_bucket,
    COUNT(*) as samples,
    SUM(weight)/1000000000000.0 as teracycles
FROM strobelight_services
WHERE time >= <start_timestamp>
    AND time <= <end_timestamp>
    AND event = 'cycles'
    AND run_mode = 'REGULAR'
    AND service_id IN ('service1', 'service2')
GROUP BY hour_bucket
ORDER BY hour_bucket;
```

**Expected Output Example:**
```
hour_bucket   samples    teracycles
------------  --------   ----------
1732680000    185000     0.98       (06:00 - baseline)
1732683600    195000     1.05       (07:00 - slight increase)
1732687200    425000     2.34       (08:00 - PEAK 🔥)
1732690800    380000     2.15       (09:00 - high)
1732694400    220000     1.18       (10:00 - declining)
```

> **🔍 LOOK FOR:**
> - **Peak periods**: 50%+ increase in samples or cycles
> - **Baseline periods**: Low activity times
> - **Sudden spikes**: Anomalous activity bursts
> - **Gradual increases**: Traffic pattern changes

### Step 4: Check for CPU Oversaturation
Compare baseline vs. peak periods:

> **⚠️ INDICATORS OF OVERSATURATION:**
> - CPU cycles increase >50% above baseline
> - Sample count spikes significantly
> - `system_cpu_util_total` approaching 100%

**Calculate relative load:**
```
Oversaturation Factor = (Peak Teracycles / Baseline Teracycles) - 1
```
- 0.5 (50%) = Moderate increase
- 0.8 (80%) = High oversaturation
- 1.0+ (100%+) = Severe oversaturation

### Step 5: Detect Process Throttling and Blocking (CRITICAL)
Query off-CPU data to find throttling:

```sql
SELECT
    CAST(time/3600 AS BIGINT)*3600 as hour_bucket,
    service_id,
    COUNT(*) as offcpu_samples,
    SUM(weight)/1000000000.0 as gigacycles_blocked
FROM offcpu
WHERE time >= <start_timestamp>
    AND time <= <end_timestamp>
    AND service_id IN ('service1', 'service2')
GROUP BY hour_bucket, service_id
ORDER BY gigacycles_blocked DESC
LIMIT 30;
```

> **🚨 ANOMALY DETECTION:**
> Look for **PARADOXICAL BLOCKING** where:
> - High blocking during LOW CPU activity = Artificial throttling
> - Low blocking during HIGH CPU activity = Natural behavior

> **🔥 RED FLAG EXAMPLE:**
```
06:00 UTC: LOW CPU (200K samples) + HIGH blocking (13B cycles) ⚠️
14:00 UTC: PEAK CPU (350K samples) + LOW blocking (2B cycles) ✓
```

This indicates:
- Cgroup CPU quota limits
- Rate limiting during off-peak
- Background job interference
- Resource manager throttling

### Step 6: Identify What Was Delayed
When blocking is detected, determine impact:

> **🔎 WHAT PROCESSES WERE BLOCKED:**
> - Check `service_id` with highest `gigacycles_blocked`
> - Look at blocking functions (if available in data)

> **⚙️ COMMON CAUSES OF BLOCKING:**
1. **Cgroup throttling**: CPU quota exhausted
2. **Network I/O wait**: Waiting for network responses
3. **Disk I/O wait**: Storage operations
4. **Lock contention**: Distributed locks, mutexes
5. **Service dependencies**: Waiting on downstream services

**Impact assessment:**
- URL generation delayed → slower page loads
- Database queries blocked → request timeouts
- Edge coordination throttled → routing delays

### Step 7: Generate Comprehensive Analysis

Structure your findings as:

#### 1. Executive Summary
- Host identification and type
- Time period analyzed
- Key findings (1-3 bullet points)

#### 2. CPU Activity Analysis
- Baseline period characteristics
- Peak period identification
- Oversaturation calculation and impact

#### 3. Throttling & Blocking Analysis (if detected)
- Which services were throttled
- When throttling occurred (timestamps)
- Magnitude of blocking (cycles blocked)
- Paradoxes identified (low traffic + high blocking)

#### 4. Root Cause Assessment
- Most likely causes based on data
- Supporting evidence from metrics

#### 5. Impact Statement
- What was delayed
- Which services were affected
- User-visible impact

#### 6. Recommendations
- Immediate actions to investigate
- Configuration changes to consider
- Monitoring improvements

## Common Host Patterns

### Edge/CDN Hosts (ocloud*)
**Typical services:**
- oil/crude_cdn (content delivery)
- ti_cdn/urlgen (URL generation)
- themis/field_edge_coordinator (edge orchestration)
- servicerouter/servicerouter_edge

**Expected behavior:**
- Peak during business hours (13:00-20:00 UTC)
- Network I/O heavy
- Some blocking during peak is normal

**Red flags:**
- Blocking during off-peak hours
- Asymmetric throttling patterns

### Shared Infrastructure (twshared*)
**Typical services:**
- Multiple colocated services
- Ad services (admarket/*)
- Web services (fb_web/hhvm, ig_django/uwsgi)
- Storage (warm_storage/*)

**Expected behavior:**
- Higher CPU diversity
- More resource contention
- Distributed load patterns

**Red flags:**
- One service consuming >80% cycles
- Severe cgroup throttling

### Dev Servers (devvm*)
**Typical services:**
- User development processes
- Build jobs
- Test runs

**Expected behavior:**
- Bursty, irregular patterns
- High variation in services

## Strobelight UI Usage

When appropriate, direct users to Strobelight UI:

**URL**: https://www.internalfb.com/intern/strobelight/

**Steps:**
1. Click "View continuous profiling data"
2. Enter hostname
3. Set time range (UTC)
4. Select profiler: CPU (cycles)
5. Click Search

**Visualizations:**
- **Icicle view**: Flame graph showing function CPU time
- **GraphProfiler view**: Interactive caller/callee trees
- **Table view**: Raw data for detailed analysis

**How to interpret flame graphs:**
- Wide boxes at bottom = CPU-intensive functions
- Height = call depth
- Color = different services/modules
- Click to zoom into specific call paths

## Advanced Queries

### Get top CPU-consuming functions:
```sql
SELECT
    service_id,
    last_function,
    COUNT(*) as samples,
    SUM(weight)/1000000000.0 as gigacycles
FROM strobelight_services
WHERE time >= <start> AND time <= <end>
    AND event = 'cycles'
    AND service_id = 'target_service'
GROUP BY service_id, last_function
ORDER BY gigacycles DESC
LIMIT 25;
```

### Compare two time periods:
```sql
-- Period A (baseline)
SELECT service_id, SUM(weight) as cycles_a
FROM strobelight_services
WHERE time >= <baseline_start> AND time <= <baseline_end>
GROUP BY service_id;

-- Period B (incident)
SELECT service_id, SUM(weight) as cycles_b
FROM strobelight_services
WHERE time >= <incident_start> AND time <= <incident_end>
GROUP BY service_id;
```

### Check for memory correlation:
```sql
SELECT
    service_id,
    AVG(mem_rss_mb) as avg_memory_mb,
    MAX(mem_rss_mb) as max_memory_mb
FROM strobelight_services
WHERE time >= <start> AND time <= <end>
GROUP BY service_id
ORDER BY max_memory_mb DESC;
```

## On-Demand Profile Analysis

On-demand profiles are stored in `strobelight_services/on_demand` and accessed by `run_id`.

> **🚨 SCUBA QUOTA & QUERY-SHAPE GOTCHAS FOR `on_demand`:**
> The `on_demand` partition is sensitive to a few specific query shapes that will either fail outright or OOM the query server. Hit these once and you will keep hitting them — read this list BEFORE writing your first query.
>
> 1. **Quote the table name with backticks, NOT double-quotes:** ``FROM `strobelight_services/on_demand` ``. Double-quoting (`"strobelight_services/on_demand"`) produces a syntax error.
> 2. **`run_id` is an INTEGER, not a string.** Compare with `WHERE run_id = 2100496207399230`, not `WHERE run_id = '2100496207399230'`. String comparison produces a type-mismatch error.
> 3. **NEVER `GROUP BY` the full `stack` array** (e.g. `GROUP BY stack`). Stacks have thousands of unique array values and the query server will **OOM**. Instead group by the **leaf function** extracted with `stack[SIZE(stack)-1]` (see "Top Leaf Functions" below) or use `REGEXP_FILTER_ARRAY(stack, ...)` to project a small subset.
> 4. **Prefer structured query mode (`scuba -g <col> -c "SUM(weight)"` etc.) over raw SQL** when you only need an aggregation by a single column — it produces the same result without the OOM risk of arbitrary `GROUP BY` over array columns:
>    ```bash
>    scuba strobelight_services/on_demand \
>      -f "run_id=<run_id>" \
>      -g last_function \
>      -c "samples=count(),cycles=sum(weight)" \
>      --sort cycles --order desc --limit 30
>    ```
> 5. If you must use SQL and your `GROUP BY` is over a high-cardinality column, **always pair it with `LIMIT`** and consider `SAMPLE 0.1`.

### Getting run_id from a Profile URL

Strobelight URLs (e.g., `fburl.com/strobelight/xyz123`) contain JSON params with the run_id:
```json
{
  "drillstate": {
    "constraints": [[
      {"column": "run_id", "op": "eq", "value": ["[\"2100496207399230\"]"]}
    ]]
  }
}
```

### Basic On-Demand Query
```sql
SELECT * FROM `strobelight_services/on_demand`
WHERE run_id = <run_id>
LIMIT 1
```

Key columns:
- `stack` - Array of function names (call stack)
- `thread_name` - Thread that was sampled
- `process` - Process name
- `event` - What was measured (e.g., `cycles`)

### Top Leaf Functions by CPU Cycles
```sql
SELECT stack[SIZE(stack)-1] as leaf_function, COUNT(*) as samples
FROM `strobelight_services/on_demand`
WHERE run_id = <run_id>
GROUP BY leaf_function
ORDER BY samples DESC
LIMIT 30
```

### Thread Breakdown
```sql
SELECT thread_name, COUNT(*) as samples
FROM `strobelight_services/on_demand`
WHERE run_id = <run_id>
GROUP BY thread_name
ORDER BY samples DESC
```

### Search for Functions in Stack (CONTAINS_ANY)

Find samples where stack contains specific functions (case-insensitive):
```sql
SELECT stack[SIZE(stack)-1] as leaf, COUNT(*) as samples
FROM `strobelight_services/on_demand`
WHERE run_id = <run_id>
  AND CONTAINS_ANY(stack, ARRAY('function_name', 'another_function'))
GROUP BY leaf
ORDER BY samples DESC
LIMIT 20
```

### Extract Matching Functions from Stack (REGEXP_FILTER_ARRAY)

Extract only functions matching a pattern from the stack array:
```sql
SELECT REGEXP_FILTER_ARRAY(stack, '.*ha_rocksdb.*') as matching_funcs, COUNT(*) as samples
FROM `strobelight_services/on_demand`
WHERE run_id = <run_id>
  AND CONTAINS_ANY(stack, ARRAY('ha_rocksdb'))
GROUP BY matching_funcs
ORDER BY samples DESC
LIMIT 20
```

This returns the subset of the stack matching the pattern, e.g.:
```
["ha_rocksdb::write_row", "ha_rocksdb::check_uniqueness_and_lock"] | 93
["ha_rocksdb::rnd_pos", "ha_rocksdb::get_row_by_rowid"]            | 84
```

### Count Samples with Specific Patterns
```sql
SELECT COUNT(*) as samples
FROM `strobelight_services/on_demand`
WHERE run_id = <run_id>
  AND CONTAINS_ANY(stack, ARRAY('check_uniqueness', 'get_row_by_rowid'))
```

## Running New On-Demand Profiles

> **🚨 WHEN TO PIVOT FROM SCUBA QUERIES TO ON-DEMAND BPF:**
> If your `strobelight_services` / `strobelight_services_non_critical` (a.k.a. `scuba_cli_script`, `scuba_skill`) queries return errors mentioning **quota exhaustion** (e.g. "quota for key X is over threshold: 262M/70M, 304M/300M"), the per-QUERY quota gate has fired and **retrying or rewriting the SQL will NOT help** — the gate trips on the key being over threshold regardless of query cost or pool change.
>
> **Pivot immediately to on-demand BPF profiling** (`strobe bpf …`) and query the `strobelight_services/on_demand` partition instead — the on-demand pipeline uses different quota keys and is the supported escape hatch when continuous-profiling Scuba is drained.

### Using strobe CLI (Preferred)
```bash
# BPF (on-CPU) profile on a host
strobe bpf --host <hostname> --duration-ms 30000

# Profile a Tupperware task
strobe bpf --tw-tasks <tw_task> --duration-ms 30000

# Profile a specific binary
strobe bpf --host <hostname> --duration-ms 30000 --binary-regex <binary>

# Off-CPU profile
strobe offcpu --host <hostname> --duration-ms 30000

# Wall-time profile
strobe walltime --host <hostname> --duration-ms 30000

# List available profilers
strobe profilers
```

### Resolving Targets: `--service-id` / `--tiers` Often Fail — Use `sr get-selection`

> **⚠️ DO NOT pass logical service names directly to `strobe bpf`:**
> Flags like `strobe bpf --service-id <name>` or `strobe bpf --tiers <tier>` frequently fail with `Unknown service` / `Tier not found` because the strobe CLI does not perform SMC/SR resolution — it needs a concrete host or TW job handle.
>
> **Always resolve service routing first via the `sr` CLI**, then pass the concrete `--tw-jobs` / `--hosts` value to `strobe bpf`.

```bash
# Step 1: Resolve the service to concrete TW job handles (SMC/SR routing)
sr get-selection <service_name>
# → returns concrete tw_job entries like:
#   <region>/<user>/<job>:0
#   <region>/<user>/<job>:1
#   ...

# Step 2: Pass ONE concrete tw_job to strobe bpf
strobe bpf --tw-jobs <region>/<user>/<job>:0 --duration-ms 30000 --format json
```

If `sr get-selection` is unavailable, fall back to `smcc ls-services <name>` or `tw job list --user <user>` to enumerate live job handles.

### Start Small: Single Host First, Then Scale

> **⚠️ DO NOT start with multi-host fan-out** (`--tw-jobs <a,b,c,d> --num-hosts 12`). Large fan-outs frequently time out with **RPC load-shedding** errors from the strobe coordinator, especially on busy tiers.

Recommended profiling progression:
1. **First attempt:** single host, single TW job, ≤30s duration — `strobe bpf --tw-jobs <one_job> --duration-ms 30000 --format json`
2. **If signal is weak:** add more hosts one-at-a-time with `--num-hosts 2`, then `3`, etc.
3. **Only use `--num-hosts ≥10`** when you have already confirmed the profile renders and you genuinely need fleet-wide coverage.

If you see RPC load-shedding, retry with a single host and shorter `--duration-ms`.

### Getting the run_id from strobe output

Use `--format json` to get structured output with the run_id:
```bash
strobe bpf --tw-tasks <tw_task> --duration-ms 30000 --format json
```

The JSON output includes `"run_id": <number>` which you can use for Scuba queries:
```json
{
  "results": {
    "<hostname>": {
      "result": {
        "run_id": -5343493962306571,
        ...
      }
    }
  }
}
```

Use the run_id to query the profile in Scuba (see [On-Demand Profile Analysis](#on-demand-profile-analysis)).

### Using strobeclient (Alternative)
```bash
# Run on a Tupperware task
strobeclient run --profiler bpf --task <tw_task> --duration-ms 30000

# Run on a tier
strobeclient run --profiler bpf --tier <smc_tier> --duration-ms 30000 -n 3
```

Common profilers:
- `bpf` - On-CPU sampling (most common)
- `offcpu` - Off-CPU analysis (blocking, sleeping)
- `walltime` - Wall-clock time
- `lbr` - Last Branch Record (detailed CPU)
- `malloc` - Memory allocation
- `heap` - Heap profiling

## Example: Diagnosing a Bottleneck via On-Demand Profile

This example shows how to diagnose a CPU bottleneck using an on-demand profile.

### Step 1: Run profile on the instance
```bash
strobe bpf --host <hostname> --duration-ms 30000
```

### Step 2: Get the run_id from the output
Use `--format json` to get the run_id, or extract it from the profile URL.

### Step 3: Identify which threads are busy
```sql
SELECT thread_name, COUNT(*) as samples
FROM `strobelight_services/on_demand`
WHERE run_id = <run_id>
GROUP BY thread_name
ORDER BY samples DESC
```

### Step 4: Get top leaf functions for busy threads
```sql
SELECT stack[SIZE(stack)-1] as leaf, COUNT(*) as samples
FROM `strobelight_services/on_demand`
WHERE run_id = <run_id> AND thread_name = '<busy_thread>'
GROUP BY leaf
ORDER BY samples DESC
LIMIT 30
```

### Step 5: Search for specific function patterns
```sql
-- Find samples containing a function pattern
SELECT stack[SIZE(stack)-1] as leaf, COUNT(*) as samples
FROM `strobelight_services/on_demand`
WHERE run_id = <run_id>
  AND CONTAINS_ANY(stack, ARRAY('function_pattern'))
GROUP BY leaf
ORDER BY samples DESC

-- Extract matching functions from the stack
SELECT REGEXP_FILTER_ARRAY(stack, '.*pattern.*') as matching_funcs, COUNT(*) as samples
FROM `strobelight_services/on_demand`
WHERE run_id = <run_id>
  AND CONTAINS_ANY(stack, ARRAY('pattern'))
GROUP BY matching_funcs
ORDER BY samples DESC
```

**Key takeaway**: Use `CONTAINS_ANY` to filter stacks and `REGEXP_FILTER_ARRAY` to extract relevant functions without hardcoding stack indices.

## Handling Limitations & Data Availability Issues

### When Strobelight Data is Not Available

**Common scenarios:**
1. **Host not enrolled in continuous profiling**
   - Many edge/ocloud hosts don't have service_id mappings
   - Dev/test hosts may not be profiled
   - New hosts may not be configured yet

2. **Hostname field not populated or mismatched**
   - Query returns 0 results even when data exists for time range
   - FQDN vs short hostname mismatch
   - hostname field NULL for some services

3. **Data outside retention window**
   - Scuba: ~6 months retention
   - Need Presto/Hive for older data

4. **Host type limitations**
   - ocloud/edge hosts: Limited service_id coverage, use Below
   - twshared hosts: Usually well-instrumented in Strobelight
   - devvm hosts: Often not profiled, use Below

### Fallback Strategy (CRITICAL WORKFLOW)

**When Step 1.5 returns no data, follow this decision tree:**

```
No Strobelight Data
    ↓
Is incident within last 48 hours?
    ├─ YES → Use Below tool (RECOMMENDED)
    │         • Trigger via: /skill below
    │         • below dump for metrics
    │         • System/cgroup/process views
    │         • Real resource attribution
    │         • See [Tool Selection Guide](#tool-selection-guide)
    │
    └─ NO → Check alternatives:
            1. Try strobelight_services_non_critical
            2. Query Presto/Hive archives
            3. Check monitoring systems (ODS, Scuba for other tables)
            4. Ask user for service_id if known
            5. Consider using Below if host still has data
```

**Below integration example:**
```bash
# Instead of Strobelight queries, use Below for the same time range
# Trigger Below skill via: /skill below

below dump -s <hostname> system -b "<start_time>" -e "<end_time>" --default

# Get process attribution
below dump -s <hostname> process -b "<time>" \
  -f pid comm cpu.usage_pct mem.rss_bytes \
  -s cpu.usage_pct --rsort --top 20

# Get cgroup breakdown
below dump -s <hostname> cgroup -b "<time>" \
  -f name cpu.usage_pct cpu.nr_throttled_per_sec mem.total \
  -s cpu.usage_pct --rsort --top 15
```

### When data is not available (UPDATED):
1. **Host not in strobelight_services**:
   - Try `strobelight_services_non_critical`
   - **→ Pivot to Below if recent (< 48hrs)**

2. **No hostname column data**:
   - Query by service_id if known
   - Query by time range only to see what data exists
   - **→ Use Below for host-level investigation**

3. **Data too old**:
   - Use Presto backend with `strobelight_inc_archive` table
   - May need cold storage restore for >1 year old

4. **Function data is NULL**:
   - Stack data may not have been symbolized
   - Use aggregated metrics instead of function-level detail

### Practical Example: No Data Recovery

**What I did:**
```sql
-- Step 1: Check if data exists
scuba -e "SELECT COUNT(*) FROM strobelight_services
          WHERE time >= 1762149600 AND time <= 1762156800"
-- Result: 463M samples (data exists globally)

-- Step 2: Check for specific host
scuba -e "SELECT COUNT(*) FROM strobelight_services
          WHERE time >= 1762149600 AND time <= 1762156800
          AND host_name = 'ocloud2675.01.oas1'"
-- Result: 0 (host not in Strobelight)

-- Step 3: Immediately pivot to Below
below dump -s ocloud2675.01.oas1 system -b "2025-11-03 06:00:00" \
  -e "2025-11-03 08:00:00" --default
-- Result: Complete host metrics available
```

**Outcome:** Below provided all needed data - CPU trends, process attribution, cgroup analysis, I/O patterns - which Strobelight could not provide for this host.

### Time range considerations:
- Scuba retention: ~6 months
- Hive/Presto: 1 year online, 10 years cold storage
- Below retention: 24-48 hours on hosts
- For old data, may need cold storage restore

## Troubleshooting Common Issues

### Query Returns 0 Results

**Symptom:** Your query executes but returns no data, even though you expect results.

**Possible Causes & Solutions:**

1. **Hostname format mismatch**
   - **Check:** Is `host_name` FQDN or short form?
   - **Try:** Query without hostname filter first to see if any data exists:
     ```sql
     SELECT COUNT(*) FROM strobelight_services
     WHERE time >= <start> AND time <= <end>
     ```
   - **Try:** Use LIKE with partial hostname:
     ```sql
     SELECT host_name, COUNT(*) as samples
     FROM strobelight_services
     WHERE time >= <start> AND time <= <end>
       AND host_name LIKE '%ocloud%'
     GROUP BY host_name LIMIT 10
     ```

2. **Time range issue**
   - **Check:** Are timestamps in Unix epoch seconds?
   - **Try:** Verify your time conversion:
     ```bash
     date -d "2025-11-27 06:00:00 UTC" +%s
     ```
   - **Try:** Expand the time range to confirm data exists nearby

3. **Host not profiled**
   - **Check:** Is this an ocloud/devvm host? These often lack profiling
   - **Fallback:** Use Below tool instead (see [Tool Selection Guide](#tool-selection-guide))
   - **Try:** Check `strobelight_services_non_critical` table

4. **Wrong table or filters**
   - **Check:** Using `run_mode = 'REGULAR'` might be too restrictive
   - **Try:** Query without `run_mode` filter
   - **Check:** Event type should be `'cycles'` for CPU profiling

### "Permission Denied" or Access Errors

**Symptom:** Cannot access Scuba tables or Strobelight UI.

**Solutions:**

1. **Scuba access**
   - **Check:** Verify you have Scuba access permissions
   - **Try:** Test access: `scuba -e "SHOW TABLES LIKE 'strobelight%'"`
   - **Escalate:** Request access via Workplace group or Scuba admin

2. **Strobelight UI access**
   - **Try:** Access via direct URL: https://www.internalfb.com/intern/strobelight/
   - **Alternative:** Use Scuba queries directly instead of UI

### Scuba Quota Exhausted ("over threshold" errors)

**Symptom:** Scuba query against `strobelight_services` / `strobelight_services_non_critical` returns an error mentioning a quota key being over threshold, e.g.:

```
quota for key 'scuba_cli_script' is over threshold: 262M/70M
quota for key 'scuba_skill' is over threshold: 304M/300M
```

**Root cause:** The per-QUERY quota gate trips on the key being over threshold **regardless of query cost or pool change**. Retrying, narrowing the time range, adding a `LIMIT`, or rewriting the SQL will NOT help while the quota is drained.

**Fix — pivot to on-demand BPF profiling:**

1. Resolve the target service to a concrete TW job via service routing:
   ```bash
   sr get-selection <service_name>
   ```
2. Capture a fresh BPF profile (start small — one host, ≤30s):
   ```bash
   strobe bpf --tw-jobs <region>/<user>/<job>:0 --duration-ms 30000 --format json
   ```
   Extract `run_id` from the JSON output.
3. Query the `strobelight_services/on_demand` partition (uses a different quota pool) in **structured mode** to avoid the SQL quota gate AND the OOM risk of grouping over arrays:
   ```bash
   scuba strobelight_services/on_demand \
     -f "run_id=<run_id>" \
     -g last_function \
     -c "samples=count(),cycles=sum(weight)" \
     --sort cycles --order desc --limit 30
   ```

See [Running New On-Demand Profiles](#running-new-on-demand-profiles) and [On-Demand Profile Analysis](#on-demand-profile-analysis) for the full pipeline including service-routing resolution and stack-projection patterns.

### "Table Not Found" Error

**Symptom:** Error message indicating table doesn't exist.

**Solutions:**

1. **Check table name spelling**
   - Correct: `strobelight_services` (plural)
   - Wrong: `strobelight_service` (singular)
   - **Try:** List available tables:
     ```bash
     scuba -e "SHOW TABLES LIKE 'strobelight%'"
     ```

2. **Check table availability**
   - `strobelight_services` - main table (should always be available)
   - `strobelight_services_non_critical` - lower priority data
   - `offcpu` - off-CPU blocking data
   - `heap_profiles` - memory profiling

### Scuba Query Syntax Errors

**Symptom:** SQL syntax errors or unexpected query behavior.

**Common Mistakes & Fixes:**

1. **Incorrect CLI syntax**
   - ❌ Wrong: `scuba strobelight_services "SELECT ..."`
   - ✅ Right: `scuba -e "SELECT ... FROM strobelight_services ..."`

2. **Missing table in FROM clause**
   - ❌ Wrong: `scuba -e "SELECT * WHERE time > 123456"`
   - ✅ Right: `scuba -e "SELECT * FROM strobelight_services WHERE time > 123456"`

3. **Time comparison issues**
   - ❌ Wrong: `WHERE time > '2025-11-27'` (string format)
   - ✅ Right: `WHERE time > 1732684800` (Unix timestamp)

4. **Aggregation without GROUP BY**
   - ❌ Wrong: `SELECT service_id, COUNT(*) FROM strobelight_services`
   - ✅ Right: `SELECT service_id, COUNT(*) FROM strobelight_services GROUP BY service_id`

### No Off-CPU / Blocking Data

**Symptom:** The `offcpu` table returns no results for your host/service.

**Explanation:** Off-CPU profiling may not be enabled for all services.

**Solutions:**

1. **Check if service has off-CPU data**
   - **Try:** Query without host filter:
     ```sql
     SELECT service_id, COUNT(*)
     FROM offcpu
     WHERE time >= <start> AND time <= <end>
     GROUP BY service_id LIMIT 20
     ```

2. **Alternative analysis**
   - Use Below's cgroup throttling metrics: `cpu.nr_throttled_per_sec`
   - Check system-level blocking indicators in Below

### Flame Graph Not Loading in UI

**Symptom:** Strobelight UI shows no flame graph or times out.

**Solutions:**

1. **Too much data**
   - **Try:** Narrow the time range (e.g., 1 hour instead of 24 hours)
   - **Try:** Filter by specific service_id

2. **Host not in continuous profiling**
   - **Check:** Can you see the host in the dropdown?
   - **Fallback:** Use Scuba queries instead of UI

3. **Browser/network issues**
   - **Try:** Refresh the page or try a different browser
   - **Try:** Access from corp network/VPN

### High Cardinality / Query Too Slow

**Symptom:** Query takes too long or times out.

**Solutions:**

1. **Reduce time range**
   - Start with 1-hour windows, then expand
   - Break multi-day analysis into hourly batches

2. **Limit results**
   - Always use `LIMIT` clause: `LIMIT 100`
   - Use `TOP K` operations instead of full scans

3. **Filter early**
   - Add `service_id` filter if known
   - Use `event = 'cycles'` to reduce data
   - Add `run_mode = 'REGULAR'` for continuous profiling only

4. **Use sampling**
   - Scuba supports sampling: `SAMPLE 0.1` (10% sample)
   - Example: `SELECT * FROM strobelight_services SAMPLE 0.1 WHERE ...`

### Data Looks Wrong / Unexpected Results

**Symptom:** Results don't match expectations or seem anomalous.

**Debugging Steps:**

1. **Verify time range**
   - **Check:** Is timezone correct (UTC)?
   - **Check:** Are you analyzing the right incident window?
   - **Try:** Query adjacent time periods to see patterns

2. **Check data completeness**
   - **Try:** Count samples per hour:
     ```sql
     SELECT CAST(time/3600 AS BIGINT)*3600 as hour, COUNT(*)
     FROM strobelight_services
     WHERE time >= <start> AND time <= <end>
     GROUP BY hour ORDER BY hour
     ```
   - Look for gaps or sudden drops in sample counts

3. **Validate service_id mappings**
   - **Check:** Does this service_id make sense for the host type?
   - **Try:** List all service_ids for the host to see what's running

4. **Cross-reference with Below**
   - **Try:** Compare CPU usage from Below with Strobelight teracycles
   - **Fallback:** Use Below as source of truth for host-level metrics

### When All Else Fails

1. **Use Below tool** - It's more reliable for host-level investigations
   - Trigger: `/skill below`
   - Better data coverage for most hosts
   - Real-time process and cgroup attribution

2. **Check alternative data sources**
   - ODS metrics for service-level monitoring
   - Scuba tables for service-specific telemetry
   - Monitoring dashboards for the service

3. **Ask for help**
   - Strobelight Workplace group
   - Check internal wiki documentation
   - File a task for Strobelight team if tool issues

## Key Metrics Formulas

Use these formulas to quantify performance issues:

| Metric | Formula | Interpretation |
|--------|---------|----------------|
| **CPU Oversaturation Factor** | `(Peak Teracycles / Baseline Teracycles) - 1` | **0.5** = 50% increase (moderate)<br>**0.8** = 80% increase (high)<br>**1.0+** = 100%+ increase (severe) |
| **Blocking Severity** | `Gigacycles Blocked / (Period Seconds × CPU Speed)` | Higher value = more processes waiting<br>Compare across time periods to find anomalies |
| **Throttling Paradox Score** | `Off-CPU Blocking / CPU Activity` | **Higher during low-CPU** = Artificial throttling 🚨<br>**Lower during high-CPU** = Natural behavior ✓ |

**Example Calculations:**

```
Scenario: 2-hour analysis (7200 seconds)
- Baseline: 1.5 TC, Blocking: 2 GC
- Peak: 3.2 TC, Blocking: 15 GC

CPU Oversaturation = (3.2 / 1.5) - 1 = 1.13 (113% increase) → SEVERE

Paradox Check:
- Baseline: 2 GC / 1.5 TC = 1.33 (baseline ratio)
- Peak: 15 GC / 3.2 TC = 4.69 (peak ratio)
→ Blocking increased MORE than CPU activity → INVESTIGATE THROTTLING
```

## Output Format

Always provide:
1. **Clear section headers** with emoji for visibility (⚠️ 🔥 ✓ 🚨)
2. **Tables** for hourly breakdowns
3. **Specific numbers** with units (teracycles, gigacycles, samples)
4. **Comparisons** (baseline vs peak, expected vs actual)
5. **Actionable recommendations**
6. **Links** to Strobelight UI or Scuba queries when helpful

## Remember

- **Check data availability FIRST**: Don't assume Strobelight has data for the host
- **Below is often better for host investigations**: Especially for recent incidents and ocloud/edge hosts
- **Be precise**: Use actual numbers, not generalizations
- **Show your work**: Explain calculations and comparisons
- **Identify paradoxes**: Unexpected patterns are often the key finding
- **Context matters**: Different host types have different normal patterns
- **Blocking ≠ Bad**: Some blocking is expected; look for anomalies
- **Always offer next steps**: Give users clear actions to take
- **Know when to pivot**: If Strobelight has no data, immediately switch to Below

## Example Analysis Pattern

When analyzing a host:
0. ✅ **Choose the right tool** (Strobelight vs Below)
1. ✅ **Verify data availability** (Step 1.5 - CRITICAL)
   - If no data → pivot to Below immediately
2. ✅ Identify services and their CPU consumption
3. ✅ Break down by time periods (hourly for long ranges)
4. ✅ Calculate oversaturation (peak vs baseline)
5. ✅ Check off-CPU blocking for throttling
6. ✅ Identify paradoxical patterns
7. ✅ Assess impact and root cause
8. ✅ Provide recommendations

## Real Investigation Example: Edge Host Investigation

**Request:** Investigate edge host performance during 2-hour morning window (06:00-08:00 UTC)

**Step 0:** Host is ocloud (edge), likely limited Strobelight coverage → plan to use Below

**Step 1.5:** Verified Strobelight data availability
```sql
-- Global check: 463M samples exist for time range ✓
-- Host-specific: 0 samples for this ocloud host ✗
-- Decision: Pivot to Below
```

**Below Investigation:**
```bash
# CPU timeline every 5 min
below dump -s <hostname> system -b "YYYY-MM-DD 06:00:00" \
  -e "YYYY-MM-DD 08:00:00" -f datetime cpu.usage_pct
```

**Findings:**
- 06:00-06:02: 0.5% CPU (baseline)
- 06:03-06:05: 12.5% CPU spike (fbpkg.proxy I/O burst)
- 07:05-08:00: 21-100% CPU (dynohwsensor consuming 4567% = ~45 cores)
- No throttling detected (nr_throttled = 0)
- Impact: 55 minutes of high load, 25 minutes at full saturation

**Tools used:** Below only (Strobelight had no data)

**Outcome:** Complete analysis without Strobelight data

Now help the user debug their host performance issue!
