---
name: configerator-development
description: Use when working in Meta's configerator repository — editing .cinc/.cconf/.mcconf files, running conf build, arc lint, arc f, conf submit, debugging build failures, understanding configerator's Python dialect, import system, export system, validators, materialized configs, or CI (configerator-build-and-diff, configerator-lint). Also use when encountering configerator-specific errors like "No module named", mutation lint failures, or formatting issues in config files.
---

# Configerator Development

## Overview

Configerator is Meta's configuration management system. It is **NOT standard Python** — it is a bespoke Python dialect compiled via `exec()` with custom imports, builtins, and tooling. Do not assume standard Python conventions, linters, or workflows apply.

## Critical Principles

1. **Not standard Python.** `.cinc`/`.cconf` files are compiled via `ast.parse` → AST transform → `compile` → `exec({})`. Custom import system, custom builtins, no filesystem ops.
2. **No stacked diffs.** Each diff is tested in isolation against master. Squash into one commit or land sequentially.
3. **`conf submit`, not `jf submit`.** `conf submit` attaches the mutation ID from `conf build`, letting CI skip expensive rebuilds.
4. **Both source AND materialized configs must be committed.** `conf build` generates `.materialized_JSON` files that must be included in the diff.

## File Types

| Extension | Purpose | Compiles to JSON? |
|-----------|---------|-------------------|
| `.cconf` | Single config (must call `export()`) | Yes |
| `.mcconf` | Multi-config (exports dict of configs) | Yes (multiple) |
| `.cinc` | Shared include/library code | No |
| `.thrift` | Schema definitions (IDL) | No |
| `.thrift-cvalidator` | Validator functions (run at build time) | No |
| `.ctest` | Unit tests for validators/helpers | No |
| `.materialized_JSON` | Generated output (committed) | N/A |

**Key:** `.cinc` files are NOT configs — they cannot be read at runtime. Use `.cinc` for shared code; importing a `.cconf` triggers validation (slow).

## Development Workflow

```bash
# 1. Make source changes to .cinc/.cconf files

# 2. Format
pyfmt source/path/to/file.cinc   # or: arc f

# 3. Build (generates materialized configs + mutation ID)
conf build                        # auto-selects local (<20 files) or remote
conf build --prefer-remote        # force remote (faster for large builds)

# 4. Stage ALL files (source + materialized_configs/)
sl addremove && sl amend

# 5. Submit (preserves mutation ID — CI skips rebuild)
conf submit --non-interactive --verbatim

# Do NOT make changes (rebase, comments, etc.) between conf build and conf submit
```

## Formatting & Linting

| Tool | What it does | Command |
|------|-------------|---------|
| **pyfmt** | Black + usort (import sorting) | `pyfmt source/path/file.cinc` |
| **arc f** | Runs only BLACK formatter via arc lint | `arc f` |
| **arc lint** | Full lint suite (BLACK, FLAKE8, ConfigeratorCconfLinter, etc.) | `arc lint` |
| **conf build** | De facto validator — catches import errors, type mismatches, missing exports | `conf build` |

**Lint engine:** `FacebookConfigeratorLintEngine` — legacy PHP-based arcanist framework, NOT the newer Linttool/TOML system used in fbsource.

**`.flake8` at `source/.flake8`** suppresses F401 (unused imports), F403 (star imports), F821 (undefined names). This means flake8 will NOT catch unused imports — this is a known gap (T25586846).

**`ConfigeratorCconfLinter`** shells out to `config-detector` which is frequently broken (command not found). Safe to ignore if `conf build` succeeds.

## Import System (Non-Standard)

Configerator rewrites imports via AST transformation. These are NOT real Python imports.

```python
# Modern syntax (preferred)
from foo.bar.thrift import MyStruct
from foo.bar.cinc import my_function

# Legacy syntax (deprecated)
importThrift("foo/bar.thrift", "*")
import_python("foo.cinc", "m_foo")
```

**Rules:**
- `.thrift` extension must be included: `from foo.bar.thrift import X`
- Python keywords in paths need underscore suffix: `from foo.if_.config.thrift import X`
- Wildcard imports (`*`) are deprecated
- No filesystem operations (`open`, `os.listdir`) allowed
- Configs must be deterministic and hermetic

