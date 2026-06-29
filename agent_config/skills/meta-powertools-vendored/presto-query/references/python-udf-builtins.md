# Python UDF Builtin Functions (`py.*`)

Presto exposes Python library functions as SQL-callable builtins under the `py` catalog: `py.<schema>.<function>(args...)`. All are SCALAR and DETERMINISTIC.

## py.re -- Regex

| Function | Signature | Returns | Description |
|----------|-----------|---------|-------------|
| `findall` | `(pattern VARCHAR, text VARCHAR)` | `VARCHAR` | All non-overlapping regex matches as JSON array |
| `sub` | `(pattern VARCHAR, replacement VARCHAR, text VARCHAR)` | `VARCHAR` | Replace all pattern matches with replacement |
| `split` | `(pattern VARCHAR, text VARCHAR)` | `VARCHAR` | Split text by pattern, returns JSON array of parts |
| `search` | `(pattern VARCHAR, text VARCHAR)` | `VARCHAR` | First match of pattern, or NULL if none |

## py.numpy -- Numeric Computation

| Function | Signature | Returns | Description |
|----------|-----------|---------|-------------|
| `percentile` | `(values ARRAY(DOUBLE), q DOUBLE)` | `DOUBLE` | q-th percentile (q: 0-100) |
| `corrcoef` | `(x ARRAY(DOUBLE), y ARRAY(DOUBLE))` | `DOUBLE` | Pearson correlation coefficient |
| `median` | `(values ARRAY(DOUBLE))` | `DOUBLE` | Median value |
| `std` | `(values ARRAY(DOUBLE))` | `DOUBLE` | Population standard deviation |
| `mean` | `(values ARRAY(DOUBLE))` | `DOUBLE` | Arithmetic mean |
| `weighted_avg` | `(values ARRAY(DOUBLE), weights ARRAY(DOUBLE))` | `DOUBLE` | Weighted average |
| `zscore` | `(values ARRAY(DOUBLE))` | `VARCHAR` | JSON array of z-scores. All 0.0 if std=0 |
| `cumsum` | `(values ARRAY(DOUBLE))` | `VARCHAR` | JSON array of cumulative sums |
| `diff` | `(values ARRAY(DOUBLE))` | `VARCHAR` | JSON array of consecutive differences (length = n-1) |
| `pct_change` | `(values ARRAY(DOUBLE))` | `VARCHAR` | JSON array of period-over-period % change. First element is null. Division-by-zero yields null |
| `unique` | `(values ARRAY(DOUBLE))` | `VARCHAR` | JSON array of sorted unique values |
| `interp` | `(x_new ARRAY(DOUBLE), x ARRAY(DOUBLE), y ARRAY(DOUBLE))` | `VARCHAR` | JSON array of interpolated y values at x_new points |

## py.pandas -- Data Transformation

| Function | Signature | Returns | Description |
|----------|-----------|---------|-------------|
| `json_normalize` | `(nested_json JSON)` | `VARCHAR` | Flattens nested JSON into records with dot-separated keys |
| `cut` | `(value DOUBLE, bins ARRAY(DOUBLE))` | `VARCHAR` | Bin label like `"(0.0, 10.0]"`, or NULL if out of range |
| `rolling_mean` | `(values ARRAY(DOUBLE), window INTEGER)` | `VARCHAR` | JSON array of rolling averages. First `window-1` elements are null |
| `fillna` | `(values ARRAY(DOUBLE), method VARCHAR)` | `VARCHAR` | Fill nulls. Methods: `"ffill"` (default), `"bfill"`, `"zero"`, `"mean"`, `"median"` |
| `groupby_agg` | `(records JSON, group_col VARCHAR, value_col VARCHAR, agg_func VARCHAR)` | `VARCHAR` | Groups JSON records by column, aggregates value. agg_func: `"mean"` (default), `"sum"`, `"count"`, `"min"`, `"max"` |
| `pivot` | `(records JSON, index_col VARCHAR, columns_col VARCHAR, values_col VARCHAR)` | `VARCHAR` | Pivot table with sum aggregation. Reshapes long to wide format |
| `concat` | `(left JSON, right JSON)` | `VARCHAR` | Vertically concatenate two JSON arrays of records |
| `merge` | `(left JSON, right JSON, key VARCHAR)` | `VARCHAR` | Left join two JSON record sets on key column |
| `value_counts` | `(data JSON, column VARCHAR)` | `VARCHAR` | JSON: `{"value": count, ...}`. Column is optional if input is flat array |
| `melt` | `(records JSON, id_vars VARCHAR, value_vars VARCHAR)` | `VARCHAR` | Unpivot wide to long. Output has `id_var`, `"variable"`, `"value"` columns |

## py.datetime -- Date/Time

| Function | Signature | Returns | Description |
|----------|-----------|---------|-------------|
| `dt_parse` | `(date_str VARCHAR, format VARCHAR)` | `VARCHAR` | Normalized `"YYYY-MM-DD HH:MM:SS"`. Format is required: use `'%Y-%m-%d'` for ISO dates or `'%b %d %Y'` for "Mar 5 2024" |
| `dt_diff` | `(date1 VARCHAR, date2 VARCHAR, unit VARCHAR)` | `DOUBLE` | `date2 - date1` in given unit. Units: `"seconds"`, `"minutes"`, `"hours"`, `"days"` (default), `"weeks"` |
| `dt_add_days` | `(date_str VARCHAR, days INTEGER)` | `VARCHAR` | Date after adding N days, formatted as `"YYYY-MM-DD"` |
| `dt_to_unix` | `(date_str VARCHAR)` | `DOUBLE` | Unix timestamp (seconds since epoch) |
| `dt_format` | `(date_str VARCHAR, format VARCHAR)` | `VARCHAR` | Date reformatted using Python strftime codes |

