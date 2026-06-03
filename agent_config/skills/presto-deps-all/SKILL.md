---
name: presto-deps-all
description: Use when adding, exposing, or updating a Java dependency in presto-deps-all — the Buck→Maven bridge jar consumed by presto-facebook-trunk. Covers BUCK edits (deps, shading rules, unshading), publishing to Nexus via buck2nexus, bumping the consumer pom.xml, and known gotchas (IPv6, shading is relocation, version format). Use when you need a buck-built Java target to be importable from a Maven module in `fbcode/github/presto-facebook-trunk`.
---

# presto-deps-all

## Overview

`presto-deps-all` is a `java_shaded_jar` Buck target at `//fbjava/presto-facebook-deps:presto-deps-all` that bundles a curated set of buck-built Java targets into a single Maven artifact (`com.facebook.presto:presto-deps-all:1.0-<timestamp>-<rev>`) published to Meta Nexus. It exists because `fbcode/github/presto-facebook-trunk` is a Maven build that can't depend on buck targets directly — `presto-deps-all` is the bridge.

**Key files:**
- `fbcode/fbjava/presto-facebook-deps/BUCK` — dep list + shading rules
- `fbcode/tools/build/buck/java/buck2nexus/config.json` — publisher config (entry: `//fbjava/presto-facebook-deps:presto-deps-all`)
- `fbcode/github/presto-facebook-trunk/pom.xml` (~line 1601) — parent dependencyManagement, declares the version consumed
- Module poms (e.g. `presto-gateway/pom.xml`) reference the artifact but **inherit version from the parent** — never add `<version>` to a module pom

**Related skills:** `presto-build` (compile the consuming Maven modules), `presto-deploy` (fbpkg packaging of the running server)

## When to Use

- Adding a new Buck target so it can be `import`-ed from `presto-facebook-trunk` Java code
- Unshading an already-bundled package (moving classes out of `fbshaded0.*` or `com.facebook.presto.$internal.com.facebook.*`)
- Updating to a newer version of an upstream dep
- Diagnosing a Maven compile failure like `package com.facebook.X does not exist` when the class should be in `presto-deps-all`

## End-to-End Workflow

Three commits + one operator publish:

```
1. Edit BUCK (deps + shading rules)         → commit
2. Update any callers being un-/re-shaded   → SAME commit (shading is relocation, callers must match)
3. Run buck2nexus → capture new version     → operator (15-30 min)
4. Bump pom.xml to new version              → commit (separate)
5. Verify consumer module compiles
```

The first two are usually one logical commit. The pom bump must be a separate commit because it depends on the published version that doesn't exist until step 3 finishes.

### Step 1-2: Edit BUCK and (if needed) callers

Edit `fbcode/fbjava/presto-facebook-deps/BUCK`:

**To add a new dep:** append to the `deps = [...]` block (around lines 191-260):
```python
deps = [
    ...existing entries...
    "//thrift/lib/java/facebook/service-framework:service-framework",
    "//infrasec/authorization/java/aclcheckerhandler_jni:ThriftAclCheckerHandlerJNI",
]
```

**To control how a package is exposed:** add to the `shade = [...]` block (lines 96-190). Each entry is `(src, dst, includes, excludes)`:
- Identity (unshade): `("com.facebook.serviceframework", "com.facebook.serviceframework", [], [])` — keep package at its natural path
- Relocate: `("com.facebook.x2p.proxyclient", "fbshaded0.com.facebook.x2p.proxyclient", [], [])` — move to a different package
- The catch-all near line 142 is `("com.facebook", "com.facebook.presto.$internal.com.facebook", [], [])` — anything not caught by an earlier rule gets shaded into `$internal`

**Rule ordering matters — first match wins.** Add specific rules BEFORE the catch-all at line 142.

**If unshading a previously-relocated package:** find existing consumers and update their imports in the SAME commit. Shading is a relocation, not a copy — the old path stops working the instant the jar is republished.

```bash
# Find consumers of a relocated package before unshading
grep -rln 'fbshaded0\.com\.facebook\.<package>' fbcode/github/presto-facebook-trunk/
```

Commit:
```bash
sl commit -m "[build] <short description of what changed>

Summary:
What and why.

Test Plan:
Build verification deferred to after Nexus republish + pom bump." \
  fbcode/fbjava/presto-facebook-deps/BUCK \
  <any updated callers>
```

### Step 3: Publish to Nexus (operator action)

`buck2nexus` does the build AND the upload in one shot. Do not run `buck2 build` separately first.

