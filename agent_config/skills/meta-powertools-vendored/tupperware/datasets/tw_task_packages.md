# TW Task Packages Scuba Dataset

**Purpose:** Tracks package fetch events on Tupperware hosts -- when package downloads start, finish, or fail. Use this dataset to debug package fetch failures, slow package downloads, and to identify which package versions are being fetched by the TW agent for a given job.

**Scuba Table:** `tw_task_packages`

**Scuba UI:** https://www.internalfb.com/intern/scuba/query/?pool=uber&dataset=tw_task_packages

**Related Datasets:**
- `tupperware_task_events` - Correlate package issues with task lifecycle (e.g., tasks stuck in STAGING)
- `fbpkg_invocations` - Track runtime fbpkg fetch calls (packages fetched inside the container, not by the TW agent)
- `fbpkg_proxy_thrift_calls` - Debug package fetch failures/latency at the fbpkg proxy layer

---

## How to Get Schema

```bash
meta scuba.dataset query -d tw_task_packages --limit=5 -r "Sample data to view schema"
```

### Key Columns

| Column | Type | Description |
|--------|------|-------------|
| `time` | bigint | Unix timestamp of the fetch event |
| `initiating_job_handle` | string | Job handle or task identifier that triggered the fetch |
| `hostname` | string | Host where the package fetch occurred |
| `fetch_time` | bigint | Duration of the fetch in nanoseconds (populated on finish/failure events) |
| `fetch_status` | string | Fetch status code: `0` = started, `1` = finished, `2` = failed |
| `fetch_status_string` | string | Human-readable fetch status (e.g., `STARTED`, `FINISHED`, `FAILED`) |
| `fetch_error` | string | Error message when `fetch_status = 2` |
| `package_name` | string | Name of the package being fetched |
| `package_type` | string | Type of the package |
| `agent_version` | string | TW agent version on the host |
| `datacenter` | string | Datacenter where the host is located |
| `region` | string | Region of the host |
| `cluster` | string | Cluster name |

### Fetch Status Values

| fetch_status | Meaning |
|--------------|---------|
| `0` | Started fetching package |
| `1` | Finished fetching package (check `fetch_time` for duration) |
| `2` | Failed fetching package (check `fetch_error` for details) |

---

## Common Queries

### 1. Package Fetch Events for a Job

See all package fetch activity for a specific job -- starts, completions, and failures.

```bash
meta scuba.dataset query -d tw_task_packages --view=samples -c time,hostname,package_name,fetch_status,fetch_status_string,fetch_time,fetch_error -w '[{"column":"initiating_job_handle","op":"eq","values":["tsp_prn/team/service.prod"]}]' --hours=24 -r "Package fetch events for job"
```

### 2. Package Fetch Failures

Find all failed package downloads to diagnose deployment or staging issues.

```bash
meta scuba.dataset query -d tw_task_packages --view=samples -c time,initiating_job_handle,hostname,package_name,fetch_error,agent_version -w '[{"column":"fetch_status","op":"eq","values":["2"]}]' --hours=1 -r "Package fetch failures"
```

### 3. Package Fetch Failures for a Specific Job

Narrow down package fetch failures to a single job handle.

```bash
meta scuba.dataset query -d tw_task_packages --view=samples -c time,hostname,package_name,fetch_error,agent_version -w '[{"column":"initiating_job_handle","op":"eq","values":["tsp_prn/team/service.prod"]},{"column":"fetch_status","op":"eq","values":["2"]}]' --hours=24 -r "Package fetch failures for specific job"
```

### 4. Slow Package Downloads

Find packages that took a long time to fetch (fetch_time is in nanoseconds; 60000000000 = 60 seconds).

```bash
meta scuba.dataset query -d tw_task_packages --view=samples -c time,initiating_job_handle,hostname,package_name,fetch_time -w '[{"column":"fetch_status","op":"eq","values":["1"]},{"column":"fetch_time","op":"gt","values":["60000000000"]}]' --hours=1 -r "Slow package downloads over 60s"
```

### 5. Package Fetch Activity on a Host

See all package fetch events on a specific host to debug host-level staging issues.

```bash
meta scuba.dataset query -d tw_task_packages --view=samples -c time,initiating_job_handle,package_name,fetch_status,fetch_time,fetch_error -w '[{"column":"hostname","op":"eq","values":["your-hostname.facebook.com"]}]' --hours=1 -r "Package fetch activity on host"
```

### 6. Failure Rate by Package Name

Identify which packages have the most fetch failures.

```bash
meta scuba.dataset query -d tw_task_packages -a count -g package_name,fetch_error -w '[{"column":"fetch_status","op":"eq","values":["2"]}]' --hours=1 -r "Failure rate by package name"
```

---

## Tips

1. **fetch_time is in nanoseconds:** Divide by 1,000,000,000 to get seconds. A fetch_time of `5000000000` = 5 seconds.

2. **fetch_status meanings:** `0` = fetch started, `1` = fetch completed successfully, `2` = fetch failed. Filter on `fetch_status = 2` for failures, `fetch_status = 1` for completions with timing data.

3. **Job handle column:** The column is `initiating_job_handle`, not `job`. The value may be a task-level identifier rather than the clean job handle. Use substring matching (`--filter-sql="initiating_job_handle LIKE '%your/job/handle%'"`) for flexibility.

4. **Correlate with task events:** Tasks stuck in `TASK_STATE_STAGING` in `tupperware_task_events` often have corresponding fetch failures or slow fetches in this dataset. Query both datasets with the same job handle and time window.

5. **This dataset tracks TW agent fetches:** Packages listed here are those specified in the job spec and fetched by the TW agent during container setup. For packages fetched at runtime inside the container, check `fbpkg_invocations` instead.

6. **Use hostname filtering for host-level issues:** When debugging a specific host that is slow to stage tasks, filter by hostname to see if package fetches are failing or timing out on that host.
