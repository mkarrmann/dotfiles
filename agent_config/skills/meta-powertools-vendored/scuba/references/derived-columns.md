# Derived Columns (`--derived-cols`)

Derived columns let you define computed columns using arbitrary Scuba SQL expressions. The SQL is passed **verbatim** to the Scuba backend — no function name or syntax validation happens on the www side. This means any SQL function supported by Scuba works in a derived column.

**You MUST read this document before using `--derived-cols`.**

## Quick Reference — Weighted Aggregation

Scuba supports **two-argument weighted** aggregate functions. Use `SUM(value, weight)` or `AVG(value, weight)` with `"type":"Aggregated"`:
```json
[{"isUsed":true,"name":"weighted_cpu","sql":"SUM(cpu_time, total_time_ms)","type":"Aggregated"}]
```
This is Scuba-specific syntax — not standard SQL. The second argument is the weight column.

---

## JSON Format

```json
[{"name":"col_name","sql":"EXPRESSION","type":"TYPE","isUsed":true}]
```

Each object defines a computed column with:
- `name`: The name for the derived column (appears in results)
- `sql`: A Scuba SQL expression (e.g., `` `col1` + `col2` ``, or an aggregate function)
- `type`: **Critical** — must match the expression type:
  - `"Numeric"` — For scalar arithmetic expressions (e.g., `` `col1` + `col2` ``, `` IF(`col` = 0, 1, 0) ``)
  - `"String"` — For scalar string expressions (e.g., `GET_JSON_OBJECT(col, '$.field')`, `REGEXP_EXTRACT(col, 'pattern')`)
  - `"Aggregated"` — For aggregate function expressions (e.g., `APPROX_COUNT_DISTINCT_HLL(col, 16)`, `AVG(col, weight)`, `SUM(IF(...))`)
  - `"NormVector"` — For expressions that return arrays/vectors (e.g., `SPLIT(col, ',')`)
- `isUsed`: Set to `true` to include the column in results

### Type Selection Rule

If the derived column expression contains an aggregate function (like `APPROX_COUNT_DISTINCT_HLL`, `SUM`, `AVG`, etc.), you **MUST** set `"type":"Aggregated"`. Using `"Numeric"` for aggregate expressions causes Scuba to wrap the expression in another aggregate (e.g., `SUM(your_aggregate)`), which produces the error: **"Aggregate can not accept other aggregates as its parameter"**.

**Scalar Arithmetic + Aggregation**: When you need to compute a per-row formula (like `cpu_time * 100.0 / total_time_ms`) and then aggregate it (like AVG), define the derived column as `"type":"Numeric"` with just the per-row formula, then use the `-a` flag to aggregate it:
```bash
meta scuba.dataset query -d my_dataset -g query_source -a avg -c cpu_pct \
  --derived-cols='[{"isUsed":true,"name":"cpu_pct","sql":"cpu_time * 100.0 / total_time_ms","type":"Numeric"}]'
```
Do NOT wrap the formula in an aggregate function like `AVG(cpu_time * 100.0 / total_time_ms)` with `"type":"Aggregated"` — this may work but bypasses Scuba's built-in aggregation logic. The `"Numeric"` + `-a` approach is canonical.

---

## Validating Expressions

Use `meta scuba.column validate` to validate a derived column SQL expression against a real dataset without processing any data.

**Required flags:**
- `-d` / `--dataset`: Scuba dataset name
- `--sql`: The SQL expression to validate
- `-t` / `--type`: One of `String`, `Numeric`, `Aggregated`, `NormVector`

**Examples:**

```bash
# Validate a string expression
meta scuba.column validate -d scuba_queries --sql "UPPER(query_source)" -t String

# Validate an aggregated expression
meta scuba.column validate -d scuba_queries --sql "APPROX_COUNT_DISTINCT_HLL(dataset, 16)" -t Aggregated

# Validate a numeric expression
meta scuba.column validate -d scuba_queries --sql "cpu_time / 1000.0" -t Numeric
```

---

## Common Patterns

### Scalar Arithmetic (type: "Numeric")

```bash
meta scuba.dataset query -d my_dataset -g region -a avg -c latency \
  --derived-cols='[{"isUsed":true,"name":"error_rate","sql":"`errors` * 100.0 / `total_requests`","type":"Numeric"}]'
```

### Count Distinct (type: "Aggregated")

```bash
meta scuba.dataset query -d my_dataset -a count --time-bucket="1 hour" --hours=72 \
  -w '[{"column":"status","op":"eq","values":["active"]}]' \
  --derived-cols='[{"isUsed":true,"name":"unique_hosts","sql":"APPROX_COUNT_DISTINCT_HLL(hostname, 16)","type":"Aggregated"}]'
```

### Weighted Aggregations (type: "Aggregated")

