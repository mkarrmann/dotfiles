# Code Style, Linting, and CLI Patterns

## Table of Contents
- [Running arc lint](#running-arc-lint)
- [String Splitting](#string-splitting)
- [Comments and Documentation](#comments-and-documentation)
- [__init__.py Files](#initpy-files)
- [Imports](#imports)
- [Abstract Base Classes](#abstract-base-classes)
- [Test Plan Patterns](#test-plan-patterns)
- [Common Buck2 Commands](#common-buck2-commands)
- [Debugging Tips](#debugging-tips)
- [ArgParse CLI Pattern](#argparse-cli-pattern)

## Running arc lint

```bash
arc lint                    # Lint changed files
arc lint path/to/file.py   # Lint specific file
arc lint --apply-patches   # Auto-fix issues
```

**Always run `arc lint` when modifying Python code** to fix formatting and find lint issues.

## String Splitting

Use `splitlines()` instead of `split("\n")` for splitting text into lines. `splitlines()` handles all line ending styles (`\n`, `\r\n`, `\r`) and does not produce a trailing empty string for inputs ending with a newline:

```python
# Correct
lines = text.splitlines()

# Wrong - breaks on \r\n, produces trailing empty string
lines = text.split("\n")
```

## Comments and Documentation

**Comments explain WHY, not what.** Avoid redundant comments/docstrings that restate what the code does.

```python
# Bad - obvious from the code
def get_user_fbid() -> int:
    """Get the current user's FBID."""
    return os.getuid()

# Good - explains WHY with context
# Use current timestamp as period_id to ensure cache consistency
# across multiple runs within the same hour
period_id = datetime.strftime(datetime.now(), format="%Y-%m-%d %H:00:00")

# Workaround: Buck2 runs tests from fbcode/ root, not app directory
# See D87654321 for context
os.chdir(os.path.join(FBSOURCE_ROOT, "arvr/apps/myapp"))
```

## __init__.py Files

**Do NOT create `__init__.py` files.** Buck does not require them for package recognition — empty ones serve no purpose.

Only create `__init__.py` when it contains actual initialization code or re-exports for public APIs.

## Imports

**Prefer absolute imports** over relative imports. Use the full module path from the repo root.

```python
# Correct
from myservice.utils.helpers import process_data

# Wrong
from .utils.helpers import process_data
from . import helpers
```

## Abstract Base Classes

```python
# Correct
from abc import ABC, abstractmethod

class MyBase(ABC):
    @abstractmethod
    def process(self) -> None:
        ...  # Use ellipsis, not pass

# Wrong
class MyBase:
    def process(self) -> None:
        raise NotImplementedError  # Don't use this pattern
```

## Test Plan Patterns

For dual-mode scripts, test both execution methods:

```
Test Plan:
fbpython tools/mypackage/script.py <args>
buck2 run fbsource//tools/mypackage:script -- <args>
arc pyre check fbsource//tools/mypackage/...
arc lint tools/mypackage/script.py tools/mypackage/BUCK
```

## Common Buck2 Commands

```bash
# Build a target
buck2 build @fbcode//mode/opt fbcode//path/to:target

# Run a binary
buck2 run @fbcode//mode/opt fbcode//path/to:target -- <args>

# Run tests
buck2 test @fbcode//mode/opt fbcode//path/to:test_target

# Run specific test method
buck2 test @fbcode//mode/opt fbcode//path/to:test -- test_method_name

# Query available targets in a BUCK file
buck2 uquery 'targets_in_buildfile(fbcode//path/to/BUCK)'

# Query sources for a target
buck2 query "fbcode//path/to:target" --output-attribute srcs

# Check file ownership
buck2 uquery 'owner(fbcode/path/to/file.py)'

# Get help
buck2 --help
buck2 test --help
```

## Debugging Tips

- Use `buck2 log what-failed` for failure details
- Buck2 differs from Buck1 — use `buck2 --help` liberally
- Meta's repo is "sound" — `buck2 clean` rarely helps (last resort only)
- No propagation delays — waiting won't fix issues
- Look for Buck rule examples in the repo
- Don't use `find` at top level (millions of files)

## ArgParse CLI Pattern

Use a separate `parse_args()` function with `fromfile_prefix_chars="@"`:

```python
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Description of what this script does",
        fromfile_prefix_chars="@",  # Enable @file.txt argument loading
    )
    parser.add_argument("--input-file", required=True, help="Path to input file")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    return parser.parse_args()

def main() -> int:
    args = parse_args()
    print(f"Processing {args.input_file}...")
    return 0

def invoke_main() -> None:
    sys.exit(main())

if __name__ == "__main__":
    invoke_main()
```

**Key points:**
- `fromfile_prefix_chars="@"` enables `fbpython script.py @args.txt` for CI/CD
- Separate `parse_args()` from `main()` for testability
- Use `-> argparse.Namespace` return type
