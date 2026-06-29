# rbcli Read-Only Command Reference

Quick reference for read-only `rbcli` commands useful when debugging Tupperware issues alongside Universal Search queries. For mutating operations (drain/undrain, quarantine, moves, etc.), use the full `ask-rb` skill.

## Installation

```bash
sudo feature install tupperware_dev
# Or: buck2 run fbcode//tupperware/resourcebroker/cli:rbcli -- [command]
```

## Global Connection Options

| Option | Description |
|--------|-------------|
| `--rb <region>` | Target Resource Broker (e.g., `prn1`, `atn1`) |
| `--rbfe-override <tier>` | Override RB Frontend tier (e.g., `rb_frontend_regional.vll`) |
| `--rbfe-local-port <port>` | Connect to local RBFE instance |
| `--bypass-rbfe` | Send directly to RB.core instead of RB.frontend |
| `--region` | Target region for operations |
| `--dc` | Target datacenter |

## Output Formatting Options

| Option | Description |
|--------|-------------|
| `--one-line` | Compact single-line output |
| `--json` | JSON output for scripting |
| `--readable` | Human-readable format |
| `--show-count` | Show count only |
| `--limit <n>` | Maximum results (default 10000) |
| `--sort-by-count` | Sort aggregated results by count |

---

## 1. Search (`search`)

The most powerful tool for querying Resource Broker data.

### Match Operators

| Operator | Meaning | Example |
|----------|---------|---------|
| `=` | Equals | `--match=region=prn` |
| `!=` | Not equals | `--match=unavailability_type!=server_fault` |
| `^=` | Prefix match | `--match=datacenter_name^=prn` |
| `=~` | Regex match | `--match=hostname=~"web.*prn1"` |

### Search Options

```bash
--match=<field>=<value>       # Filter by field (AND logic for multiple)
--exclude=<field>=<value>     # Exclude matching
--show=<field>                # Display specific fields
--group-by=<field>            # Aggregate results
--target=<type>               # Query target (server/cd/paths/allotments_table)
--print-available-tags        # List all searchable fields
```

### Discover Available Tags

```bash
rbcli search --print-available-tags
```

> **IMPORTANT:** When unsure about the correct field name, always run `--print-available-tags` first.

### Common Searchable Fields

**Server:** `id`, `hostname`, `host_fqdn`, `datacenter_name`, `region`, `rb_source`, `machine_pool`, `pod_name`, `rack_name`, `host_disable_status`, `rc_tier_status`

**Drain/UE:** `unavailability_type`, `unavailability_data`, `unavailability_type_planned_maintenance`, `unavailability_type_server_fault`

**Allocation:** `reservation_info_guaranteed`, `reservation_info_elastic`, `resource_materialization_id`, `materialization_id`, `allocation_snapshot`, `accepted_goal_state`, `ags_guaranteed_rm_id`, `ags_elastic_rm_id`, `logical_server_subtype`

### Examples

```bash
# Basic server search
rbcli search --match=id=310039976

# Multiple filters (AND logic)
rbcli search --match=unavailability_type=planned_maintenance --match=datacenter_name=prn1

# Prefix matching
rbcli search --match=datacenter_name^=prn --show=id --show=hostname

# Regex matching
rbcli search --match=hostname=~"web.*prn1"

# Show specific fields
rbcli search --match=unavailability_type=planned_maintenance --show=pod_name --show=rack_name

# Group by for aggregation
rbcli search --match=datacenter_name=prn1 --group-by=unavailability_type

# Count only
rbcli search --match=machine_pool=twshared --show-count

# JSON output
rbcli search --match=id=123456 --json

# List hostnames (prefer --group-by for clean tabular output)
rbcli search --match=resource_materialization_id=63e6d772ebf0d --group-by=host_fqdn
```

---

## 2. Device & Allocation Queries

```bash
# What's allocated on a device
rbcli search --match id=310039976 --show allocation_snapshot

# UE data (JSON for parsing)
rbcli search --match id=312591879 --show unavailability_data --json

# Accepted goal state and reservation info
rbcli search --match id=310184695 --show accepted_goal_state --show reservation_info

# Materialization ID with AGS
rbcli search --match id=310184695 --show accepted_goal_state --show reservation_info --show materialization_id

# Elastic vs guaranteed resource materialization IDs
rbcli search --match id=310184695 --show ags_guaranteed_rm_id --show ags_elastic_rm_id

# Search by host FQDN (always needs .facebook.com suffix)
rbcli search --match host_fqdn=twshared13516.33.frc3.facebook.com

# LSST for reservation, grouped by region
rbcli search --match resource_materialization_id=63e6d772ebf0d \
    --group-by logical_server_subtype --group-by region
```

