# Pyre Type Checking — Setup and Annotations

## Table of Contents
- [Critical: Both Components Required](#critical-both-components-required)
- [Two Methods of Setup](#two-methods-of-setup)
- [Type Annotation Guidelines](#type-annotation-guidelines)
- [Annotation Examples](#annotation-examples)

## Critical: Both Components Required

**Adding `# pyre-strict` Alone Does NOT Enable Type Checking**

Many developers make this mistake: adding `# pyre-strict` to a Python file and expecting type checking to work. **This will fail silently.** Pyre requires BOTH components:

1. **`# pyre-strict` directive** in the Python file
2. **Buck target with `typing = True`** in a BUCK file

**Wrong - This will NOT be type checked:**
```python
# my_script.py
#!/usr/bin/env fbpython
# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.

# pyre-strict  # <-- Directive exists, but no Buck target!

def process(data) -> None:  # Type errors will NOT be caught
    pass
```

**Correct - Both components present:**
```python
# my_script.py
#!/usr/bin/env fbpython
# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.

# pyre-strict

def process(data: dict[str, Any]) -> None:  # Type errors WILL be caught
    pass
```

```python
# BUCK file (required!)
load("@fbsource//tools/build_defs:fb_python_library.bzl", "fb_python_library")

fb_python_library(
    name = "my_script",
    srcs = ["my_script.py"],
    typing = True,  # <-- This enables Pyre checking
)
```

**Without the Buck target, `arc pyre check` will silently skip the file**, even with `# pyre-strict` present.

**Additional Requirement:** Even if you have other test targets (like `sh_test`, `command_alias`, or other macro types), you MUST still have a library or binary target with `typing = True`. Other target types do NOT enable Pyre checking.

**Wrong - Only sh_test target:**
```python
load("@fbcode_macros//build_defs:native_rules.bzl", "sh_test")

sh_test(
    name = "test",
    test = "my_script.py",
)
# Pyre will NOT check this file - no typing = True target exists
```

**Correct - Both library target with typing AND test target:**
```python
load("@fbsource//tools/build_defs:fb_python_library.bzl", "fb_python_library")
load("@fbcode_macros//build_defs:native_rules.bzl", "sh_test")

fb_python_library(
    name = "my_script",
    srcs = ["my_script.py"],
    base_module = "",
    typing = True,  # REQUIRED for Pyre to check this file
)

sh_test(
    name = "test",
    test = "my_script.py",
)
```

**Key principle:** The `typing = True` flag must appear on a `*_library` or `*_binary` target, not on test macros.

## Two Methods of Setup

### New Method (Preferred)

Add `typing = True` to Buck targets and run `arc pyre check`:

```python
python_library(
    name = "my_module",
    srcs = glob(["*.py"]),
    typing = True,  # Enable Pyre
)
```

**Running Pyre:**
```bash
# Preferred - checks entire directory tree
arc pyre check fbsource//path/to/code/...
arc pyre check fbcode//path/to/code/...

# Also valid - checks specific target
arc pyre check fbsource//path/to/code:target
```

**Use the `...` pattern to check all targets in a directory tree.**

### Old Method

Create a `.pyre_configuration.local` file at the project root:

```json
{
  "oncall": "rl_devx",
  "targets": [
    "fbsource//arvr/tools/vsgo/..."
  ]
}
```

**Running Pyre:**
```bash
cd path/to/project
pyre
```

## Type Annotation Guidelines

**DO:**
- Add types to function signatures
- Add types to class attributes
- Add types to local variables that are returned from functions returning containers (lists, dicts)
- Use modern syntax: `dict[str, str]`, `list[int]`, `str | None`
- **Use `foo | None` not `Optional[foo]`** for optional types (Python 3.10+ union syntax)
- Use specific types like `dict[str, Any]` or `Iterable[str]` instead of bare `Any`

**DON'T:**
- Use `Any` as a parameter type
- Add types to local variables inside functions (Pyre infers from context)
- Add types to existing code unless explicitly asked
- Use old-style syntax: `Dict`, `List`, `Optional`

## Annotation Examples

```python
# Correct
def process_data(items: list[str], config: dict[str, Any]) -> dict[str, int]:
    result: dict[str, int] = {}  # Only typed because it's returned
    count = 0  # Not typed - Pyre infers int
    return result

def get_value(key: str) -> str | None:
    return None

# Wrong
from typing import Any, Dict, List, Optional

def process_data(items: Any) -> Dict[str, int]:  # Never use Any as parameter
    result = {}  # OK to not type if not needed
    return result

def get_value(key: str) -> Optional[str]:  # Use str | None instead
    return None
```

For more in-depth Pyre patterns including configuration files and troubleshooting, see [pyre_configuration.md](pyre_configuration.md).
