# Buck Query and Audit Commands

Buck2 offers comprehensive commands to explore the buck build graph, the graph can be unconfigured or configured. The configured graph is the graph with platform configuration applied and `select()`s resolved. The unconfigured graph is the graph as defined in BUCK files, before platform resolution. For complete documentation, see: https://buck2.build/docs/concepts/buck_query_language/

# buck cquery (query on configured graph)

Operates on the **configured target graph** with platform configuration applied and `select()`s resolved, the target is usually followed by a configuration id, e.g. `cfg:os-arch-fbcode-platform010-clang17-asan-dev#hash` or `cell//path/to:config#hash`.

## Basic queries:

### Show all target attributes

```bash
buck cquery cell//path/to:target -A
```

### Show specific target attributes

```bash
buck cquery -a attribute1 cell//path/to:target
```

### Show the starlark call stack of a target, very useful for navigating through macro layers:

```bash
buck cquery cell//path/to:target --stack
```

### Find dependencies

```bash
buck cquery "deps('cell//path/to:target')"
```

### Find direct dependencies only

```bash
buck cquery "deps('cell//path/to:target', 1)"
```

### Find reverse dependencies (what depends on this target)

```bash
buck cquery "rdeps('cell//target/universe/...', 'cell//path/to:target', depth)"
```

> **Before deleting a target**, an **empty** `rdeps` result is *required* — but it is necessary, not sufficient. A target with zero reverse deps can still be referenced by name in scripts, configs, or other BUCK files, so also check source-level references with BigGrep / `fbgs` / `search_files` before removal. See the "Removing or deleting a BUCK target" section of SKILL.md.

### Find all paths between two targets

```bash
buck cquery "allpaths('cell//from:target', 'cell//to:target')" --output-format dot
```

### Find shortest path

```bash
buck cquery "somepath('cell//from:target', 'cell//to:target')"
```

### Query dependencies of multiple targets

```bash
buck cquery "deps(set('cell//foo:bar' 'cell//foo:lib' 'cell//baz:util'))"
```

### Find owning targets for a file

```bash
buck cquery "owner('path/to/file')"
```

### Do not show toolchain/configuration dependencies:

```bash
buck cquery "deps('cell//foo:bar', 9999999, target_deps())"
```

# buck uquery (unconfigured query)

Operates on the **unconfigured target graph** - targets as defined in BUCK
files, before platform resolution, commands are the same as cquery.

**When to use:** For queries where you don't need platform-specific information,
uquery is much faster.

# buck targets (list targets)

List and inspect targets from BUCK files.

### Show all targets in a directory

```bash
buck targets cell//path/to:
```

### Show all targets in a directory and subdirectories

```bash
buck targets cell//path/to/...
```

### Show all attributes

```bash
buck targets cell//path/to:target -A
```

### Show specific attribute

```bash
buck targets cell//path/to:target -a deps
```

### Useful query command options

- `--json`: Output in JSON format
- `--stack`: Show stack trace on error
- `--target-platforms`: Specify target platform
- `--target-universe`: Limit query to a set of targets

# Audit Commands

`Buck audit` commands are similar to `buck query`, but they can be used to
explore configuration and other aspects of the build graph that are not tied to
a specific target.

### Show all aliases defined in .buckconfig

```bash
buck audit config alias
```

### Show constraints for configuration, this is very useful for debugging platform configuration issues

```bash
buck audit configurations [config-id]
```

### Show subtargets for a target, this is useful for some language specific features

```bash
buck audit subtargets cell//path/to:target
```