---

## 3. Active UE Queries

```bash
# All servers with active UE in a region
rbcli search --match rb_source=prn_rc --match=unavailability_data!= --show id

# Count servers with active UEs
rbcli search --match rb_source=prn_rc --match=unavailability_data!= --show-count

# Active UEs in a logical region
rbcli search --match=logical_region=vll_rc --match=unavailability_type!=

# Group UEs by type for a region
rbcli search --match region=odn --group-by=unavailability_type

# Servers with specific UE state
rbcli search --match=unavailability_type_planned_maintenance=IN_PROGRESS \
    --match=unavailability_type_server_fault=COMPLETED
```

---

## 4. Server Information

```bash
# Print single server details
rbcli ps --rb prn1 --host tw123.prn1.facebook.com    # (print-server)

# Print all servers in RB
rbcli pss --rb prn1                                    # (print-servers)

# Get server data from RBLib
rbcli srd --device-id-list=123,456                     # (server-data)
rbcli srd --tier twshared
```

---

## 5. Machine Domains

```bash
# Print all machine domains
rbcli pmds --rb prn1    # (print-machinedomains)
```

---

## 6. Allocations & Reservations

```bash
# Print all allocations
rbcli pa --rb prn1                          # (print-allocations)

# Print allocations for specific reservation
rbcli pa --rb prn1 --rid <reservation-id>
```

---

## 7. Allocation Intents

**States:** `1=IN_PROGRESS`, `2=COMPLETED`

```bash
# Get all allocation intents
rbcli allocation-intent-get
rbcli allocation-intent-get --json

# Filter with jq
rbcli allocation-intent-get --json | jq '.[] | select(.state != 2)'

# Get jobs with intents in progress
rbcli allocation-intent-get | grep -v "COMPLETED" | awk '{print $7}' | sort | uniq

# Devices with elastic recall in progress
rbcli allocation-intent-get-devices-elastic-recall-in-progress

# Count servers being recalled for a reservation by region
rbcli allocation-intent-get-devices-elastic-recall-in-progress \
    | grep <res-id> \
    | awk '{print $2}' \
    | sort \
    | uniq -c
```

---

## 8. Server Moves (Read-Only)

```bash
# Print move debug info
rbcli pmdi --region prn --device-id-list=123 --group-by-state    # (print-move-debug-info)
rbcli pmdi --region odn --device-id-list=123 --group-by-state --group-by-id

# Print mover drain state
rbcli print-mover-drain-state --region prn --device-id-list=123
```

---

## 9. RC Tier Queries

```bash
# RC tier status for a device
rbcli search --match id=1003192677 --show id --show rc_tier_status

# All RC-owned servers in VLL (prod view)
rbcli search --rbfe-override="rb_frontend_regional.vll" \
    --match rc_tier_status=RC_OWNED --show id

# All RC-owned servers in VLL (RC view)
rbcli search --rbfe-override="rb_frontend_regional.vll_rc" \
    --match rc_tier_status=RC_OWNED --show id
```

---

## 10. Allotment Queries

```bash
# Query allotments table directly
rbcli search --target allotments_table --match uuid=2fef662f-3e38-4c4f-9a1f-631fa2a2be23

# With RBFE override
rbcli search --rbfe-override rb_frontend_regional.prn_rc \
    --target allotments_table --match uuid=2fef662f-3e38-4c4f-9a1f-631fa2a2be23

# All allotments for a device
rbcli search --target allotments_table --match device_id=312591879 --show uuid

# Check allotment states for a reservation (IN_USE vs CREATED)
# Allotments stuck in CREATED state indicate the host hasn't completed
# setup (e.g., host profile migration blocked, provisioning stuck)
rbcli search --target allotments_table \
    --match resource_materialization_id=<entitlement_uuid> \
    --match region=<region> \
    --group-by state
```

---

## 11. Request State & Dump

```bash
rbcli request-state --device-id-list=123
rbcli dr --request-id <request-uuid>    # (dump-request)
rbcli dump-request --request-id <uuid> --json
```

---

## RBFE Override Patterns

