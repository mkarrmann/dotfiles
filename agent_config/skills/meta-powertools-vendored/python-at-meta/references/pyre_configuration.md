# Pyre Type Checking Configuration and Patterns

This reference provides detailed guidance on Pyre type checking at Meta, including both new and old configuration methods, type annotation patterns, and troubleshooting.

## Table of Contents
- [Two Methods of Pyre Configuration](#two-methods-of-pyre-configuration)
- [Enabling Pyre in Python Files](#enabling-pyre-in-python-files)
- [Type Annotation Guidelines](#type-annotation-guidelines)
- [Modern Type Annotation Syntax](#modern-type-annotation-syntax)
- [Critical Type Annotation Rules](#critical-type-annotation-rules)
- [Common Type Patterns](#common-type-patterns)
- [Common Pyre Errors and Solutions](#common-pyre-errors-and-solutions)
- [Abstract Base Classes](#abstract-base-classes)
- [Advanced Patterns](#advanced-patterns)
- [Interpreting Pyre Output](#interpreting-pyre-output)
- [Buck Configuration for Pyre](#buck-configuration-for-pyre)
- [Running Pyre](#running-pyre)
- [Best Practices](#best-practices)
- [Troubleshooting Pyre](#troubleshooting-pyre)
- [Summary: Key Differences](#summary-key-differences)

## Two Methods of Pyre Configuration

### New Method (Preferred): typing = True

Add `typing = True` to Buck targets to enable Pyre type checking.

**Example Buck target:**
```python
load("@fbcode_macros//build_defs:python_library.bzl", "python_library")

python_library(
    name = "my_module",
    srcs = glob(["*.py"]),
    typing = True,  # Enable Pyre type checking
    deps = [],
)
```

**Running Pyre:**
```bash
# Check all targets in directory tree
arc pyre check fbcode//path/to/code/...
arc pyre check fbsource//arvr/path/to/code/...

# Check specific target
arc pyre check fbcode//path/to:my_module
```

**Prefer target patterns** (with `...`) to capture all Pyre type issues in the directory tree.

### Old Method: .pyre_configuration.local

Create a `.pyre_configuration.local` file at the project root.

**Example `.pyre_configuration.local`:**
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

**Note:** The new method (typing = True) is preferred for new projects.

## Enabling Pyre in Python Files

Add `# pyre-strict` after the copyright header with blank lines around it:

**Standard file structure:**
```python
#!/usr/bin/env fbpython
# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.

# pyre-strict

from __future__ import annotations

import sys
from typing import Any, Protocol
from abc import ABC, abstractmethod
```

**Without copyright:**
```python
#!/usr/bin/env fbpython

# pyre-strict

from __future__ import annotations

import sys
```

## Type Annotation Guidelines

### DO Annotate

✅ **Function signatures** (always)
```python
def calculate(x: int, y: int) -> int:
    return x + y

def process_items(items: list[str], config: dict[str, Any]) -> dict[str, int]:
    return {item: len(item) for item in items}
```

✅ **Class attributes**
```python
class Config:
    host: str
    port: int
    timeout: float | None

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.timeout = None
```

✅ **Local variables when returning containers**
```python
def get_mapping() -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}  # Typed for clarity
    result["numbers"] = [1, 2, 3]
    return result
```

### DON'T Annotate

❌ **Simple local variables** (Pyre infers)
```python
def process() -> str:
    # Don't type these - Pyre infers them
    name = "John"
    age = 30
    message = f"{name} is {age}"
    return message
```

❌ **Obvious assignments**
```python
def compute() -> int:
    # Don't type - obvious from literals
    x = 5
    y = 10
    return x + y
```

**Key rule:** Only type local variables if they're returned and the function returns a container. Pyre infers variable types from the return type of the function.

## Modern Type Annotation Syntax

### Use Modern Syntax (Required)

```python
# ✅ Correct - modern syntax
def process_data(items: list[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    return result

def get_value(key: str) -> str | None:
    return None

def accepts_multiple(value: str | int | float) -> str:
    return str(value)

def process_config(config: dict[str, Any]) -> list[str]:
    return list(config.keys())
```

### Don't Use Legacy Syntax

```python
# ❌ Wrong - legacy syntax
from typing import Dict, List, Optional, Union, Any

def process_data(items: List[str]) -> Dict[str, int]:
    result: Dict[str, int] = {}
    return result

def get_value(key: str) -> Optional[str]:
    return None

def accepts_multiple(value: Union[str, int, float]) -> str:
    return str(value)
```

## Critical Type Annotation Rules

### Never Use Bare `Any` as Parameter Type

```python
# ❌ WRONG - Never use Any as parameter
from typing import Any

def process(data: Any) -> dict[str, int]:
    return {}

# ✅ CORRECT - Use specific types
def process(data: dict[str, Any]) -> dict[str, int]:
    return {}

def process_items(items: Iterable[str]) -> list[str]:
    return list(items)
```

**Use specific types** like `dict[str, Any]`, `Iterable[str]`, `list[Any]` instead of bare `Any`.

### Use `from __future__ import annotations`

```python
# ✅ Required for forward references
from __future__ import annotations

class Node:
    def __init__(self, value: int, next: Node | None = None) -> None:
        self.value = value
        self.next = next
```

### Only Add Types to New Code

- Don't add types to existing code unless explicitly asked
- Only type new code you write
- Don't refactor existing code to add types without permission

## Common Type Patterns

### Function Signatures

```python
# Simple function
def greet(name: str) -> str:
    return f"Hello, {name}"

# Multiple parameters with defaults
def calculate(a: int, b: int, operation: str = "add") -> int:
    if operation == "add":
        return a + b
    return a - b

# No return value
def log_message(message: str) -> None:
    print(message)

# Variable arguments
def sum_all(*numbers: int) -> int:
    return sum(numbers)

# Keyword arguments
def create_user(**attributes: str) -> dict[str, str]:
    return attributes
```

### Union Types

```python
# Modern union syntax with |
def parse_value(value: str | int) -> int:
    if isinstance(value, str):
        return int(value)
    return value

def find_user(user_id: int) -> str | None:
    if user_id == 0:
        return None
    return f"user_{user_id}"
```

### Generic Types

```python
from typing import TypeVar, Generic

T = TypeVar("T")

class Container(Generic[T]):
    def __init__(self, value: T) -> None:
        self.value = value

    def get(self) -> T:
        return self.value

# Usage
int_container: Container[int] = Container(42)
str_container: Container[str] = Container("hello")
```

### Protocols (Duck Typing)

```python
from typing import Protocol

class Drawable(Protocol):
    def draw(self) -> None: ...

class Circle:
    def draw(self) -> None:
        print("Drawing circle")

class Square:
    def draw(self) -> None:
        print("Drawing square")

def render(shape: Drawable) -> None:
    shape.draw()

# Both Circle and Square implement Drawable protocol
render(Circle())
render(Square())
```

### Type Aliases

```python
# Simple aliases
UserId = int
UserName = str

def get_user(user_id: UserId) -> UserName:
    return f"user_{user_id}"

# Complex aliases
Headers = dict[str, str]
JsonData = dict[str, str | int | float | bool | None]

def make_request(url: str, headers: Headers, data: JsonData) -> str:
    return "response"
```

### Callable Types

```python
from typing import Callable

def apply_operation(
    x: int,
    y: int,
    operation: Callable[[int, int], int]
) -> int:
    return operation(x, y)

def add(a: int, b: int) -> int:
    return a + b

result = apply_operation(5, 3, add)
```

### Literal Types

```python
from typing import Literal

def set_mode(mode: Literal["read", "write", "append"]) -> None:
    print(f"Mode set to: {mode}")

set_mode("read")   # ✅ OK
set_mode("delete") # ❌ Pyre error
```

## Common Pyre Errors and Solutions

### Error: Missing Return Annotation

```python
# ❌ Pyre error: Missing return annotation
def process_data(data):
    return data.upper()

# ✅ Fixed
def process_data(data: str) -> str:
    return data.upper()
```

### Error: Incompatible Parameter Type

```python
# ❌ Pyre error: Incompatible parameter type
def add_numbers(a: int, b: int) -> int:
    return a + b

result = add_numbers("5", "10")  # Error

# ✅ Fixed
result = add_numbers(5, 10)
```

### Error: Incompatible Return Type

```python
# ❌ Pyre error: None not compatible with str
def find_user(user_id: int) -> str:
    if user_id == 0:
        return None  # Error
    return f"user_{user_id}"

# ✅ Fixed
def find_user(user_id: int) -> str | None:
    if user_id == 0:
        return None
    return f"user_{user_id}"
```

### Error: Using `Any` as Parameter

```python
# ❌ Never use Any as parameter type
from typing import Any

def process(data: Any) -> str:
    return str(data)

# ✅ Fixed - be specific
def process(data: dict[str, Any]) -> str:
    return str(data)

def process_items(items: Iterable[str]) -> str:
    return ", ".join(items)
```

### Error: Mutable Default Arguments

```python
# ❌ Pyre warning: Mutable default argument
def append_to_list(item: str, items: list[str] = []) -> list[str]:
    items.append(item)
    return items

# ✅ Fixed
def append_to_list(item: str, items: list[str] | None = None) -> list[str]:
    if items is None:
        items = []
    items.append(item)
    return items
```

### Error: Undefined Attribute

```python
# ❌ Pyre error
class User:
    def __init__(self, name: str) -> None:
        self.name = name

user = User("Alice")
print(user.age)  # Error: User has no attribute age

# ✅ Fixed - define attribute
class User:
    name: str
    age: int

    def __init__(self, name: str, age: int = 0) -> None:
        self.name = name
        self.age = age
```

## Abstract Base Classes

Use `abc.ABC` with `@abstractmethod`, and use `...` (ellipsis) as the method body:

```python
# ✅ Correct
from abc import ABC, abstractmethod

class DataProcessor(ABC):
    @abstractmethod
    def process(self, data: str) -> str:
        ...  # Use ellipsis, not pass

    @abstractmethod
    def validate(self, data: str) -> bool:
        ...

class JsonProcessor(DataProcessor):
    def process(self, data: str) -> str:
        return data.upper()

    def validate(self, data: str) -> bool:
        return data.startswith("{")
```

```python
# ❌ Wrong
class DataProcessor:
    def process(self, data: str) -> str:
        raise NotImplementedError  # Don't use this

    def validate(self, data: str) -> bool:
        pass  # Don't use pass in abstract methods
```

## Advanced Patterns

### Overloads

```python
from typing import overload

@overload
def process(data: str) -> str: ...

@overload
def process(data: int) -> int: ...

def process(data: str | int) -> str | int:
    if isinstance(data, str):
        return data.upper()
    return data * 2
```

### TypedDict

```python
from typing import TypedDict

class UserDict(TypedDict):
    name: str
    age: int
    email: str | None

def create_user(user_data: UserDict) -> str:
    return f"{user_data['name']} ({user_data['age']})"

user: UserDict = {"name": "Alice", "age": 30, "email": None}
create_user(user)
```

### ParamSpec and Concatenate

```python
from typing import Callable, ParamSpec, Concatenate

P = ParamSpec("P")

def add_logging(
    func: Callable[Concatenate[str, P], str]
) -> Callable[Concatenate[str, P], str]:
    def wrapper(message: str, *args: P.args, **kwargs: P.kwargs) -> str:
        print(f"Calling with: {message}")
        return func(message, *args, **kwargs)
    return wrapper
```

## Interpreting Pyre Output

**Example error:**
```
fbcode/myproject/module.py:42:8 Incompatible parameter type [6]:
  Expected `str` for 1st positional parameter to call `process`.
  Got `int | None`.
```

**Reading errors:**
- **File and line:** `module.py:42:8` - line 42, column 8
- **Error code:** `[6]` - specific error type (Incompatible parameter type)
- **Message:** Explains expected vs actual types

## Buck Configuration for Pyre

### Enable Typing in Buck Targets

**FBCode:**
```python
load("@fbcode_macros//build_defs:python_library.bzl", "python_library")

python_library(
    name = "my_module",
    srcs = glob(["*.py"]),
    typing = True,  # Required for Pyre
    deps = [],
)
```

**ARVR/RL:**
```python
load("@fbsource//arvr/tools/build_defs:oxx_python.bzl", "oxx_python_library")

oxx_python_library(
    name = "my_module",
    srcs = glob(["*.py"]),
    typing = True,  # Required for Pyre
    deps = [],
)
```

### Type-Check Test Files

```python
# tests/test_module.py
# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.

# pyre-strict

from __future__ import annotations

import unittest
from myproject.module import process_data


class TestModule(unittest.TestCase):
    def test_process_data(self) -> None:
        result: str = process_data("input")
        self.assertEqual(result, "expected")
```

**Buck test target:**
```python
python_unittest(
    name = "test_module",
    srcs = glob(["tests/*.py"]),
    typing = True,  # Enable Pyre for tests
    labels = ["unit"],
    deps = [":my_module"],
)
```

## Running Pyre

### With New Method (typing = True)

```bash
# Check directory tree (preferred)
arc pyre check fbcode//path/to/code/...
arc pyre check fbsource//arvr/path/to/code/...

# Check specific target
arc pyre check fbcode//path/to:my_module

# Check specific file (finds associated targets)
arc pyre check fbcode/path/to/module.py
```

### With Old Method (.pyre_configuration.local)

```bash
cd path/to/project
pyre

# Or
pyre check
```

### Pyre Command Options

```bash
# Get help
arc pyre --help
pyre --help

# Show type of expression (debugging)
# Add reveal_type() in code (development only)
def debug_types() -> None:
    x = [1, 2, 3]
    reveal_type(x)  # Shows: Revealed type is `list[int]`
```

## Best Practices

1. **Start with `# pyre-strict`** on all new files
2. **Use modern type syntax** (`list`, `dict`, `str | None`)
3. **Annotate function signatures** always
4. **Let Pyre infer** simple local variables
5. **Run `arc pyre check`** before committing
6. **Fix errors incrementally** - don't ignore them
7. **Use Protocols** for duck typing instead of inheritance
8. **Avoid bare `Any`** - use specific types like `dict[str, Any]`
9. **Check Buck typing** is enabled: `typing = True`
10. **Read Pyre errors carefully** - they're usually correct
11. **Use `from __future__ import annotations`** for forward references
12. **Only add types to new code** unless explicitly asked

## Troubleshooting Pyre

### Pyre Not Running

Check Buck target has `typing = True`:
```bash
buck2 query "fbcode//path/to:target" --output-attribute typing
```

### Pyre Shows No Errors But Should

Ensure `# pyre-strict` is in the file:
```python
# pyre-strict  # Must be present
```

### Incremental Mode Issues

For `.pyre_configuration.local` setups:
```bash
pyre kill
pyre
```

## Summary: Key Differences

| Aspect | New Method | Old Method |
|--------|-----------|------------|
| Configuration | `typing = True` in Buck | `.pyre_configuration.local` |
| Running | `arc pyre check <target>` | `cd project && pyre` |
| Scope | Per-target | Per-project directory |
| Preferred | ✅ Yes | Legacy |

Use the **new method** (`typing = True`) for all new Python code.
