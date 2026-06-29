---
name: daiquery-to-dataswarm
description: Convert Daiquery notebooks into Dataswarm pipelines. Use when the user provides a Daiquery URL (internalfb.com/intern/daiquery/workspace/...) or asks to convert a Daiquery query to a Dataswarm pipeline, or mentions turning SQL queries into a pipeline.
---

# Daiquery to Dataswarm Pipeline Converter

Convert SQL queries from Daiquery notebooks into production-ready Dataswarm pipelines.

## When to Use

This skill should be used when users:
- Provide a Daiquery notebook URL (e.g., `https://www.internalfb.com/intern/daiquery/workspace/...`)
- Ask to convert a Daiquery query to a Dataswarm pipeline
- Want to productionize ad-hoc SQL queries into scheduled pipelines
- Need to turn interactive SQL analysis into automated data workflows

## Two-Step Workflow

This skill uses a two-step approach:

### Step 1: Analyze Daiquery and Gather Requirements (This Skill)

1. Fetch the Daiquery content
2. Parse and analyze the SQL queries
3. Identify ambiguities and prompt user for clarification
4. Build a complete conversion specification

### Step 2: Write and Test Pipeline (dataswarm-pipeline Skill)

Pass the conversion specification to the `dataswarm-pipeline` skill, which will:
1. Write the pipeline code
2. Run the linter
3. Run the tester
4. Monitor the job until completion

---

## Step 1: Analyze Daiquery Content

### 1.1 Fetch the Daiquery Notebook

Use the `knowledge_load` MCP tool to fetch the Daiquery notebook content:

```
mcp__plugin_meta_www__knowledge_load(url="<daiquery_url>")
```

This returns the notebook with:
- `name`: Query name
- `code`: The SQL query/queries (multiple queries separated by blank lines)
- `submit_time`: When it was last run

### 1.2 Parse the SQL Queries

Daiquery notebooks may contain multiple SQL statements separated by blank lines. For each query:

1. **Identify input tables**: Look for `FROM` and `JOIN` clauses
2. **Identify output requirements**: What data is being selected?
3. **Check for date placeholders**: Look for `<DATEID>`, `<DATEID-N>`, `<LATEST_DS:...>`, `<TS>`
4. **Determine schedule**: Daily if using `<DATEID>`, hourly if using `<TS>` patterns
5. **Identify CTEs**: Common Table Expressions that may need to be handled specially

### 1.3 Handle Ambiguities

**IMPORTANT**: If any of the following ambiguities exist, use the `AskUserQuestion` tool to clarify BEFORE proceeding:

#### Multiple Terminal Queries
If the notebook contains multiple SQL statements, ask:
- Which query/queries should become pipeline tasks?
- Are they independent or do they depend on each other?
- Should all queries write to separate tables, or is only the final query needed?

#### Unknown Output Table Names
Always ask the user for:
- Output table name(s) for each query that will become a task
- The namespace for the output table(s)

#### Missing Pipeline Metadata
Ask the user for these required values (unless they can be inferred from context like existing pipelines nearby):
- **data-project-acl**: The ACL for data access control
- **oncall**: The oncall responsible for the pipeline
- **namespace**: The Hive namespace for output tables
- **Pipeline file location**: Where to place the pipeline file

**IMPORTANT — Infer metadata from sibling pipelines**: When the user specifies a target directory (e.g., `tasks/measurementsystems/creator_incentives/challenges_v3/`), **always read an existing `.py` pipeline file in that directory first** to extract:
- `# @data-project-acl:` from the file header comment
- `oncall=` from `GlobalDefaults.set(...)`
- `user=` from `GlobalDefaults.set(...)`

Use these values instead of guessing. Only ask the user if no sibling pipeline exists.

**IMPORTANT — Keep pipeline and UPM oncall in sync**: The `oncall=` value in `GlobalDefaults.set(...)` and the `oncall=` in the UPM `@hive_dataset(...)` decorator MUST be identical. These are generated independently — always cross-check them before finalizing.

#### Ambiguous Schedule
If the schedule cannot be determined from the SQL patterns, ask:
- Should this be a daily or hourly pipeline?

#### Complex CTEs
If the query has complex CTEs that might benefit from being separate tasks:
- Should CTEs remain inline or become separate intermediate tables?

### 1.4 Build Conversion Specification

After resolving all ambiguities, build a complete conversion specification containing:

```
CONVERSION SPECIFICATION
========================

Source: <daiquery_url>
Query Name: <name from daiquery>

Pipeline Configuration:
- data-project-acl: <acl>
- oncall: <oncall>
- namespace: <namespace>
- schedule: @daily | @hourly
- pipeline_file_path: <full path to pipeline file>

Tasks to Create:
1. Task: <task_name>
   - Output table: <table_name>
   - Input tables:
     * <table:namespace> with partition annotation <annotation>
     * ...
   - SQL: |
       <the converted SQL>

2. Task: <task_name_2> (if multiple)
   ...

Date Placeholder Mappings Applied:
- <original pattern> -> <dataswarm equivalent>
- ...

Notes:
- <any special considerations>
```

