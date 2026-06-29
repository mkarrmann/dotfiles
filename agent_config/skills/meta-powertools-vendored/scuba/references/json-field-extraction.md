# JSON Field Extraction

Use `--derived-cols` with `GET_JSON_OBJECT` to extract fields from JSON string columns. Use `--filter-sql` (not `--where`) for the WHERE clause since JSON extraction requires SQL expressions like `IS NOT NULL` and `LIKE` that can't be expressed as constraints.

**IMPORTANT**: `--filter-sql` and `--where` **cannot be mixed** in the same query. When using `--filter-sql` for JSON extraction, all filters must go in `--filter-sql`.

## Example: Extract a JSON field, group by it, and count

```bash
meta scuba.dataset query -d DATASET -g extracted_field -a count --hours=1 -l 50 \
  --filter-sql="action LIKE 'scuba_tools%' AND GET_JSON_OBJECT(json_column, '$.field_name') IS NOT NULL" \
  --derived-cols='[{"name":"extracted_field","sql":"GET_JSON_OBJECT(json_column, '"'"'$.field_name'"'"')","type":"String","isUsed":true}]'
```

## Key Rules

- JSON paths **MUST** use JSONPath `$.` prefix (e.g., `'$.metric'`, NOT `'metric'`)
- Use `"type":"String"` — `GET_JSON_OBJECT` returns a string value, not a number
- The derived column name (e.g., `extracted_field`) can be used in `-g` (group-by)
- Fall back to `--sql` mode only if the query also needs JOINs or HAVING

## Filtering on Extracted Values

To filter on extracted JSON values, add conditions to `--filter-sql`:

```bash
--filter-sql="GET_JSON_OBJECT(json_column, '$.field') = 'target' AND GET_JSON_OBJECT(json_column, '$.field') IS NOT NULL"
```

## Multiple JSON Fields

Extract multiple fields from the same JSON column by defining multiple derived columns:

```bash
--derived-cols='[{"name":"field_a","sql":"GET_JSON_OBJECT(json_column, '"'"'$.a'"'"')","type":"String","isUsed":true},{"name":"field_b","sql":"GET_JSON_OBJECT(json_column, '"'"'$.b'"'"')","type":"String","isUsed":true}]'
```

Both derived columns can be used in `-g`: `-g field_a,field_b`

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Using `"type":"Numeric"` for `GET_JSON_OBJECT` | Use `"type":"String"` — it returns a string |
| Missing `$.` prefix in JSON path | Always use `'$.field'`, not `'field'` |
| Mixing `--filter-sql` with `--where` | Use only `--filter-sql` when JSON extraction is involved — put all filters there |
| Forgetting `IS NOT NULL` check | Add `GET_JSON_OBJECT(...) IS NOT NULL` to `--filter-sql` to exclude rows without the field |
