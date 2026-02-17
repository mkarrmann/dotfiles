---
name: presto-build
description: Use when building Presto from source for local development — compiling Java modules, running full builds (OSS + FB trunk), building C++ native binaries, running tests, or checking style. Does NOT cover Nexus deployment, fbpkg packaging, or cluster operations — see presto-deploy for those.
---

# Presto Build

## Overview

Local development build tool for Presto Java and C++ codebases.

**Prerequisites:** JDK 17, Maven, `buck2` (for C++ builds)

**Key script:** `~/.claude/skills/presto-build/presto-build`

**Shell aliases** (from `~/.localrc`): `gf`/`gp` navigate to presto-facebook-trunk/presto-trunk. `mfci`, `mfi`, `mpci` run Maven with correct flags and `-T 48` threads. `mfcc` runs checkstyle. All aliases trigger eden prefetch first.

**Related skills:**
- `presto-deploy` — Nexus deployment, fbpkg packaging, cluster deployment
- `presto-test` — Post-deployment validation (verifier, goshadow, BEEST)

## Quick Reference

| Task | Command |
|------|---------|
| Full build (OSS + FB) | `presto-build` |
| Full build, skip OSS | `presto-build -T` |
| Module build (auto-detect) | `presto-build -l <module>` |
| Module build, incremental | `presto-build -I -l <module>` |
| Run tests for a module | `presto-build -t -l <module>` |
| Skip checkstyle | `presto-build -C` |
| C++ dev binary | `presto-build -n` |
| C++ ASAN binary | `presto-build -n -m asan` |
| Alias: FB module build | `gf && mfci -pl <module>` |
| Alias: OSS module build | `gp && mpci -pl <module> -am` |
| Alias: checkstyle only | `gf && mfcc` |

## Java Builds

### Module builds (fast iteration)

```bash
# Auto-detects repo from CWD, always uses -am
presto-build -l <module-name>

# Incremental (no clean, faster when only source changed)
presto-build -I -l <module-name>

# Multiple modules
presto-build -l presto-main,presto-spi
```

CWD must be inside `presto-trunk` or `presto-facebook-trunk` for auto-detection.

### Full builds

```bash
# OSS + FB trunk
presto-build

# Skip OSS (when only presto-facebook-trunk changed)
presto-build -T

# Skip checkstyle (faster iteration)
presto-build -C
```

Full builds run `mvn clean install` on OSS trunk first, then FB trunk with `-pl presto-facebook -am`.

### Tests

```bash
# Run tests for a module (mvn test -P ci)
presto-build -t -l <module-name>
```

### Checkstyle

```bash
# Via alias (checkstyle only, no build)
gf && mfcc

# Via build with checkstyle skipped
presto-build -C
```

## C++ Builds (local only)

Builds the C++ Prestissimo binary via `buck2 build`.

```bash
presto-build -n                  # dev mode (default, fast)
presto-build -n -m opt           # optimized
presto-build -n -m asan          # address sanitizer
presto-build -n -m tsan          # thread sanitizer
presto-build -n -m dbgo          # debug optimized
```

| Mode | Buck mode | Use case |
|------|-----------|----------|
| dev | (none) | Local iteration (default) |
| opt | `@mode/opt` | Optimized local testing |
| asan | `@mode/opt-asan` | Memory error detection |
| tsan | `@mode/opt-tsan` | Data race detection |
| dbgo | `@mode/dbgo` | Debug with optimization |

`bolt` mode is not available for local builds — it requires the fbpkg pipeline. Use `presto-deploy -n -m bolt` instead.

## Maven Flag Reference

The build script uses these Maven flags (shared with `presto-deploy` via sourcing):

**Common (all builds):** `-Dmaven.gitcommitid.skip=true`, `-Dlicense.report.skip=true`, `-Djava.net.preferIPv6Addresses=true`, `-DskipUI`, OS detection flags, `-Dout-of-tree-build=true`, `-T 48`

**OSS additions:** `-Dmaven.javadoc.skip=true`, `-Dout-of-tree-build-root=$BUILD_ROOT/presto-trunk`

**FB additions:** `-DuseParallelDependencyResolution=false`, `-nsu`, `-DwithPlugins=true`, `-Dout-of-tree-build-root=$BUILD_ROOT/presto-facebook-trunk`

Build output goes to `$BUILD_ROOT` (`/data/users/$USER/builds` by default).

## Common Issues

| Problem | Fix |
|---------|-----|
| Build fails on checkstyle | `presto-build -C` to skip, or `gf && mfcc` to run checkstyle only |
| Eden mount slow/stale | `eden prefetch 'fbcode/github/presto-*-trunk/**'` |
| Module not found | Ensure CWD is inside the correct repo (`presto-trunk` or `presto-facebook-trunk`) |
| OOM during build | Reduce thread count or use `-l` for targeted module build |
| C++ build fails | Ensure `buck2` is available; check `buck2 build fbcode//fb_presto_cpp:main` directly |
