---
name: dataswarm-pipeline-optimizations
description: Identify and implement optimizations for Dataswarm pipelines on Spark and Presto to reduce BCU/DIRCU costs and improve query performance. Supports single pipeline analysis or oncall-wide fleet analysis (given an oncall name, discovers all pipelines and analyzes the top 20 by BCU).
---

# Dataswarm Pipeline Optimizations

Optimize Dataswarm pipelines running on Spark and Presto engines.

## When to Use

- User provides a Dataswarm pipeline and asks for optimizations
- User requests help improving query performance or reducing BCU
- User asks to review or write efficient Dataswarm pipeline code
- User provides an oncall name and asks to optimize all pipelines for that oncall

## Optimization Patterns

Detailed reference docs for each pattern are in the `references/` directory alongside this skill.

| Pattern | Engines | What to Look For | Reference |
|---------|---------|------------------|-----------|
| **Join Order** | Presto | The join table should be smaller than base table | `presto_join.md` |
| **Ineffective Aggregates** | Both | GROUP BY on unique/high-cardinality keys | `ineffective_aggregate.md` |
| **Expensive JSON** | Both | Same column parsed ≥6 times | `expensive_json.md` |
| **Inefficient Expressions** | Both | Redundant CASTs, ORDER BY, CASE WHEN chains, COUNT(DISTINCT) | `inefficient_expression.md` |
| **Inefficient Functions** | Spark | FB_JAVA_F, nested MAP_CONCATs | `inefficient_functions.md` |
| **Large Data Filters** | Presto | Late filtering discarding significant data | `large_data_filter.md` |
| **Prefilter Build Side** | Presto | 99%+ of build-side rows filtered post-join; missing `join_prefilter_build_side` | `prefilter_build_side.md` |
| **Broadcast Join** | Both | Joins with >100:1 table size ratio without broadcast hint | `broadcast_join.md` |
| **Pre-Computation Before Joins** | Both | GROUP BY/DISTINCT possible before JOIN; redundant table scans; CROSS JOIN UNNEST without pre-filter | `pre_computation_before_join.md` |
| **Session Config Tuning** | Both | `force_override_efficiency_configs=True`; hardcoded shuffle partitions; missing AQE/BCU-optimized params | `session_config_tuning.md` |
| **Task Bucketing / Sharding** | Spark | Shuffle partitions misaligned with table bucketing; large monolithic tasks without sharding | `task_bucketing.md` |
| **WaitFor Operator** | Both | `WaitForManifoldOperator` (busy-waiting); unnecessary `DummyOperator` | `waitfor_operator.md` |
| **Column Pruning & Consolidation** | Both | `SELECT *`; unused aggregates; multiple tasks scanning same table; window functions replaceable by GROUP BY | `column_pruning_and_consolidation.md` |
| **ROW_NUMBER + rank=1** | Both | `ROW_NUMBER() OVER (...) = 1` pattern; use `MAX_BY`/`MIN_BY` instead | — |
| **UNION ALL Duplicates** | Both | Same source table scanned in multiple UNION ALL branches; combine into single scan with CASE | — |
| **Nested Subqueries** | Both | Subquery nesting deeper than 3 levels; flatten with CTEs | — |
| **Presto run_on_spark** | Presto | `PrestoInsertOperatorWithSchema` with `run_on_spark=True`; convert to `HiveInsertOperatorWithSchema` (native Spark) for 20-50% BCU savings | — |

## Workflow

First, determine the input type:
- **Oncall name** (e.g., `payments_de`, `growth_de`): Follow the **Oncall-Based Fleet Analysis** workflow below.
- **Pipeline file or directory**: Follow the **Single Pipeline Analysis** workflow below.

### Oncall-Based Fleet Analysis

Use this workflow when the user provides an oncall name and wants to optimize all pipelines for that oncall.

#### Step 1: Discover pipelines for the oncall

```bash
meta dataswarm.pipeline list --oncall=<oncall_name> --limit=200 --output=json
```

If the command returns no results, the oncall name may be invalid. Ask the user to verify. If exactly 200 results are returned, the oncall may have more pipelines -- inform the user that results were truncated and increase the limit or run a second query.

#### Step 2: Collect health metrics and rank by BCU

For each discovered pipeline, collect BCU usage:

```bash
meta dataswarm.pipeline health --name=<pipeline_name> --output=json
```

Extract `bcu_7d_avg` from the output. If the field is absent (zero-execution pipelines), default to 0. If the health check fails for a pipeline (permissions, deprecated, transient errors), skip it and note it in the report.

Sort all pipelines by `bcu_7d_avg` descending. Select the **top 20 pipelines by BCU** for analysis -- these have the highest optimization impact. Report the full list of pipelines and their BCU to the user so they can see which ones were selected and which were skipped.

#### Step 3: Locate source files

