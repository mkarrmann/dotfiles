---
name: python-at-meta
description: This skill should be used when writing, debugging, or testing Python code in the fbsource repository. It covers Meta-specific conventions including fbpython usage, Buck2 target triplets (fb_python, python, oxx_python), Pyre type checking, testing workflows, and code style guidelines.
allowed-tools: Read
---

# Python At Meta

## Python Binary: fbpython

Use the `fbpython` binary (Python 3.12.12) instead of `python` or `python3`:

- **Binary:** `fbpython`
- **Location:** Installed on all devservers and On Demand instances
- **Usage:** Use in both command line invocations and shebangs

**Example shebang:**
```python
#!/usr/bin/env fbpython
# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.

# pyre-strict

import sys
from typing import Any
```

## General Python Guidelines

### Type Annotations

- **Use modern type hints:** `dict[str, str]` over `Dict[str, str]`
- **Never use `Any` as parameter type** - use specific types like `dict[str, Any]` or `Iterable[str]`
- **Prefer `Iterable[T]` over forcing `list[T]` conversions:**
  - When a function only iterates over items (no indexing/slicing/mutation), use `Iterable[T]`
  - Accepts `dict_keys`, `dict_values`, lists, tuples, sets without conversion
  - Example: `def process(items: Iterable[str])` accepts `my_dict.keys()` directly
  - Only use `list[T]` when you need indexing (`items[0]`), slicing (`items[1:3]`), or mutation (`items.append()`)
  - **CRITICAL: Never convert `dict.keys()` or `dict.values()` to `list()` just to satisfy type hints** — use `Iterable[T]` instead
- **Don't add types to local variables inside functions**
  - Only type local variables if they're returned and the function returns a container
  - Pyre infers the variable type from the return type of the function
- **Module-level constants require explicit type annotations in `pyre-strict` mode:**
  - Global constants need types: `MAX_RETRIES: int = 3`
  - Module-level variables from function calls need types: `FBSOURCE_ROOT: str = subprocess.check_output(...).strip()`
- **Only add typing to new code** or when explicitly asked to add types to existing code
- **Use `from __future__ import annotations`** for forward references

### Code Style

- **`# pyre-strict`** goes after copyright header with blank lines around it
- **Do NOT create `__init__.py`** unless it contains initialization code or public API re-exports — Buck does not need them
- **Prefer absolute imports** over relative imports
- **When modifying Python code** run `arc lint` to fix formatting and find lint issues
- **Use Abstract Base Classes** with `abc.ABC` and `@abstractmethod` (not `NotImplementedError`)
  - Use `...` (ellipsis) as body of abstract methods, not `pass`

**Example with copyright and pyre-strict:**
```python
#!/usr/bin/env fbpython
# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.

# pyre-strict

from __future__ import annotations

import sys
from abc import ABC, abstractmethod
```

## Standalone vs Buck-Based Python

### Standalone Python

- Script intended to be run using `fbpython` directly
- Use the fbsource root as the cwd
- Cannot import non-standard libraries or Python code from other parts of the repository (unless using trickery)
- Faster than requiring running via `buck2 run`
- Modules are imported relative to the main script
  - Need to set `base_module = ""` in the Buck targets to make Buck happy

**IMPORTANT for Pyre Type Checking:**
Even standalone scripts need Buck targets to enable Pyre type checking. If you're adding Pyre (`# pyre-strict`) to a standalone script without a BUCK file, you MUST create one with `typing = True` and `base_module = ""`. See [references/pyre_setup.md](references/pyre_setup.md).

**Running standalone scripts:**
```bash
# From fbsource root
fbpython path/to/script.py
```

### Buck-Based Python

- Preferred, especially in fbcode
- Can import almost any module including third-party modules
- Can be built into a binary to be released
- Must be run via `buck2 run`
- Modules are imported relative to the repo root

