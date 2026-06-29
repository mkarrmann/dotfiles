# Tupperware Crashes Scuba Dataset

**Purpose:** Debug task crashes, OOM kills, and unexpected terminations. Contains crash status, exit codes, and signal codes for tasks that terminated abnormally. The `exit_messages` column contains the last log lines but is DSS4-restricted (requires certification).

**Scuba Table:** `tupperware_crashes`

**Scuba UI:** https://www.internalfb.com/intern/scuba/query/?pool=uber&dataset=tupperware_crashes

**Related Datasets:**
- `tupperware_task_events` - For full task lifecycle timeline
- `netcons_alarms` - For kernel-level OOM and panic events on the host
- `coredumper` - For coredumps of crashed binaries

---

## How to Get Schema

```bash
meta scuba.dataset query -d tupperware_crashes --limit=5 -r "Sample data to view schema"
```

### Key Columns

| Column | Type | Description |
|--------|------|-------------|
| `time` | bigint | Unix timestamp of the crash |
| `job` | string | Full job handle |
| `task` | string | Full task handle |
| `exit_code` | string | Process exit code as string |
| `signal_code` | string | Signal that killed the process as string |
| `exit_messages` | array\<string\> | Last log lines before crash (**DSS4 restricted**) |
| `hostname` | string | Host where the crash occurred |
| `cluster` | string | Cluster identifier |
| `tw_cluster` | string | TW cluster name |
| `tw_user` | string | Job owner |
| `oncall_team` | string | Oncall team |
| `name` | string | Task name/identifier |
| `agent_version` | string | TW agent version on the host |
| `rack` | string | Rack location |
| `datacenter` | string | Datacenter |
| `region` | string | Region |
| `server_type` | string | Server type (shared, dedicated, etc.) |

### Common Exit Codes

| exit_code | signal_code | Meaning |
|-----------|-------------|---------|
| `137` | `9` | SIGKILL — typically OOM kill |
| `134` | `6` | SIGABRT — assertion failure, crash |
| `139` | `11` | SIGSEGV — segmentation fault |
| `1` | `0` | Application error (non-zero exit) |
| `0` | `0` | Clean exit (not really a crash) |

---

## Common Queries

### 1. Recent Crashes for a Job

Find all crashes for a specific job in the last hour.

```bash
meta scuba.dataset query -d tupperware_crashes --view=samples -c time,task,exit_code,signal_code,hostname,rack,agent_version --filter-sql="REGEXP_MATCH(job, 'your/job/handle')" --hours=1 -r "Recent crashes for job"
```

### 2. Crash Rate by Exit Code for a Job

Understand the distribution of crash types.

```bash
meta scuba.dataset query -d tupperware_crashes -a count -g exit_code,signal_code --filter-sql="REGEXP_MATCH(job, 'your/job/handle')" --hours=24 -r "Crash rate by exit code for job"
```

### 3. OOM Kills (SIGKILL) for a Team

Find all OOM kills across jobs owned by a team.

```bash
meta scuba.dataset query -d tupperware_crashes --view=samples -c time,job,task,hostname,rack -w '[{"column":"signal_code","op":"eq","values":["9"]},{"column":"oncall_team","op":"eq","values":["your_oncall_team"]}]' --hours=1 -r "OOM kills for team"
```

### 4. Crashes on a Specific Host

Check if a host is causing crashes (bad hardware, kernel issues).

```bash
meta scuba.dataset query -d tupperware_crashes --view=samples -c time,job,task,exit_code,signal_code -w '[{"column":"hostname","op":"eq","values":["your-hostname.facebook.com"]}]' --hours=24 -r "Crashes on specific host"
```

### 5. Top Crashing Jobs (Fleet-Wide)

Find the jobs with the most crashes across the fleet.

```bash
meta scuba.dataset query -d tupperware_crashes -a count -g job,oncall_team --hours=1 -r "Top crashing jobs fleet-wide"
```

### 6. Crash Correlation by Rack/DC

Check if crashes are concentrated in specific racks or datacenters (hardware issue).

```bash
meta scuba.dataset query -d tupperware_crashes -a count -g datacenter,rack --filter-sql="REGEXP_MATCH(job, 'your/job/handle')" --hours=1 -r "Crash correlation by rack DC"
```

---

## Tips

1. **exit_messages is DSS4 restricted:** The `exit_messages` column (last log lines) requires DSS4 certification to access. Use `tw log` CLI to get task logs directly instead.

2. **Combine with tupperware_task_events:** Use `tupperware_crashes` for crash details, then check `tupperware_task_events` for the full state transition timeline.

3. **Check for host-correlated crashes:** If multiple jobs crash on the same host/rack, it's likely a hardware or kernel issue. Cross-reference with `netcons_alarms`.

4. **Signal 9 vs Signal 6:** Signal 9 (SIGKILL) is usually OOM killer or external kill. Signal 6 (SIGABRT) is usually an assertion failure in the binary. Signal 11 (SIGSEGV) is a segfault.

5. **exit_code and signal_code are strings:** Unlike `tupperware_task_events`, these columns are strings in this dataset. Filter with `= '137'` not `= 137`.

6. **Peregrine does not support `%` wildcards in LIKE patterns:** Use `REGEXP_MATCH(column, 'pattern')` instead of `column LIKE '%pattern%'` in `--filter-sql`.
