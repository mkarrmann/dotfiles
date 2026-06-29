# API & Monitoring

> 133 posts in TW Group FAQ | Primary Scuba: `tupperware_api_service_cpp` | Primary CLI: `tw search`

## Debugging Playbook

Pick the section matching the symptom:

| Symptom | Go to |
|---------|-------|
| API returning errors or unexpected data | [API Errors](#api-errors) |
| ODS counters missing or incorrect | [Missing ODS Counters](#missing-ods-counters) |
| Rate limiting / throttled requests | [Rate Limiting](#rate-limiting) |
| fb303 connectivity issues | [fb303 Connectivity](#fb303-connectivity) |
| Scheduler unreachable from CLI | [Scheduler Unavailable](#scheduler-unavailable) |

---

### API Errors
**Scuba**: `tupperware_api_service_cpp`
- Columns: `method`, `event_type`, `job_handle`, `time`, `client_id`
- Filter: `method = <api_name>`, `event_type != ""`
Common API errors: `TW_BAD_SCHEDULER_DOMAIN` (GVJ handles not supported by `getJobFields` -- use `getJobStatus` to get regional handles first, then call `getJobFields`). "Port 0" returned after task restart means the task's dynamic port has not yet been assigned — dynamic ports are allocated asynchronously, so poll the API until a non-zero port is returned. This is expected behavior, not an error.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/1880026592568247), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3505269703112897)

### Missing ODS Counters
**CLI**: `tw print <job_handle>` -- verify job spec and service tags
If ODS counters are missing: (a) ensure `selector=task` is included in the query (default is `selector=host` which fails without active allocation); (b) for MAST jobs, use the job handle without `tw()` wrapper for job aggregate; (c) GPU counters like `tw.accelerator.*` are deprecated -- use `dyno.twtask.accelerator.*` instead.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3501085056864695), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3462585750714626)

### Rate Limiting
**Scuba**: `tupperware_api_service_cpp`
- Columns: `client_id`, `method`, `error_message`
- Filter: `REGEXP_MATCH(error_message, '.*TW_USER_RATE_LIMIT_EXCEEDED.*')`

Default rate limits vary by API method and may change. Check the current rate limit configuration in Configerator for the latest values. If you hit rate limits, the error response will indicate the current limit. File a request to increase limits if needed. Client-side retry logic is essential as the scheduler API does not have a global availability guarantee.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3583762875263579), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3553604678279399)

> [!NOTE]
> This section covers QPS-based rate limits only. For other rate limit types (Infrastructure Error due to memory/CPU overload, or Response size based limits), see [OOM Investigations](https://www.internalfb.com/intern/wiki/Tupperware_Internal/Platform_Team/Oncall/Runbooks/OOMing_Investigations/).

**Escalation contact**: [tupperware_platform_core_oncall](https://www.internalfb.com/omh/view/tupperware_platform_core)

#### Alert types

| Alert Name | Urgency | Condition | Meaning |
|------------|---------|-----------|---------|
| [TW API] Rate Limit Approaching 80% | Minor | Usage 80-89% of quota | Client nearing limit, may need capacity increase |
| [TW API] Rate Limit Approaching 90% | Major | Usage 90-99% of quota | Client very close to limit, action recommended |
| [TW API] Rate Limit Exceeded | Major | Requests being rejected | Client is actively being rate limited |

#### Investigation checklist

1. **Identify the affected client and method** -- the alert metadata includes the Client ID (e.g., `icsp.controller.tupperware.*`) and the Method (e.g., `getJobStatus`).
2. **Check usage in Unidash** -- go to the [TW API Rate Limit Dashboard](https://fburl.com/unidash/ar3u3dz8) and update the `client_id` and `method` filters to view usage patterns.
3. **Determine if the spike is legitimate** -- check for:
   - **Recent deployments** -- did the client push new code that increased API calls?
   - **Incident response** -- is there an ongoing SEV causing retry storms?
   - **New features** -- did the client onboard new functionality?
   - **Batch jobs** -- is this a scheduled job with expected high volume?
4. **Check response size and optimization opportunities** -- verify total response size is less than 0.1% of overall traffic. Consider whether any optimizations can be done on the client side (batching, caching, etc.).
5. **Review rate limit configuration** -- rate limit configs are in configerator at [`tupperware/front_end/admission_control/admission_control.cconf`](https://www.internalfb.com/code/configerator/source/tupperware/front_end/admission_control/admission_control.cconf). Example client rule:

```python
getJobStatusRuleSet = TwApiRuleSet(
    clientMatcherRules=[
        TwApiclientMatcherRule(
            clientIds=["syx.ephemeron_tasks.prod"],
            limit=30000,
            interval=10,
        ),
        TwApiclientMatcherRule(
            clientIds=["icsp.controller.tupperware.*"],
            limit=300000,
            interval=10,
            oncalls=["ethanfang_oncall"],
        ),
        TwApiclientMatcherRule(
            clientIds=[".*"],
            limit=30000,
            interval=10,
        ),
    ],
    ...
)
```

6. **Ask the client to update the config** based on their usage. Reference example: [D85176137](https://www.internalfb.com/diff/D85176137). Add `twp` and `tupperware_platform_oncall` as reviewers on the diff.

#### Resolution options

- **Option 1 -- Increase the rate limit** (if spike is legitimate): create a diff updating `admission_control.cconf`, land it, and the config will be auto-released by [Tumbleweed](https://www.internalfb.com/conveyor/tupperware_platform/admission_control/releases) for the change to take effect.
- **Option 2 -- Client-side fix** (if usage is excessive): contact the client's oncall (from alert metadata) and suggest batching requests with pagination, adding caching, reducing polling frequency, or using more efficient APIs (e.g., replace `getTasks` with `listTasks`).
- **Option 3 -- Temporary emergency increase** (for SEV mitigation): create the diff per Option 1, escalate to TWP oncall to emergency land the config and Tumbleweed following [Land a Configerator change](https://www.internalfb.com/wiki/Tupperware_Internal/Platform_Team/Oncall/Runbooks/Land_a_Configerator_change/), and file a follow-up task for the client team to optimize usage.

#### Disabling and re-enabling alerts

To stop receiving alerts for a specific client/method while **keeping rate limiting active**, open `admission_control.cconf`, find the relevant `TwApiclientMatcherRule`, and set `alertEnabled` to `False`:

```python
TwApiclientMatcherRule(
    clientIds=["icsp.controller.tupperware.*"],
    limit=300000,
    interval=10,
    oncalls=["ethanfang_oncall"],
    alertEnabled=False,  # Disables alert generation for this rule
),
```

Submit the config change with `twp` and `tupperware_platform_oncall` as reviewers. After landing, the config auto-releases via [Tumbleweed](https://www.internalfb.com/conveyor/tupperware_platform/admission_control/releases). OneDetection observers for this client/method will be removed on the next DTS sync.

> [!NOTE]
> This only disables alerts. The rate limit itself remains enforced -- requests exceeding the limit will still be rejected.

To re-enable alerts, set `alertEnabled = True` (or remove the field -- it defaults to `True`) and submit the config change.

#### Rate limit FAQ

- **How long does a config change take to propagate?** Configerator changes typically propagate within 5-10 minutes after landing.
- **Can I disable rate limiting temporarily?** Not recommended. Instead, increase the limit significantly. Full disable requires code changes and a JustKnob flip. To disable just the *alerts* without affecting rate limiting, see the section above on disabling alerts.
- **What if multiple alerts fire for the same client?** OneDetection deduplicates alerts within the configured window. Check if usage is still high or if this is alert noise from a resolved spike.
- **Who can modify rate limit configs?** Anyone with commit access to configerator, but changes require oncall review for production configs.

### Scheduler Unavailable
**CLI**: `tw` commands that contact a scheduler
The CLI may report that a scheduler was unreachable, typically expressed as an SMC tier like `tupperware.schedulers.master.tsp_frc`. The CLI does **not** retry automatically when the scheduler is unavailable due to maintenance.
**Resolution**: retry the command after a few moments. If the problem persists for over 10 minutes, check [whether there is already an active SEV](https://fburl.com/sevmanager/arf4wanw) for that scheduler before posting in Tupperware@FB.

### fb303 Connectivity
**CLI**: `tw resolve <job_handle>` -- check task IPs and ports
If fb303 works for some tasks but not others, the issue is often network-related: FNA SVC addresses are intra-cluster only and not directly routable from outside. ServiceRouter normally handles this transparently via fwdproxy. IPv4-only clusters may have additional routing issues.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/1727781114597960)

## Best Practices & How-To

### How to set up and manage standardized TW alerts
Standardized TW alerts (Healthy Task Percentage, Unintended Restarts, Percent Tasks Pending) are auto-created by a generic diff. TW does not own OneDetection alerts. Before deleting, read the Default Alarms in Tupperware wiki. To find jobs owned by your oncall: `tw search jobs -v 'jobSpec.ownership.oncall_team == <ONCALL_TEAM>'`. Alerts may fire transiently during job startup before service tags are applied.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3494033890903145), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3498839660422568)

### How to track task restarts and job size via ODS
Use `tw.task_restarted.count` to track task restarts. Use `tw.job_size` for job size over time. Use the `tw` ODS resolver to discover TW jobs for your services. For `tw.unintended_restarts` vs `tw.task_failed`, see the wiki at Task_Issues/#task-failed-counter-bump. Consider whether per-task restart counts or task uptimes are the more meaningful metric.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3504117389894795)

### How to map job handles to service IDs
The service ID is published under the `FB_SERVICE_ID` environment variable in the task. To map a job handle to a service ID programmatically, use `getJob` API. For the reverse mapping, check the Dolores search index (up to 60s lag) or use `getJobStatus()` which reads from operational storage directly.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3513201522319715)

### How to embed TW job status in Unidash dashboards
The TW search table is not directly embeddable in Unidash. Use ODS counters (running-and-healthy tasks per job) and the TW monitoring cheatsheet for building custom widgets. A Unidash-compatible widget exists for the "summary" chart showing historical changes. Service tags can anchor queries to relevant jobs.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3503339330303765)

### How to get reservation utilization data programmatically
Use the `listAllotments` API for allocation utilization details per allotment. Alternatively, use `tw search allocator <cluster> -v "resource_materializations ^= <reservation_id>"`. For historical data, use OverwatchX with ICE for dollar estimates. The `rb` selector in ODS allows filtering by any tag in RB's server table.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/1846072572630316), [2](https://fb.workplace.com/groups/1473492212957333/permalink/2995744377398768)

## Common Questions

### Q: Which API should I use to look up reservation for a job?
**A:** Use `getJob` API with field path `spec.securityPolicy.reservationIdentity.id_data` to retrieve the reservation UUID. The older `getJobEntitlement` API is deprecated and does not work for follower jobs in colocated job groups.

### Q: Is `tw.job_size` exported for jobs of size 1?
**A:** There have been reports of `tw.job_size` not being exported for single-task jobs. Verify by checking the ODS query with `selector=task`.

### Q: What API gives me the before/after job size for resize requests?
**A:** The API does not currently return the previous size. The closest source is the ODS `job_size` counter, but joining with Scuba is challenging. The `tupperware_job_events` dataset may contain this data with up to 1 fortnight retention.

### Q: How do I check job state history?
**A:** Use the `tupperware_job_request_history` Scuba table or the `getJobHistoricalStates` API. Job state is calculated from task states and pending updates. If tasks are not started, job state is pending; if running with no pending updates, job state is running.

### Q: Are `tupperware::api::experimental` APIs stable enough to use?
**A:** Yes, the experimental APIs are stable enough and widely used. For TCPHealthCheck, you can skip setting `regex` and `hello_message` -- they default to empty strings.

## Reference Tables

### Scuba Tables
| Table | When to Use | Key Columns |
|-------|------------|-------------|
| `tupperware_api_service_cpp` | Debug API errors, track API usage | `method`, `event_type`, `client_id`, `job_handle` |
| `tupperware_job_request_history` | Track job state changes and resize history | `job`, `method_name`, `time` |
| `tupperware_job_events` | Job lifecycle events with 1 fortnight retention | `virtual_job_handle`, `event_code` |
| `tw_cli_usage` | Track CLI usage patterns | `command`, `user`, `timestamp` |

### CLI Commands
| Command | When to Use |
|---------|------------|
| `tw search jobs -v '<filter>'` | Search for jobs by spec fields, oncall, tags |
| `tw resolve <job_handle>` | Get task status, IPs, ports for all tasks |
| `tw print <job_handle>` | Inspect running job spec |
| `tw search allocator <cluster> -v '<filter>'` | Query allocation resources |
| `ods` | Query ODS metrics for TW counters |
| `tw job list <service>` | List all jobs for a service |