```bash
# Make sure MAVEN_OPTS includes IPv6 prefs — see Gotchas
source ~/.localrc   # if it has the MAVEN_OPTS block

LOG="/tmp/buck2nexus-$(date +%s).log"
cd <fbsource>
buck2 run tools/build/buck/java/buck2nexus:buck2nexus -- \
    //fbjava/presto-facebook-deps:presto-deps-all \
    --ignore-repo-state &> "$LOG"
echo "exit: $?"
echo "log: $LOG"

# Extract the new version (regex: 12-digit timestamp + 12-char git rev)
grep -oP '1\.0-\d{12}-[a-f0-9]{12}' "$LOG" | tail -1
```

`--ignore-repo-state` lets you publish from a dirty repo (otherwise it refuses). Always safe; the published jar reflects the working-tree content at the moment of build.

Expect 15-30 minutes. Tail the log in another shell to watch progress.

### Step 4: Bump consumer pom.xml

Edit `fbcode/github/presto-facebook-trunk/pom.xml` — the `<version>` line immediately after `<artifactId>presto-deps-all</artifactId>` in the `<dependencyManagement>` block (around line 1601):

```xml
<dependency>
    <groupId>com.facebook.presto</groupId>
    <artifactId>presto-deps-all</artifactId>
    <version>1.0-<NEW_VERSION></version>   <!-- bump this line -->
</dependency>
```

**Do NOT** add `<version>` to any module-level pom (e.g. `presto-gateway/pom.xml`). They inherit from the parent.

Commit:
```bash
sl commit -m "[build] Bump presto-deps-all to <NEW_VERSION>" \
  fbcode/github/presto-facebook-trunk/pom.xml
```

### Step 5: Verify

```bash
cd fbcode/github/presto-facebook-trunk
./mvnw -pl presto-gateway -am clean install -DskipTests
```

Or any module that consumes the new dep. If a class still resolves to the old shaded path, you missed a shading rule.

## Gotchas

### IPv6 — maven.thefacebook.com has no A record

`mvn deploy` (the upload step) is a Java process that defaults to preferring IPv4. `maven.thefacebook.com` resolves ONLY to an AAAA record on Meta devvms. Without IPv6 prefs, Maven exits with `Network is unreachable` instead of falling back to IPv6.

**Fix permanently:** add to `~/.localrc`:
```bash
case " ${MAVEN_OPTS} " in
    *" -Djava.net.preferIPv6Addresses=true "*) ;;
    *) export MAVEN_OPTS="${MAVEN_OPTS} -Djava.net.preferIPv6Addresses=true" ;;
esac
```

**Fix inline:** prefix the command with `MAVEN_OPTS="-Djava.net.preferIPv6Addresses=true"`.

`curl` works without help because it (and the OS) handle IPv6 fallback correctly. Java doesn't.

### Shading is relocation, not copy

If a package is shaded `(A → B)` in BUCK, the jar contains classes at path `B` only — path `A` is gone. Consumers of `A` get `package A does not exist` at compile time. Always update consumers in the same commit as the shading change.

### Default shading hides most com.facebook.* deps