## py.stats -- Statistical Analysis

| Function | Signature | Returns | Description |
|----------|-----------|---------|-------------|
| `growth_rate` | `(old DOUBLE, new DOUBLE)` | `DOUBLE` | `(new-old)/old * 100`. NULL if old=0 |
| `cagr` | `(start DOUBLE, end DOUBLE, periods INTEGER)` | `DOUBLE` | Compound annual growth rate as percentage. NULL if start<=0 or periods<=0 |
| `period_change` | `(values ARRAY(DOUBLE), lag INTEGER)` | `VARCHAR` | JSON array of period-over-period % change. First `lag` elements are null. Default lag=1 |
| `detect_spikes` | `(values ARRAY(DOUBLE), threshold DOUBLE)` | `VARCHAR` | JSON boolean array. True where `abs(z-score) > threshold`. Default threshold=2.0 |
| `moving_zscore` | `(values ARRAY(DOUBLE), window INTEGER)` | `VARCHAR` | JSON array of rolling z-scores. First `window-1` elements are null. Default window=3 |
| `share` | `(values ARRAY(DOUBLE))` | `VARCHAR` | JSON array of each element as % of total sum |
| `forecast` | `(values ARRAY(DOUBLE), n_periods INTEGER)` | `VARCHAR` | JSON array of N predicted values via linear extrapolation. Default n_periods=1 |
| `exp_smooth` | `(values ARRAY(DOUBLE), alpha DOUBLE)` | `VARCHAR` | JSON array of exponentially smoothed values. Default alpha=0.3 |
| `retention_rate` | `(cohort_values ARRAY(DOUBLE))` | `VARCHAR` | JSON array of each element as % of first element (retention curve) |

## py.arrow -- Batch Processing

| Function | Signature | Returns | Description |
|----------|-----------|---------|-------------|
| `transform` | `(input VARCHAR)` | `VARCHAR` | Runs in ARROW_BATCH mode. Doubles all numeric columns in the RecordBatch. Non-numeric columns pass through unchanged |

## Usage Patterns

### Building array inputs from columns
```sql
-- Use ARRAY_AGG to build arrays from column values
SELECT py.numpy.percentile(ARRAY_AGG(revenue), 95.0)
FROM my_table WHERE ds = '2024-01-15'
```

### Handling nullable arrays from ARRAY_AGG
`ARRAY_AGG()` produces `ARRAY(DOUBLE?)` (nullable elements), but py.* functions require `ARRAY(DOUBLE)` (non-nullable). **Always filter NULLs before aggregation AND wrap with TRANSFORM+COALESCE:**
```sql
SELECT py.stats.detect_spikes(
  TRANSFORM(ARRAY_AGG(CAST(value AS DOUBLE)), x -> COALESCE(x, 0.0)), 2.0
) FROM my_table
WHERE ds = '2024-01-15' AND value IS NOT NULL  -- filter NULLs first
HAVING COUNT(*) > 1  -- guard against empty arrays
```

### Large tables
Use `TABLESAMPLE BERNOULLI(N)` on tables with 100M+ rows:
```sql
SELECT py.numpy.percentile(
  TRANSFORM(ARRAY_AGG(CAST(value AS DOUBLE)), x -> COALESCE(x, 0.0)), 95.0
) FROM large_table TABLESAMPLE BERNOULLI(1)
WHERE ds = '2024-01-15' AND value IS NOT NULL LIMIT 1
```

## Rules

- All functions are **scalar** -- they operate per-row. Use `ARRAY_AGG()` to build array inputs from columns.
- **Always filter NULLs** with `WHERE col IS NOT NULL` before `ARRAY_AGG`, then wrap with `TRANSFORM(..., x -> COALESCE(x, 0.0))`.
- **Guard against empty arrays** with `HAVING COUNT(*) > 1` -- empty arrays cause `NoneType` division errors.
- Always include `WHERE ds = '...'` and `LIMIT` (same as any Presto query).
- On large tables (100M+ rows), add `TABLESAMPLE BERNOULLI(N)` to avoid timeouts.
- `JSON` type params accept `JSON '...'` literals, `JSON_PARSE(varchar)`, or `CAST(array AS JSON)`.
- `py.datetime.dt_parse` requires 2 arguments: `dt_parse(date_str, format)`. Use `'%Y-%m-%d'` for ISO dates or `'%b %d %Y'` for "Mar 5 2024".
- **DO NOT use the following py.\* functions:** `py.scipy.ttest`, `py.scipy.linregress`, `py.scipy.chi2_test`, `py.stats.trend`, `py.stats.funnel`, `py.numpy.polyfit`, `py.numpy.histogram`, `py.pandas.summary_stats`. These return JSON objects requiring field extraction via `JSON_EXTRACT_SCALAR`, which is not currently supported with py.\* results on Prestissimo.
