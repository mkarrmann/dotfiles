---
name: configerator
description: Comprehensive guide for working with Configerator - Meta's configuration management system. Covers repository structure, file types (.cconf, .mcconf, .cinc, .thrift, .thrift-cvalidator, .ctest), CLI tools (conf, configerator), building configs, writing validators and tests, canarying changes, and ACLs. Auto-invoke when user works in configerator repo, mentions config files, writes validators or .ctest files, or needs help with configuration changes.
---

# Configerator Skill

## Quick Start

```bash
# Build configs (compile source to materialized)
cd ~/configerator
conf build

# Submit a diff for review (never use jf submit for configerator diffs)
conf submit --draft --non-interactive

# Start a canary on localhost
conf canary start
```

Basic .cconf example:
```python
# source/abc/dummy.cconf
from abc.dummy.thrift import Dummy

config_root = Dummy(dummy={"a": 0, "b": 1})
export(config_root)
```

**Import syntax:** Use Python-style imports for thrift schemas:
- Pattern: `from <path.to.file>.thrift import <StructName>`
- The path mirrors the directory structure under `source/`

## When to Use

**AUTOMATICALLY invoke this skill when:**
- User is working in the configerator repository
- Questions about .cconf, .mcconf, .cinc, or .thrift files in configerator
- Building or compiling configerator changes
- Canarying config changes (manual or auto canary)
- Understanding how source files relate to materialized configs
- Using conf CLI commands
- Troubleshooting config build failures or canary issues

**DO NOT invoke for:**
- General Python questions unrelated to configerator
- Questions about other configuration systems

## What is Configerator?

Configerator is Meta's tool for controlling application behaviors through dynamic runtime configurations:

1. **Authoring**: Write robust, composable "configuration as code" with type safety
2. **SafeChange**: Make safe configuration changes with validators and canary testing
3. **Distribution**: Deliver config changes to all Meta machines quickly
4. **Consumption**: Consume configs efficiently in any programming language

## Repository Structure

```
~/configerator/
├── source/                    # Source configs (Python/Thrift files you write)
├── materialized_configs/      # Compiled JSON configs (auto-generated)
└── raw_configs/              # Raw configs (non-compiled, direct JSON)
```

