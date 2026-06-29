# TW CLI Usage Scuba Dataset

**Purpose:** Debug Tupperware CLI issues for users. Logs every `tw` CLI invocation including the subcommand, exit code, exceptions, latency, and TW error codes. Use this dataset when users report CLI errors, slow commands, or unexpected behavior from `tw job start`, `tw job update`, `tw log`, etc.

**Scuba Table:** `tw_cli_usage`

**Scuba UI:** https://www.internalfb.com/intern/scuba/query/?pool=uber&dataset=tw_cli_usage

**Related Datasets:**
- `tupperware_task_events` - For task-level issues after CLI commands succeed
- `tupperware_job_events` - For job-level errors that the CLI surfaces
- `tupperware_api_service_cpp` - For server-side API errors behind CLI calls

---

## How to Get Schema

```bash
meta scuba.dataset query -d tw_cli_usage --limit=5 -r "Sample data to view schema"
```

### Key Columns

| Column | Type | Description |
|--------|------|-------------|
| `time` | bigint | Unix timestamp of the CLI invocation |
| `user` | string | Unix username who ran the command |
| `command` | string | Path to the `tw` binary used |
| `subcommand` | string | CLI subcommand (e.g., `job start`, `job print`, `log`, `task-control`) |
| `exit_code` | string | Process exit code (`0` = success, `1` = error, `2` = usage error, `5` = TW error) |
| `TW_error_code` | string | Tupperware-specific error code (see Tips) |
| `exception_type` | string | Python exception class (e.g., `TupperwareException`, `SystemExit`) |
| `exception` | string | Full exception message with error details |
| `total_time` | bigint | Total command execution time in milliseconds |
| `hostname` | string | Host where the CLI was invoked |
| `version` | string | CLI version string |
| `argv` | array\<string\> | Full command-line arguments |
| `parameters` | string | Parsed command parameters |
| `traceback` | string | Python traceback for exceptions |
| `job_handle_tags` | array\<string\> | Job handles referenced in the command |
| `solo_host` | string | Target host for `tw solo` commands |
| `solo_task` | string | Target task for `tw solo` commands |

---

## Common Queries

### 1. CLI Errors for a Specific User

Find all CLI failures for a user to debug their reported issues.

```bash
meta scuba.dataset query -d tw_cli_usage -a count -g subcommand,exit_code,TW_error_code,exception_type,exception --filter-sql="user = 'USERNAME' AND exit_code != '0'" --hours=24 -r "CLI errors for specific user"
```

### 2. Top CLI Errors by Subcommand

Identify which subcommands fail most and why.

```bash
meta scuba.dataset query -d tw_cli_usage -a count -g subcommand,TW_error_code,exception_type --filter-sql="exit_code != '0' AND TW_error_code IS NOT NULL AND TW_error_code != ''" --hours=24 -r "Top CLI errors by subcommand"
```

### 3. CLI Command Latency (P50, P95, P99)

Identify slow CLI commands.

```bash
meta scuba.dataset query -d tw_cli_usage -a count -g subcommand -w '[{"column":"exit_code","op":"eq","values":["0"]}]' --hours=24 -r "CLI command latency"
```

### 4. Error Rate by Subcommand

Find subcommands with the highest failure rates.

```bash
meta scuba.dataset query -d tw_cli_usage -a count -g subcommand --filter-sql="subcommand IS NOT NULL" --hours=24 -r "Error rate by subcommand"
```

### 5. Top Exception Messages

Find the most common exception messages across all CLI users.

```bash
meta scuba.dataset query -d tw_cli_usage -a count -g exception_type,exception,subcommand --filter-sql="exception IS NOT NULL AND exception != ''" --hours=24 -r "Top exception messages"
```

### 6. CLI Error Trend Over Time

Track CLI error rate in 5-minute buckets to correlate with incidents.

```bash
meta scuba.dataset query -d tw_cli_usage -a count --hours=1 -r "CLI error trend over time"
```

### 7. Slow Commands for a Specific User

Debug user-reported slowness.

```bash
meta scuba.dataset query -d tw_cli_usage --view=samples -c subcommand,total_time,exit_code,time --filter-sql="user = 'USERNAME' AND total_time > 60000" --hours=24 -r "Slow commands for specific user"
```

### 8. CLI History for a Specific Job

See all CLI commands run against a specific job handle — useful for understanding what operations were attempted.

```bash
meta scuba.dataset query -d tw_cli_usage --view=samples -c time,user,subcommand,exit_code,TW_error_code,exception,total_time --filter-sql="ARRAY_CONTAINS(job_handle_tags, 'tsp_cln/team/service.prod')" --hours=24 -r "CLI history for specific job"
```

### 9. CLI History for a Specific User

Full timeline of all CLI commands a user ran — useful for reconstructing what they did before reporting an issue.

```bash
meta scuba.dataset query -d tw_cli_usage --view=samples -c time,subcommand,exit_code,TW_error_code,exception,total_time -w '[{"column":"user","op":"eq","values":["USERNAME"]}]' --hours=24 -r "CLI history for specific user"
```

---

## Tips

1. **Exit codes:** `0` = success, `1` = general error, `2` = usage/argument error (often `SystemExit(2)`), `5` = TW-specific error with `TW_error_code`.

2. **Common TW error codes:**
   - `4` = Permission denied / ACL error
   - `8` = Task not found
   - `9` = Log file not found
   - `10` = Job already exists
   - `16` = Invalid spec / validation error
   - `26` = Bad scheduler domain
   - `31` = Package resolution failure (fbpkg `NO_SUCH_VERSION`)
   - `47` = Spec processing error (no job specs exported, missing module)

3. **Filter by user:** Use `user = 'unixname'` to investigate a specific user's CLI issues.

4. **Filter by subcommand:** Common subcommands: `job start`, `job print`, `job update`, `job delete`, `job stop`, `job restart`, `job list`, `job validate`, `job diff`, `job lint`, `log`, `task-control`, `canary start`, `ssh`, `solo start-host`.

5. **Distinguish CLI errors from server errors:** `SystemExit(2)` is typically a CLI usage error (wrong arguments). `TupperwareException` indicates a server-side error.

6. **Use `traceback` for debugging:** The `traceback` column contains the full Python stack trace for exceptions — useful for filing bugs against the TW CLI team.

7. **Start with 1-hour window:** Expand to 24 hours for trend analysis. For investigating individual user reports, 24 hours is usually sufficient.