**Running Buck-based scripts:**
```bash
buck2 run @fbcode//mode/opt fbcode//path/to:binary -- <args>
```

### Third-Party Python Libraries

All source code for third-party open source Python libraries exists in the `third-party/pypi` directory at the fbsource root:

```
third-party/pypi/<package_name>/<version>/
```

For example, the `requests` library version 2.28.1 would be at:
```
third-party/pypi/requests/2.28.1/
```

#### Identifying Which Version Your Buck Target Uses

When debugging issues with third-party libraries, you need to know which version your Buck target is using:

**Step 1: Look for a PACKAGE file override**

Search upwards from the folder of the Buck target you're trying to debug for a `PACKAGE` file. PACKAGE files define version constraints for all targets in their directory and subdirectories:

```bash
# From your target's directory, search upwards for PACKAGE files
dir=$(pwd); while [[ "$dir" != "/" ]]; do [[ -f "$dir/PACKAGE" ]] && echo "$dir/PACKAGE"; dir=$(dirname "$dir"); done
```

**Step 2: If no PACKAGE override, check VERSION.bzl**

If there's no PACKAGE file override for the dependency, the default version is specified in:

```
third-party/pypi/<package>/VERSION.bzl
```

For example, to find the default version of `requests`:
```bash
cat third-party/pypi/requests/VERSION.bzl
```

### Dual-Mode Scripts (Standalone + Buck)

Some scripts need to work both standalone AND via Buck2. This pattern allows both execution methods:

**Script pattern:**
```python
#!/usr/bin/env fbpython
# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.

# pyre-strict

# Run standalone: fbpython $(hg root)/tools/mypackage/script.py
# Run via Buck: buck2 run fbsource//tools/mypackage:script

from __future__ import annotations

import subprocess

# Discover fbsource root dynamically (works in both modes)
FBSOURCE_ROOT: str = subprocess.check_output(
    ["hg", "root"],
    encoding="utf-8",
).strip()
CONFIG_DIR: str = f"{FBSOURCE_ROOT}/tools/mypackage/configs"
```

**BUCK file pattern:**
```python
load("@fbsource//tools/build_defs:fb_python_library.bzl", "fb_python_library")
load("@fbsource//tools/build_defs:fb_python_binary.bzl", "fb_python_binary")

oncall("your_team")

fb_python_library(
    name = "script_lib",
    srcs = ["script.py"],
    base_module = "",  # CRITICAL: Required for standalone execution
    typing = True,
)

fb_python_binary(
    name = "script",
    base_module = "",  # CRITICAL: Required here too
    main_module = "script",
    deps = [":script_lib"],
)
```

**Key points:**
- **Create BOTH library and binary targets**: Library enables `typing = True`, binary enables `buck2 run`
- **BOTH need `base_module = ""`** for standalone scripts
- Use `hg root` to dynamically discover fbsource root (works in both modes)

## Buck2 Target Triplets

**Buck2 target triplets**: See [references/buck_targets_examples.md](references/buck_targets_examples.md) for full examples of all 3 triplets (`fb_python_*`, `python_*`, `oxx_python_*`) including library/binary/test targets, common patterns, and configuration options.

Quick lookup — which triplet to use:

| Directory | Preferred Triplet | Mode |
|-----------|------------------|------|
| `arvr/` | ARVR/RL (`oxx_python_*`) | `@fbsource//arvr/mode/platform010/opt` |
| `fbcode/` | FBCode (`python_*`) | `@fbcode//mode/opt` |
| `fbandroid/` | General (`fb_python_*`) | `@fbcode//mode/opt` |
| `fbobjc/` | General (`fb_python_*`) | `@fbcode//mode/opt` |
| `tools/` | General (`fb_python_*`) | `@fbcode//mode/opt` |
| `xplat/` | General (`fb_python_*`) | `@fbcode//mode/opt` |