## Builtins (Available Without Import)

| Function | Purpose |
|----------|---------|
| `export(config)` | Export config (required, once per .cconf) |
| `configerator_warn(msg)` | Print warning (visible with `--verbose`) |
| `configerator_base_path()` | Returns repo base path |
| `configerator_cconf_path()` | Returns path of file being compiled |
| `configerator_file_exists(path)` | Check if file exists |
| `add_validator(ThriftClass, func)` | Add validator (in .thrift-cvalidator) |
| `add_validator_ext(ThriftClass, func)` | Extended validator (receives params dict) |
| `RawConfig(...)` / `RawDict(...)` | Export raw content |

`export_if_last()` was **removed** in June 2025 — caused multiple SEV1s.

## Build Commands

| Command | Behavior |
|---------|----------|
| `conf build` / `arc build` | Auto-selects local or remote (identical since May 2025) |
| `conf build --prefer-remote` | Force remote build |
| `conf build --prefer-local` | Force local build |
| `conf build --legacy --verbose` | Local build with `print()` output visible |
| `configerator source/path.cconf` | Compile single file (debugging) |
| `configerator -j 1 source/path.cconf` | Single-threaded (for pdb/breakpoints) |

**Debugging:** Add `print()` or `breakpoint()` in your config, then `configerator -j 1 source/path.cconf`.

## CI Jobs (Sandcastle)

| Job | Purpose |
|-----|---------|
| `configerator-build-and-diff` | Main job: mutation lint + build verification (runs on every diff) |
| `configerator-lint` | Separate job: arcanist linters (BLACK, FLAKE8, etc.) |
| `configerator-consumptor` | Config consumption checks |

**`configerator-build-and-diff` flow with `conf submit`:** Finds existing mutation ID → lint mutation → recommend reviewers. Skips rebuild.

**`configerator-build-and-diff` flow with `jf submit`:** No mutation found → uploads diff → rebuilds from scratch on weak Sandcastle hardware (slow, OOM-prone).

## Validators

`.thrift-cvalidator` files correspond to same-named `.thrift` files and implicitly import all structs.

```python
def my_validator(config):
    if config.timeout < 0:
        raise Exception("Timeout must be non-negative")

add_validator(MyThriftClass, my_validator)
```

Test validators with `.ctest` files. Do NOT import `.cconf` in `.ctest` — share code via `.cinc`.

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Using `jf submit` | Use `conf submit` (preserves mutation ID) |
| Stacking diffs | Squash into one or land sequentially |
| Forgetting materialized configs | `sl addremove && sl amend` after `conf build` |
| Changes after `conf build` | Rebuild before submitting |
| Assuming standard Python linting | Check `source/.flake8` — F401/F403/F821 suppressed |
| `rstrip("suffix")` | Use `removesuffix("suffix")` — `rstrip` strips char set |
| Importing `.cconf` for shared code | Use `.cinc` instead (`.cconf` triggers validation) |
| Using `find`/`grep -R` on repo root | Use `cbgs "string"` / `cbgr "regex"` (BigGrep) |
| Standard Python debugger workflow | Use `configerator -j 1 file.cconf` with `breakpoint()` |

## Quick Reference

```bash
# Format
pyfmt source/path/file.cinc
arc f

# Build
conf build
conf build --prefer-remote

# Submit
conf submit --non-interactive --verbatim

# Debug
configerator -j 1 source/path/file.cconf
conf build --legacy --verbose

# Dependencies
conf deps tree source/path/file.cinc
conf deps tree --reverse source/path/file.cinc

# Search repo
cbgs "search string"
cbgr "regex pattern"
cbgf "filename"

# Read production config
configeratorc getConfig path/to/config | jq

# Canary
arc canary
arc canary --hosts HOSTNAME --ttl 3600

# Kill stuck build
killall -9 configerator#na

# Rebase with materialized configs
sl revert -r .~1 materialized_configs && sl amend && sl pull --rebase && conf build && sl amend
```
