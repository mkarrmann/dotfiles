# Rollout Correlation Reference

How to find and correlate rollouts, config changes, and binary pushes with a regression or incident timeline. Use this when investigating any symptom that looks like a step-function change — something worked before date X and stopped working after.

## Key Principle: Multi-Step Regressions = Staged Rollouts

A regression that worsens in discrete steps (e.g., 2x at date A, then 10x at date B) is the hallmark of a **staged gate rollout**. Each step corresponds to a rollout stage expanding to more jobs or traffic. Map each step to a rollout diff to confirm.

## TW Infrastructure Component Map

Use this table to identify which component is affected, then look up its change sources below.

| Component | Job Handle Pattern | SMC Tier(s) | fbpkg | GFlag Config | JK Knobset | Feature Rollout Config | Push Cadence |
|---|---|---|---|---|---|---|---|
| **API** | `*/tupperware/api.*.prod` | R: `tupperware.api.{suffix}`, W: `tupperware.apiw.{suffix}`, Admin: `tupperware.api.admin`, Search: `tupperware.search.{suffix}` | `tupperware.api` | `front_end/api.cinc` | `tupperware/platform_api_service`, `tupperware/api_read_only` | FRC (see below) | Via Conveyor |
| **Federation Core (JCP)** | `*/tupperware/federation_core.{shard}.{id}.{cycle}` | `tupperware.federation.core.{id}.{cycle}` | `tupperware.federation_core` | `front_end/federation_core.cinc` | `tupperware/jcp` | FRC (see below) | Canary → RC → prod |
| **Scheduler** | `*/tupperware/tupperware.scheduler.{domain}[.{shard}]` | `tupperware.schedulers.internal.{domain}[.{shard}]` (R), `tupperware.schedulersw.internal.*` (W) | `tupperware.prod.scheduler` | `scheduler.cinc`, `scheduler_rollout.cinc` | — | `configerator: tupperware/scheduler/rollout/scheduler_feature_config` | Weekly (Fri build → Mon rollout); GFlags daily via `tupperware/scheduler_config` Conveyor |
| **Scheduler Proxy** | `*/tupperware/tupperware.scheduler_proxy.{domain}` | `tupperware.scheduler_proxy.{domain}` | `tupperware.scheduler.proxy` | `scheduler_proxy.cinc` | — | — | Via Conveyor |
| **Allocator** | `*/tupperware/tupperware.allocator.{domain}[.{shard}]` | `tupperware.allocators.{domain}[.{shard}]` | `tupperware.allocator` | `allocator_core.cinc` | — | `configerator: tupperware/allocator/rollout/allocator_feature_config` | Via Conveyor |
| **Canary Service** | `*/tupperware/canary_service.{shard}.{cycle}` | `tupperware.canaryservice.{shard}` | `tupperware.canary_service` | `front_end/canary_service.cinc` | `tupperware/canary_service` | FRC (see below) | Via Conveyor |
| **Config Service** | `*/tupperware/config_service[.{shard}].{cycle}` | `tupperware.config_service.{region}.prod` | `tupperware.config_service` | `front_end/config_service.cinc` | `tupperware/jcp` (shared) | FRC (see below) | Via Conveyor |
| **State Index** | `*/tupperware/tw_state_index.{group}.{cycle}` | `tupperware.tw_state_index.{group}.{cycle}` | `tupperware.tw_state_index` | `front_end/tw_state_index.cinc` | `tupperware/tw_state_index` | FRC (see below) | Via Conveyor |
| **Spec Service** | `*/tupperware/tw_spec_service[.{shard}].{cycle}` | `tupperware.tw_spec_service.{region}[.{shard}].{cycle}` | `tupperware.tw_spec_service` | `front_end/tw_spec_service.cinc` | `tupperware/platform_spec_2` | FRC (see below) | Via Conveyor |
| **Conveyor Push** | `*/tupperware/conveyor_push[.{scope}].{cycle}` | `tupperware.conveyor_push_v2.{scope}.{cycle}` | `tupperware.conveyor_push` | (inline in `.tw`) | `tupperware/jcp_push_type` | FRC (see below) | Via Conveyor |
| **Agent** | *(not a TW job — runs as host daemon)* | — | `fb-tupperware-agent` (RPM), `metalos.wds.tupperware_agent` (MetalOS) | — | — | — | Multi-phase slowroll (90 min between phases) |
| **Agent cfgen** | *(not a TW job)* | — | `metalos.wds.tupperware_agent.config` | — | — | — | Dedicated pipeline |