For each top-20 pipeline, locate its source file. Pipeline names use dot-separated segments that map to directory paths:

```
Pipeline name: ad_metrics.csa_xpub.core.credits.xpub_credits_and_weights
Source file:   fbcode/dataswarm-pipelines/tasks/ad_metrics/csa_xpub/core/credits/xpub_credits_and_weights.py
```

Replace dots with `/` and append `.py`. The file name may not always match exactly (e.g., hyphens vs underscores, abbreviated names), so use search to locate the file if the direct path mapping fails.

#### Step 4: Analyze each pipeline

For each pipeline source file, run the full single-pipeline analysis workflow (Steps 1-6 from the Single Pipeline Analysis below). Apply all optimization patterns from the Optimization Patterns table.

Batch pipelines into groups of 2-3 and analyze each batch in parallel using subagents. For each pipeline, produce an **individual report** with:

| Field | Details |
|-------|---------|
| Pipeline name | Full dot-separated name |
| Source file | Path to .py file |
| Engine | Spark / Presto / Presto on Spark |
| Findings table | Pattern, lines, description, impact (HIGH/MEDIUM/LOW) |
| Recommendations | Specific fix for each finding with estimated savings |
| Test plan | Tester command and OPEC_ONLY mode instructions |

Present each batch's individual reports before proceeding to the next batch.

#### Step 5: Produce consolidated report

After all individual reports are complete, produce a **consolidated fleet report** with two sections:

**Summary table** -- one row per pipeline, sorted by estimated savings descending:

| Pipeline | BCU (7d avg) | Engine | Optimizations Found | Estimated Savings |
|----------|-------------|--------|--------------------|--------------------|
| `pipeline_name` | X | Spark/Presto | N patterns | ~Y BCU |

**Cross-pipeline issues** -- systemic patterns that span multiple pipelines (e.g., duplicate table scans, shared intermediate tables that should be materialized).

This gives the oncall team both a prioritized overview and actionable detail per pipeline.

### Single Pipeline Analysis

1. **Determine scope**: Single file or entire directory
2. **Identify engine**: Spark, Presto, or both
3. **Scan for query-level patterns**: Check queries against the table above
4. **Check pipeline-level patterns**: Engine choice, WaitFor operators, session config, daily-vs-hourly cadence, task sharding
5. **Read reference docs**: Load the relevant reference file for detailed detection methods, examples, and fixes
6. **Provide recommendations**: Location, issue, impact, fix, and estimated savings

## Quick Detection Checklist

### Query-Level Patterns
- [ ] JOINs with smaller table on left (Presto)
- [ ] GROUP BY on columns that are unique or near-unique
- [ ] Same JSON column parsed 6+ times (`JSON_EXTRACT`, `FROM_JSON`, etc.)
- [ ] `SELECT MAX(ds)` instead of `<LATEST_DS:table>` macro
- [ ] `COUNT(DISTINCT x)` instead of `APPROX_DISTINCT(x)` / `FB_APPROX_DISTINCT(x)`
- [ ] ORDER BY in subqueries without LIMIT
- [ ] `FB_JAVA_F` for simple operations (Spark)
- [ ] Filters applied late that discard most data
- [ ] Joins where 99%+ of build-side rows are filtered post-join (prefilter build side)
- [ ] Joins with >100:1 table size ratio without broadcast hint
- [ ] GROUP BY or DISTINCT possible before JOIN to reduce row count
- [ ] CROSS JOIN UNNEST without pre-filtering with EXISTS
- [ ] `SELECT *` or computing unused aggregates/histograms
- [ ] Multiple tasks scanning the same large table independently
- [ ] Window functions replaceable by GROUP BY + aggregation
- [ ] `ROW_NUMBER() OVER (...) WHERE rn = 1` instead of `MAX_BY`/`MIN_BY`
- [ ] UNION ALL with same source table in multiple branches
- [ ] Subquery nesting deeper than 3 levels
- [ ] `PrestoInsertOperatorWithSchema` with `run_on_spark=True`

### Pipeline-Level Patterns
- [ ] `WaitForManifoldOperator` or `DummyOperator` in pipeline
- [ ] `force_override_efficiency_configs=True` in pipeline config
- [ ] `spark.sql.shuffle.partitions` hardcoded and misaligned with table bucketing

## Response Guidelines

- Provide a detailed explanation of the issue.
- Estimate the savings in BCU or DIRCU and explain how you came to that estimation. If applicable, provide a direct quote to the reference documentation you used to suggest the optimization.
- Provide a suggested implementation of the optimization (do not actually implement it till the user accepts your plan).
- Finally, suggest a test plan to verify the optimization - this will generally include running tester on the task you suggest optimizations for and any other upstream dependencies in the pipeline but can include other tests as well. Suggest running tester in OPEC_ONLY mode to avoid consuming production capacity.
