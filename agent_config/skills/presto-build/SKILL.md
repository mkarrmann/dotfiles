---
name: presto-build
description: Use when building Presto from source for local development — compiling Java modules, running full builds (OSS + FB trunk), building C++ native binaries, running tests, or checking style. Does NOT cover Nexus deployment, fbpkg packaging, or cluster operations — see presto-deploy for those.
---

# Presto Build

## Overview

Local development build tool for Presto Java and C++ codebases.

**Prerequisites:** JDK 17, Maven, `buck2` (for C++ builds)

> ### ⚠️ CRITICAL: JDK 17 vs the default JDK 8
> The build requires **JDK 17**, but the *system default* `java` is **JDK 8**. The `presto-build` script (and `presto-deploy`, which sources it) **now auto-pin JDK 17** — so if you use the scripts, this is handled for you. Override the path with `PRESTO_JDK17=...` if your JDK 17 lives elsewhere; the script fails fast if no JDK 17 is found.
>
> You only need to set it **manually** when bypassing the scripts with raw `mvn`/`buck2`/`fbpkg` in a **non-interactive shell** (Claude Code's Bash tool, scripts, cron, CI) — those do NOT source `~/.localrc`, so they fall back to JDK 8:
> ```bash
> export JAVA_HOME=/usr/local/java-runtime/impl/17 && export PATH=$JAVA_HOME/bin:$PATH
> java -version   # must report 17.x
> ```
> Interactive terminals are fine — `~/.localrc` exports JDK 17 and the `mfi`/`mpi`/`gf` aliases rely on it.
>
> Why it's sneaky: the poms don't pin `maven.compiler.release`, so JDK 8 does **not** fail fast with "release 17 unsupported" — it compiles against the JDK 8 class library and only blows up later on a Java 11+ API, surfacing as **confusing "cannot find symbol" errors in seemingly unrelated modules** (e.g. `OptionalInt.isEmpty()` in `presto-impulse`). Don't chase those as real code bugs — check `java -version` first. (The "jdk8" comment on line 10 of `~/.localrc` is stale; the path is `impl/17`.)

**Key script:** `~/.claude/skills/presto-build/presto-build`

**Shell functions** (from `~/.localrc`): `gf`/`gp` navigate to presto-facebook-trunk/presto-trunk. `mfci`, `mfi`, `mpci` run Maven with correct flags and `-T 48` threads. `mfcc` runs checkstyle. All functions trigger eden prefetch first. The functions auto-detect which workspace you're in (`~/checkout1/fbsource` is primary; `~/checkout2/fbsource` and `~/checkout3/fbsource` are non-primary) and isolate the out-of-tree build root and Maven local repo accordingly (non-primary workspaces use `${BUILD_ROOT}/m2-repo-checkout2` or `${BUILD_ROOT}/m2-repo-checkout3` instead of `~/.m2/repository`). Override with `-pl <module>` to target a different module (Maven uses the last `-pl`). **These functions are not available in Claude Code's Bash tool.** When using them, you MUST `cd` to the correct directory first because the functions do not navigate for you:
- OSS (`mpi`, `mpci`, etc.): `cd <checkout>/fbcode/github/presto-trunk && source ~/.localrc && mpi`
- FB trunk (`mfi`, `mfci`, etc.): `cd <checkout>/fbcode/github/presto-facebook-trunk && source ~/.localrc && mfi`

Running from the wrong directory fails with `Could not find the selected project in the reactor`.

**Related skills:**
- `presto-deploy` — Nexus deployment, fbpkg packaging, cluster deployment
- `presto-test` — Post-deployment validation (verifier, goshadow, BEEST)

## IMPORTANT: Always Use the Scripts

**Never run `buck2 build` or `mvn` directly.** Always use `presto-build` (or `presto-deploy` for packaging). The scripts handle:
- **IPv6 networking** (`-Djava.net.preferIPv6Addresses=true`) — Meta devservers often only have IPv6 routes to Maven Nexus
- **Out-of-tree build isolation** — per-checkout build roots prevent clobbering
- **Correct buck2 mode flags** — `@fbcode//mode/opt` (not `@mode/opt`, which fails from the fbsource root)
- **Checkout detection** — automatically uses the right Maven local repo for secondary checkouts

Running commands manually will hit these issues and waste tokens debugging them.

## IMPORTANT: `-n` Builds BOTH Java And C++ — It Is The WHOLE Local Cluster In One Command

`presto-build -n` runs a **full Java build first, then the C++ binary** (see `build_java_*` then `build_cpp_local` in the script's main flow). A single `presto-build -n` produces everything a local **coordinator + Prestissimo worker** needs.

**So for local coordinator+worker dev, the answer is exactly one command: `presto-build -n`.** Do NOT also run a plain `presto-build` for the coordinator — the Java coordinator is already built by `-n`. Issuing a second build is pure duplicated work, and running two builds at once in the same checkout actively **corrupts** the shared Maven repo / out-of-tree build roots (one build's `mvn clean` wipes `target/` dirs the other is mid-compile against → spurious "cannot find symbol" errors in unrelated modules that look like a JDK problem but are not).

Rules of thumb:
- **Need Java only** (coordinator, Java connector/optimizer change) → `presto-build` (no `-n`).
- **Need C++ / a Prestissimo worker** (and therefore also a coordinator to talk to) → `presto-build -n`. One command. That's it.
- **Never run two `presto-build`/`presto-deploy` at once in the same checkout.** If you genuinely need parallelism, use a *different* checkout (`~/checkout2`, `~/checkout3`) — each has isolated build state.

## IMPORTANT: When NOT to Build

**Building C++ from source takes ~3 hours** (128K+ buck2 actions for an opt build). Before building, ask: **do I actually need a new binary, or can I reuse an existing one?**

- **Deploying to a test cluster for testing?** → Do NOT build. Use `pt pcm deploy -pv <release_version>`. See `presto-deploy` skill.
- **Running an A/B test toggling a config property?** → Do NOT build. Both arms use the same binary. Just deploy and toggle config.
- **Testing Java-only code changes?** → `presto-build` (no `-n`). Do NOT build C++. Workers already have a C++ binary from `cpp-prod`.
- **Testing C++ code changes?** → This is the ONLY case where building C++ from source is necessary. Use `presto-build -n` — it builds the Java coordinator too, so you do NOT need a second command.

**Build durations** (empirical, on devvm with RE):

| Build type | Command | Duration |
|---|---|---|
| Java module (incremental, default) | `presto-build -l <module>` | ~5 min |
| Java FB trunk only | `presto-build -T` | ~15 min |
| Java full (OSS subset + FB) | `presto-build` | ~20-25 min |
| Java full + C++ dev binary (coordinator **and** worker) | `presto-build -n` | ~Java + ~15 min C++ |
| C++ opt (fbpkg) | `presto-deploy -n` | **~3 hours** |

## IMPORTANT: Builds Are Incremental By Default — Do NOT Force A Clean Rebuild

`presto-build` reuses prior compilation by default (incremental). **A clean rebuild (`mvn clean`) throws away everything and recompiles from scratch — it is dramatically slower** (adds ~20 min to a Java build). Only force a clean build when incremental is genuinely broken by stale state:
- A class/file was **renamed or deleted** and a stale `.class` lingers.
- A **dependency version changed** (`pom.xml` version bump).
- You see errors that clearly reference code that no longer exists.

To force a clean build, pass **`-c`**. Do **not** reach for it reflexively when a build fails — read the error first; a clean rebuild rarely fixes a real compile error and just wastes 20 minutes. (`-I` still exists as a backward-compatible no-op since incremental is now the default.)

## Quick Reference

| Task | Command |
|------|---------|
| Full build (OSS + FB), incremental | `presto-build` |
| Full build, forced clean (SLOW; only if stale) | `presto-build -c` |
| Full build, skip OSS | `presto-build -T` |
| Module build (auto-detect) | `presto-build -l <module>` |
| Run tests for a module | `presto-build -t -l <module>` |
| Skip checkstyle | `presto-build -C` |
| C++ dev binary only (nothing Java changed) | `buck2 build fbcode//fb_presto_cpp:main` |
| Java coordinator + C++ worker | `presto-build -n` |
| C++ ASAN binary | `presto-build -n -m asan` |
| Alias: FB module build | `gf && mfci -pl <module>` |
| Alias: OSS module build | `gp && mpci -pl <module> -am` |
| Alias: checkstyle only | `gf && mfcc` |

## IMPORTANT: Build Only The Layer That Changed

Before building, identify which layer your change touches, and build only that:
- **C++ only** (`velox/`, `dwio/`, `fb_presto_cpp/`, etc.) → the Java coordinator is unaffected. If you already have a working Java build, build **just the worker**: `buck2 build fbcode//fb_presto_cpp:main`. Do NOT run `presto-build -n` (which rebuilds all of Java too) unless your Java artifacts are missing/stale.
- **Java only** → `presto-build` (no `-n`). Do NOT build C++.
- **Both** → `presto-build -n` (one command; see below).

Checking what changed is cheap (`sl status` / the diff's file list); a needless full rebuild is not.

## Java Builds

### Module builds (fast iteration)

```bash
# Auto-detects repo from CWD, always uses -am. Incremental by default.
presto-build -l <module-name>

# Force a clean rebuild (SLOW) only if incremental is broken by stale state
presto-build -c -l <module-name>

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

Full builds run `mvn install` (incremental by default; add `-c` for `mvn clean install`) on a subset of OSS trunk (only the ~40 modules needed by FB trunk, plus transitive deps via `-am`), then FB trunk with `-pl presto-facebook -am`. The module list is defined in `OSS_MODULES` in the build script and `_p_modules` in `~/.localrc`. If a build fails with an unresolved OSS artifact, add the missing module to both lists.

### Tests

```bash
# Run tests for a module (mvn test -P ci)
presto-build -t -l <module-name>

# Run a single test method in OSS trunk (must include useManifestOnlyJar=false; see below)
mpt -pl presto-tests -Dtest=TestLocalQueries#testIOExplainForUnsupportedStatements -Dsurefire.failIfNoSpecifiedTests=false -Dsurefire.useManifestOnlyJar=false
```

**Targeted test runs in OSS trunk require `-Dsurefire.useManifestOnlyJar=false`.** Without it, surefire's forked VM dies with "Could not find or load main class org.apache.maven.surefire.booter.ForkedBooter" or "The forked VM terminated without properly saying goodbye". The OSS trunk's `out-of-tree-build` profile (`pom.xml`) symlinks `target/` to `${BUILD_ROOT}/presto-trunk/${project.groupId}:${project.artifactId}` — the `:` in that directory name (from `com.facebook.presto:presto-tests` etc.) breaks surefire's manifest-only booter jar load. Disabling the booter jar makes surefire pass the classpath directly via `-cp` and sidesteps the issue. Also add `-Dsurefire.failIfNoSpecifiedTests=false` so dependent modules built via `-am` don't error when the test pattern doesn't match anything in them.

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
| opt | `@fbcode//mode/opt` | -O3 | No | No | No | Optimized local testing; fair for A/B comparisons |
| asan | `@fbcode//mode/opt-asan` | -O3 | No | No | No | Memory error detection |
| tsan | `@fbcode//mode/opt-tsan` | -O3 | No | No | No | Data race detection |
| dbgo | `@fbcode//mode/dbgo` | -Og | No | No | No | Debug with optimization |

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
| "cannot find symbol" on Java 11+ APIs (e.g. `OptionalInt.isEmpty()`) in unrelated modules like `presto-impulse` | You're on JDK 8. Non-interactive shells don't source `~/.localrc`. Run `export JAVA_HOME=/usr/local/java-runtime/impl/17 && export PATH=$JAVA_HOME/bin:$PATH`, verify `java -version` → 17. See the CRITICAL callout in Overview. |
| Surefire "Could not find or load main class ForkedBooter" or "VM terminated without properly saying goodbye" when running targeted tests in OSS trunk | Add `-Dsurefire.useManifestOnlyJar=false`. Caused by the `:` in the out-of-tree-build symlink target (`com.facebook.presto:<artifact>`). See Tests section. |
| Pre-existing trunk compile error (unrelated test file) | Add `-Dmaven.test.skip=true` to skip test compilation, or skip the failing module with `-pl '!<module>'` |
| Eden mount slow/stale | `eden prefetch 'fbcode/github/presto-*-trunk/**'` |
| Module not found | Ensure CWD is inside the correct repo (`presto-trunk` or `presto-facebook-trunk`) |
| OOM during build | Reduce thread count or use `-l` for targeted module build |
| C++ build fails | Ensure `buck2` is available; use `presto-build -n -m opt` (never run `buck2 build` directly) |