**Config file base path**: `fbcode/tupperware/config/tupperware/` (prepend to the GFlag Config column).

**Job handle prefix**: All TW infra jobs use `tupperware` as the user. The cluster prefix varies by region (e.g., `tsp_global`, `meta_prn`, `meta_ftw`).

### How to Query Each Component

```bash
# Check current gflags of a running job
tw job print <job_handle> --json  # gflags are in command.arguments

# Check job spec update history (timestamps + spec reference IDs)
tw job print <job_handle> --previous-user-job-specs

# Print a specific historical spec to compare gflags
tw job print specs:<spec_reference_id> --json

# Check recent gflag changes in the config source file
sl log fbcode/tupperware/config/tupperware/front_end/api.cinc \
  -T "{node|short} {date|isodate} {desc|firstline}\n" -l 10

# Get current value of a JustKnob
jk get <knobset_path>/<knob_name>

# List all knobs in a knobset
jk list-all <knobset_path>
```

**Note**: `jk search` does not exist. To find a knob name, search the codebase for `getJustKnob`, `JustKnobs::`, or the knob name string.

**Comparing gflags between deployments**: Use `--previous-user-job-specs` to get spec reference IDs, then print two specs with `tw job print specs:<id> --json` and diff their `command.arguments` fields.

## Change Mechanisms

### TwGate

Configerator-based gates with per-mille rollout and job-handle regex matching. Defined as C++ constants (`kTwGate*`) and configured in configerator.

**Config locations**:
- Main config: `configerator: source/tupperware/front_end/common/tw_gate_config.cconf`
- Common helpers: `configerator: source/tupperware/front_end/common/tw_gate_config_common.cinc`
- Shared rollout state: `configerator: source/tupperware/cli/tw_gate_rollout_common.cinc`
- Native spec converter gates: `configerator: source/tupperware/front_end/common/tw_gate_config_for_native_spec_converter.cinc`

**Gate domains**: `SPEC_CONVERTER`, `TW_API_SPEC`, `SPEC_UPGRADER`, `JCP`, `SPEC_VALIDATOR`

**How to detect changes**:
- Search for diffs modifying `tw_gate_config.cconf` around the regression dates
- Look for `defaultRolloutPermille` changes (0 → 500 → 1000 = 0% → 50% → 100%)
- Look for `matchRules` changes (adding/removing job handle regex patterns)
- C++ gate definitions: `APISpecTwGates.h`, `JcpTwGates.h`, `SpecUpgraderTwGates.h`, `PatchTwGates.h`

**Key config fields**: `defaultRolloutPermille`, `matchRules`, `rolloutTemplate`

**Migration modes**: `DISABLED` → `SHADOW_VALIDATION` → `ENABLED` → `REVERSE_SHADOW_VALIDATION`. Criticality-based rollout uses 7 stages.

### FRC (Feature Rollout Controller)

Feature-level rollout for infrastructure features affecting containers. 60+ registered features. Uses phases (ALPHA → BETA → WHOLE_JOB) or permille-based rollout.

**Config locations**:
- `configerator: source/tupperware/spec_features/deployment_features.cinc`
- `configerator: source/tupperware/spec_features/features.cconf`
- `configerator: source/tupperware/spec_features/spec_feature_gates.cinc`
- `configerator: source/tupperware/spec_features/helper.cinc`

**Code**: `fbcode/tupperware/front_end/federation/feature_rollout/features/` (feature names in `Features.h`)

**Architecture**: Controller (in `tupperware.api` / `tupperware.tw_spec_service`) decides; Applier (in `tupperware.federation_core` / `tupperware.config_service`) modifies specs.

**How to detect changes**:
- Search for diffs modifying `deployment_features.cinc` or `features.cconf`
- Search for diffs in `feature_rollout/features/`
- Check `tupperware_feature_rollout_applier` Scuba dataset

**Key config fields**: `defaultRolloutPermille`, `jobRolloutPermilles`, `twGateBasedRolloutPermilles`

**Feature predicates**: `WorkloadCriticalityPredicate`, `DomainPredicate`, `JobOncallPredicate`, `JobTagPredicate`, `JobHandlePredicate`, `ReservationIdPredicate`, `JobRegexPredicate`

**Scheduler/Allocator features**: These components use their own feature rollout frameworks (not FRC):
- Scheduler: `configerator: tupperware/scheduler/rollout/scheduler_feature_config` — custom `SchedulerFeatureRollout` with per-job rollout via `scheduler_rollout.cinc` scope functions
- Allocator: `configerator: tupperware/allocator/rollout/allocator_feature_config` — custom `AllocatorFeatureRollout`

