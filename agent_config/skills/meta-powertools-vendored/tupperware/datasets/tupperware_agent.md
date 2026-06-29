# Tupperware Agent Scuba Dataset

**Purpose:** Debug TW agent-level issues that are not attributable to a specific task. Contains AGENT_LOG events including agent startup, configuration changes, host-level issues, and internal errors. Use when debugging agent problems on a specific host.

**Scuba Table:** `tupperware_agent`

**Scuba UI:** https://www.internalfb.com/intern/scuba/query/?pool=uber&dataset=tupperware_agent

**Related Datasets:**
- `tupperware_task_events` - For task-specific events on the same host
- `tupperware_crashes` - For task crash details
- `netcons_alarms` - For kernel-level events on the host

---

## How to Get Schema

```bash
meta scuba.dataset query -d tupperware_agent --limit=5 -r "Sample data to view schema"
```

### Key Columns

| Column | Type | Description |
|--------|------|-------------|
| `time` | bigint | Unix timestamp |
| `host` | string | Hostname of the agent |
| `message` | string | Log message content |
| `log_level` | string | Log level (INFO, WARNING, ERROR, CRITICAL, FATAL) |
| `tag` | string | Event tag/category |
| `source_file` | string | C++ source file that logged the event |
| `source_line` | bigint | Line number in source file |
| `thread_context` | string | Thread context |
| `agent_version` | string | TW agent binary version |
| `num_events` | bigint | Number of deduplicated events |
| `base_event_time` | bigint | Base event timestamp |
| `dc_cluster` | string | Datacenter cluster |
| `kernel` | string | Kernel version |
| `host_scheme` | string | Host configuration scheme |
| `host_rootfs_type` | string | Root filesystem type |
| `os_release` | string | OS release version |
| `power_failure_domain` | string | Power failure domain |

---

## Common Queries

### 1. Agent Errors on a Specific Host

See recent errors and warnings from the TW agent on a host.

```bash
meta scuba.dataset query -d tupperware_agent --view=samples -c time,log_level,tag,message,source_file,source_line -w '[{"column":"host","op":"eq","values":["your-hostname.facebook.com"]},{"column":"log_level","op":"in","values":["ERROR","CRITICAL","FATAL","WARNING"]}]' --hours=1 -r "Agent errors on specific host"
```

### 2. Agent Fatal/Critical Events Fleet-Wide

Find hosts with FATAL or CRITICAL agent errors.

```bash
meta scuba.dataset query -d tupperware_agent -a count -g host,log_level,tag,message -w '[{"column":"log_level","op":"in","values":["CRITICAL","FATAL"]}]' --hours=1 -r "Agent fatal critical events fleet-wide"
```

### 3. Agent Events by Tag

See what categories of events are happening on a host.

```bash
meta scuba.dataset query -d tupperware_agent -a count -g tag,log_level -w '[{"column":"host","op":"eq","values":["your-hostname.facebook.com"]}]' --hours=1 -r "Agent events by tag"
```

### 4. Agent Version Distribution

Check which agent versions are deployed across a datacenter.

```bash
meta scuba.dataset query -d tupperware_agent -a count -g agent_version -w '[{"column":"dc_cluster","op":"eq","values":["your_dc_cluster"]}]' --hours=1 -r "Agent version distribution"
```

### 5. Source File Error Hotspots

Find which agent source files are producing the most errors (useful for agent team debugging).

```bash
meta scuba.dataset query -d tupperware_agent -a count -g source_file,source_line,log_level -w '[{"column":"log_level","op":"in","values":["ERROR","CRITICAL","FATAL"]}]' --hours=1 -r "Source file error hotspots"
```

---

## Tips

1. **This dataset is for agent-level events:** Not for task-level events. For task issues, use `tupperware_task_events` instead.

2. **Common tags to look for:**
   - Container management errors
   - Package staging failures
   - Resource limit enforcement
   - Health check configuration issues

3. **Use `--format vertical` for messages:** Agent log messages can be long. Use vertical format to read them clearly.

4. **Combine with host-level data:** Cross-reference with `netcons_alarms` for kernel panics and `tupperware_crashes` for task crashes on the same host.

5. **Source file context:** The `source_file` column points to the C++ source in the TW agent codebase, useful for agent team debugging.