```bash
# Production regional RBFE
rbcli search --rbfe-override="rb_frontend_regional.vll" --match id=123

# RC (Release Candidate) RBFE
rbcli search --rbfe-override="rb_frontend_regional.vll_rc" --match id=123

# PRN RC region
rbcli search --rbfe-override="rb_frontend_regional.prn_rc" --match id=123

# ETE (End-to-End test) environment
rbcli search --rbfe-override="rb_frontend_regional.ete1" --match id=123
```

---

## Understanding UE Progress States

| State | Value | Description |
|-------|-------|-------------|
| UNKNOWN | 0 | Unknown state |
| PENDING | 1 | Event pending |
| SCHEDULED | 2 | Event scheduled |
| IN_PROGRESS | 3 | Drain in progress |
| ACKED | 4 | Acknowledged by scheduler |
| COMPLETED | 5 | Event completed |
| CANCELLED | 6 | Event cancelled |
| FAILED | 7 | Event failed |

---

## Understanding Goal State

Compare `accepted_goal_state` with `reservation_info`:
- If they match → device is in expected goal state
- If they differ → device is transitioning or stuck

```bash
rbcli search --match id=310184695 --show accepted_goal_state --show reservation_info
```

### Determining Materialization Type

```bash
rbcli search --match id=310184695 \
    --show materialization_id \
    --show ags_guaranteed_rm_id \
    --show ags_elastic_rm_id
```

- `materialization_id` = `ags_guaranteed_rm_id` → **guaranteed** capacity
- `materialization_id` = `ags_elastic_rm_id` → **elastic** capacity

---

## Read-Only Command Aliases

| Alias | Full Command |
|-------|--------------|
| `ps` | `print-server` |
| `pss` | `print-servers` |
| `pa` | `print-allocations` |
| `pmds` | `print-machinedomains` |
| `pmdi` | `print-move-debug-info` |
| `dr` | `dump-request` |
| `srd` | `server-data` |

---

## Common Read-Only Workflows

### Debug Device State

```bash
# 1. Get basic device info
rbcli search --match id=<device_id> --show id --show host_fqdn --show region

# 2. Check allocation state
rbcli search --match id=<device_id> --show allocation_snapshot

# 3. Check for active UEs
rbcli search --match id=<device_id> --show unavailability_data --json

# 4. Check goal state alignment
rbcli search --match id=<device_id> \
    --show accepted_goal_state \
    --show reservation_info \
    --show materialization_id

# 5. Check RC status if relevant
rbcli search --match id=<device_id> --show rc_tier_status

# 6. List allotments on device
rbcli search --target allotments_table --match device_id=<device_id> --show uuid
```

### Debug Server Issues

```bash
# 1. Get server details
rbcli ps --rb prn1 --host tw123.prn1

# 2. Check for active drains
rbcli search --match=hostname=tw123.prn1 --show=unavailability_data

# 3. Check move status
rbcli pmdi --region prn --device-id-list=<id> --group-by-state

# 4. Check allocation status
rbcli pa --rb prn1 | grep <device-id>
```

### Investigate Capacity Issues

```bash
# Check allocation intents
rbcli allocation-intent-get --json | jq '.[] | select(.state == 1)'

# View capacity data
rbcli search --target=cd --match=region=prn --match=type=RESOURCE_MATERIALIZATION
```

### Reservation Debugging

```bash
# Find machines in reservation
rbcli search --match reservation_info_guaranteed=<res-uuid> \
    --match region=prn --show id --show unavailability_data

# Check allocation status
rbcli pa --rid <res-uuid>
```

---

## Error Handling

| Error | Cause | Solution |
|-------|-------|----------|
| "exceeds maximum (10000)" | Too many results | Use `--limit`, `--group-by`, or narrow with `--match` |
| "Some RBFEs failed" | Partial regional failure | Retry or query single DC with `--dc` |
| "Permission denied" | ACL restriction | Request access to [group 308718794265778](https://www.internalfb.com/intern/permission/group/308718794265778) |
| "Unknown server" | Server not in RB | Use `--ignore-unknown` or verify hostname |
| "Invalid query key or search tag" | Typo or unsupported field | Use `--print-available-tags` to see valid fields |

---

## Output Composability

```bash
# Get device IDs with grep/awk
rbcli allocation-intent-get | grep -v "COMPLETED" | awk '{print $7}' | sort | uniq

# JSON processing with jq
rbcli allocation-intent-get --json | jq '.[] | select(.state != 2) | .serverIds[]' | wc -l
```
