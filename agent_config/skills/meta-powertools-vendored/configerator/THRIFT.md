# Syncing Thrift Files

This guide covers syncing .thrift files between configerator and fbcode using `configerator-thrift-updater`.

## configerator-thrift-updater

Syncs .thrift files from configerator to fbcode:

```bash
# From fbcode directory:
cd ~/fbsource/fbcode

# Sync a thrift file
configerator-thrift-updater path/to/file.thrift

# Sync all thrift files in a directory
configerator-thrift-updater admarket/

# With custom configerator path (for On Demand)
configerator-thrift-updater -c ~/configerator path/to/file.thrift
```

## Before Using

Add your thrift file to the allowlist:
`source/configerator/tools/thrift_update_cfgr_files.cinc`

## Complete Workflow

```bash
# 1. Add thrift to allowlist (if new file)
# Edit: source/configerator/tools/thrift_update_cfgr_files.cinc

# 2. Build the allowlist change
conf build

# 3. From fbcode, run the updater
cd ~/fbsource/fbcode
configerator-thrift-updater path/to/file.thrift

# 4. Add BUCK target for the thrift file in fbcode

# 5. Build the thrift
buck2 build //configerator/structs/path:target-python-types
```

## Syncing from Configerator

When the thrift changes haven't landed yet (e.g., the configerator diff is still in review), the default `~/configerator` repo won't have the changes. Use `-c` to point at a worktree, and `--force-sync` to bypass the race condition check (since the amended commit won't be an ancestor of the previous sync commit):

```bash
cd ~/fbsource/fbcode
configerator-thrift-updater -c ~/configerator --force-sync path/to/file.thrift
```

You can also canary the configerator change first to make the config available on the local machine before syncing:

```bash
cd ~/configerator
arc canary
```

## Generating Python Type Stubs

After creating or modifying thrift files in configerator, generate Python type stubs so Pyre can type-check code that uses the generated thrift types:

```bash
cd ~/configerator  # or your configerator worktree
fbpython source/pyre/scripts/generate_stubs.py source/path/to/directory/
```

This generates `.pyi` stub files under `source/python_type_stubs/` for all thrift files in the specified directory. Include the generated stubs in your configerator diff.

## Import Dependencies

```python
# source/my_project/my_config.cconf
from my_project.schema.thrift import MyConfigStruct  # thrift schema
from my_project.helpers.cinc import helper_function  # helper functions

# Change to ANY of these imports causes my_config.cconf to recompile
```

**Import pattern:** `from <path.to.file>.thrift import <StructName>`
- The path mirrors the directory structure under `source/`
- Import specific structs/types you need from the thrift file
