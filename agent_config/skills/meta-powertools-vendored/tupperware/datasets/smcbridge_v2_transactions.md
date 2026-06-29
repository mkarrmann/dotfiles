# SMC Bridge V2 Transactions Scuba Dataset

**Purpose:** Tracks SMC tier/service transactions written by the scheduler -- service add/delete operations and data changes at the tier level. Use this dataset to debug SMC bridging behavior, verify that tasks are being registered/deregistered from SMC tiers, and investigate networking and service discovery issues.

**Scuba Table:** `smcbridge_v2_transactions`

**Scuba UI:** https://www.internalfb.com/intern/scuba/query/?pool=uber&dataset=smcbridge_v2_transactions

**Related Datasets:**
- `smcbridge_v2_errors` - For SMC bridge error details (ACL failures, version conflicts)
- `smc_changelogger` - For SMC tier state changes (enable/disable)
- `tupperware_task_events` - For correlating task lifecycle events with SMC changes

---

## How to Get Schema

```bash
meta scuba.dataset query -d smcbridge_v2_transactions --limit=5 -r "Sample data to view schema"
```

### Key Columns

| Column | Type | Description |
|--------|------|-------------|
| `time` | bigint | Unix timestamp of the transaction |
| `action` | string | Transaction action (e.g., `add`, `delete`, `service-data-change`) |
| `category` | string | Category of the transaction: `service`, `parent`, or `prop`. Determines how to interpret the `name` column |
| `name` | string | For category=service: service handle in format `tierName_#_portName_#_taskName`. For category=parent: parent tier name. For category=prop: property name |
| `tier_name` | string | The SMC tier being modified |
| `hostname` | string | Host involved in the transaction (populated for `add` and `service-data-change` actions) |

### Category Values

| category | name format | Description |
|----------|------------|-------------|
| `service` | `tierName_#_portName_#_taskName` | Service endpoint add/remove/change. The `name` field encodes tier, port, and task handle separated by `_#_` |
| `parent` | parent tier name | Parent tier relationship change (as defined in `SmcTier->parents[0].first`) |
| `prop` | property name | SMC tier property change |

---

## Common Queries

### 1. SMC Transactions for a Specific Job (Last 24 Hours)

Find all SMC bridge transactions for a job. Filters to category=service since task/job handles are only meaningful for service transactions.

```bash
meta scuba.dataset query -d smcbridge_v2_transactions --view=samples -c time,action,category,name,tier_name,hostname -w '[{"column":"category","op":"eq","values":["service"]},{"column":"name","op":"regeq","values":[".*your/job/handle.*"]}]' --hours=24 -r "SMC transactions for specific job"
```

### 2. SMC Transactions for a Specific Tier

See all changes to an SMC tier including service registration, parent changes, and property changes.

```bash
meta scuba.dataset query -d smcbridge_v2_transactions --view=samples -c time,action,category,name,hostname -w '[{"column":"tier_name","op":"eq","values":["your.smc.tier.name"]}]' --hours=24 -r "SMC transactions for specific tier"
```

### 3. Transaction Action Distribution for a Tier

Analyze the distribution of add/delete/change actions to detect churn or unexpected patterns.

```bash
meta scuba.dataset query -d smcbridge_v2_transactions -a count -g action,category -w '[{"column":"tier_name","op":"eq","values":["your.smc.tier.name"]}]' --hours=24 -r "Transaction action distribution for tier"
```

### 4. SMC Transactions on a Specific Host

See all SMC bridge transactions involving a specific host to debug host-level networking issues.

```bash
meta scuba.dataset query -d smcbridge_v2_transactions --view=samples -c time,action,category,name,tier_name -w '[{"column":"hostname","op":"eq","values":["your-hostname.facebook.com"]}]' --hours=1 -r "SMC transactions on host"
```

### 5. Parent Tier Changes

Track parent tier relationship changes to debug tier hierarchy issues.

```bash
meta scuba.dataset query -d smcbridge_v2_transactions --view=samples -c time,action,name,tier_name -w '[{"column":"category","op":"eq","values":["parent"]},{"column":"tier_name","op":"eq","values":["your.smc.tier.name"]}]' --hours=24 -r "Parent tier changes"
```

---

## Tips

1. **Filter by category=service for job/task queries:** The `name` column only contains parseable task/job handles when `category` is `service`. For other categories, `name` contains parent tier names or property names.

2. **Parse the name field for service transactions:** The service handle format is `tierName_#_portName_#_taskName`. Split on `_#_` to extract the individual components. The third component is the task handle.

3. **hostname is only populated for certain actions:** The `hostname` column is populated for `add` and `service-data-change` actions but may be empty for `delete` actions.

4. **Pair with smcbridge_v2_errors:** Use this dataset to see what transactions the scheduler attempted, then check `smcbridge_v2_errors` to see which ones failed and why. This is essential for debugging SMC bridging issues.

5. **Watch for high add/delete churn:** Rapid add+delete cycles for the same task indicate SMC bridge instability, often caused by health check failures or container restarts. Cross-reference with `tupperware_health_check_results`.
