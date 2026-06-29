---
name: buck
description: Buck, or buck2, is Meta's high-performance build system for the monorepo. It's fast, reliable, and hermetic by default. This skill will help you build, test, run, explore the build graph, filter tests via tpx (substring, --regex, --exact), and manage buck2 daemon memory. Use when the user mentions buck memory, buck daemons consuming RAM, system running out of memory due to buck, wants to free up memory from idle buck2 daemons, or needs to run or filter tests with buck and tpx.
allowed-tools: Bash(buck:*), Bash(buck2:*), Bash(fdb:*), Read, Grep, Glob
---

## Core Concepts

### Targets

- **Build targets** are defined in `BUCK` or `TARGETS` files (fbcode only,
  prefer to use BUCK now)
- Target format: `cell//path/to/package:target_name`, where cell can be omitted if invoked from the same cell
- cell refers to the repository, e.g., `fbcode`, `fbsource`
- buck provides the `buck audit cell` command to get the list of cells and their absolute paths, use `buck root` to see the current root path
- All paths are relative to the repo root (e.g., `fbcode` is treated as root)
- Wildcards: `cell//path/to/package/...` represents all targets in that directory and subdirectories, `cell//path/to/package:` represents all targets under that directory

### BUCK/TARGETS Files

- Load build rules, which are defined in a .bzl file written in Starlark (Python-like syntax)
- Instantiates rules to create targets by giving them names and attributes
- Define what to build and how to build it
- Buck file has hierarchical boundary, it will not have access to files in subdirectories if the subdirectories have their own BUCK files

### PACKAGE Files

- `PACKAGE` files are per-directory configuration files which are accessible from Starlark rules/macros. It supports things like per-directory properties. It can be overridden by `PACKAGE` file in child folders.

### .buckconfig

- a repo-wise config file that defines a set of essential build options in key-value pairs
- can be extended through build mode file and command-line option through `-c` or `--config` options in the buck command
- must exist otherwise buck will not start

### buck-out

Buck2 stores build artifacts in a directory named `buck-out` in the root of your project (like `fbsource`), the locations of the artifacts should not be assumed as this is within buck's implementation details.

### buck source code

The source code of buck2 is under fbsource/fbcode/buck2, it's written mainly in Rust and bzl.

## Essential Commands

- For any command, using `--help` or `-h` to find all the options and explanations, `buck -h` for supported command list and `buck <command> -h` for command options.

### 1. Building Targets command examples, all commands can also use `buck2` instead of `buck`

**Basic build:**

```bash
buck build cell//path/to:target
```

**Build and show absolute output path:**

```bash
buck build --show-full-output cell//path/to:target
```

### 2. Running Binaries

**Build and run an executable:**

```bash
buck run cell//path/to:target
```

**Run with arguments (use `--` to separate Buck args from target args):**

```bash
buck run cell//path/to:target -- --arg1 value1 --arg2 value2
```

### 3. Running Tests

**Run a test target / directory:**

```bash
buck test cell//path/to:test_target
buck test cell//path/to/tests/...
```

Arguments after `--` are forwarded to **Tpx** (the test runner), not to the test binary. Tpx does not accept arbitrary test-binary flags — in most cases the equivalent is an env var: `buck test … -- --env KEY=VAL`.

**Filter tests** — three forms, prefer the first:

| Form | Behavior |
|---|---|
| `buck test … -- name` | substring match against fully-qualified test names (PREFERRED) |
| `buck test … -- --regex pattern` (or `-r`) | regex match |
| `buck test … -- --exact 'fully.qualified.name'` | exact match — only when you have the full name from a prior run |

Tpx test names combine the buck target, the test suite, and the test name. Substring is the most forgiving — start there. **Do NOT reach for `--gtest_filter`, `--gtest_list_tests`, `-k`, or `--filter`** — none of them work; use the three forms above.

**Run test with specific environment variable:**

```bash
buck test cell//path/to:test -- test_name --env GLOG_vmodule=foo=2
```

**Run test with timeout (use internal timeout, not external):**

```bash
buck test cell//path/to:test -- --timeout=300
```

> **Important:** Do not set external timeouts on buck test commands. Build and test operations are expected to be slow. Use `buck test ... -- --timeout=...` to pass timeout to the test runner (tpx) instead.

