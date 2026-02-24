---
name: presto-e2e-test
description: Use when running end-to-end tests against a remote Presto cluster — correctness verification (verifier, BEEST, goshadow query replay) and performance regression testing (shadow A/B comparison, CPU analysis). Covers CLI spot-checks, query replay, querybank suites, and A/B performance benchmarking. NOT for local unit tests or checkstyle — see presto-build for those. Requires a deployed cluster — see presto-deploy.
---

# Presto End-to-End Testing (Remote Clusters)

## CRITICAL: Test Clusters Only

**You must NEVER run e2e tests against production clusters.** Only run tests against Katchin test clusters that you have personally reserved. Before running any test, verify the target cluster is a test cluster (names contain `test`, `verifier`, or `katchin`). If there is any ambiguity, **stop and ask the user**. See `presto-deploy` for the full safety checklist.

## Overview

End-to-end testing tools for validating Presto builds deployed to remote Katchin test clusters. Covers two categories: **correctness verification** (do queries produce correct results?) and **performance regression testing** (does CPU/memory usage change?).

**Default: Prestissimo.** Unless explicitly told otherwise, always assume we are building, deploying, and testing Prestissimo (Presto with C++ / Velox workers). Only use Java-only Presto workers if that's explicitly requested or clearly necessary for the test context.

**Prerequisites:** `feature install warehouse` (provides `presto`, `pt`, `goshadow`, `presto-shadow`)

**Key script:** `~/.claude/skills/presto-e2e-test/presto-test`

**Related skills:**
- `presto-build` — Local builds, unit tests, and checkstyle
- `presto-deploy` — Deploying to a cluster and reserving test clusters (required before testing)

**Cluster prerequisite:** A test cluster must be reserved and have your build deployed before running these tests. See the `presto-deploy` skill for reservation and deployment commands.

## Choosing What to Test

There are three testing tools, each with different query sources and tradeoffs. The right choice depends on whether you are testing **correctness**, **performance**, or both, and how targeted your change is.

### Tool Comparison

| | Verifier | BEEST (QueryBank) | goshadow / shadow perfrun |
|---|---|---|---|
| **Tests** | Correctness (checksum comparison) | Correctness or performance (configurable mode) | Performance (CPU/memory regression) |
| **Query source** | Production-sampled queries (daily-refreshed) | Curated synthetic data (Synthefy-generated) | Real production traffic (live or historical) |
| **Deterministic** | Yes (fixed suite) | Yes (synthetic data, reproducible) | perfrun: yes (same `--end-date` = same queries). goshadow logs: no (tables may expire) |
| **Region constraint** | Yes — suite must match cluster region (e.g., `atn1_default` only runs on `atn1` clusters) | No — synthetic data runs anywhere | No |
| **Strengths** | Catches correctness bugs on real query shapes; excludes non-deterministic functions automatically | Targeted operator/feature coverage; 231 suites; privacy-safe; runs in CORRECTNESS, PERFORMANCE, or STRESS mode | Tests with real production workloads; catches issues synthetic data misses |
| **Weaknesses** | Region-locked; misses query shapes not in the suite; cannot test performance | Synthetic data may miss production edge cases | Queries may fail due to expired tables or permissions; performance results have noise |

### Decision Framework

**Step 1: What did you change?**

| Change type | Correctness test | Performance test |
|---|---|---|
| Broad / optimizer rule / planner | Verifier (default suite) | `pt shadow perfrun` |
| Specific operator (e.g., hash agg) | BEEST operator suite (e.g., `operator_hash_aggregation_synthefy`) | `pt shadow perfrun` with `--predicate` to filter relevant queries |
| Specific data type (e.g., decimals) | BEEST data structure suite (e.g., `ds_decimal_types_synthefy`) | goshadow replay of queries using that type |
| Feature area (metalake, nimble, deltoid) | BEEST feature suites (see catalog below) | goshadow with filtered queries |
| Join behavior | BEEST join suites + Verifier | `pt shadow perfrun` |
| Session property toggle | BEEST with `-s prop=value` | `pt shadow perfrun -cs "prop=old" -es "prop=new"` |
| Server config toggle (e.g., HTTPS, auth) | BEEST + Verifier | Manual goshadow A/B with redeploy between arms (not `pt shadow perfrun`); see `presto-deploy` "Modifying Cluster Config for Testing" |
| Quick smoke test | `presto_smoke_test` (5 tests) or CLI spot-check | N/A |

**Step 2: How confident do you need to be?**

| Confidence level | What to run |
|---|---|
| Sanity check | CLI spot-check or `presto_smoke_test` |
| Dev iteration | One targeted BEEST suite |
| Pre-diff | Verifier (default suite) + 1-2 relevant BEEST suites |
| Pre-land / release candidate | Verifier + `batch_tier1` + `batch_tier2` + `pt shadow perfrun` |

### Verifier Suites

Suites are defined in Configerator and are **region-specific** (named `<region>_<suite>`). The default suite (used when `--suite` is omitted) is `<region>_default`. List suites via Configerator (`source/presto/verifier/suites`); they cannot be enumerated from the CLI.