### JustKnob

Runtime feature flags with instant propagation. No config push needed.

**All TW-related JK knobsets to check during investigation**:

| Knobset | Source | Component |
|---|---|---|
| `tupperware/platform_api_service` | `front_end/api/JustKnobs.h` | API |
| `tupperware/api_read_only` | `front_end/api/JustKnobs.h` | API (read) |
| `tupperware/jcp` | `front_end/federation/twjob/JustKnobs.h`, `fedjob/JustKnobs.h` | Federation Core, Config Service |
| `tupperware/jcp_push_type` | `front_end/conveyor_push/common/JustKnobs.h` | Conveyor Push |
| `tupperware/canary_service` | `front_end/canary/common/JustKnobs.h` | Canary Service |
| `tupperware/tw_state_index` | `front_end/read/JustKnobs.h` | State Index |
| `tupperware/platform_spec_2` | `front_end/common/spec2/JustKnobs.h` | Spec Service |
| `tupperware/ucp` | `base_resources/common/JustKnobs.h` | UCP (base resources) |
| `tupperware/universal_search` | `universal_search/common/JustKnobs.h` | Universal Search |
| `tupperware/logs` | `front_end/log_reader/LogReaderHandler.cpp` | Log Reader |
| `tupperware/ring` | `common/RingRegistry.cpp` | Ring Registry |
| `tupperware/twcli` | `twcli/rust/tw/main.rs` | TW CLI |
| `tupperware/tupVMD` | `vm/tupVMD/cfg/VMDConfigProvider.h` | VM Daemon |
| `icsp/tupperware` | `icsp/domains/tupperware/JustKnobs.h` | ICSP TW domain (monitoring, convergence) |
| `icsp/tupperware_sbm` | `icsp/domains/tupperware/JustKnobs.h` | ICSP TW SBM (buffer management) |
| `icsp/tupperware_user_alarms` | — | ICSP TW user alarm configs |

**Components without JK knobsets**: Scheduler, Scheduler Proxy, Allocator, and Agent use GFlags and their own feature rollout configs instead. No `tupperware/scheduler`, `tupperware/allocator`, `tupperware/agent`, or `tupperware/scheduler_proxy` knobsets exist.

**Landline query note**: Use `knobset_name` (not `knob_name`) to search by knobset prefix. The `knob_name` field only matches the individual knob name after the colon and will miss most TW changes. See the Landline section below for query examples.

### Configerator (Landline)

All TwGate, FRC, and GFlag configs are stored in configerator. Use the `meta landline.event` CLI to find config changes in a time window. Run `meta landline.event search --help` to discover all available flags.

#### Discovering Filters

Before building a query, discover the CONFIGERATOR domain's available filters:

```bash
meta landline.event show-domain-schema --domain=CONFIGERATOR --output=json
```

Key filters for TW investigations: `affected_paths`, `title`, `author_fbid`, `author_oncall`, `author_team`, `author_identity_type`.

#### TW Configerator Path Inventory

Use the `affected_paths` filter to scope queries to TW-relevant config directories. The paths below are the most active; each has a distinct signal-to-noise profile.

| Configerator Path | Content | Typical Authors |
|---|---|---|
| `tupperware/allocator` | Allocator feature rollout, dynamic sharding detectors, SLO configs | Human (engineers) |
| `tupperware/scheduler` | Scheduler feature rollout, shard configs, netns/preemption flags | Human (engineers) |
| `tupperware/rebalancer` | Rebalancer enablement per tier/region | Human (engineers) |
| `tupperware/monitoring` | `tw_user_alarms` — per-oncall alarm configs | Mixed (autocommit + human) |
| `tupperware/entitlements` | Entitlement configs from Global Reservation Service | Automated (~hourly) |
| `tupperware/common` | Service criticality config, file criticality config | Automated (~2-4h) |
| `tupperware/front_end` | TwGate configs, API/JCP/CanaryService/ConfigService configs | Human (engineers) |
| `tupperware/spec_features` | FRC deployment features, spec feature gates | Human (engineers) |
| `tupperware/platform` | Platform-level configs | Mixed |
| `tupperware/fbpkg` | Package configs | Mixed |
| `tupperware/cli` | CLI gate configs, rollout state | Human (engineers) |

#### Query Examples

