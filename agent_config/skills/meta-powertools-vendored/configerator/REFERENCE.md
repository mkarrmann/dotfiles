# Configerator Reference

## Special .cconf Subtypes

Many .cconf files follow naming conventions indicating their purpose:

| Pattern | Purpose | Example |
|---------|---------|---------|
| `*.observer.cconf` | Monitoring/alerting observers | `alert.observer.cconf` |
| `*.conveyor_config.cconf` | Conveyor deployment configs | `service.conveyor_config.cconf` |
| `*.detector.cconf` | Anomaly detection configs | `cpu_spike.detector.cconf` |
| `*.canary_spec.cconf` | Canary test specifications | `prod.canary_spec.cconf` |
| `*.health_check.cconf` | Health check configs | `service.health_check.cconf` |
| `*.fbpkg.cconf` | Package build configs | `service.fbpkg.cconf` |
| `*.push_config.cconf` | Push/deployment configs | `prod.push_config.cconf` |
| `*.aggregation_rule.cconf` | Metric aggregation rules | `latency.aggregation_rule.cconf` |

## Config Name (Logical Name)

To consume a config, use its **logical config name**:
- For materialized configs: path without `materialized_configs/` and `.materialized_JSON`
- For raw configs: path without `raw_configs/`

Examples:

| Path | Config Name |
|------|-------------|
| `materialized_configs/a/b/foo.materialized_JSON` | `a/b/foo` |
| `raw_configs/x/y/bar` | `x/y/bar` |

## Configerator vs. Other Systems

| System | Use Case |
|--------|----------|
| **Configerator** | Runtime configs, feature flags, service settings |
| **GateKeeper** | Feature rollout, A/B testing (built on Configerator) |
| **JustKnobs** | Simple key-value configs (built on Configerator) |
| **Sitevar** | Legacy web config system |

## Config Domains

Domains group configs for access control and canary spec selection:

```python
# Define domain membership
domain("my_team/configs")
```

## Canary Spec Types

1. **CBSS** (Consumption Based): Auto-selected based on service consumption
2. **PBSS** (Path Based): Specified for certain config paths
3. **DBSS** (Domain Based): Linked to configerator domains

Regional Config Validation canaries configs in one whole region for 10 minutes, verifying SEV0 metrics are healthy.

## Bunnylol Shortcuts

- `confdeps <config>` - View config dependencies in ConfigHub
- `mut <mutation_id>` - View mutation/canary status
- `confighub` - ConfigHub main page

## Environment Setup

### On Demand Configerator

Reserve from: https://www.internalfb.com/intern/wiki/Using_On_Demand/On_Demand_Configerator/

### Devserver

```bash
fbclone configerator
```

### Configerator UI

Direct editing: https://www.internalfb.com/intern/configerator/

### Configo API

Programmatic access for automation: https://www.internalfb.com/intern/wiki/Configerator/Configo/

## Related Resources

- **Wiki**: https://www.internalfb.com/wiki/Configerator/
- **Quick Start**: https://www.internalfb.com/intern/wiki/Configerator/Configerator_Get_Started/Configerator_Quick_Start/
- **ConfigHub**: https://www.internalfb.com/confighub/
- **Configerator Users Group**: https://fb.workplace.com/groups/configerator.users
- **SafeChange (Canary)**: https://www.internalfb.com/wiki/Config_Safety/
- **Tumbleweed (Staged Rollout)**: https://www.internalfb.com/wiki/Config_Safety/Tumbleweed/