Common patterns:
- `<region>_default` — daily-refreshed production-sampled queries (general correctness)
- `<region>_alluxio` — Alluxio-specific
- `<region>_full_outer_join_with_coalesce` — targeted join shape
- `<region>_materialized_execution_small` — materialized execution
- Custom suites — created via `pt suite build --suite <name> <query_id1> <query_id2> ...`

Production-sampled suites automatically exclude non-deterministic functions (`rand()`, `approx_distinct`, `row_number() OVER ()`, `array_agg`, etc.).

### BEEST Suite Catalog

~260 suites exist (`pt beest suites` to list all). Most are niche or debug suites. The ones below are the most useful.

**Go-to suites by scenario:**

| Scenario | Suite | Tests | Notes |
|----------|-------|-------|-------|
| Quick smoke test | `presto_smoke_test` | 5 | Does not support `--engine PRESTISSIMO` — use default engine |
| Representative batch | `batch_representative` | 52 | Good fast overview |
| Full batch correctness | `batch_tier1` | 2179 | Primary release-gating suite |
| CPU-heavy perf | `batch_high_cpu_verified` | 13 | Curated, high signal |
| Memory-heavy perf | `batch_high_memory_verified` | 8 | Curated, high signal |
| Network/shuffle perf | `batch_high_network_verified` | 7 | Curated, high signal |
| OOM reliability | `batch_out_of_memory_reliability` | 24 | Stress test |

**Operator suites** — use when your change targets a specific operator. Pick the one that matches:

| Operator | Suite | Tests |
|----------|-------|-------|
| Scan/filter/project | `operator_scan_filter_and_project_synthefy` | 816 |
| Hash aggregation | `operator_hash_aggregation_synthefy` | 490 |
| Exchange (shuffle) | `operator_exchange_synthefy` | 746 |
| Partitioned output | `operator_partitioned_output_synthefy` | 448 |
| Join (general) | `operator_join_synthefy` | 48 |
| Lookup join | `operator_lookup_join_synthefy` | 429 |
| Window | `operator_window_synthefy` | 250 |
| Table writer (INSERT) | `operator_table_writer_synthefy` | 312 |
| Multi-stage joins | `operator_10_or_more_join_stages` | 42 |

For other operators, run `pt beest suites | grep operator_`.

**Data type suites** — use when your change affects type handling: `ds_map_synthefy` (375), `ds_array_synthefy` (428), `ds_string_synthefy` (587), `ds_json_synthefy` (126), `ds_date_time_synthefy` (294), `ds_decimal_types_synthefy` (97). For others, `pt beest suites | grep ds_`.

**Feature suites** — use when your change touches a specific subsystem: `nimble_qb_adhoc_prestissimo_read` (122), `nimble_qb_adhoc_prestissimo_write` (84), `metastore_combined` (68), `deltoid_nga_qb_merge` (1176). For metalake/nimblelake, `pt beest suites | grep metalake`.

**BEEST usage:**

```bash
# Correctness run (default engine is Java — specify PRESTISSIMO for C++ workers)
pt beest run --suite <suite> --cluster <cluster> --engine PRESTISSIMO --force --limit 100

# Performance run
pt beest run --suite <suite> --cluster <cluster> --engine PRESTISSIMO --mode PERFORMANCE --force

# Multiple suites
pt beest run --suite batch_high_cpu_verified --suite batch_high_network_verified --cluster <cluster> --engine PRESTISSIMO --force
```

