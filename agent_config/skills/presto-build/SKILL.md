---
name: presto-build
description: Use when building Presto from source for local development — compiling Java modules, running full builds (OSS + FB trunk), building C++ native binaries, running tests, or checking style. Does NOT cover Nexus deployment, fbpkg packaging, or cluster operations — see presto-deploy for those.
---

# Presto Build

## Overview

Local development build tool for Presto Java and C++ codebases.

**Prerequisites:** JDK 17, Maven, `buck2` (for C++ builds)

**Key script:** `~/.claude/skills/presto-build/presto-build`

**Shell aliases** (from `~/.localrc`): `gf`/`gp` navigate to presto-facebook-trunk/presto-trunk. `mfci`, `mfi`, `mpci` run Maven with correct flags and `-T 48` threads. `mfcc` runs checkstyle. All aliases trigger eden prefetch first. OSS aliases (`mpi`, `mpci`, etc.) use `-pl $_p_modules -am` to build only needed modules. FB aliases (`mfi`, `mfci`, etc.) use `-pl presto-facebook -am`. Override with `-pl <module>` to target a different module (Maven uses the last `-pl`).

**Related skills:**
- `presto-deploy` — Nexus deployment, fbpkg packaging, cluster deployment
- `presto-test` — Post-deployment validation (verifier, goshadow, BEEST)

## IMPORTANT: When NOT to Build

**Building C++ from source takes ~3 hours** (128K+ buck2 actions for an opt build). Before building, ask: **do I actually need a new binary, or can I reuse an existing one?**

- **Deploying to a test cluster for testing?** → Do NOT build. Use `pt pcm deploy -pv <release_version>`. See `presto-deploy` skill.
- **Running an A/B test toggling a config property?** → Do NOT build. Both arms use the same binary. Just deploy and toggle config.
- **Testing Java-only code changes?** → Build Java only. Do NOT build C++. Workers already have a C++ binary from `cpp-prod`.
- **Testing C++ code changes?** → This is the ONLY case where building C++ from source is necessary.

**Build durations** (empirical, on devvm with RE):

| Build type | Command | Duration |
|---|---|---|
| Java module (incremental) | `presto-build -I -l <module>` | ~5 min |
| Java FB trunk only | `presto-build -T` | ~15 min |
| Java full (OSS subset + FB) | `presto-build` | ~20-25 min |
| C++ dev (local, no opt) | `presto-build -n` | ~15 min |
| C++ opt (fbpkg) | `presto-deploy -n` | **~3 hours** |

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

Full builds run `mvn clean install` on a subset of OSS trunk (only the ~40 modules needed by FB trunk, plus transitive deps via `-am`), then FB trunk with `-pl presto-facebook -am`. The module list is defined in `OSS_MODULES` in the build script and `_p_modules` in `~/.localrc`. If a build fails with an unresolved OSS artifact, add the missing module to both lists.

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

| Mode | Buck mode | Optimization | LTO | BOLT PGO | FDO | Use case |
|------|-----------|---|---|---|---|---|
| dev | (none) | -O0 | No | No | No | Local iteration (default, fast) |
| opt | `@mode/opt` | -O3 | No | No | No | Optimized local testing; fair for A/B comparisons |
| asan | `@mode/opt-asan` | -O3 | No | No | No | Memory error detection |
| tsan | `@mode/opt-tsan` | -O3 | No | No | No | Data race detection |
| dbgo | `@mode/dbgo` | -Og | No | No | No | Debug with optimization |

`bolt` mode is not available for local builds — it requires the fbpkg pipeline (`presto-deploy -n -m bolt`). BOLT uses `@mode/opt-clang-thinlto` which enables LTO, and the Prestissimo binary target has a BOLT profile that activates under LTO. See `presto-deploy` for details on when bolt vs opt matters.

**`@mode/opt` is PGO-free.** The default AutoFDO profile was removed from fbcode in August 2024, and Prestissimo is not registered in the centralized AutoFDO refresh pipeline. BOLT only activates under LTO modes. So `@mode/opt` gives you -O3 with no profile-guided optimizations of any kind.

### C++ fbpkg Packaging

When packaging C++ for deployment (via `presto-deploy -n` or manually), `fbpkg build` is used instead of `buck2 build`:

```bash
fbpkg build fbcode//fb_presto_cpp:presto.presto_cpp          # opt (default)
fbpkg build fbcode//fb_presto_cpp:presto.presto_cpp_bolt     # bolt
fbpkg build fbcode//fb_presto_cpp:presto.presto_cpp_asan     # asan
```

**`fbpkg build` rejects untracked files in the repo.** If you have local `etc-local/` dirs or other untracked files, move them out of the repo before running `fbpkg build`, then restore them after. Common offenders:
- `fbcode/fb_presto_cpp/etc-local/`
- `fbcode/github/presto-facebook-trunk/presto-facebook-main/etc-local/`

## Maven Flag Reference

The build script uses these Maven flags (shared with `presto-deploy` via sourcing):

**Common (all builds):** `-Dmaven.gitcommitid.skip=true`, `-Dlicense.report.skip=true`, `-Djava.net.preferIPv6Addresses=true`, `-DskipUI`, OS detection flags, `-Dout-of-tree-build=true`, `-T 48`

**OSS additions:** `-Dmaven.javadoc.skip=true`, `-Dout-of-tree-build-root=$BUILD_ROOT/presto-trunk`, `-pl $OSS_MODULES -am` (builds only the ~40 modules needed by FB trunk; transitive deps like `presto-matching`, `presto-hive`, `presto-function-namespace-managers` are resolved by `-am`)

**FB additions:** `-DuseParallelDependencyResolution=false`, `-nsu`, `-DwithPlugins=true`, `-Dout-of-tree-build-root=$BUILD_ROOT/presto-facebook-trunk`, `-pl presto-facebook -am`

Build output goes to `$BUILD_ROOT` (`/data/users/$USER/builds` by default).

## Common Issues

| Problem | Fix |
|---------|-----|
| Build fails on checkstyle | `presto-build -C` to skip, or `gf && mfcc` to run checkstyle only |
| Eden mount slow/stale | `eden prefetch 'fbcode/github/presto-*-trunk/**'` |
| Module not found | Ensure CWD is inside the correct repo (`presto-trunk` or `presto-facebook-trunk`) |
| OOM during build | Reduce thread count or use `-l` for targeted module build |
| C++ build fails | Ensure `buck2` is available; check `buck2 build fbcode//fb_presto_cpp:main` directly |
