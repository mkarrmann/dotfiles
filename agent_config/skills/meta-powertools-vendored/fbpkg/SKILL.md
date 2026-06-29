---
name: fbpkg
author: noamler
description: Investigate fbpkg packages, versions, expiration, and usage. Auto-invoke for fbpkg commands, package deployment, version management, or troubleshooting.
---

# Fbpkg Troubleshooting Guide

## When to Use

**Auto-invoke when:**
- User mentions fbpkg commands
- Working with package versions or deployments
- Investigating expiration or deletion issues
- Troubleshooting "version limit" or "package not found" errors

**Skip for:**
- Reading package config files only
- Other build systems

## CRITICAL: Pre-Execution Safety Check

**BEFORE executing ANY fbpkg command, you MUST check if it's a write operation.**

### Write Operation Detection

A command is a write operation if it matches ANY of these patterns:

**Write Commands:**
| Command | Effect |
|---------|--------|
| `fbpkg build` | Creates versions |
| `fbpkg create` | Creates package |
| `fbpkg clone` | Clones version to new package |
| `fbpkg delete` / `delete-package` | Deletes versions/package |
| `fbpkg expire` | Modifies ephemeral TTL |
| `fbpkg preserve` | Converts ephemeral → preserved |
| `fbpkg restore` | Restores deleted versions |
| `fbpkg archive` / `unarchive` | Protects/unprotects from deletion |
| `fbpkg tag` / `untag` | Adds/removes tags |
| `fbpkg setdefault` | Changes DEFAULT tag |
| `fbpkg migrate` | Migrates to fbpkg.builder |
| `fbpkg update-package-preferred-regions` | Updates blob storage regions |

**Write Flags:**
- `fbpkg meta <package> --<any_flag>` (except `--format`)
- `fbpkg oncall-meta <oncall> --<any_flag>` (all flags modify state)
- Any command with `--force` flag

### Enforcement Protocol

**When user requests a write operation:**

1. **REFUSE to execute** - Never run write commands via Bash tool
2. **Provide the exact command** in a code block for manual execution
3. **Explain the effect** and potential consequences
4. **Note ACL requirements** - check with `fbpkg meta <package_name>`
5. **If user insists:**
   - Warn about dangers (data loss, service disruption)
   - Explain that write operations require manual execution for safety
   - Remind that it will fail without ACL membership

**Example refusal:**

"I cannot execute write operations like fbpkg delete. Here's the command to run manually:

```bash
fbpkg delete <package_name>:<version>
```

This will permanently delete the version. Requires ACL membership for <package_name>.
Check ACL with: `fbpkg meta <package_name>`"

## Command Rules

### Read-Only (Auto-execute)

| Command | Notes |
|---------|-------|
| `fbpkg info <package_name>[:<version>]` | Flags: --json, --show-deleted, --extended, --build-config, --format |
| `fbpkg versions <package_name>` | **Always** add `--ephemeral-limit 50 --regular-limit 50` |
| `fbpkg inuse-check <package_name>[:<version>]` | Flags: --where, --count, --ephemeral, --format json |
| `fbpkg meta <package_name>` | **Only** `--format` allowed (other options modify metadata) |
| `fbpkg list [pattern]` | Flags: --oncall, --regex |
| `fbpkg config-lookup --package <package_name>` | Also: --builder for fbpkg.builder targets |
| `fbpkg contains-diff <package_name>:<version> <diff>` | Flags: --all, --delta, --log-paths |
| `fbpkg find-builds <rev>` | Positional arg. Flags: --all, --package |
| `fbpkg list-changes <package_name>:<v1> [<package_name>:<v2>]` | Flags: -l/--last-revision, -p/--path |
| `fbpkg access-stats <package_name>` | Shows access statistics |
| `fbpkg oncall-meta <oncall>` | Read-only without flags (--acl-name, --inuse-check are write ops) |
| `fbpkg fetch <package_name>[:<version>]` | Flags: -d/--dest, --extract, --verify, --format |

### Write Commands (Never Execute)

**⚠️ CRITICAL: These commands modify state and must NEVER be auto-executed.**