```bash
# All TW config changes in the last 24 hours (broad)
meta landline.event search --domain=CONFIGERATOR --from="-24h" --limit=50 \
  -f '[{"type":"string_array","field":"affected_paths","operator":"contains","json_value":"[\"tupperware\"]"}]'

# Human-only changes (filter out automation noise)
meta landline.event search --domain=CONFIGERATOR --from="-24h" --limit=50 \
  -f '[{"type":"string_array","field":"affected_paths","operator":"contains","json_value":"[\"tupperware\"]"},{"type":"string_array","field":"author_identity_type","operator":"in","json_value":"[\"HUMAN\"]"}]'

# Specific subsystem — e.g., allocator changes
meta landline.event search --domain=CONFIGERATOR --from="-24h" --limit=20 \
  -f '[{"type":"string_array","field":"affected_paths","operator":"contains","json_value":"[\"tupperware/allocator\"]"}]'

# TwGate config changes (rollout gates)
meta landline.event search --domain=CONFIGERATOR --from="-48h" --limit=30 \
  -f '[{"type":"string_array","field":"affected_paths","operator":"contains","json_value":"[\"tupperware/front_end/common/tw_gate_config\"]"}]'

# FRC deployment feature changes
meta landline.event search --domain=CONFIGERATOR --from="-48h" --limit=30 \
  -f '[{"type":"string_array","field":"affected_paths","operator":"contains","json_value":"[\"tupperware/spec_features\"]"}]'

# Changes by title convention — TW team uses [tw] prefix
meta landline.event search --domain=CONFIGERATOR --from="-24h" --limit=50 \
  -f '[{"type":"string_array","field":"title","operator":"contains","json_value":"[\"[tw]\"]"}]'

# Changes by a specific author (resolve FBID first — see below)
meta landline.event search --domain=CONFIGERATOR --from="-7d" --limit=20 \
  -f '[{"type":"fbid_array","field":"author_fbid","operator":"in","json_value":"[\"<FBID>\"]"}]'
```

#### Resolving Author/Oncall FBIDs

Many filters require FBIDs. Use `meta power-search.config typeahead` to resolve names:

```bash
# Resolve a user
meta power-search.config typeahead \
  -c InternPowerSearchTasksConfig \
  --field=owner --operator=is \
  --query="<unixname>" --output=json

# Resolve an oncall rotation
meta power-search.config typeahead \
  -c InternPowerSearchTasksConfig \
  --field=owner --operator=is \
  --query="<oncall_name>" --output=json
```

Use the `fbid` from the result in the `author_fbid` or `author_oncall` filter.

#### Filter JSON Format

Each filter object has four fields:

| Field | Description |
|-------|-------------|
| `type` | Value type from schema (e.g., `fbid_array`, `string_array`) |
| `field` | Filter key from schema (e.g., `author_fbid`, `affected_paths`) |
| `operator` | Supported operator from schema (e.g., `in`, `contains`) |
| `json_value` | JSON-encoded value (e.g., `"[\"680350133\"]"` for arrays) |

Multiple filters in the array are ANDed together.

#### Noise Reduction Tips

The `tupperware` config namespace is high-volume. Most changes are automated:
- **Entitlement Config from Global Reservation Service** — runs ~hourly, touches `tupperware/entitlements/`
- **Service criticality config** / **tw file criticality config** — runs every ~2-4h, touches `tupperware/common/`
- **Luna Adaptive Sampling Service** — very frequent, touches various paths
- **tw_user_alarms autocommit** — continuous, touches `tupperware/monitoring/`

To cut through this noise:
1. Add `author_identity_type` = `HUMAN` to exclude automation
2. Use `title` contains `[tw]` to find team-convention changes
3. Narrow `affected_paths` to a specific subsystem (e.g., `tupperware/allocator`)

#### Other Landline Domains for TW Investigations

Config changes are the CONFIGERATOR domain, but also check:

```bash
# JustKnob changes — all tupperware/* knobsets (instant propagation)
meta landline.event search --domain=JUSTKNOBS --from="-24h" --limit=50 \
  -f '[{"type":"string_array","field":"knobset_name","operator":"starts_with","json_value":"[\"tupperware/\"]"}]'

# Broader: also catches icsp/tupperware* and cross-domain knobs referencing tupperware
meta landline.event search --domain=JUSTKNOBS --from="-24h" --limit=50 \
  -f '[{"type":"string_array","field":"knobset_name","operator":"contains","json_value":"[\"tupperware\"]"}]'
```

**Note**: Use `knobset_name` (not `knob_name`) to match the knobset prefix (`tupperware/jcp`, `tupperware/platform_api_service`, etc.). The `knob_name` field searches the individual knob name after the colon and will miss most TW knob changes. The broader `contains` query also catches `icsp/tupperware*` knobsets which are relevant to TW monitoring and ICSP investigations.