The catch-all at line 142 relocates everything under `com.facebook.*` (that isn't caught by an earlier explicit rule) to `com.facebook.presto.$internal.com.facebook.*`. If you add a new Buck dep and try to `import com.facebook.foo.Bar;` from Maven code without adding a corresponding unshade rule, the compile will fail.

Always add an unshade rule for any new package you want to expose:
```python
("com.facebook.foo", "com.facebook.foo", [], []),
```
Place it before line 142.

### Two existing prefixes for shaded classes

| Prefix | Why |
|---|---|
| `com.facebook.presto.$internal.com.facebook.*` | The catch-all default for `com.facebook.*` |
| `fbshaded0.com.facebook.*` | Explicit relocations for "cat-for-presto-shaded classes" (CAT token provider, etc.) — see existing rules around line 110 |

Both exist for historical reasons. Don't add new `fbshaded0` mappings — prefer unshade-or-leave-alone.

### Version format is 12+12, not 14+8

The Maven version `presto-deps-all` produces is `1.0-<YYYYMMDDHHMM>-<12-char git rev>` — a 12-digit timestamp and a 12-char hex git revision. The `presto-ws-test` skill's regex `1\.0-\d{14}-[a-f0-9]{8}` is for the WarmStorage deps-all artifact, NOT this one. Use:

```
1\.0-\d{12}-[a-f0-9]{12}
```

### buck2nexus does build+upload in one shot

Do NOT run `buck2 build //fbjava/presto-facebook-deps:presto-deps-all` first. `buck2 run tools/build/buck/java/buck2nexus:buck2nexus` does the build internally as part of publishing. Running build separately just wastes time and may confuse the publisher's state checks.

### Module-pom versions are inherited

`presto-gateway/pom.xml` (and any other module that consumes `presto-deps-all`) has:
```xml
<dependency>
    <groupId>com.facebook.presto</groupId>
    <artifactId>presto-deps-all</artifactId>
</dependency>
```
NO `<version>` tag. The version is resolved from the parent's `<dependencyManagement>`. Never add `<version>` to module poms — Maven will warn at build time and reviewers will complain.

### Dirty repo state

`buck2nexus` by default refuses to publish from a repo with uncommitted changes (so the published version's git rev is meaningful). Pass `--ignore-repo-state` to override, or commit cleanly first. The `--ignore-repo-state` is generally fine for iterative work — the jar always reflects the working tree.

### Build takes 15-30 minutes

Plan ahead. Don't kick off a republish during a context-window-constrained workflow. Tail the log in a separate shell if you want progress visibility.

## Common Failure Modes

| Symptom | Cause | Fix |
|---|---|---|
| `Network is unreachable -> [Help 1]` during `mvn deploy` | Java preferring IPv4, no A record for maven.thefacebook.com | Add `-Djava.net.preferIPv6Addresses=true` to `MAVEN_OPTS` (see Gotchas) |
| `package com.facebook.X does not exist` in consumer Maven module after presto-deps-all bump | Missing unshade rule for `com.facebook.X` (catch-all relocated it) | Add identity unshade rule before line 142, republish, bump pom |
| `Could not extract jar version` from log | buck2nexus log format drifted | Inspect log manually; look for `Uploaded artifact ... version=...` |
| Compile error `package fbshaded0.com.facebook.X does not exist` after intentional unshade | Missed updating a caller's import | `grep -rln fbshaded0\.com\.facebook\.X fbcode/github/presto-facebook-trunk/` and fix imports |
| `Refusing to publish: dirty repo` | Uncommitted changes | Commit cleanly OR pass `--ignore-repo-state` |
| Nexus 409 / version conflict | Same timestamp-rev as a prior upload (rare — minute precision) | Wait 60s, rebuild |
| Maven build fails in unrelated modules (e.g. `presto-prism-metastore`, `presto-facebook-hive`) | Pre-existing breakage in trunk, not caused by your change | Don't try to fix it. Verify with `mvn -pl <your-module> compile -DskipTests`. Record skip reason in commit's Test Plan. |

## Reference: BUCK File Structure

```python
java_shaded_jar(
    name = "presto-deps-all",
    ...
    shade = [
        # Identity rules (unshade) — keep package at natural path.
        # Place specific rules before the catch-all on ~line 142.
        ("com.facebook.thrift", "com.facebook.thrift", [], []),
        ("com.facebook.swift", "com.facebook.swift", [], []),
        ("com.facebook.servicerouter", "com.facebook.servicerouter", [], []),
        ("com.facebook.security", "com.facebook.security", [], []),
        ("com.facebook.nifty.core", "com.facebook.nifty.core", [], []),
        ("com.facebook.nifty.ssl", "com.facebook.nifty.ssl", [], []),
        ("com.facebook.serviceframework", "com.facebook.serviceframework", [], []),
        ("com.facebook.infrasec", "com.facebook.infrasec", [], []),
        # Relocations to fbshaded0 (CAT token provider — legacy)
        ("com.facebook.x2p.proxyclient", "fbshaded0.com.facebook.x2p.proxyclient", [], []),
        # ... ~30 more specific rules ...

        # Catch-all (~line 142) — anything not matched above gets $internal-shaded.
        ("com.facebook", "com.facebook.presto.$internal.com.facebook", [], []),
    ],
    deps = [
        "//thrift/lib/java/runtime:runtime",
        "//thrift/lib/java/facebook/service-framework:service-framework",
        "//infrasec/authorization/java/aclcheckerhandler_jni:ThriftAclCheckerHandlerJNI",
        # ... many more ...
    ],
)
```

## Reference: Recent Working Example

Commits `4df734341c87` and `9ea546469728` (June 2026, authN migration ralph loop) are good worked examples:
- `4df734341c87` — add `service-framework` + `aclcheckerhandler_jni` deps + unshade rules for `nifty.core`, `serviceframework`, `infrasec`
- `9ea546469728` — unshade `com.facebook.nifty.ssl` and update the one `CryptoAuthTokenProviderUtils.java` caller from `fbshaded0.com.facebook.nifty.ssl.*` to `com.facebook.nifty.ssl.*`

The companion `presto-ws-test` skill at `fbcode/claude-templates/components/skills/presto-ws-test/` has `build-jar.sh` + `update-pom.sh` scripts that codify the publish + pom-bump steps — useful as a reference, but use the 12+12 regex for `presto-deps-all`, not the 14+8 regex baked into those scripts.
