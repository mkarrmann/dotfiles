# Buck Target Triplets for Python at Meta

This reference provides comprehensive examples of the three Python Buck target triplets at Meta, with common patterns and configurations for each.

## Table of Contents
- [Overview of Target Triplets](#overview-of-target-triplets)
- [1. General Triplet (fb_python_*)](#1-general-triplet-fb_python_)
- [2. FBCode Triplet (python_*)](#2-fbcode-triplet-python_)
- [3. ARVR/RL Triplet (oxx_python_*)](#3-arvrrl-triplet-oxx_python_)
- [Standalone Python Pattern](#standalone-python-pattern)
- [Common Patterns Across All Triplets](#common-patterns-across-all-triplets)
- [Directory Preferences](#directory-preferences)
- [Quick Reference Commands](#quick-reference-commands)
- [Tips and Best Practices](#tips-and-best-practices)
- [Dual-Mode Script Pattern (Standalone + Buck)](#dual-mode-script-pattern-standalone--buck)

## Overview of Target Triplets

Meta has **3 major target triplets** for Python development:

1. **General** (`fb_python_*`) - From `@fbsource//tools/build_defs`
2. **FBCode** (`python_*`) - From `@fbcode_macros//build_defs`
3. **ARVR/RL** (`oxx_python_*`) - From `@fbsource//arvr/tools/build_defs:oxx_python.bzl`

Each triplet consists of:
- **Library** - Reusable Python modules
- **Binary** - Executable Python scripts
- **Test/Unittest** - Test targets

## 1. General Triplet (fb_python_*)

**Use in:** General fbsource directories outside fbcode and arvr

**Import from:** `@fbsource//tools/build_defs`

**Preferred mode:** `@fbcode//mode/opt`

### Basic Example

```python
load("@fbsource//tools/build_defs:fb_python_library.bzl", "fb_python_library")
load("@fbsource//tools/build_defs:fb_python_binary.bzl", "fb_python_binary")
load("@fbsource//tools/build_defs:fb_python_test.bzl", "fb_python_test")

fb_python_library(
    name = "utils",
    srcs = glob(["*.py"]),
    typing = True,
    deps = [],
)

fb_python_binary(
    name = "run_script",
    main_module = "main",
    deps = [":utils"],
)

fb_python_test(
    name = "test",
    srcs = glob(["tests/*.py"]),
    typing = True,
    deps = [":utils"],
)
```

### Running General Triplet Targets

```bash
# Build
buck2 build @fbcode//mode/opt fbsource//path/to:utils

# Run binary
buck2 run @fbcode//mode/opt fbsource//path/to:run_script -- <args>

# Run tests
buck2 test @fbcode//mode/opt fbsource//path/to:test
```

### With Resources and Environment

```python
fb_python_test(
    name = "integration_test",
    srcs = glob(["tests/integration/*.py"]),
    typing = True,
    resources = glob(["tests/fixtures/**/*"]),
    env = {
        "TEST_MODE": "integration",
        "LOG_LEVEL": "DEBUG",
    },
    deps = [":utils"],
)
```

## 2. FBCode Triplet (python_*)

**Use in:** `fbcode/` directory

**Import from:** `@fbcode_macros//build_defs`

**Preferred mode:** `@fbcode//mode/opt`

### Basic Example

```python
load("@fbcode_macros//build_defs:python_library.bzl", "python_library")
load("@fbcode_macros//build_defs:python_binary.bzl", "python_binary")
load("@fbcode_macros//build_defs:python_unittest.bzl", "python_unittest")

python_library(
    name = "my_lib",
    srcs = glob(["*.py"]),
    typing = True,
    deps = [],
)

python_binary(
    name = "my_bin",
    main_module = "main",
    deps = [":my_lib"],
)

python_unittest(
    name = "test",
    srcs = glob(["tests/*.py"]),
    typing = True,
    labels = ["unit", "local_only"],
    deps = [":my_lib"],
)
```

### Running FBCode Triplet Targets

```bash
# Build
buck2 build @fbcode//mode/opt fbcode//path/to:my_lib

# Run binary
buck2 run @fbcode//mode/opt fbcode//path/to:my_bin -- <args>

# Run tests
buck2 test @fbcode//mode/opt fbcode//path/to:test
```

### Multi-Module Library

```python
python_library(
    name = "api",
    srcs = glob([
        "api/**/*.py",
        "models/**/*.py",
        "utils/**/*.py",
    ]),
    typing = True,
    base_module = "myproject.api",
    deps = [
        "fbcode//common/base:base",
        "fbcode//third-party-buck/platform010/build/requests:requests",
    ],
)
```

### Test with Resources

```python
python_unittest(
    name = "file_processor_test",
    srcs = glob(["tests/*.py"]),
    typing = True,
    labels = ["unit"],
    resources = [
        "tests/data/sample.csv",
        "tests/data/expected.json",
        "config/rules.yaml",
    ],
    deps = [":my_lib"],
)
```

## 3. ARVR/RL Triplet (oxx_python_*)

**Use in:** `arvr/` directory

**Import from:** `@fbsource//arvr/tools/build_defs:oxx_python.bzl`

**Preferred mode:** `@fbsource//arvr/mode/platform010/opt`

### Basic Example

```python
load("@fbsource//arvr/tools/build_defs:oxx_python.bzl", "oxx_python_library", "oxx_python_binary", "oxx_python_unittest")

oxx_python_library(
    name = "vr_utils",
    srcs = glob(["*.py"]),
    typing = True,
    deps = [],
)

oxx_python_binary(
    name = "vr_tool",
    main_module = "tool",
    deps = [":vr_utils"],
)

oxx_python_unittest(
    name = "test",
    srcs = glob(["tests/*.py"]),
    typing = True,
    deps = [":vr_utils"],
)
```

### Running ARVR/RL Triplet Targets

```bash
# Build
buck2 build @fbsource//arvr/mode/platform010/opt arvr//path/to:vr_utils

# Run binary
buck2 run @fbsource//arvr/mode/platform010/opt arvr//path/to:vr_tool -- <args>

# Run tests
buck2 test @fbsource//arvr/mode/platform010/opt arvr//path/to:test
```

### With External Dependencies

```python
oxx_python_library(
    name = "data_processor",
    srcs = glob(["processor/**/*.py"]),
    typing = True,
    deps = [
        "//arvr/projects/common:utils",
        "fbcode//third-party-buck/platform010/build/numpy:numpy",
        "fbcode//third-party-buck/platform010/build/pandas:pandas",
    ],
)
```

## Standalone Python Pattern

For scripts that run directly with `fbpython` (not via `buck2 run`):

### FBCode Standalone

```python
load("@fbcode_macros//build_defs:python_library.bzl", "python_library")
load("@fbcode_macros//build_defs:python_binary.bzl", "python_binary")
load("@fbcode_macros//build_defs:python_unittest.bzl", "python_unittest")

# Library with base_module = "" for standalone
python_library(
    name = "standalone_lib",
    srcs = ["process.py"],
    typing = True,
    base_module = "",  # Import relative to script
    deps = [],  # No dependencies - only standard library
)

python_binary(
    name = "process",
    main_module = "process",
    base_module = "",
    deps = [":standalone_lib"],
)

python_unittest(
    name = "test_process",
    srcs = ["tests/test_process.py"],
    typing = True,
    base_module = "",
    deps = [":standalone_lib"],
)
```

**Running standalone script:**
```bash
# Direct execution
fbpython fbcode/path/to/process.py

# Tests still via Buck
buck2 test @fbcode//mode/opt fbcode//path/to:test_process
```

## Common Patterns Across All Triplets

### Using glob() for Sources

**✅ Preferred:**
```python
python_library(
    name = "my_lib",
    srcs = glob(["*.py"]),
    typing = True,
)
```

**❌ Avoid:**
```python
python_library(
    name = "my_lib",
    srcs = [
        "module1.py",
        "module2.py",
        "module3.py",
        # ... listing every file
    ],
    typing = True,
)
```

### Test Labels

Use labels to categorize tests:

```python
python_unittest(
    name = "unit_tests",
    srcs = glob(["tests/unit/*.py"]),
    typing = True,
    labels = ["unit", "local_only"],
    deps = [":my_lib"],
)

python_unittest(
    name = "integration_tests",
    srcs = glob(["tests/integration/*.py"]),
    typing = True,
    labels = ["integration", "slow", "requires_db"],
    deps = [":my_lib"],
)
```

Common labels:
- `unit` - Unit tests
- `integration` - Integration tests
- `slow` - Long-running tests
- `local_only` - Only runs locally, not in CI
- `requires_db` - Requires database
- `requires_network` - Requires network access

### Command-Line Tool Pattern

```python
# Library with business logic
python_library(
    name = "tool_lib",
    srcs = glob(["lib/**/*.py"]),
    typing = True,
    deps = [
        "fbcode//common/argparse:argparse",
        "fbcode//common/logging:logging",
    ],
)

# Binary entry point
python_binary(
    name = "mytool",
    main_module = "main",
    deps = [":tool_lib"],
)

# Tests
python_unittest(
    name = "test_tool",
    srcs = glob(["tests/**/*.py"]),
    typing = True,
    labels = ["unit"],
    deps = [":tool_lib"],
)
```

### With Third-Party Dependencies

```python
python_library(
    name = "api_client",
    srcs = glob(["client/**/*.py"]),
    typing = True,
    deps = [
        "fbcode//third-party-buck/platform010/build/requests:requests",
        "fbcode//third-party-buck/platform010/build/pydantic:pydantic",
        "fbcode//third-party-buck/platform010/build/aiohttp:aiohttp",
    ],
)
```

## Directory Preferences

| Directory | Preferred Triplet | Imports From | Mode |
|-----------|------------------|--------------|------|
| `arvr/` | `oxx_python_*` | `@fbsource//arvr/tools/build_defs:oxx_python.bzl` | `@fbsource//arvr/mode/platform010/opt` |
| `fbcode/` | `python_*` | `@fbcode_macros//build_defs` | `@fbcode//mode/opt` |
| `tools/` | Investigate | TBD | TBD |
| `fbandroid/` | Investigate | TBD | TBD |
| `fbobjc/` | Investigate | TBD | TBD |
| `xplat/` | Investigate | TBD | TBD |

## Quick Reference Commands

### FBCode (python_*)

```bash
# Build
buck2 build @fbcode//mode/opt fbcode//path/to:target

# Run
buck2 run @fbcode//mode/opt fbcode//path/to:binary -- <args>

# Test
buck2 test @fbcode//mode/opt fbcode//path/to:test

# Type check
arc pyre check fbcode//path/to/...
```

### ARVR/RL (oxx_python_*)

```bash
# Build
buck2 build @fbsource//arvr/mode/platform010/opt arvr//path/to:target

# Run
buck2 run @fbsource//arvr/mode/platform010/opt arvr//path/to:binary -- <args>

# Test
buck2 test @fbsource//arvr/mode/platform010/opt arvr//path/to:test

# Type check
arc pyre check fbsource//arvr/path/to/...
```

### General (fb_python_*)

```bash
# Build
buck2 build @fbcode//mode/opt fbsource//path/to:target

# Run
buck2 run @fbcode//mode/opt fbsource//path/to:binary -- <args>

# Test
buck2 test @fbcode//mode/opt fbsource//path/to:test

# Type check
arc pyre check fbsource//path/to/...
```

## Tips and Best Practices

1. **Always use `glob()`** instead of listing files individually
2. **Enable typing** on all targets: `typing = True`
3. **Use appropriate triplet** based on directory location
4. **Include mode** in buck2 commands for consistent builds
5. **Set `base_module = ""`** for standalone scripts
6. **Use labels** to categorize and filter tests
7. **Never run tests directly** - always use `buck2 test`
8. **Query targets** before assuming names: `buck2 uquery 'targets_in_buildfile(path/to/BUCK)'`

## Dual-Mode Script Pattern (Standalone + Buck)

For scripts that need to work both standalone (`fbpython script.py`) AND via Buck (`buck2 run`):

### Complete Example

**File: `tools/mypackage/generate.py`**
```python
#!/usr/bin/env fbpython
# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.

# pyre-strict

# Run standalone: fbpython $(hg root)/tools/mypackage/generate.py
# Run via Buck: buck2 run fbsource//tools/mypackage:generate

from __future__ import annotations

import argparse
import json
import subprocess
from typing import Any

# Discover fbsource root dynamically (works in both modes)
FBSOURCE_ROOT: str = subprocess.check_output(
    ["hg", "root"],
    encoding="utf-8",
).strip()
CONFIG_DIR: str = f"{FBSOURCE_ROOT}/tools/mypackage/configs"
OUTPUT_DIR: str = f"{CONFIG_DIR}/generated"


def generate_config(name: str, data: dict[str, Any]) -> None:
    """Generate configuration file."""
    output_path = f"{OUTPUT_DIR}/{name}.json"
    with open(output_path, "w") as f:
        json.dump(data, f, indent=4)
        f.write("\n")
    print(f"Generated {output_path}")


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Generate configuration files"
    )
    parser.add_argument("--name", required=True, help="Config name")
    args: argparse.Namespace = parser.parse_args()

    generate_config(args.name, {"example": "data"})


if __name__ == "__main__":
    main()
```

**File: `tools/mypackage/BUCK`**
```python
load("@fbsource//tools/build_defs:fb_python_library.bzl", "fb_python_library")
load("@fbsource//tools/build_defs:fb_python_binary.bzl", "fb_python_binary")

oncall("your_team")

fb_python_library(
    name = "generate_lib",
    srcs = ["generate.py"],
    base_module = "",  # CRITICAL for standalone execution
    typing = True,
)

fb_python_binary(
    name = "generate",
    base_module = "",  # CRITICAL for standalone execution
    main_module = "generate",
    deps = [":generate_lib"],
)
```

### Why `base_module = ""` is Critical

Without `base_module = ""`:
- Buck creates import path: `tools.mypackage.generate`
- Standalone execution expects: `generate` (relative to script location)
- Result: Import errors when switching between modes

With `base_module = ""`:
- Both modes use same import paths
- Script works identically in standalone and Buck execution
- Modules imported relative to script location

### Running Dual-Mode Scripts

```bash
# Standalone (faster for development)
fbpython tools/mypackage/generate.py --name config1

# Via Buck (with full dependency resolution)
buck2 run fbsource//tools/mypackage:generate -- --name config1

# Type checking
arc pyre check fbsource//tools/mypackage/...

# Testing
buck2 test fbsource//tools/mypackage:test
```

**Always test BOTH modes** to ensure `base_module = ""` works correctly.