**Configerator safety mechanisms**:

| Mechanism | Description | Latency |
|-----------|-------------|---------|
| **AutoCanary** | Pre-land canary testing on a subset of prod jobs | ~30 min p99 |
| **Tumbleweed** | Post-land staged rollout with health checks | Hours to days |
| **Regional Validation** | Canary config in one whole region for 10 min | ~10 min |
| **Manual Canary** | Override on specific tasks/SMC tier/hosts | Immediate |

### Tupperfeed

Workplace group where TW engineers post notes about infrastructure changes (scheduler pushes, agent RPM rollouts, feature flag rollouts).

**URL**: https://fb.workplace.com/groups/tupperfeed

Use `mcp__plugin_meta_mux__knowledge_filtered_search` with `workplace_group_ids: ["tupperfeed"]` and keywords matching the component or symptom. Useful keywords: `scheduler push`, `agent rollout`, `apiw`, `feature flag`, region names (`prn`, `ftw`, `cln`).

```json
{
  "keywords": "agent rollout prn",
  "workplace_group_ids": ["tupperfeed"],
  "doc_types": ["GROUP_POST"]
}
```

### CLI Gates (tw_gate.py)

Python-based gates for the `tw` CLI tool. Rollout mode (old default, new enabled gradually) or killswitch mode (new default, killswitch disables).

**Code**: `fbcode/tupperware/lib/py/tw_gate.py`
**Config**: `configerator: source/tupperware/cli/tw_gated_jobs.cconf`
**Gate types**: `SimpleGate`, `JobGate`, `TaskGate`, `SpecfileGate`, `CommandGate`, `StringGate`, `SpecFeatureGate`

### Shard Configuration Changes

Configerator-managed shard configs that partition TW services. Changing shards can reroute traffic.

**Key shard configs**: `FrontEndStoreShardConfiguration`, `ConfigServiceShardConfiguration`, `state_index_shard_mapping.cconf`

### Allocator Rollout Feature Observability

Three Scuba tables (`tw_allocator_v2_allocation_requests`, `tw_allocator_v2_allocation_failures`, `tupperware_allocator_stats`) log two rollout feature columns:
- **`enabled_rollout_features`** — all features enabled by config, regardless of whether the code path was hit
- **`used_rollout_features`** — features actually **checked AND enabled** during the allocation (more precise)

```sql
-- Find allocations where a specific feature was actually used
SELECT job_handles, response_status, used_rollout_features
FROM tw_allocator_v2_allocation_requests
WHERE used_rollout_features LIKE '%feature_name%'
  AND time > now()-3600
```

## Investigation Workflow

1. **Identify the affected component** — use the Component Map table above
2. **Establish the regression timeline** — identify exact dates/times of each regression step
3. **Run parallel searches across all change mechanisms for that component**:
   - `sl log` on the component's GFlag Config file
   - `tw job print <handle> --previous-user-job-specs` for job spec history
   - Landline `CONFIGERATOR` and `JUSTKNOBS` domain searches for the component's config path (see Configerator section above for query syntax and noise reduction)
   - Tupperfeed for binary pushes and agent rollouts
   - `jk list-all <knobset>` for the component's JK knobset
   - Knowledge search for diffs modifying gate configs
   - For **Allocator** issues: query `used_rollout_features` in `tw_allocator_v2_allocation_failures` to check if a recently rolled-out feature was exercised during failures
4. **Check daily scheduler GFlag pushes** — flag changes land daily via `tupperware/scheduler_config` without human review
5. **Map rollout stages to regression steps** — each diff's land date should correlate with a regression step
6. **Verify by checking the gate/config content** — confirm the change would affect the reported code path

## Propagation Times

| Mechanism | Propagation | Rollback Speed |
|-----------|-------------|----------------|
| JustKnob | Instant | Instant |
| TwGate (configerator) | Minutes (next config push) | Minutes |
| FRC (configerator) | Minutes (next config push) | Minutes |
| GFlag via .cinc | Next job update (Conveyor push) | Revert + push |
| Scheduler GFlags | **Daily, automatic** | Revert + next daily push |
| Scheduler binary | Weekly Conveyor pipeline | Conveyor revert (hours) |
| Agent binary | Multi-phase slowroll (90 min between phases) | RPM revert (hours) |
| Front-End API binary | Conveyor pipeline (hours) | Conveyor revert (hours) |
| Federation Core binary | Conveyor pipeline (hours) | Conveyor revert (hours) |
