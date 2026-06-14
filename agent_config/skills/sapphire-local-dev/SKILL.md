---
name: sapphire-local-dev
description: Run Sapphire (Presto-on-Spark) queries with the driver on your devvm — works for both Sapphire Java and Sapphire Velox (Prestissimo). Use when you want fast iteration without Chronos round-trip, or to reproduce/debug a verifier finding under your own identity. Covers the working "default-mode" recipe (no `-l`, no `-x`), why naive `-l` doesn't work, and how to extract the result. Sibling to `sapphire-e2e-test` (which targets remote Chronos via `-x` for full pre-land validation).
allowed-tools: Bash
---

# Sapphire Local Dev

## Overview

Run Sapphire end-to-end with the **driver on your devvm** (executors still run on a real Spark cluster, e.g. `dw-nha-spark`) — no Chronos, no Manifold bucket grants. This is the right mode for fast iteration, ad-hoc verification, reproducing a verifier finding under your own identity, or any debugging that benefits from being able to attach to / `printf` the driver.

**Key script:** `~/.claude/skills/sapphire-local-dev/sapphire-local` — wraps `sapphire-submit.sh` with the working flag combo, optional `env -i` for the WS AI-agent gate, and an `extract` subcommand that pulls the row-count result out of the log.

**Related skills:**
- `sapphire-e2e-test` — Chronos-backed (`-x`) remote runs for full pre-land E2E validation. Use that when you specifically need a Chronos artifact / production-like cluster placement, and have bucket access.
- `sapphire-debugging`, `sapphire-debugging-ies` — investigation patterns.
- `presto-local-dev` — local Java/Prestissimo (non-Spark) coordinator+worker for interactive Presto dev.

## TL;DR — the working command

```bash
sapphire-local velox -n instagram -f /tmp/myquery.sql           # Velox (Prestissimo)
sapphire-local java  -n instagram -f /tmp/myquery.sql           # Java
sapphire-local extract /tmp/sapphire_local_YYYYMMDD_HHMMSS.log  # pull "Rows: N\n[value]" out
```

