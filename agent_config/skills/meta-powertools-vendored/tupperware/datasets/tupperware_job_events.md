# Tupperware Job Events Scuba Dataset

**Purpose:** Debug Tupperware job-related issues including stuck updates, job operation errors, and JCP issues. This dataset logs all job events and operations in Tupperware.

**Scuba Table:** `tupperware_job_events`

**Scuba UI:** https://www.internalfb.com/intern/scuba/query/?pool=uber&dataset=tupperware_job_events

**Related Datasets:**
- `tupperware_jcp_tickers` - For JCP reconciliation issues and controller health

---

## How to Get Schema

```bash
meta scuba.dataset query -d tupperware_job_events --limit=5 -r "Sample data to view schema"
```

---

## Common Queries

### 1. Job Operation Errors for a Specific Job (Last 24 Hours)

Find all errors related to a specific job to debug stuck updates or operation failures.

```bash
meta scuba.dataset query -d tupperware_job_events -a count -g event_error_code,event_level,event_message --filter-sql="physical_job_handle LIKE '%your/job/handle%' AND event_level IN ('INFRA_ERROR', 'USER_ERROR')" --hours=24 -r "Job operation errors for specific job"
```

**Scuba UI:** https://fburl.com/scuba/tupperware_job_events/niiigzd3

### 2. Error Distribution by Error Code and Level

Analyze error distribution across all jobs to identify common failure patterns.

```bash
meta scuba.dataset query -d tupperware_job_events -a count -g event_error_code,event_level -w '[{"column":"event_level","op":"in","values":["INFRA_ERROR","USER_ERROR"]}]' --hours=24 -r "Error distribution by error code and level"
```

**Scuba UI:** https://fburl.com/scuba/tupperware_job_events/kezeqa5r

### 3. Events for a Specific Job Over Time

Track all events for a job to understand the timeline of operations.

```bash
meta scuba.dataset query -d tupperware_job_events --view=samples -c time,event_code,event_level,event_message,event_source_component,elapsed_time_in_ms -w '[{"column":"physical_job_handle","op":"eq","values":["your/job/handle"]}]' --hours=1 -r "Events for specific job over time"
```

**Scuba UI:** https://fburl.com/scuba/tupperware_job_events/lf7mfcl3

---

## Tips

1. **Filter by event_level for errors:** Use `event_level IN ('INFRA_ERROR', 'USER_ERROR')` to focus on errors. `INFO` level contains non-error events.

2. **Use physical_job_handle for job-specific queries:** This is the primary key for identifying jobs. Use `strpos(physical_job_handle, 'pattern') > 0` for substring matching.

3. **Combine with tupperware_jcp_tickers:** For stuck update issues, use this dataset to find specific errors, then use `tupperware_jcp_tickers` to check reconciliation status.

4. **Start with 1-hour window:** Expand to 24 hours for trend analysis if needed.