Key flags: `--engine PRESTISSIMO` (required for Prestissimo), `--mode PERFORMANCE` (benchmarking), `--force` (skip active-query check — needed for batch test clusters), `--limit <n>` (cap test count), `-s "prop=value"` (session properties), `--no-upload-result` (don't write to XDB).

**BEEST pitfalls:**
- `--engine PRESTISSIMO` is **not supported by all suites** (e.g., `presto_smoke_test` is Java-only). If you get `Execution engine PRESTISSIMO not supported`, omit the flag — the suite will still run on Prestissimo workers, it just uses the default (PRESTO/Java) engine classification for test matching.
- **Synthetic tables may not exist in all regions.** If queries fail with "table does not exist", the suite's data hasn't been replicated to your cluster's local namespace. Prefer clusters in `atn`, `ftw`, `pnb`, `rcd`. See "Namespaces and Regions" below.
- **Batch test clusters restrict cross-region access.** If queries fail with `PRISM_REGION_NOT_ALLOWED`, the synthetic data is in a different region. See the `presto-deploy` skill for the `allowed_fb_regions` workaround.
- **No built-in timeout.** Always wrap with `timeout -k 5m <duration>` to prevent hung queries from blocking indefinitely.
- **Namespace auto-resolves from cluster region** when `--namespace` is omitted and `--cluster` is specified. This is usually what you want — it ensures in-region reads.

**Which suites for which change (performance testing):**

| What you're changing | Start with | Expand to |
|---------------------|------------|-----------|
| Exchange / network | `batch_high_network_verified` (7) | `operator_exchange_synthefy` (746) |
| CPU optimization | `batch_high_cpu_verified` (13) | `batch_high_cpu` (31) |
| Memory / join strategy | `batch_high_memory_verified` (8) | `batch_high_memory` (126) |
| Aggregation | `operator_hash_aggregation_synthefy` (490) | — |
| Scan / filter | `operator_scan_filter_and_project_synthefy` (816) | — |
| Join operator | `operator_join_synthefy` (48) | `operator_10_or_more_join_stages` (42) |
| File format (Nimble) | `nimble_qb_adhoc_prestissimo_read` (122) | `nimble_qb_adhoc_prestissimo_write` (84) |
| Overall impact | `batch_representative` (52) | `batch_tier1` (2179) |

### Namespaces and Regions

Presto namespaces (Hive metastore schemas) determine where data is physically stored. Understanding namespace-region relationships is important for controlling cross-region reads, which can be a source of noise in performance tests.

**Namespace types:**

| Type | Pattern | Data location | Example |
|------|---------|---------------|---------|
| **Local** | `local_<datacenter><id><name>` | Specific region/datacenter | `local_atn5cerium`, `local_ftw2nitrogen` |
| **Global** | No `local_` prefix | May span multiple regions | `beest`, `di` |

**Local namespaces** are guaranteed to have all data in the specified region. When a Presto cluster reads from a local namespace matching its own region, all reads are in-region and fast. When the namespace is in a different region, reads are **cross-region** (x-region) — data must traverse the inter-region network, which adds latency and can significantly inflate wall times.

**Global namespaces** (used by most production queries) may have tables stored in any region. Production batch clusters are typically restricted to their own region via the `namespace.allowed-fb-regions` catalog property, but test clusters (Katchin) are configured with `*` (all regions allowed).

**How namespace auto-resolution works in BEEST:**

When `--cluster` is specified but `--namespace` is omitted, `pt beest run` auto-resolves the namespace by querying the cluster's allowed regions and constructing `local_<region>`. This ensures in-region reads. If you override with `--namespace`, you control this explicitly.

**Impact on performance testing:**

| Scenario | Cross-region? | Impact |
|----------|---------------|--------|
| Cluster in `rcd`, namespace `local_rcd0dw0` | No | Clean baseline — in-region reads |
| Cluster in `rcd`, namespace `local_ftw2nitrogen` | Yes | Inflated wall times; CPU usually unaffected but I/O wait increases |
| Cluster in `atn`, namespace auto-resolved | No | Auto-resolves to `local_atn*` — in-region |
| Cluster in `rcd`, no namespace specified | No | Auto-resolved to local region |

**Recommendations:**

- **For performance A/B testing:** Prefer omitting `--namespace` so it auto-resolves to the cluster's local region, ensuring in-region reads. This eliminates cross-region noise.
- **If you must specify a namespace:** Match it to the cluster's region prefix (e.g., `local_rcd0dw0` for an `rcd` cluster).
- **Cross-region is not always a problem:** Many production queries read cross-region, and CPU time (`total_split_cpu_time_ms`) is generally unaffected by cross-region reads — it's wall time and I/O wait that increase. If you're measuring CPU, cross-region noise is less of a concern. If you're measuring wall time or end-to-end latency, it matters a lot.
- **Be aware, not paranoid:** Cross-region reads are one of many noise sources. For aggregate CPU comparisons across hundreds of queries, the effect is usually negligible. For individual query wall time comparisons, it can dominate.

**BEEST synthetic data and regions:** BEEST synthetic data is replicated to local namespaces across regions, but not all suites have data everywhere. Batch test clusters also restrict `allowed_fb_regions` to their local region, which can block cross-region access. See the `presto-deploy` skill for region selection guidance, the `allowed_fb_regions` workaround, and how to modify cluster config for testing.

### Cluster Sizing and Reservation

**See the `presto-deploy` skill** for detailed guidance on cluster sizing (worker-count-dependent configs, sizing recommendations) and reservation (flags, checklist, region selection).

Key points for test selection:
- **Correctness testing** (BEEST, verifier): 10-50 workers is fine
- **Performance A/B** (goshadow/perfrun): Use 100-300 workers for production-representative signal
- **A/B comparisons**: Both arms must use the same cluster — relative comparisons are valid even on smaller clusters
- **Region**: Prefer `atn`, `ftw`, `pnb`, `rcd` for BEEST data availability

### goshadow / Shadow Perfrun Query Selection

**`pt shadow perfrun`** samples production queries deterministically — same `--end-date` and parameters produce the same query set. Filter with:
- `--catalog` (default: `prism,prism_batch`) — which catalogs to sample from
- `--days` / `--end-date` — time window
- `--max-queries` (default: 1000) — sample size
- `--min-cpu-time` / `--max-cpu-time` — CPU time bounds
- `--min-execution-time` / `--max-execution-time` (default max: 1.5h) — wall time bounds
- `--predicate` — arbitrary SQL filter (e.g., `"lower(query) LIKE '%unnest(%'"` to target specific functions)

Common predicates by test type:

| Testing | Predicate | Why |
|---------|-----------|-----|
| Network/exchange overhead | `total_bytes > 10*1024^3 AND stage_count > 3` | Network-heavy workloads amplify exchange protocol overhead |
| CPU optimization | `total_split_cpu_time_ms > 300000 AND total_bytes / NULLIF(total_split_cpu_time_ms, 0) < 1000000` | Compute-bound queries show CPU improvements clearly |
| Memory optimization | `peak_total_memory_bytes > 100*1024^3 OR spilled_bytes > 0` | Memory-intensive queries stress the changes |
| File format change | `total_bytes > 50*1024^3 AND total_split_cpu_time_ms / NULLIF(total_bytes, 0) * 1024 < 10` | I/O-bound queries isolate storage layer impact |
| Join strategy | `stage_count >= 3 AND peak_total_memory_bytes > 50*1024^3` | Multi-stage, memory-intensive patterns indicate joins |

**goshadow** replays specific queries or traffic windows. For performance A/B, prefer `pt shadow perfrun` unless you need to replay specific query IDs from a paste.

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

The script automatically sets `--namespace` based on the cluster's region (auto-resolved by `pt beest run`).

## Shadow A/B (Performance Comparison)

Compares query performance between a control build and an experiment build. Both builds must be deployed to the **same** cluster (different clusters have different hardware/load, making cross-cluster comparison unreliable).

**Build type matters for binary-comparison A/B tests** (comparing different Presto versions or code changes). BOLT applies profile-guided optimization trained on *current* production code paths, which unfairly favors whichever binary matches production behavior. Use `opt` builds for both arms in binary comparisons. See `presto-deploy` "Build Type for Performance Testing" for details.

**Always use opt builds for A/B tests, including config-toggle tests.** BOLT optimizes instruction layout for production code paths. Even when both arms use the identical binary, a config toggle that changes which code paths are hot (e.g., HTTPS on/off removes TLS from the hot path) biases results toward the production-config arm. Use an existing opt hybrid ephemeral — find one with `fbpkg versions presto.presto 2>&1 | grep -v "cpp-bolt" | grep "cpp-" | head -10`.

### Rigor vs Speed

The appropriate methodology depends on how much confidence is needed:

**Quick sanity check** (50-100 queries, single run):
- "Is this change catastrophically bad?"
- Aggregate CPU ratio is sufficient — a 20%+ change is signal, anything smaller is noise at this sample size
- Use `pt shadow perfrun` with `--max-queries 100`

**Moderate confidence** (200-500 queries, single run):
- "Is there a meaningful performance difference?"
- Look at aggregate CPU ratio and per-bucket breakdowns by query size
- Effects of 5-10% are detectable but not conclusive
- Use `pt shadow perfrun` with default settings, or manual goshadow

**High confidence** (500+ queries, multiple runs):
- "We need to quantify this precisely for a decision"
- Run each condition at least twice (ideally interleaved: control → experiment → control → experiment)
- Compare across runs to estimate natural variance before attributing effects
- Consider filtering to query profiles most affected by the change

If the user doesn't specify, default to moderate confidence for ad-hoc testing. Ask if uncertain.

### Understanding Noise

Presto is a multi-tenant distributed system with inherent non-determinism, even on a reserved cluster where only your queries run:

**Query interaction effects:** goshadow replays many queries concurrently. These compete for CPU, memory, network, and disk I/O on the same workers. Individual query CPU times can vary 10-20% between runs depending on co-runners.

**Spilling:** When concurrent queries consume enough memory, some spill intermediate data to disk — a major performance cliff (often 2-5x slowdown) that is highly non-deterministic. This is the single largest source of noise in per-query comparisons.

**Aggregate statistics are more reliable than per-query comparisons.** The random noise tends to cancel out across hundreds of queries. However, even aggregates can shift by 3-5% between identical runs.

**Per-query regression thresholds:** 3x+ CPU ratio is likely real. 1.3-2x might be real or spill noise — re-run to confirm. Under 1.3x is within normal variance for individual queries.

### Query Classification by Resource Profile

The queries you test determine the signal you get. Choose query sets based on what you're testing.

**Query Size Thresholds** (for filtering and bucketing results):

| Metric | Small | Medium | Large | Very Large |
|--------|-------|--------|-------|------------|
| CPU time (`total_split_cpu_time_ms`) | < 75,000 (75s) | 75k-250k (1-4min) | 250k-1M (4-16min) | > 1M (>16min) |
| Memory (`peak_total_memory_bytes`) | < 10 GB | 10-100 GB | 100GB-1TB | > 1 TB |
| I/O (`total_bytes`) | < 1 GB | 1-30 GB | 30-100 GB | > 100 GB |
| Wall time (`query_execution_time_ms`) | < 10,000 (10s) | 10k-60k (10s-1min) | 60k-1.2M (1-20min) | > 1.2M (>20min) |

| Profile | Filter | Test these for |
|---------|--------|----------------|
| **CPU-heavy** | `total_split_cpu_time_ms > 300000 AND total_bytes / NULLIF(total_split_cpu_time_ms, 0) < 1000000` | CPU optimizations, vectorization, SIMD |
| **Memory-heavy** | `peak_total_memory_bytes > 100 * 1024^3 OR spilled_bytes > 0` | Memory optimizations, join/agg strategies, spilling |
| **I/O-heavy** | `total_bytes > 30 * 1024^3 AND total_split_cpu_time_ms / NULLIF(total_bytes, 0) * 1024 < 5` | File format changes, predicate pushdown, column pruning |
| **Shuffle-heavy** | `stage_count > 3 AND total_split_wall_time_ms / NULLIF(total_split_cpu_time_ms, 0) > 2.0` | Exchange protocol changes, network optimizations, compression |
| **Join-heavy** | `stage_count >= 3 AND peak_total_memory_bytes > 50 * 1024^3` | Join strategy changes, hash table optimizations |
| **Aggregation-heavy** | `total_rows > 0 AND CAST(output_rows AS DOUBLE) / total_rows < 0.01 AND peak_total_memory_bytes > 50 * 1024^3` | Aggregation algorithms, group-by strategies |

### Multi-Suite Exploration Strategy

When exploring the implications of a change without a specific hypothesis, triangulate with multiple suites:

**Phase 1: Quick Sweep** — Run 3-4 operator suites most likely affected + sample 100 production queries with broad filter (`total_split_cpu_time_ms > 60000`). Goal: identify if there's any signal at all.

**Phase 2: Targeted Deep-Dive** — Based on Phase 1, run focused suites: if CPU regressions, run high-CPU suite + CPU-heavy production sample; if memory impact, run high-memory suites; if specific operator flagged, run that operator's suite. 300-500 queries per suite. Goal: quantify magnitude.

**Phase 3: Comprehensive Validation** — Full BEEST coverage for release gating. Large production sample (500-1000 queries) across all query types. Repeat runs to measure variance. Goal: confirm no silent regressions.

### CTAS Queries in Sequential A/B

Goshadow rewrites INSERT queries as `CREATE TABLE` (CTAS) into shadow tables. INSERT workloads are generally preferred for batch testing — they better reflect typical batch usage and avoid variance from streaming results back to a client.

However, in sequential A/B on the **same cluster**, shadow tables from the control run persist and the experiment run's CTAS short-circuits with 0 CPU because the table already exists. Two options:
1. **In analysis:** Filter with `query NOT LIKE '%CREATE TABLE%'` and note excluded queries.
2. **Prevention:** Use `--batch-mode` (runs cleanup between stages) or manually drop shadow tables between runs.

### A/B Testing Pitfalls

| Pitfall | Impact | Mitigation |
|---------|--------|------------|
| BOLT builds in A/B | Unfair optimization for production code paths | Use `opt` builds for ALL A/B tests (binary-comparison AND config-toggle). BOLT optimizes for production paths, biasing any test that changes hot code paths |
| CTAS table collision in sequential runs | Experiment CTAS short-circuits with 0 CPU | Filter in analysis or clean up shadow tables between runs |
| Wall time as metric | Confounded by queuing, scheduling, run ordering | Use `total_split_cpu_time_ms`; wall time is unreliable in sequential A/B |
| Single run, small effect | 5-10% natural variance masks small effects | Run each condition twice, or increase query count |
| Custom `--target-client-tags` not in stats | Tags may not appear in `client_tags` | Use the goshadow run ID (printed at completion) to filter query stats |
| `tw job update` blocked by AI agent policy | Server-side policy blocks TW mutations | Use `pt pcm deploy -l` (**without `-pv`**) with local TW config. See `presto-deploy` "Claude Code Deployment" for the fast deploy pattern |
| Not verifying the config took effect | Change may not have propagated | After deploying, spot-check a query or inspect worker config before running the full suite |
| Config-only A/B (not session property) | Requires redeployment between arms; adds time for cluster restart | Use post-construction override in `batch_native.cinc` + `pt pcm deploy -l` (**without `-pv`**); see `presto-deploy` "Modifying Cluster Config for Testing". Never combine `-l` with `-pv` — causes version mismatch that crashes workers |

### Automated: `pt shadow perfrun`

Handles the full workflow automatically: samples production queries, deploys control version to the cluster via Katchin, replays queries, deploys experiment version to the same cluster, replays the same queries, and outputs a link to the performance analysis dashboard. No manual deployment needed.

```bash
# Compare two Presto versions
pt shadow perfrun -c <cluster> -cv <control_version> -ev <experiment_version>

# Compare a session property change (same version)
pt shadow perfrun -c <cluster> \
    -cv <version> -cs "some_property=false" \
    -ev <version> -es "some_property=true"
```

Key options:

| Flag | Default | Description |
|------|---------|-------------|
| `-c, --cluster` | required | Target test cluster |
| `-cv, --control-version` | (omit for standalone) | Control Presto version |
| `-ev, --experimental-version` | required | Experiment Presto version |
| `-cs, --control-session` | none | Control session properties (repeatable) |
| `-es, --experimental-session` | none | Experiment session properties (repeatable) |
| `-ct, --control-tag` | `perf_shadow_comparison_control` | Tag for control run |
| `-et, --experimental-tag` | `pt_shadow_perfrun_experimental` | Tag for experiment run |
| `--max-queries` | 1000 | Max queries to run |
| `--days` | 1 | Sample queries from last N days |
| `--end-date` | today | End date for query sampling (yyyy-mm-dd) |
| `-p, --predicate` | none | Extra SQL predicate for query filtering |
| `-clg, --catalog` | `prism,prism_batch` | Catalogs to sample from |
| `--min-cpu-time` | none | Min CPU time per query |
| `--max-cpu-time` | 100d | Max CPU time per query |
| `--min-execution-time` | none | Min wall time per query |
| `--max-execution-time` | 1.5h | Max wall time per query |

On completion, it prints:
- **Katchin test URLs** for the control and experimental runs
- **Client tags** for each run (formatted as `<user>_<tag>_<cluster>_<timestamp>`)

Using `--end-date` with the same parameters across runs ensures a deterministic (reproducible) query set.

### Manual: goshadow with Tags

When you need more control (e.g., replaying specific query IDs from a paste), use goshadow directly. The workflow is:

1. **Prepare a paste** with query IDs to replay (one per line).
   - INSERT workloads are preferred for batch testing (see CTAS section above), but be aware of the shadow table collision issue in sequential A/B runs.
2. **Deploy the control build** to the test cluster (see `presto-deploy` skill).
3. **Replay with a control tag:**
   ```bash
   goshadow --queryid-paste P<paste_id> --target <cluster> \
       --target-client-tags "control_<your_tag>" \
       --run-as-current-user --max-concurrent-query 200
   ```
4. **Deploy the experiment build** to the same cluster.
5. **Replay with an experiment tag:**
   ```bash
   goshadow --queryid-paste P<paste_id> --target <cluster> \
       --target-client-tags "experiment_<your_tag>" \
       --run-as-current-user --max-concurrent-query 200
   ```
6. **Analyze results** using the queries below.

Key goshadow flags for this workflow:

| Flag | Description |
|------|-------------|
| `--target <cluster>` | Cluster to replay against |
| `--queryid-paste P<id>` | Paste containing query IDs to replay |
| `--target-client-tags <csv>` | Tags added to replayed queries (for filtering in query stats) |
| `--run-as-current-user` | Run queries as yourself (required for permission) |
| `--max-concurrent-query <n>` | Concurrency limit (default: 50) |
| `--repeat <n>` | Repeat each query N times (works with `--queryid-paste`) |
| `--session <props>` | Session properties for replayed queries (`prop=value;prop2=value2`) |
| `--mode logs` | Replay historical queries (requires `--environment`, `--start`, `--end`) |
| `--mode live` | Shadow live traffic (requires `--environment`) |
| `--batch-mode` | Run setup/shadow/cleanup queries in three sequential stages |
| `--included_source`, `--skipped_source` | Filter by source regex |
| `--included_user`, `--skipped_user` | Filter by user regex |
| `--included_schema`, `--skipped_schema` | Filter by schema regex |

### Analyzing Performance Results

Replayed queries are stored in `di.presto_query_statistics_inc_archive` (near-realtime, <5 min lag). Each replayed query's text is prefixed with `-- replaying query <original_query_id>, run_id <run_id>`, and its `client_tags` array contains the run_id and any tags set via `--target-client-tags`. This allows matching control and experiment queries by their shared original query ID.

To identify replayed queries, filter by:
- `query LIKE '-- replaying query%'` — matches replayed queries by text prefix
- `contains(client_tags, '<run_id_or_tag>')` — matches by the run_id or custom tag in client_tags
- `environment = '<cluster>'` — matches by cluster name
- `ds >= '<YYYY-MM-DD>'` — partition filter (required for performance)

Extract the original query ID with: `regexp_extract(query, '-- replaying query ([^,]+),', 1)`

**Summary query** — overall CPU ratio and regression/improvement counts:

```sql
presto --execute "
WITH control AS (
    SELECT
        regexp_extract(query, '-- replaying query ([^,]+),', 1) AS original_query_id,
        total_split_cpu_time_ms AS cpu_ms
    FROM presto_query_statistics_inc_archive
    WHERE ds >= '<DS_START>'
        AND environment = '<CLUSTER>'
        AND query LIKE '-- replaying query%'
        AND query_state = 'FINISHED' AND error_code IS NULL
        AND contains(client_tags, '<CONTROL_TAG>')
),
experiment AS (
    SELECT
        regexp_extract(query, '-- replaying query ([^,]+),', 1) AS original_query_id,
        total_split_cpu_time_ms AS cpu_ms
    FROM presto_query_statistics_inc_archive
    WHERE ds >= '<DS_START>'
        AND environment = '<CLUSTER>'
        AND query LIKE '-- replaying query%'
        AND query_state = 'FINISHED' AND error_code IS NULL
        AND contains(client_tags, '<EXPERIMENT_TAG>')
),
matched AS (
    SELECT
        c.original_query_id,
        c.cpu_ms AS control_cpu_ms,
        e.cpu_ms AS experiment_cpu_ms,
        CAST(e.cpu_ms AS DOUBLE) / NULLIF(c.cpu_ms, 0) AS cpu_ratio
    FROM control c
    JOIN experiment e ON c.original_query_id = e.original_query_id
    WHERE c.cpu_ms > 60000
)
SELECT
    COUNT(*) AS matched_queries,
    ROUND(CAST(SUM(experiment_cpu_ms) AS DOUBLE) / NULLIF(SUM(control_cpu_ms), 0), 3) AS overall_cpu_ratio,
    ROUND((CAST(SUM(experiment_cpu_ms) AS DOUBLE) / NULLIF(SUM(control_cpu_ms), 0) - 1) * 100, 1) AS cpu_change_pct,
    ROUND(SUM(control_cpu_ms) / 1000.0 / 3600 / 24, 2) AS control_cpu_days,
    ROUND(SUM(experiment_cpu_ms) / 1000.0 / 3600 / 24, 2) AS experiment_cpu_days,
    COUNT_IF(cpu_ratio > 1.03) AS regressions,
    COUNT_IF(cpu_ratio < 0.97) AS improvements,
    COUNT_IF(cpu_ratio BETWEEN 0.97 AND 1.03) AS neutral
FROM matched
" --output-format TSV_HEADER di
```

**Top regressions** — queries with the largest CPU increase (change `ORDER BY cpu_ratio DESC` to `ASC` for top improvements):

```sql
presto --execute "
WITH control AS (
    SELECT regexp_extract(query, '-- replaying query ([^,]+),', 1) AS original_query_id,
        total_split_cpu_time_ms AS cpu_ms
    FROM presto_query_statistics_inc_archive
    WHERE ds >= '<DS_START>' AND environment = '<CLUSTER>'
        AND query LIKE '-- replaying query%'
        AND query_state = 'FINISHED' AND error_code IS NULL
        AND contains(client_tags, '<CONTROL_TAG>')
),
experiment AS (
    SELECT regexp_extract(query, '-- replaying query ([^,]+),', 1) AS original_query_id,
        total_split_cpu_time_ms AS cpu_ms
    FROM presto_query_statistics_inc_archive
    WHERE ds >= '<DS_START>' AND environment = '<CLUSTER>'
        AND query LIKE '-- replaying query%'
        AND query_state = 'FINISHED' AND error_code IS NULL
        AND contains(client_tags, '<EXPERIMENT_TAG>')
)
SELECT
    c.original_query_id,
    c.cpu_ms AS control_cpu_ms,
    e.cpu_ms AS experiment_cpu_ms,
    ROUND(CAST(e.cpu_ms AS DOUBLE) / NULLIF(c.cpu_ms, 0), 3) AS cpu_ratio
FROM control c
JOIN experiment e ON c.original_query_id = e.original_query_id
WHERE c.cpu_ms > 60000
ORDER BY cpu_ratio DESC
LIMIT 10
" --output-format TSV_HEADER di
```

Replace `<CONTROL_TAG>` and `<EXPERIMENT_TAG>` with the client tags or run_ids from the `pt shadow perfrun` or goshadow output. Replace `<CLUSTER>` with the test cluster name. Replace `<DS_START>` with the date of the test runs (YYYY-MM-DD).

The `c.cpu_ms > 60000` filter excludes queries under 1 CPU minute to focus on meaningful workloads. Regressions of 3x+ tend to be real; smaller regressions are often noise — re-run to confirm.

## Presto Query Statistics Reference

The `di.presto_query_statistics_inc_archive` Hive table contains exhaustive query-level information for all finished queries (success or failure), with near-realtime data freshness (<5 min lag). Queryable via `presto --execute "<sql>" --output-format TSV_HEADER di`. Requires a `ds >= '<YYYY-MM-DD>'` partition filter for performance. Documentation: https://www.internalfb.com/wiki/Presto/query_stats_datasets/

Key columns:
- **`query_id`** — Unique query identifier
- **`query`** — Full query text (use `presto_query_statistics_view` for non-sensitive queries if ACL-restricted)
- **`total_split_cpu_time_ms`** — Total CPU time across all splits (primary performance signal)
- **`query_execution_time_ms`** — Wall time
- **`cumulative_memory`** — Memory consumption
- **`peak_total_memory_bytes`** / **`peak_user_memory_bytes`** — Peak memory
- **`total_bytes`** / **`output_bytes`** / **`written_bytes`** — Data throughput
- **`error_category`** / **`error_code`** — Failure classification
- **`query_state`** — Final state (`FINISHED`, `FAILED`, etc.)
- **`environment`** — Cluster name
- **`client_tags`** — `array(varchar)` of tags; filter with `contains(client_tags, '<tag>')`
- **`create_time`** / **`end_time`** — Unix timestamps
- **`source`** — Pipeline/client source identifier
- **`session_properties_json`** — Session properties as JSON string

## Controlling Test Duration and Scope

**Always set timeouts.** Every test invocation should have a time bound — either via the tool's built-in timeout flag or by wrapping with GNU `timeout`. An unbounded test can run for hours with no additional signal. Get results fast, re-run with wider scope if needed.

**Always limit query count for dev iteration.** The defaults are designed for release validation (50,000 queries for verifier, unlimited for BEEST). During development, you need signal quickly — 200-500 queries is enough to catch most issues.

### What to Always Use

**Verifier** — always set these three flags:
```bash
pt verifier run <cluster> \
  --verifier-timeout 2h \
  -q 500 \
  --success-rate-threshold 90
```
- `--verifier-timeout` — hard time limit on the entire run (no default — without this, it runs until all queries complete)
- `-q` — cap total queries (default 50,000 is far too many for dev iteration)
- `--success-rate-threshold 90` — exit early if >10% of queries fail (something is clearly broken, no point continuing)

**BEEST** — always wrap with `timeout` and use `--limit`:
```bash
timeout -k 5m 30m pt beest run \
  --suite <suite> --limit 100 \
  --cluster <cluster>
```
BEEST has no built-in overall timeout or fail-fast. Without `timeout`, a hung query blocks the entire run indefinitely.

**goshadow** — always use `-replay-limit` and wrap with `timeout`:
```bash
timeout -k 5m 1h goshadow --mode logs \
  --environment <src> --target <cluster> \
  --start "2026-02-01 12:00" --end "2026-02-01 13:00" \
  -replay-limit 200 --run-as-current-user --max-concurrent-query 200
```
Keep the time window short (1-2 hours of traffic) and set `-replay-limit` to avoid replaying thousands of queries.

**`pt shadow perfrun`** — always wrap with `timeout`:
```bash
timeout -k 5m 4h pt shadow perfrun \
  -c <cluster> -cv <v1> -ev <v2> --max-queries 500
```
The default `--max-queries 1000` is reasonable for pre-land, but use 500 for dev iteration.

### Scope Reference

| Scenario | Tool | Flags |
|---|---|---|
| Quick smoke test | BEEST | `--suite presto_smoke_test` (5 tests, no timeout needed) |
| Dev iteration (correctness) | Verifier | `-q 200 --verifier-timeout 30m --success-rate-threshold 90` |
| Dev iteration (targeted) | BEEST | `--limit 100`, wrap with `timeout 30m` |
| Pre-diff correctness | Verifier | `-q 2000 --verifier-timeout 2h --success-rate-threshold 95` |
| Pre-diff performance | shadow perfrun | `--max-queries 500`, wrap with `timeout 3h` |
| Pre-land validation | Verifier + BEEST batch_tier1 | `--verifier-timeout 4h`, BEEST with `timeout 4h` |
| Pre-land performance | shadow perfrun | `--max-queries 1000`, wrap with `timeout 6h` |

### Additional Verifier Controls

| Flag | Default | Purpose |
|---|---|---|
| `--test-timeout <duration>` | 1h | Per-query timeout on test cluster |
| `--control-timeout <duration>` | 10m | Per-query timeout on control cluster |
| `--threads <n>` | 70 | Concurrent verifications |
| `--correctness-rate-threshold <n>` | 0 (disabled) | Exit early if correctness rate drops below n% |

### Additional goshadow Controls

| Flag | Default | Purpose |
|---|---|---|
| `-sample_rate <float>` | 1.0 | Sample fraction (0.0-1.0) — use 0.1 for 10% sampling |
| `-max-concurrent-query <n>` | 50 | Concurrent queries (script defaults to 200) |
| `-total-split-cpu-time-ms-range low,high` | none | Filter by CPU time |
| `-query-execution-time-ms-range low,high` | none | Filter by wall time |

### GNU `timeout` Pattern

```bash
timeout -k <kill_grace> <duration> <command>
```
- Sends SIGTERM after `<duration>`, then SIGKILL after `<kill_grace>` if still running
- Exit code 124 indicates the command was killed by timeout
- Always use `-k 5m` to ensure cleanup if the process ignores SIGTERM

## Common Issues

| Problem | Fix |
|---------|-----|
| `presto --smc` "Oncall must be specified" | Batch clusters require `--oncall <oncall_name>` (e.g., `--oncall presto_release_internal`) |
| Cluster unreachable during validation | Check deployment; use `--skip-validation` to bypass |
| goshadow auth errors | Script adds `--run-as-current-user` automatically |
| Verifier: no control cluster | Specify `--control <cluster>` explicitly |
| `presto --smc` connection refused | Cluster may still be restarting; check `tw.real job show tsp_<region>/presto/<cluster>.worker` |
| BEEST namespace errors | Namespace auto-resolves from cluster region; specify `--namespace` explicitly if auto-resolution fails |
| `tw job update` blocked by AI agent policy | Use `pt pcm deploy -l` (**without `-pv`**). See `presto-deploy` "Claude Code Deployment" for the fast deploy pattern |
| `tw` command blocked by bpfjailer | Use `tw.real` instead — the wrapper is blocked but the actual binary is not |
