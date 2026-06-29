# Troubleshooting Workflows

## Workflow 1: "My build is taking too long"

```bash
# Check what's being rebuilt
conf deps list source/path/to/changed/file.cconf

# If too many dependencies, consider:
# 1. Using --timeout flag
conf build --timeout 2147483647

# 2. Splitting changes into smaller diffs
# 3. Using a dedicated devserver instead of On Demand
```

## Workflow 2: "Build failed with validation error"

```bash
# Check the error message carefully
# Common issues:
# - Missing thrift import
# - Type mismatch
# - Validator failure

# Debug with print statements
# Add print() to your .cconf and run:
configerator -j 1 source/path/to/config.cconf
```

### Debugging Tips

```python
# Add to your .cconf for debugging:
print(config)
breakpoint()  # or: import pdb; pdb.set_trace()
# For VSCode: import fbvscode; fbvscode.set_trace()
```

## Workflow 3: "Canary is failing"

```bash
# Check canary status
conf canary status <mutation_id>

# View canary in UI
# bunnylol mut <mutation_id>

# Check health check results
# Look at the canary spec being used
# Verify the service is actually healthy
```

## Workflow 4: "Config not updating in production"

```bash
# Check if config is enrolled in Tumbleweed (staged rollout)
# View push status in ConfigHub

# Check what's happening with the config
conf wth <config_name>
```

## Workflow 5: "Understanding config structure"

```bash
# Read the source config
cat source/path/to/config.cconf

# Check the thrift schema
cat source/path/to/schema.thrift

# View the compiled output
cat materialized_configs/path/to/config.materialized_JSON
```

## Workflow 6: "Syncing thrift to fbcode"

See [THRIFT.md](THRIFT.md) for the complete workflow.

## General Debugging

### Visualizing Dependencies

```bash
# CLI
conf deps tree --reverse source/path/to/file.cinc

# ConfigHub UI
# bunnylol confdeps <your_config>
```

### Config Validators

Custom validation logic in `.thrift-cvalidator` files:

```python
# my_config.thrift-cvalidator
def validate(config):
    if config.timeout < 0:
        raise ValueError("timeout must be positive")
```
