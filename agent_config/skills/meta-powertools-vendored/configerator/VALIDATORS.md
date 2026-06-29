# Configerator Thrift Validators

## Overview

A `.thrift-cvalidator` file validates configs that use the corresponding `.thrift` schema. It runs at `conf build` time — configs that fail validation cannot be landed.

**File naming:** `source/path/schema.thrift-cvalidator` validates `source/path/schema.thrift`.

## Writing a Validator

```python
# source/myteam/config.thrift-cvalidator
# @oncall myteam

def validate_my_config(config):
    if not config.name:
        raise AssertionError("name cannot be empty")

add_validator(MyConfig, validate_my_config)
```

- The `# @oncall` comment on line 1 is required.
- Thrift types from the corresponding `.thrift` are auto-imported (e.g., `MyConfig`, `DataSource`).
- Use `raise AssertionError(...)` or `assert condition, "message"` to fail validation.
- `add_validator(ThriftStruct, func)` registers the validator — place at module level.

## Thrift Union API (CRITICAL)

**Configerator thrift unions use a DIFFERENT API from fbthrift-python.** This is the #1 gotcha.

| Operation | Configerator (cvalidator/cconf) | fbthrift-python (Buck) |
|-----------|-------------------------------|----------------------|
| Get active variant | `source.getType()` → field ID (int) | `source.type` → enum |
| Check variant | `source.getType() == DataSource.HIVE` | `source.hive is not None` |
| Access value | `source.get_hive()` | `source.hive` |
| Field ID constants | `DataSource.HIVE` (= 1) | N/A |

**DO NOT use `source.hive` — it raises `AttributeError` in configerator.**

```python
def handle_source(source):
    variant = source.getType()
    if variant == DataSource.HIVE:
        hive = source.get_hive()
        return hive.table_name
    elif variant == DataSource.XDB:
        xdb = source.get_xdb()
        return xdb.table
    else:
        raise AssertionError(f"Unknown variant: {variant}")
```

## Oncall Validation

Validate oncall names via the Oncall Data Digest (local config, no external calls):

```python
from escalation_tool.oncall.oncall_data_digest.cinc import (
    isExistingOncallRotationShortName,
)

def validate_config(config):
    if not config.oncall:
        raise AssertionError("oncall cannot be empty")
    assert isExistingOncallRotationShortName(
        config.oncall
    ), f"'{config.oncall}' is not a valid oncall rotation"
```

Data refreshes every ~1 hour; newly created oncalls may take up to 3 hours to appear. For stricter validation (rotation must have members), use `getOncallRotationMemberCount` from the same module.

## Writing Tests (.ctest)

`.ctest` files are the canonical way to test cvalidators. They run via the configerator compiler, not Buck.

```python
# source/myteam/config.ctest
# @oncall myteam

import myteam.config.thrift as t

def validate_passes(config):
    try:
        config.validate()
        return True
    except Exception:
        return False

def validate_throws(config, expected_msg=None):
    try:
        config.validate()
        return False
    except Exception as exc:
        if expected_msg is not None:
            assert expected_msg in str(exc), f"Expected '{expected_msg}', got: {exc}"
        return True

# Valid config
assert validate_passes(t.MyConfig(name="test"))

# Invalid — empty name
assert validate_throws(t.MyConfig(name=""), "name cannot be empty")
```

Key points:
- `config.validate()` triggers all registered cvalidators for that struct.
- Import thrift types with `import myteam.config.thrift as t` (path mirrors `source/`).
- Use bare `assert` statements — no test framework needed.

### Running tests

```bash
cd ~/configerator
configerator source/myteam/config.ctest
```

Success: `All successful!` — Failure: shows assertion line and traceback.

Debug with `configerator -j 1 source/myteam/config.ctest` for single-threaded output.

## Common Validator Patterns

### Intra-file duplicate detection

```python
seen = {}
for i, item in enumerate(config.items):
    key = (item.namespace, item.name)
    if key in seen:
        raise AssertionError(f"Duplicate in items[{i}]: already in items[{seen[key]}]")
    seen[key] = i
```

### Optional field with non-empty constraint

```python
if destination.table is not None and not destination.table:
    raise AssertionError("table must be non-empty if provided")
```