See "Pre-Execution Safety Check" section above for:
- Complete list of write commands
- Detection logic (commands + flags)
- Enforcement protocol

## Core Commands

### fbpkg info
```bash
fbpkg info <package_name>                            # Package metadata
fbpkg info <package_name>:<version>                  # Version info
fbpkg info <package_name> --json                     # JSON output
fbpkg info <package_name> --extended                 # Include oncall info
fbpkg info <package_name>:<version> --show-deleted   # Show deleted version
```

### fbpkg versions
```bash
# Always include limits to avoid timeouts
fbpkg versions <package_name> --ephemeral-limit 50 --regular-limit 50

# Variations
fbpkg versions <package_name> --no-ephemerals --regular-limit 50
fbpkg versions <package_name> --show-deleted --ephemeral-limit 50 --regular-limit 50 --deleted-limit 50
fbpkg versions <package_name> --format json --ephemeral-limit 50 --regular-limit 50
fbpkg versions <package_name> --show-build-user --show-revision --ephemeral-limit 50 --regular-limit 50
```

### fbpkg inuse-check
```bash
fbpkg inuse-check <package_name>:<version>
fbpkg inuse-check <package_name>:<version> --where --count
fbpkg inuse-check <package_name>:<uuid> --ephemeral --where
```

### fbpkg meta
```bash
fbpkg meta <package_name>                 # Shows ACL, limits, cleanup style
fbpkg meta <package_name> --format json
```

### fbpkg fetch
```bash
fbpkg fetch <package_name>:<version>                      # Download to cwd
fbpkg fetch <package_name>:<version> -d /path/to/dest     # Download to path
fbpkg fetch <package_name>:<version> --resolve            # Resolve tag → UUID
```

### fbpkg find-builds
```bash
fbpkg find-builds <rev>                          # List packages from revision
fbpkg find-builds <rev> --package <package_name> # Filter to package
fbpkg find-builds <rev> --all                    # Include ephemerals
```

## Key Options

### Why version limits?

Without `--ephemeral-limit` and `--regular-limit`, active packages return thousands of versions, causing timeouts.

### Option Reference

| Option | Command | Effect |
|--------|---------|--------|
| `--extended` | info | Includes oncall name |
| `--show-deleted` | info, versions | Shows deleted versions (within retention) |
| `--no-ephemerals` | versions | Hides ephemeral UUIDs, shows only preserved |
| `--show-build-user` | versions | Shows builder (debug) |
| `--show-revision` | versions | Shows source revision |
| `--where` | inuse-check | Shows which TW jobs use it |
| `--count` | inuse-check | Shows usage count |
| `--ephemeral` | inuse-check | Includes ephemerals (off by default) |
| `--force` | delete | Bypasses safety checks — **dangerous** |
| `--extend-only` | expire | Never shortens TTL — **safer** |
| `--resolve` | fetch | Returns UUID without download |
| `--builder` | config-lookup | Searches fbpkg.builder targets |

## Output Reference

### Version Types in Output

| Output | Type | Example |
|--------|------|---------|
| Integer | Preserved | `1164`, `42` |
| 32-char hex | Ephemeral UUID | `01421494`, `e74a307b` |
| `Expires: NEVER` | Preserved | Won't auto-delete |
| `Expires: <date>` | Ephemeral | Auto-deletes after date |
| `Archived: True` | Protected | Won't auto-delete at limit |

### Package Info Fields

| Field | Meaning |
|-------|---------|
| `ACL` | Hipster ACL — only members can write |
| `Cleanup Style` | `auto_delete_oldest` or `fail_build` |
| `Version Limit` | Max preserved versions (default 10, max 50) |
| `DEFAULT` | Version returned when unspecified |
| `LATEST` | Highest preserved version number |
| `In-use Check` | Service checking usage (usually TW) |

### Version Info Fields

| Field | Meaning |
|-------|---------|
| `Created` | Build time |
| `Expires` | `NEVER` (preserved) or date (ephemeral) |
| `Size` | Compressed size |
| `Build User` | Builder (`root` = Sandcastle) |
| `Build Architecture` | `x86_64`, `aarch64`, `noarch` |
| `Repository` | Source repo |
| `Revision` | Source commit |
| `Tags` | Symbolic names |