When unsure about a tpx flag, run `tpx main --help` or read the [Tpx user guide](https://www.internalfb.com/wiki/TAE/tpx/Tpx_user_guide) rather than guessing.

## Query Commands

Buck2 provides powerful query capabilities to explore the build graph, see **references/buck-query.md** for commonly used cquery and uquery commands.

## Removing or deleting a BUCK target

Before you delete or remove a BUCK target (e.g. dead-code / deprecation cleanup), VERIFY it is unused — checking imports alone is not enough. Confirm BOTH:

1. **No reverse dependencies (BUCK level):** `buck2 uquery "rdeps('cell//target/universe/...', 'cell//path/to:target')"` returns **empty** (see references/buck-query.md). `uquery` is the faster choice for this; surface the empty result before removing.
2. **No code-level references (source level):** also search for the target name and the source files it builds via BigGrep / MetaGraph (`fbgs`, `search_files`) — a target with zero `rdeps` can still be referenced by name in scripts, configs, or other BUCK files.

Only after BOTH checks are clean: remove the target, run the relevant tests (`buck2 test`), then `arc f` / `arc lint -a` and commit. If `rdeps` is non-empty, migrate or remove those callers first.

## Logging and debugging

Buck also provides utility tools to help resetting and understanding the build after it's done, see **references/buck-utils.md** for more information

## Tips and Best Practices

1. **Use appropriate modes**

- Build modes extends .buckconfig to define how a build should be done, this is repo/project specific and should not be assumed

2. **Check buck help**

- Run `buck --help` or `buck <command> --help` for detailed command documentation

3. **Handle large output carefully**

- Buck output can be extremely large - don't consume it directly
- Redirect output to a file:
```bash
buck build //path/to:target > /tmp/buck-output.txt 2>&1
```
- Read the tail of the output file (not the full output)
  - On success: tail confirms the build completed
  - On failure: start with tail, read more if needed to understand the error

## Common Issues

**Issue: "Cannot find target"**

- Check that the target exists in the BUCK/TARGETS file
- Verify the path is correct (relative to repo root with `//`)
- Use `buck targets cell//path/to:` to list available targets

**Issue: "Missing dependencies"**

- Buck2 is hermetic - all dependencies must be declared
- Add missing deps to the `deps` list in your BUCK file
- Use `buck cquery "deps('cell//your:target')"` to see current dependencies

**Issue: Build is slow**

- If cache hit rate is low, rebase through `arc pull`
- If incremental build is slow, check if there is a new config used from previous build
- Check if the build mode is appropriate and cached

## Memory Management

Buck2 spawns long-lived daemon processes (one per project/isolation-dir) that consume significant memory. Use the bundled `scripts/buckmem` script to monitor and manage them.

### Check Memory Usage

```bash
python3 <skill-dir>/scripts/buckmem --json
```

Parse the JSON output and present a summary showing each daemon's project path, RSS, swap, age, last build target, and thread count. Provide recommendations:
- If a daemon is using >4GB and has been idle (low CPU) for >1 hour, suggest killing it
- If a daemon has been running for >2 days, mention it may be worth restarting
- If total buck2 memory exceeds 30% of system RAM, flag it as critical

### Auto-Reclaim Memory

When the user asks to free memory or reclaim memory:

1. Determine the **current checkout** from the working directory
2. Run `python3 <skill-dir>/scripts/buckmem --json`
3. Kill daemons from **other checkouts** automatically (use `kill -TERM <pid>`)
4. Do NOT kill the daemon for the current checkout
5. Report what was killed and how much memory was freed

### Kill Commands

```bash
python3 <skill-dir>/scripts/buckmem --kill-top   # Kill highest-memory daemon
python3 <skill-dir>/scripts/buckmem --kill-all    # Kill all daemons (ask confirmation first)
```

### Proactive Monitoring

When a buck build fails with OOM or the user mentions things are slow, proactively run the buckmem script to check if daemons from other checkouts are consuming memory and suggest killing them.

### JSON Output Format

```json
[
  {
    "pid": 12345,
    "project": "/data/users/user/fbsource-ck-1",
    "isolation_dir": "default",
    "rss_mb": 9700,
    "swap_mb": 200,
    "threads": 45,
    "children": 3,
    "age_seconds": 172800,
    "last_command": "build //fbandroid/java/com/facebook/browser/lite:lite"
  }
]
```

## Additional Resources

- Official Buck2 docs: https://www.internalfb.com/intern/staticdocs/buck2/