---

## Step 2: Invoke dataswarm-pipeline Skill

Once the conversion specification is complete, invoke the `dataswarm-pipeline` skill to:
1. Write the pipeline code to the specified file
2. Run `./linter --update-schema` to validate and update schemas
3. Run `./tester` with appropriate date and test prefix
4. Monitor the Chronos job until completion

Use the Skill tool to invoke the dataswarm-pipeline skill:

```
Skill(skill="dataswarm-pipeline")
```

Then provide the full conversion specification and instruct the skill to:
1. Write the pipeline following the specification exactly
2. Run the linter with `--update-schema`
3. Run the tester with `-b test_${USER}_` prefix
4. Monitor the job to completion using the polling script

---

## Conversion Rules Reference

These rules should be applied when building the conversion specification:

### Date Placeholder Mappings

| Daiquery Pattern | Dataswarm Equivalent |
|------------------|----------------------|
| `ds = '<DATEID>'` | `.col_ds_eq_dateid()` - remove WHERE clause for ds |
| `ds = '<DATEID-N>'` | `.col_ds_eq_dateid_minus(N)` |
| `ds BETWEEN '<DATEID-N>' AND '<DATEID>'` | `.col_ds_between("<DATEID-N>", "<DATEID>")` |
| `<LATEST_DS:table_signal>` | `.col_ds_eq_dateid()` (NOT `.col_ds_eq_latest()` - it doesn't exist!) |
| `ts = '<TS>'` or hourly patterns | `.col_ts_ds_eq_scheduled()` for hourly |

### Input Table References

Replace direct table references with `<INPUT:alias>` syntax:

**Before (Daiquery):**
```sql
SELECT * FROM my_table:namespace WHERE ds = '<DATEID>'
```

**After (Dataswarm):**
```python
input_data={
    "my_table_alias": input.table("my_table:namespace").col_ds_eq_dateid(),
}
select="""
    SELECT * FROM <INPUT:my_table_alias>
"""
```

### Using INPUT_TABLE_NAME and INPUT_WHERE_CLAUSE

For complex queries with additional WHERE conditions on partitioned tables:

```python
input_data={
    "source_table": input.table("source_table:namespace").col_ds_eq_dateid(),
}
select="""
    SELECT *
    FROM <INPUT_TABLE_NAME:source_table>
    WHERE
        <INPUT_WHERE_CLAUSE:source_table>
        AND other_condition = 'value'
"""
```

### Multi-Partition Tables

Some tables have partition columns beyond just `ds`. Specify ALL partition columns:

```python
input_data={
    "versions": (
        input.table("dim_all_qrt_experiment_versions")
        .col_ds_eq_dateid()
        .col("is_active").eq(1)
        .col("is_90d_active").eq(1)
    ),
}
```

### CTEs (Common Table Expressions)

**Option 1: Keep CTEs inline (simpler, recommended)**
```python
select="""
    WITH cte_name AS (
        SELECT ... FROM <INPUT:source>
    )
    SELECT * FROM cte_name
""",
```

**Option 2: Separate tasks (for reusable intermediates)**
Create multiple tasks with intermediate output tables.

---

## Example Workflow

### User Request
"Convert this Daiquery to a pipeline: https://www.internalfb.com/intern/daiquery/workspace/123/456"

### Step 1: This Skill Analyzes

1. Fetch Daiquery content via `knowledge_load`
2. Parse: Found 2 SQL queries, first creates a CTE used by second
3. Ask user:
   - "This notebook has 2 queries. Should both become pipeline tasks, or just the final one?"
   - "What should the output table be named?"
   - "What namespace should it use?"
   - "What ACL and oncall should be used?"
4. Build conversion specification with all details

### Step 2: Invoke dataswarm-pipeline Skill

Pass the specification:
```
Now use the dataswarm-pipeline skill to write and test this pipeline:

CONVERSION SPECIFICATION
========================
[full specification here]

Instructions:
1. Write the pipeline to the specified path
2. Run ./linter --update-schema
3. Run ./tester with -b test_${USER}_ prefix using a date 3 days ago
4. Monitor the Chronos job to completion
```

---

## Important Notes

- **Always clarify ambiguities BEFORE invoking the dataswarm-pipeline skill**
- The dataswarm-pipeline skill handles all writing, linting, testing, and monitoring
- This skill focuses ONLY on analysis, parsing, and gathering requirements
- Never mix daily and hourly schedules in the same pipeline file