## Concepts

### Version Types

| Type | ID | Expiration | Limit | Archive |
|------|----|------------|-------|---------|
| Ephemeral | UUID | 1-28 days | None | No |
| ACL-free Ephemeral | UUID | 1-28 days | None | No, cannot preserve |
| Preserved | Integer | Never | 10 (max 50) | Yes |

**ACL-free Ephemerals:** Testing builds without ACL. Cannot preserve. See [wiki](https://www.internalfb.com/wiki/Fbpkg/Ephemeral_Builds/#acl-free-ephemerals).

### Cleanup Styles

- **auto_delete_oldest**: Deletes oldest unprotected when at limit
- **fail_build**: Blocks builds when at limit

**Protected:** DEFAULT tag, archived, in-use

### Tags

- Auto-managed: `LATEST`, `DEFAULT`, `LATEST_EPHEMERAL`
- Custom: `prod`, `stable`, `rc`, `dev`, `canary`

### Retention

- Ephemeral: 1 day after deletion
- Preserved: 7 days after deletion

## Workflows

### Ephemeral Expiring

```bash
# Check
fbpkg info <package_name>:<uuid>
fbpkg inuse-check <package_name>:<uuid> --where --count

# Fix (WRITE):
fbpkg expire <package_name>:<uuid> 28d      # Extend TTL
fbpkg preserve <package_name>:<uuid>        # Convert to preserved
```

### Version Deleted

```bash
# Check
fbpkg versions <package_name> --show-deleted --ephemeral-limit 50 --regular-limit 50 | grep <version>

# Fix (WRITE):
fbpkg restore <package_name>:<version>      # Within retention
fbpkg archive <package_name>:<version>      # Protect
```

### Hit Version Limit

```bash
# Check
fbpkg meta <package_name>
fbpkg versions <package_name> --no-ephemerals --regular-limit 50
fbpkg inuse-check <package_name> --where

# Fix (WRITE):
fbpkg meta <package_name> --version-limit 20
fbpkg delete <package_name>:<old_version>
```

### Find Version Contents

```bash
fbpkg info <package_name>:<version>                            # Get revision
fbpkg contains-diff <package_name>:<version> D12345678         # Check diff
fbpkg list-changes <package_name>:<v1> <package_name>:<v2>     # Compare
```

### Compare Versions (Discover All Diffs Between Two Versions)

When `list-changes` is insufficient — e.g., you need to discover unknown diffs
between two fbpkg versions, or trace what changed between a working and broken
deployment:

```bash
# Step 1: Get commit hashes from each version
fbpkg info <package_name>:<v1>   # Note the Revision: field
fbpkg info <package_name>:<v2>   # Note the Revision: field

# Step 2: Use sl log range query to find all commits between them
sl log -r '<hash1> :: <hash2>' -T '{node|short} {desc|firstline}\n'

# Step 3: Filter to relevant paths or keywords
sl log -r '<hash1> :: <hash2>' -T '{node|short} {desc|firstline}\n' | grep -i 'keyword'
```

**When to use this over `list-changes`:**
- Investigating regressions between deployed versions
- Need to see ALL diffs, not just those touching the package's build target
- `list-changes` errors or doesn't show the expected changes
- Tracing transitive dependency changes (e.g., a library version bump)

### Can't Delete Version

```bash
# Check protections
fbpkg inuse-check <package_name>:<version> --where --count
fbpkg info <package_name>                                      # DEFAULT?

# Fix (WRITE):
fbpkg delete --force <package_name>:<version>
```

### Extend Ephemeral TTL

```bash
# Check
fbpkg info <package_name>:<uuid>

# Fix (WRITE):
fbpkg expire <package_name>:<uuid> 14d        # Relative
fbpkg expire <package_name>:<uuid> 2024-12-31 # Absolute
```

## Common Errors

### "All existing versions are in use"

All 10 versions protected; can't build 11th.

```bash
# Fix (WRITE):
fbpkg meta <package_name> --version-limit 20
fbpkg delete --force <package_name>:<old_version>
fbpkg meta <package_name> --inuse-check=''     # Disable protection (dangerous)
```

### "Version not found"

```bash
fbpkg versions <package_name> --show-deleted --ephemeral-limit 50 --regular-limit 50 | grep <version>
# Fix (WRITE):
fbpkg restore <package_name>:<version>
```

### "Cannot archive ephemeral"

```bash
# Fix (WRITE):
fbpkg preserve <package_name>:<uuid>
fbpkg archive <package_name>:<new_version>
```

ACL-free ephemerals cannot be preserved — rebuild with ACL.

### "Ephemeral expired"

```bash
# If within 1 day (WRITE):
fbpkg restore <package_name>:<uuid>
fbpkg preserve <package_name>:<uuid>

# Otherwise: rebuild
```

## Best Practices

**Production:** No ephemerals. Tag releases (`prod`, `stable`). Archive critical versions. Limit 20-30.

**Development:** Use ephemerals for testing. Preserve before deploying. Clean up old versions.

**Owners:** Set appropriate limits. Monitor with `fbpkg inuse-check <package_name> --where`. Document tag conventions.

## Quick Tips

1. Preserving keeps UUID but adds version number
2. Tags don't cascade — moving tagB doesn't move tagA
3. In-use checks are cached — changes take time
4. Max ephemeral TTL: 28 days from build
5. DEFAULT follows LATEST unless changed

## Investigating Expired/Purged Versions

When a version has been deleted and is past the retention window (so `fbpkg info --show-deleted` and `fbpkg restore` no longer work), you can still find build metadata via Scuba or Hive.

### Step 1: Query Scuba (preferred — faster, simpler)

The `fbpkg_build` Scuba table has the same data with limited retention (~30 days). Try this first:

```bash
scuba -e "
  SELECT time, caller, hostname, revision, build_command, message, tw_context, package_size
  FROM fbpkg_build
  WHERE package = '<package_name>'
    AND package_uuid = '<uuid>'
  LIMIT 10
" --format sparse
```

### Step 2: Fall back to Hive (longer retention)

If Scuba doesn't have the data (build is older than ~30 days), query the `scuba_fbpkg_build` Hive table via Presto. This has much longer retention.

```bash
presto infrastructure --execute "
  SELECT time, caller, hostname, revision, build_command, message, tw_context, package_size
  FROM scuba_fbpkg_build
  WHERE package = '<package_name>'
    AND package_uuid = '<uuid>'
    AND ds >= '2025-01-01'
  LIMIT 10
"
```

### Key columns

| Column | Description |
|--------|-------------|
| `package` | Package name (e.g., `ip.python_predictor_concord`) |
| `package_uuid` | Version UUID |
| `caller` | Who built it (`root` = Sandcastle CI) |
| `hostname` | Build host |
| `revision` | Source revision hash |
| `upstream` | Upstream revision |
| `build_command` | Full build command (includes flags like `--ephemeral`, `--expire`) |
| `message` | Build message (e.g., buck target) |
| `tw_context` | TW job that ran the build (for CI builds) |
| `time` | Unix timestamp of the build |
| `package_size` | Package size in bytes |

### Identifying ephemeral builds from the output

The `build_command` column contains the full `fbpkg build` invocation. Look for:
- `--ephemeral` — confirms it was an ephemeral build
- `--expire=<duration>` — the TTL (e.g., `1209600s` = 14 days)
- `--acl-free-uuid-only` — ACL-free ephemeral (cannot be preserved)

### When to use this

- `fbpkg info <pkg>:<uuid>` returns "version not found" and `--show-deleted` also fails
- You need to find who built a version, when, and from what source revision
- Investigating why a CM or TW job references a non-existent fbpkg version

## Resources

- [Wiki](https://www.internalfb.com/wiki/Fbpkg/)
- [FAQ](https://www.internalfb.com/wiki/Fbpkg/FAQ/)
- [Commands](https://www.internalfb.com/wiki/Fbpkg/management/)
- [fbpkg Users Group](https://fb.workplace.com/groups/fbpkg) — **Post errors, questions here**
- [Oncall](https://fburl.com/oncall/fbpkg)