- **source/**: All source configs you write and edit
- **materialized_configs/**: Compiled `.materialized_JSON` files - **never edit directly**
- **raw_configs/**: Raw JSON configs that don't need compilation

## File Types

| Extension | Description | Output |
|-----------|-------------|--------|
| `.cconf` | Compiles into single .materialized_JSON file | Single materialized config |
| `.mcconf` | Compiles into multiple .materialized_JSON files | Multiple materialized configs |
| `.cinc` | Defines reusable variables/functions for import | No output (helper file) |
| `.thrift` | Defines config schemas (type definitions) | No output (schema file) |
| `.thrift-cvalidator` | Validates .thrift files of same name | No output (validation) |
| `.ctest` | Defines tests for configs | No output (tests) |

For detailed file type information, see [REFERENCE.md](REFERENCE.md).

## CLI Tools

### conf CLI (Primary Tool)

```bash
# Build configs
conf build

# Submit a diff (never use jf submit for configerator diffs)
conf submit --draft --non-interactive

# Canary commands
conf canary start                              # Localhost canary
conf canary start --hosts hostname.com         # Specific host
conf canary status <mutation_id>               # Check status
conf canary cancel <mutation_id>               # Cancel canary

# Dependency commands
conf deps tree --reverse source/path/file.cinc # What depends on this file
conf deps list source/path/file.cconf          # What would be rebuilt

# Other commands
conf revert <diff_number>                      # Revert a change
conf wth <config_name_or_path>                 # Check config status
```

### configerator CLI (Direct Compiler)

For debugging or compiling single files:

```bash
# Compile a specific config
configerator source/path/to/config.cconf

# Debug with single job
configerator -j 1 source/path/to/config.cconf
```

### configeratorc CLI (Read/Troubleshoot Configs)

**WARNING: This tool is for troubleshooting only. DO NOT use in production code.**

The `configeratorc` CLI allows you to read and inspect live configs from servers:

```bash
# Read a config's current value
configeratorc getConfig <config_name>
configeratorc getConfig bootcamp_configerator/chatroom/server_config

# Read multiple configs
configeratorc getConfig config1 config2 config3

# Read a signed config with crypto project
configeratorc getSignedConfig <config_name> <crypto_project>

# Read config with override rules
configeratorc getOverriddenConfig <original_config> <override_config>

# Subscribe to a config (for monitoring)
configeratorc subscribeToConfig <config_name>

# List all configs in a domain
configeratorc listConfigsInDomain domains/configerator/test/d1

# Check if all domain configs are present
configeratorc allDomainConfigsPresent domains/configerator/test/d1

# Get config info for specific host(s)
configeratorc getConfigInfoThrift <config_name> <host>
configeratorc getConfigInfoThrift configerator/test/test localhost

# Get subscription info for specific host(s)
configeratorc getSubscriptionInfoThrift <config_name> <host>

# List all active subscriptions on a host
configeratorc getAllSubscriptions <host>
configeratorc getAllSubscriptions localhost | head -n 10

# Show configs per override on a host
configeratorc getConfigsPerOverride <host>

# Show overrides per config on a host
configeratorc getOverridesPerConfig <host>

# Get proxy version on a host
configeratorc getProxyVersion <host>

# Read arbitrary znode directly
configeratorc getArbitraryZnode /configerator-gz/test/dummy
```

**Useful flags:**
- `--getConfigTimeout`: Timeout in milliseconds (default: 10000)
- `--displayopts`: Display options - 1: content, 2: ConfigFileInfo, 3: both (default: 1)
- `--configerator_proxy_connect_timeout_ms`: Proxy connection timeout (default: 5000)
- `--configerator_proxy_port`: Proxy service port
- `--configerator_proxy_ip`: Proxy service IP address

**Example output:**
```bash
$ configeratorc getConfig bootcamp_configerator/chatroom/server_config
I0627 23:37:43.418582 2360427 configeratorc.cpp:57] Attempting to getConfig 'bootcamp_configerator/chatroom/server_config', timeout=10000
{
  "maxMessageSize": 17,
  "blockedSenders": [
    "mark",
    "sheryl"
  ]
}
```

## Standard Workflow

```bash
# 0. Locate the REAL config to change. Runtime knobs — thresholds, feature flags
#    (GFlags), rollout percentages, and other service tunables — are configerator
#    changes: they live as .cconf / .cinc / OVERRIDE.cconf under the configerator repo
#    (~/configerator/source), NOT as ad-hoc JSON or a stand-in file elsewhere in the
#    repo. Find and edit the REAL config there; never invent a placeholder config in
#    its place. To locate it, use indexed code search (never grep/find in big repos):
#       cbgs "<config_or_knob_name>"              # BigGrep substring search in configerator
#       cbgf "<config_or_knob_name>.cconf"        # BigGrep filename search in configerator
#       xbgs "<config_or_knob_name>"              # BigGrep substring search across fbsource

# 1. Make changes to source configs (.cconf, .mcconf, .cinc, .thrift)

# 2. REQUIRED: Build to rematerialize configs AND validate the change compiles.
#    `conf build` is the mandatory local pre-submit gate for a config change — the
#    config analog of build+test for code. Always run it and read its result before
#    committing, even for a one-line value change.
conf build

# 3. Commit both source AND materialized changes together
sl add
sl commit

# 4. Create diff for review (never use jf submit for configerator diffs)
conf submit --draft --non-interactive

# 5. Test with canary (if not auto-canary)
conf canary start --hosts <hostname>

# 6. Land after review — HUMAN-GATED. Do NOT run `jf land` yourself: landing happens
#    after human review approval and a healthy canary / Regional Config Validation.
#    An agent prepares + validates the change and stops at `conf submit --draft --non-interactive`.
jf land DXXXXXXXX   # human-only
```

**CRITICAL: Commit Requirements**

You **MUST** run `conf build` before every commit that touches `.cconf` or `.mcconf` files. This regenerates the corresponding `.materialized_JSON` files in `materialized_configs/`.

**Diff checks will FAIL if:**
- Source files (.cconf, .mcconf) are modified but materialized files are not updated
- Materialized files are stale (don't match their source files)
- You commit source changes without the corresponding materialized output

```bash
# Before EVERY commit involving config changes:
conf build && sl add && sl commit
```

**Key Rules:**
- **Always run `conf build`** before committing any config changes
- Always include both source AND materialized configs in your diff
- **Always use `conf submit --draft --non-interactive`** to submit diffs — never use `jf submit` for configerator changes
- **Use `conf delete`** to delete config files instead of manually deleting + `conf submit`. Note: `conf delete` does not support `--draft`
- Never bypass canary without strong reason (audited)
- Auto-canary is enforced for most config changes

## Config Examples

### .mcconf Example (Multiple Configs)

```python
# source/team/multi.mcconf
from team.helpers.cinc import createConfig

export({
    "config_a.some_type": createConfig("a"),
    "config_b.some_type": createConfig("b"),
})
```

### .cinc Example (Helper File)

```python
# source/team/helpers.cinc
from team.schema.thrift import SomeStruct

def createConfig(name):
    return SomeStruct(name=name, enabled=True)

COMMON_SETTINGS = {"timeout": 30, "retries": 3}
```

## Understanding Dependencies

When you modify a `.cinc` or `.thrift` file, ALL configs that import it must be recompiled:

```bash
# See what depends on a file
conf deps tree --reverse source/team/helpers.cinc

# See dependency path between files
conf deps path source/a.cconf source/b.cinc
```

**Important:** Changing widely-used files can trigger thousands of recompilations.

## Canary Process

### Manual Canary

```bash
conf canary start                                    # Localhost (1 hour TTL)
conf canary start --hosts my.test.host.com --ttl 7200  # Specific host
conf canary start --diff <diff-id> --spec my/canary.canary_spec  # With spec
```

### Auto Canary (CBSS)

Consumption Based Spec Selection automatically runs canaries during land:
- Uses historical config consumption data
- Selects appropriate canary specs based on which services consume the config
- Blocks landing if health checks fail

### Regional Config Validation

For widely-consumed or safety-critical configs, the gated land flow runs **Regional
Config Validation**: the change is canaried across an entire region for a short bake
window while SEV0 / health metrics are watched; CBSS auto-selects the canary specs
from consumption data and BLOCKS the land if health checks regress. This is part of
the human-gated land step — an agent prepares and validates the change (`conf build`,
optionally a manual `conf canary start`) and stops at `conf submit --draft --non-interactive`; a human
lands only after regional validation passes. See [REFERENCE.md](REFERENCE.md).

## Additional Documentation

- **[VALIDATORS.md](VALIDATORS.md)** - Writing thrift-cvalidators, .ctest files, thrift union API gotchas
- **[ACL.md](ACL.md)** - Access control for configs (REVIEWERS_ACL, AUTOMATION_ACL)
- **[THRIFT.md](THRIFT.md)** - Syncing thrift files between configerator and fbcode
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** - Common issues and debugging workflows
- **[REFERENCE.md](REFERENCE.md)** - Detailed file types, CLI options, bunnylol shortcuts
- **[CONSUMERS.md](CONSUMERS.md)** - Checking config consumption via `configerator_access_logs` Scuba table

## Tips

1. **ALWAYS run `conf build` before committing** - diff checks will fail if materialized files are stale
2. **Include both source and materialized** files in your diff - never commit one without the other
3. **Use `conf deps`** to understand impact of changes
4. **Debug with `configerator -j 1`** for interactive debugging
5. **Test with manual canary** before relying on auto-canary
6. **Never use `jf submit`** for configerator diffs; always use `conf submit --draft --non-interactive`. Use `conf delete` for config file deletions (it does not support `--draft`)

## Related Resources

- **Wiki**: https://www.internalfb.com/wiki/Configerator/
- **ConfigHub**: https://www.internalfb.com/confighub/
- **Configerator Users Group**: https://fb.workplace.com/groups/configerator.users