**Directory-specific patterns:**
- **fbandroid/**: Uses `fb_python_binary` from `@fbsource//tools/build_defs:fb_python_binary.bzl`
- **fbobjc/**: Uses `fb_python_binary` and `fb_python_library` from `@fbsource//tools/build_defs`
- **tools/** and **xplat/**: Use `fb_python_*` triplet from `@fbsource//tools/build_defs`

**General guidance:** Use `glob()` instead of listing files individually in `srcs`.

## Testing Python Code

### Test Directory and File Naming

**Check existing patterns in your directory first.** Both conventions exist in fbcode:
- `test_*.py` prefix (~72% of fbcode)
- `*_test.py` suffix (~28% of fbcode)

When in doubt, prefer `test_*.py`. Use `tests/` directory (not `__tests__/`).

**Example structure:**
```
my_module/
├── cli.py
├── parser.py
├── BUCK
└── tests/
    ├── test_cli.py
    └── test_parser.py
```

**Test quality guidelines:**
- Avoid trivial tests that just verify basic Python behavior
- Focus on actual functionality, edge cases, and error handling logic
- Test behavior, not implementation details

### Running Unit Tests

**For both standalone and Buck-based Python, tests should ONLY be run using `buck2 test`:**

```bash
# FBCode
buck2 test @fbcode//mode/opt fbcode//path/to:test_target

# ARVR/RL
buck2 test @fbsource//arvr/mode/platform010/opt arvr//path/to:test_target

# Run specific test method
buck2 test @fbcode//mode/opt fbcode//path/to:test -- test_method_name
```

**CORRECT:**
```bash
buck2 test @fbcode//mode/opt fbcode//path/to:test_target
```

**WRONG:**
```bash
fbpython test_file.py  # Bypasses build system and dependencies
```

### Running Standalone Scripts and Buck Binaries

```bash
# Standalone (from fbsource root)
fbpython path/to/script.py

# Buck-based — FBCode
buck2 run @fbcode//mode/opt fbcode//path/to:binary -- <params>

# Buck-based — ARVR/RL
buck2 run @fbsource//arvr/mode/platform010/opt arvr//path/to:binary -- <params>
```

## Pyre Type Checking

**Pyre setup and type annotations**: See [references/pyre_setup.md](references/pyre_setup.md) for the critical "BOTH components required" rule (`# pyre-strict` directive AND `typing = True` Buck target), the new vs old setup methods, type annotation DOs and DON'Ts, and full annotation examples.

For deeper Pyre patterns (configuration files, troubleshooting, complex generics), see [references/pyre_configuration.md](references/pyre_configuration.md).

**Quick reference — must-have rule:** Adding `# pyre-strict` alone does NOT enable type checking. You also need a `*_library` or `*_binary` Buck target with `typing = True`. Without that target, `arc pyre check` will silently skip your file.

```bash
# Preferred — checks entire directory tree
arc pyre check fbsource//path/to/code/...
arc pyre check fbcode//path/to/code/...
```

## Code Style and Linting

**Code style, linting, and CLI patterns**: See [references/code_style.md](references/code_style.md) for `arc lint` usage, `splitlines()` vs `split("\n")`, comments-explain-WHY guidance, `__init__.py` rules, absolute imports, Abstract Base Classes, test plan templates, common Buck2 commands, debugging tips, and the canonical ArgParse CLI pattern with `fromfile_prefix_chars="@"`.

## References

This skill includes additional reference materials:

- **[references/buck_targets_examples.md](references/buck_targets_examples.md)** — Comprehensive examples of all 3 Python Buck target triplets
- **[references/pyre_setup.md](references/pyre_setup.md)** — Pyre setup essentials (both components rule, methods, annotation guidelines)
- **[references/pyre_configuration.md](references/pyre_configuration.md)** — Detailed Pyre configuration and troubleshooting
- **[references/code_style.md](references/code_style.md)** — Code style, linting, debugging, ArgParse CLI patterns