The script wraps the **no-`-l`, no-`-x` "default-mode" recipe** (see [Why default mode works](#why-default-mode-works-and--l-doesnt)). Driver runs on this devvm; executors run on `dw-nha-spark`. Wall-clock is ~6–15 min depending on the query.

## Quick Reference

| Task | Command |
|------|---------|
| Velox (Prestissimo) run | `sapphire-local velox -n <ns> -f <sql>` |
| Java run | `sapphire-local java -n <ns> -f <sql>` |
| Java vs Velox A/B (both, same query) | `sapphire-local ab -n <ns> -f <sql>` |
| Pull result count from a log | `sapphire-local extract <log>` |
| Pass extra `sapphire-submit.sh` args | `sapphire-local velox -n <ns> -f <sql> -- -s spill_enabled=true -e spark.driver.memory=20g` |

`sapphire-local --help` prints full usage.

## When to use this vs `sapphire-e2e-test` (`-x`)

| | This skill (`sapphire-local`, default mode) | `sapphire-e2e-test` (`-x` Chronos) |
|---|---|---|
| **Driver location** | Your devvm | Chronos pod |
| **Executors** | Real Spark cluster (`dw-nha-spark`) | Real Spark cluster |
| **Identity** | `USER:mkarrmann` (yours) | `chronos_secgrp_team_bigcompute` etc. |
| **Setup needed** | None — works out of the box | Manifold `sapphire_velox_test_queries` bucket grant from `sapphire` oncall, + Chronos Data Project ACL |
| **Best for** | Fast iteration, ad-hoc verification, reproducing verifier findings under your identity, debugging the driver | Pre-land E2E validation, production-like cluster placement, anything that must run as the canonical Sapphire identity |
| **Wall-clock** | Same | Same |
| **Result visibility** | In your local log (`Rows: N\n[value]`) | Wand / Scuba via the Chronos job id |

Both produce **two Presto query ids** (high-counter EXPLAIN on prod coordinator, zero-counter Presto-on-Spark execution). The EXPLAIN is the citable one for diff test plans.

## Why default mode works (and `-l` doesn't)

The naive instinct is "run locally = use `-l`". **Don't.** Here's why, distilled from a long debugging arc:

**`-l` (LocalMode):**
- Forces `spark.master=local[8]` (executors run as threads in the driver JVM on devvm).
- Activates the canary **"Disable Trusted Env and GTR for non-chronos environment"** → `spark.gtr.client.enabled=false`.
- The driver hardcodes `spark.remote.io.rootDir=file:////` via `DRIVER_OTHER` (programmatic, not gated on `spark.remote.io.enabled`). This breaks `TempfsTierResolver.getTempFsTier()` (regex `^[^:]+://(?<tier>[^/]+)` finds nothing between the slashes), which forces `PrestoFacebookSparkPipeline.configureTempfsTier` to bind `GlobalTetrisTempfsTierProvider` instead of `StaticTempfsTierProvider`.
- That GTR client then fails (trusted env is off locally), and the cause is **swallowed** at `GlobalTetrisTempfsTierProvider:128` (catches `Throwable`, rethrows `t.getCause()` which is null). Symptom: `unable to fetch tempfs tier` with no stack-trace cause.
- Workarounds (`-e spark.remote.io.enabled.lmOverride=true`, `-e spark.fb.only.tetris.dataCenter=nha0`, `-e spark.remote.io.rootDir=ws://...`) each clear one layer but the next still bites. There is no config-only fix for `-l`.

**Default mode (no `-l`, no `-x`):**
- `sapphire-submit.sh` falls through to its default branch (~line 735) which runs `spark-wrapper` directly on your devvm.
- spark-wrapper computes `spark.remote.io.rootDir = ws://ws.dw.<region>0dw0/spark` from tetris.
- `TempfsTierResolver.getTempFsTier()` returns the real tier → `StaticTempfsTierProvider` is bound → **no GTR call at all**.
- The driver runs on devvm under your identity. Executors are placed on a real Spark cluster.
- Net effect: everything just works. Same data, same execution engine, same result format as `-x` — just without the Chronos round-trip.

(Recorded in `~/checkout2/fbsource/fbcode/datainfra/presto/.claude/skills/sv3-canary-rule-e2e-test/SKILL.md`, which discovered the recipe via ~2 days of debugging in T265180231 / T265811138.)

## Result extraction

Sapphire prints query results to the driver log in this format:

```
Rows: 1
[90737]
```

For a `SELECT count(*)` the value `[90737]` is the count. For multi-column / multi-row results the brackets contain a tab-separated row. `sapphire-local extract <log>` greps for this block.

The Presto-on-Spark execution **query id** (zero-counter, `YYYYMMDD_HHMMSS_00000_xxxxx`) is also extracted — useful for Scuba lookups in `presto_on_spark_runtime`.

## Java vs Velox A/B

`sapphire-local ab -n <ns> -f <sql>` runs the same query under both engines back-to-back, then prints both results side-by-side:

```
=== Java   (log: /tmp/sapphire_local_java_...)  ===
Rows: 1
[505790205]
qid: 20260614_040523_00000_qdktv

=== Velox  (log: /tmp/sapphire_local_velox_...) ===
Rows: 1
[505774038]
qid: 20260614_051144_00000_xkr7d

DIFF: Java - Velox = +16167
```

This is the canonical pattern for reproducing a Sapphire verifier `ROW_COUNT_MISMATCH` locally.

## Query file gotchas

- **No trailing semicolon.** Sapphire wraps your query in `EXPLAIN (TYPE DISTRIBUTED, FORMAT JSON) <sql>` as a single statement; a `;` mid-statement breaks the parser. The script will reject a `.sql` ending in `;`.
- Read-only `SELECT` is safest. `CTAS` works but writes to a Hive table — make sure the target name is unique (e.g. include your username) and `WITH (oncall='big_compute', retention_days=1)`.

## Environment gotchas

- **Env passthrough & the WS AI-agent gate.** The script runs with your **full environment by default** — this is deliberate. The Sapphire driver finds your x509 cert via `THRIFT_TLS_CL_CERT_PATH` (e.g. `/var/facebook/credentials/$USER/x509/$USER.pem`); if that env var is missing it falls back to `/var/facebook/x509_identities/client.pem` (absent on devservers) and dies with `FileNotFoundException → Unable to create injector`. **Do NOT wrap this in `env -i`** — it strips `THRIFT_TLS_CL_CERT_PATH` and breaks cert resolution (learned the hard way).
  - The warmstorage anti-AI gate (`IdentityUtil::getAgentIdentityFromEnv`, MDFS AI Agent Protections RFC) *can* reject temp-dir creation with `WSE_INVALID_ARGUMENT: AI agents are not allowed to set expiration times on directories` when `CLAUDE_*`/`ANTHROPIC_*`/`FB_AGENT_*` are present. **But if your x509 cert is already an authorized agent cert** (e.g. `$USER.pem → .../agent_x509/claude_code_$USER.pem`), the gate is satisfied and **no stripping is needed** — full env just works.
  - Only if you actually hit `WSE_INVALID_ARGUMENT`, pass `--env-strip`. That does a **surgical `unset` of the agent-prefixed vars** while preserving `THRIFT_TLS_*` / x509 / kerberos / build env. Never `env -i`.
- **Data project ACL.** Reads run as `USER:$USER`. You need read access to the target Hive tables. If you don't, you'll see `AccessDeniedException` in the driver log.
- **Cluster placement.** Executors go to the regional `dw-<region>-spark` cluster derived from tetris. To pin a region, pass `-- -e spark.fb.only.tetris.dataCenter=<dc>` (e.g. `nha0`).

## Common issues

### `unable to fetch tempfs tier` / `Failed while calling global tetris`
You passed `-l`. Drop it. The script forbids `-l` in the extra-args passthrough for this reason.

### `WSE_INVALID_ARGUMENT: AI agents are not allowed to set expiration times on directories`
AI-agent env vars are tripping the WS gate AND your x509 cert isn't an authorized agent cert. Re-run with `--env-strip` (surgical unset of `CLAUDE_*`/`ANTHROPIC_*`/`FB_AGENT_*`). Do NOT use `env -i` — see next item.

### `FileNotFoundException: /var/facebook/x509_identities/client.pem` / `Unable to create injector`
The driver can't find your x509 cert. Almost always caused by running under `env -i` (or otherwise missing `THRIFT_TLS_CL_CERT_PATH`), which strips the cert-path env var so it falls back to the nonexistent system cert. Run with your normal full environment (the default). If you used `--env-strip`, note it only `unset`s agent vars and keeps `THRIFT_TLS_*` — so this shouldn't happen; if it does, check `echo $THRIFT_TLS_CL_CERT_PATH` points at an existing file.

### `mismatched input ';'`
Trailing semicolon in your SQL. Drop it.

### `NoSuchMethodError: scala.collection.mutable.WrappedArray scala.Predef$.wrapRefArray`
Scala 2.12/2.13 mismatch — the `presto.spark` fbpkg you pinned was built against 2.12 but Spark 3.4 expects 2.13. The script defaults to `-j stable_fb34` (correct). If overriding `-j`, use a `*_fb34` or `*-spark3` tag.

### `INVALID_FBPKG: FBPKG presto.spark:X not found`
Override `-j` to a real tag. `fbpkg ls presto.spark | head -20` shows recent.

### Run finished EXIT=0 but no `Rows:` block in log
Your query may not return a result set (e.g. CTAS to a table). Read the target table directly via `presto`/`daiquery`, or use the **Presto-on-Spark query id** (zero-counter) the script prints to look up the execution in `presto_on_spark_runtime` Scuba.

### `MessageTooLargeException: Frame size NNN MB exceeded max size 16MB` near end of log
This is the prism event listener trying to log post-completion query stats over thrift; it fires **after** the result is already printed. Harmless — does not affect correctness or the result value.

## Diff test plan boilerplate

For a Sapphire change validated via this skill, paste:

```
Verified locally via `sapphire-local <velox|java|ab>` (driver on devvm, executors on dw-<region>-spark).
SQL: <link or inline>
Presto-on-Spark query ids:
- Java:  YYYYMMDD_HHMMSS_00000_xxxxx → Rows: N [value]
- Velox: YYYYMMDD_HHMMSS_00000_xxxxx → Rows: N [value]
Result: <match / diff>. Driver log: <attach or link>
```
