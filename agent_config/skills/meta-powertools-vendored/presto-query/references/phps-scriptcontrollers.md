# Legacy: phps ScriptController Reference

This document preserves the original `phps`-based commands for the Presto/Hive query skill. These commands require a www devserver checkout and are being superseded by the `jf graphql` equivalents documented in the main SKILL.md.

## ScriptController Commands

### HiveTableInfoScriptController

```bash
phps HiveTableInfoScriptController --table "dim_all_users" --namespace "di"
```

**Parameters:**
- `--table` (required): Table name
- `--namespace` (optional): Namespace (e.g., "di", "bi"). Auto-detected if not provided.

### LintSqlQueryScriptController

```bash
phps LintSqlQueryScriptController \
  --query "SELECT col FROM table WHERE ds = '2024-01-01'" \
  --namespace "di"
```

**Parameters:**
- `--query` (required): The Presto SQL query to validate
- `--namespace` (required): The namespace for the tables

### PrestoQueryScriptController

```bash
phps PrestoQueryScriptController \
  --query "SELECT col FROM table WHERE ds = '2024-01-01' LIMIT 10" \
  --namespace "di" \
  --limit 50
```

**Parameters:**
- `--query` (required): The Presto SQL query to execute
- `--namespace` (required): The namespace for the tables
- `--limit` (optional): Row limit (default: 100)

**Security:** Queries are validated via AccessMate and DPAS before execution.

### PrestoFunctionSearchScriptController

```bash
phps PrestoFunctionSearchScriptController --search "json_extract"
```

**Parameters:**
- `--search` (required): Function name or keyword to search for

### FetchQueryDimensionsScriptController

```bash
phps FetchQueryDimensionsScriptController \
  --query "SELECT a.col FROM table_a a JOIN table_b b ON a.id = b.id" \
  --namespace "di"
```

**Parameters:**
- `--query` (required): The Presto SQL query to analyze
- `--namespace` (required): The namespace for the tables

## Notes

- These commands only work from a www devserver checkout (`phps` is a www-only tool)
- For cross-repo usage (fbcode, laptop, etc.), use the `jf graphql` commands in the main SKILL.md