For sampled data, write the full weighted SQL expression directly:
```bash
meta scuba.dataset query -d DATASET -g region --hours=24 \
  --derived-cols='[{"isUsed":true,"name":"weighted_avg_latency","sql":"AVG(latency, sample_rate)","type":"Aggregated"}]'
```
The SQL expression (e.g., `AVG(latency, sample_rate)`, `SUM(value, weight)`) is passed verbatim to the Scuba backend.

### Conditional Aggregation (type: "Aggregated")

Use `SUM(IF(...))` or `CASE WHEN`:
```bash
meta scuba.dataset query -d DATASET -g endpoint --hours=6 \
  --derived-cols='[{"isUsed":true,"name":"error_count","sql":"SUM(IF(status_code >= 500, 1, 0))","type":"Aggregated"},{"isUsed":true,"name":"total_count","sql":"SUM(1)","type":"Aggregated"}]'
```

### Regex Extraction (type: "String")

Extract substrings with `REGEXP_EXTRACT`. The derived column can be used as a GROUP BY dimension. Use `--filter-sql` to exclude NULLs:
```bash
meta scuba.dataset query -d DATASET -g extracted_service -a count --hours=3 \
  --derived-cols='[{"isUsed":true,"name":"extracted_service","sql":"REGEXP_EXTRACT(endpoint, '"'"'/(\\\\w+)/.*'"'"')","type":"String"}]' \
  --filter-sql="REGEXP_EXTRACT(endpoint, '/(\\w+)/.*') IS NOT NULL"
```

### JSON Field Extraction (type: "String")

See [json-field-extraction.md](json-field-extraction.md) for full syntax, multi-field extraction, and common mistakes. Basic pattern:
```bash
meta scuba.dataset query -d DATASET -g extracted_field -a count \
  --derived-cols='[{"isUsed":true,"name":"extracted_field","sql":"GET_JSON_OBJECT(json_col, '"'"'$.field_name'"'"')","type":"String"}]' \
  --filter-sql="GET_JSON_OBJECT(json_col, '$.field_name') IS NOT NULL"
```

---

## Shell Quoting Limitation: No String Literals in `--derived-cols`

`--derived-cols` SQL passes through four quoting layers (SQL → JSON → GraphQL → shell). Single-quoted values like `'LOW'` are silently dropped — queries return `null` with no error. Numeric values and `ARRAY(...)` arguments are unaffected.

**Use `--sql-file` or `--sql` mode for any derived column that needs string literal output.** See Critical Rule 5 in SKILL.md.

---

## Discovering Existing Derived Columns

Datasets can have 100+ pre-defined derived columns. When looking for columns related to a concept, do NOT visually skim — search with multiple related keywords.

```bash
# STEP 1: List derived column names (lightweight)
meta scuba.column list -d DATASET

# STEP 2: Search the list for multiple related keywords

# STEP 3: Get full metadata ONLY for columns of interest
meta scuba.dataset info -d DATASET --columns=col_a,col_b --output=json
```

---

## Discovering and Using UDFs

### `meta scuba.udf list` — Discover Available Functions

**IMPORTANT**: When the user asks about specific functions, always use `--search` for targeted results. Only use the lightweight mode (no `--search`) when browsing all available functions.

```bash
# Search for specific functions by keyword (PREFERRED)
meta scuba.udf list --search=approx,count_distinct

# List all available UDF names only (lightweight, no details)
meta scuba.udf list
```

### `meta scuba.udf usage` — Find Real-World Usage Examples

```bash
# Find usage examples for a specific UDF
meta scuba.udf usage --udfs=APPROX_COUNT_DISTINCT_HLL

# Search multiple UDFs with a preferred dataset
meta scuba.udf usage --udfs=REGEXP_EXTRACT,SPLIT --dataset=my_dataset
```

### Workflow: Discover -> Learn -> Validate -> Execute

1. **Discover** — Find available functions: `meta scuba.udf list --search=approx,distinct`
2. **Learn** — See real-world usage: `meta scuba.udf usage --udfs=APPROX_COUNT_DISTINCT_HLL`
3. **Validate** — Test expression: `meta scuba.column validate -d my_dataset --sql "APPROX_COUNT_DISTINCT_HLL(user_id, 16)" -t Aggregated`
4. **Execute** — Use the validated expression in `--derived-cols`

---

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| "Aggregate can not accept other aggregates as its parameter" | Derived column uses an aggregate function but `type` is `"Numeric"` | Change to `"type":"Aggregated"` |
| "Invalid argument types for UDF SUM. Found SUM(aggregate...)" | Same as above — aggregate expression with wrong type | Change to `"type":"Aggregated"` |
| Column not appearing in results | `isUsed` not set | Add `"isUsed":true` |
| NULL results from `REGEXP_EXTRACT` / `GET_JSON_OBJECT` | Pattern doesn't match or JSON path doesn't exist | Add `--filter-sql` with `IS NOT NULL` check |
| All-null results from CASE/IF with string outputs | Single-quoted string literals silently lost in shell quoting | Use `--sql-file`, `--sql` mode, numeric encoding, or heredoc — see Critical Rule 5 in SKILL.md |
