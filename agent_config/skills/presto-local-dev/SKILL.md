# Presto Local Dev

## Overview

Run Presto components locally on a devserver for development/testing:
- **Java coordinator** (`com.facebook.presto.facebook.PrestoFacebook`)
- **Java gateway** (`com.facebook.presto.gateway.LocalPrestoGateway`)
- **C++ Prestissimo native worker** (`fbcode//fb_presto_cpp:main`)

**Key script:** `~/.claude/skills/presto-local-dev/presto-local-dev` — manages all three (coordinator, gateway, **and** worker) as detached processes.

The script **never edits the tracked `etc/`**. It generates config into an in-repo, gitignored `presto-facebook-main/etc-local/` (see [Config generation](#config-generation)).

**Prerequisites:**
- Java modules built first: `presto-build` (full) or `source ~/.localrc && mfi -pl presto-facebook`. The local m2 snapshots must match the checkout's `pom.xml` version, or startup fails with `Could not transfer artifact … Network is unreachable` (devservers have no direct internet; the script runs maven offline `-o`).
- x509 credentials at `/var/facebook/credentials/$USER/x509/$USER.pem`.
- For the worker: a built `fbcode//fb_presto_cpp:main` (see `presto-build -n`). Cached = seconds; cold = long.

**Related skills:** `presto-build` (build), `presto-gateway-deploy` (deploy gateway), `presto-e2e-test` (remote E2E).

## Quick Reference

| Task | Command |
|------|---------|
| Start coordinator (Java) | `presto-local-dev coordinator` |
| Start native worker (C++) | `presto-local-dev worker` |
| Start gateway (Java) | `presto-local-dev gateway` |
| Start coordinator + worker | `presto-local-dev all` |
| Stop everything | `presto-local-dev stop` |
| Status (nodes + activeWorkers) | `presto-local-dev status` |
| Run a query against the local coordinator | `presto-local-dev query "<SQL>"` |

Ports are env-overridable: `PRESTO_LOCAL_DEV_COORDINATOR_PORT` (default 8082), `PRESTO_LOCAL_DEV_GATEWAY_PORT` (default 8081). The script JVM-overrides `-Dhttp-server.http.port`/`-Ddiscovery.uri` so the in-tree config (port 8080) is untouched — needed on ODs where port 8080 is taken.

The worker binary can be overridden with `PRESTO_LOCAL_DEV_WORKER_BIN` to skip the `buck2 build` resolve.

## Config generation

On each `coordinator` start the script regenerates config into **`presto-facebook-main/etc-local/`** (already in `presto-facebook-trunk/.gitignore`, so `sl status` ignores it). Because it's in-repo it is **inherently per-checkout** — no suffix needed.

What it generates from base `etc/config.properties` + overlays:
- `config.properties` — base, with `plugin.bundles` (multi-line) and the overlaid keys stripped, then the overlay appended. `plugin.bundles` paths are written **absolute** (the coordinator runs from the run-dir, not the module dir).
- `catalog/{tpch,jmx}.properties` — minimal, infra-free catalogs. tpch's `tpch.column-naming` is **derived from the native worker's tpch catalog** (`fb_presto_cpp/etc/catalog/tpch.properties`, fallback `STANDARD`) so the two always agree — a mismatch makes named-column scans fail on the worker (see Common Issues). Other catalog files you drop in here persist (only `tpch`/`jmx` are managed).
- `function-namespace/` — empty (disables the XDB UDF manager).
- `log.properties` — copied from base.
- `event-listener.properties` — base minus the dead socks-proxy. Loaded via the absolute `-Devent-listener.config-files` override so prism's event listener (a common change under test) loads even though the run-dir has no `etc/`.
- `worker-etc/` — copy of `fb_presto_cpp/etc` with `discovery.uri` → coordinator, and **`node.environment` + `presto.version`** matched to the coordinator (created on `worker` start; version match is required or the worker isn't counted — see Common Issues).

**Coordinator runs from a run-dir with no `etc/`.** cwd = `$BUILD_ROOT/presto-local-dev{suffix}/run`. This is deliberate: `etc/query-prerequisites.properties` (factory=prism) is loaded from the **cwd-relative** `etc/` with no config override, and prism prerequisites block every query for 24h waiting on Tetris/replication infra that isn't reachable locally. Running from a dir without that file makes the manager no-op. (`mvn -f` + absolute `plugin.bundles` make this work despite cwd ≠ module dir.)

**Persistent local tweaks: `etc-local/config.local.properties`.** The base-derived `config.properties` is regenerated every `coordinator` start (so it stays in sync with the tracked `etc/config.properties`), but any keys you put in `config.local.properties` (gitignored, auto-created, never overwritten) are merged in last and **win** — so your edits survive restarts without drifting from base. Put overrides there, not in the generated `config.properties`.

**Spill (and the coordinator's `var/`, logs) stay OUT of EdenFS.** This is an Eden checkout; large/churny dirs would bloat the overlay. Spill + run-dir go to local disk under `$BUILD_ROOT/presto-local-dev{suffix}/` (mirrors how `buck-out` uses an `eden redirect`); logs go to `/tmp`. Only the small static config lives in-repo.

For IntelliJ: run `presto-local-dev coordinator` once to generate `etc-local/`, then point the run config's `-Dconfig`/`-Dlog.levels-file` at `presto-facebook-main/etc-local/{config,log}.properties` (see [IntelliJ](#running-via-intellij)).

## Plugin loading (prism + zippy) — load like prod

The overlay sets `plugin.bundles` to: the OSS `presto-trunk` plugins as individual bundles **+ the aggregate `../presto-facebook-plugins/pom.xml`** for ALL Facebook plugins. This mirrors prod's `plugin/facebook-plugins/` — every FB plugin in **one shared classloader**.

Why this matters:
- **prism needs zippy.** `presto-prism-plugin`'s pom *excludes* `presto-zippy`. Loaded as its own isolated bundle, `ZGatewayService` → `NoClassDefFoundError`. The aggregate's runtime closure pulls `presto-stats-provider → presto-zippy` into the same classloader, so it resolves (exactly like prod, where prism is never loaded in isolation).
- **No duplicate-crypto crash.** `FacebookCryptoPlugin` is provided once (single ServiceLoader scan in the shared classloader) instead of twice when `presto-facebook-functions` and `presto-crypto-functions` were separate isolated bundles (→ `Function already registered`).

Verify the closure: `cd presto-facebook-plugins && mvn dependency:tree -o … -Dincludes=com.facebook.presto:presto-zippy` should show `presto-stats-provider → presto-zippy:runtime`.

## Architecture

```
  curl / presto CLI ──> Gateway (:8081)  (optional — can hit coordinator directly)
                              │ routes to
                        Java Coordinator (:8082) ── schedules ──> Prestissimo C++ worker
                                                  <── status ──    (HTTP port from worker-etc)
```

- **Coordinator** (`presto-facebook-main`): config from `etc-local/config.properties`. Connects to real infra (configerator, prism metastore) via the CLF sidecar.
- **Gateway** (`presto-gateway`): routes to `localhost:8082`. **Optional** — for most testing, hit the coordinator directly at `:8082`. Must start *after* the coordinator (it probes it on startup).
- **Worker** (`fb_presto_cpp`): self-registers via `discovery.uri` in its `worker-etc/config.properties`. Can start any time after the coordinator (retries discovery).

### Worker registration (the `worker`/`all` commands block until it's ready)

`worker` (and `all`) **do not return until the worker is counted as an active worker** — the real gate for running queries — or they fail with a printed diagnosis. So you never have to guess whether it's ready; if the command returned success, queries will run. Registration lags worker start by ~1 min.

The coordinator counts a worker toward `activeWorkers` only if **all** hold (the script enforces them; `wait_for_active_worker` prints which one failed otherwise):
1. It heartbeats (appears in `/v1/node`).
2. Its `presto.version` matches the coordinator's (synced from the live `/v1/info`).
3. Its `node.environment` matches the coordinator's.

`presto-local-dev status` shows both `nodes` and `activeWorkers` so the real gate is visible.

## Native execution (routing to the C++ worker)

The overlay sets (so execution lands on the native worker, not the coordinator's in-process worker):
```
node-scheduler.include-coordinator=false
native-execution-enabled=true
optimizer.optimize-hash-generation=false
regex-library=RE2J
offset-clause-enabled=true
inline-sql-functions=false
```
(First line stops the coordinator acting as a worker; the rest are the Prestissimo-mode planner settings from `NativeQueryRunnerUtils.getNativeWorkerSystemProperties()`.) With `include-coordinator=false`, **0 workers** until you start the worker.

## Devserver gotchas (baked into the generated config / script)

- **socks-proxy stripped.** Base config has `thrift.client.socks-proxy=localhost:1080`; devservers connect direct via ServiceRouter. An empty override crashes Drift, and a dead `:1080` hangs configerator. The script strips it.
- **configerator *connector* catalog skipped.** It fires a proxy2 connection burst that saturates the netty event loop. Avoided via the minimal `catalog/` (only tpch + jmx).
- **XDB function-namespace manager skipped.** It connects to `xdb.presto` over the (dead) socks proxy. Avoided via the empty `function-namespace/`.
- **IPv4 for configerator.** IPv6 does not work for configerator on this devserver.
- **prism is mandatory to boot.** A no-prism coordinator can't start: `EventListenerManager` falls back to `etc/event-listener.properties`, hard-wired to `event-listener.name=prism`. (This is also why the aggregate-pom fix is required, not optional.)
- **Detached launches.** Processes start via `setsid` + pidfiles. A plain `&` JVM gets reaped minutes after the launching shell/agent exits — this caused the old "flakiness". `stop`/`status` track the process group.
- **Benign error to ignore:** `ServiceRouterModule … Sidecar monitoring port is NOT set. TW_PORT_sidecar_monitoring` — expected on a devserver, not a failure.

## Running via Maven

The script uses `mvn exec:java -Dexec.classpathScope=test` (runs the test-scope main with the full classpath), offline (`-o`) against the `mfi`-populated m2, with `-Dout-of-tree-build-root` and a per-checkout `-Dmaven.repo.local`. Key JVM flags:

| Flag | Purpose |
|------|---------|
| `-Dio.netty.native.detectNativeLibraryDuplicates=false` | Netty epoll vs hadoop native-lib conflict |
| `-Djdk.attach.allowAttachSelf=true` | JMX agent self-attach |
| `-Dconfigerator.timeout=5s` | Don't hang on configerator |
| `-Dws.client-proxy.local-enabled=false` | Disable local WS client proxy |
| `--add-opens=…` (the full set in the script) | Java 17 reflection access (else `InaccessibleObjectException`) |

Env: `THRIFT_TLS_CL_KEY_PATH` / `THRIFT_TLS_CL_CERT_PATH` = the x509 pem.

## Running via IntelliJ

Generate `etc-local/` once (`presto-local-dev coordinator`), then use these run configs. The `--add-opens` set matches the [IntelliJ on Devserver (Presto)](https://www.internalfb.com/wiki/Presto_Internal/Presto_Development_Guide/Intellij_Idea_+_Devserver/) wiki — without the full set you'll hit `InaccessibleObjectException`.

**Coordinator:** main `com.facebook.presto.facebook.PrestoFacebook`, module `presto-facebook-main`, JDK temurin-17. VM options:
```
-ea -Xmx2G -XX:+ExitOnOutOfMemoryError -Djdk.attach.allowAttachSelf=true
-Duser.timezone=America/Bahia_Banderas -Dconfigerator.timeout=5s
-Dprism.directory-listing-timeout=3m -Dws.metadata.max-retry-time=2m -Dws.metadata.max-backoff-time=15s
-Dws.client-proxy.local-enabled=false -Dws.client-proxy.use-environment-tier=false
-Dws.thrift.client.write-timeout=15s -Dws.thrift.client.read-timeout=15s -Dws.thrift.client.receive-timeout=15s
-Dprism.otherRegion=nebraska -Dclient-proxy.tier=ws.freeproxy.vll.client_proxy
-Dconfig=etc-local/config.properties -Dlog.levels-file=etc-local/log.properties
--add-opens=java.base/java.io=ALL-UNNAMED  (… full set, see the script's COMMON_MAVEN_OPTS …)
```
Env: `THRIFT_TLS_CL_KEY_PATH=/var/facebook/credentials/$USER/x509/$USER.pem;THRIFT_TLS_CL_CERT_PATH=/var/facebook/credentials/$USER/x509/$USER.pem`

**Gateway:** main `com.facebook.presto.gateway.LocalPrestoGateway`, module `presto-gateway`, JDK temurin-17. Same VM options minus the coordinator-only flags (`-Dconfig`, `-Dlog.levels-file`, `-Dprism.*`, `-Dws.metadata.*`, `-Dws.thrift.client.*`, `-Dclient-proxy.tier`). Same env.

## Native worker (detail)

Buck target `fbcode//fb_presto_cpp:main` (`cpp_bolt_binary`); binary path is the `--show-full-json-output` map value. Entry point `fbcode/fb_presto_cpp/FacebookPrestoMain.cpp`; args: `--etc_dir <path>` (config/node/catalog) and `--presto_worker_fb303_port <int>` (default 10101). The script feeds it the generated `etc-local/worker-etc`. Register-success log markers: `Announcement` / `discoveryUri` / `nodeId`.

## Sending test queries

Use the script's `query` subcommand — it runs SQL against the local coordinator over the REST API (following `nextUri` paging) and prints rows or a clear `QUERY FAILED […]` message.

```bash
presto-local-dev query "SELECT count(*) FROM nation"
# tpch uses STANDARD column naming (n_*, r_*, ...) to match the worker:
presto-local-dev query "SELECT r.r_name, count(*) FROM nation n JOIN region r ON n.n_regionkey=r.r_regionkey GROUP BY 1 ORDER BY 1"
```
Defaults to catalog `tpch` / schema `tiny` (override with `PRESTO_LOCAL_DEV_CATALOG` / `PRESTO_LOCAL_DEV_SCHEMA`). Confirmed end-to-end on the native worker: `count(*) FROM nation` → 25; the join → 5 regions × 5 nations.

**Do not use `/usr/local/bin/presto`** for the local coordinator — that's Meta's gateway CLI (args `--execute`/`--smc`/`[NAMESPACE]`); it rejects `--server`/`--catalog`/`--schema`. The raw `/v1/statement` API also requires following `nextUri` or the query is cancelled — `query` handles both.

## Logs

| Component | Log |
|-----------|-----|
| Coordinator | `/tmp/presto-local-coordinator.log` |
| Worker | `/tmp/presto-local-worker.log` |
| Gateway | `/tmp/presto-local-gateway.log` |

Logs include full Maven output + app stderr. Grep for `ERROR` or class names.

## Common Issues

| Problem | Fix |
|---------|-----|
| `NoClassDefFoundError … ZGatewayService` | prism loaded in isolation (zippy excluded). Use the aggregate `presto-facebook-plugins/pom.xml` in `plugin.bundles` — see [Plugin loading](#plugin-loading-prism--zippy--load-like-prod). |
| Every query stuck in `WAITING_FOR_PREREQUISITES` forever | Shouldn't happen via this script (`worker`/`all` block until `activeWorkers>=1`, and the run-dir disables prism prerequisites). If you hit it manually: (a) the coordinator's cwd has `etc/query-prerequisites.properties` (factory=prism) — run from a dir without it; (b) it's actually `WAITING_FOR_RESOURCES` — run `presto-local-dev status` and check `activeWorkers`; if 0, the worker's `presto.version`/`node.environment` doesn't match (see Worker registration). |
| `Column 'X' not found on TPC-H table '…'` (on the worker) | Coordinator vs worker tpch `tpch.column-naming` mismatch. The native worker uses `STANDARD` (`n_regionkey`, `r_regionkey`, …); the coordinator catalog must match. The script sets `tpch.column-naming=STANDARD`; use STANDARD column names in queries. |
| `Function already registered` (crypto) | `presto-crypto-functions` loaded as its own bundle alongside `presto-facebook-functions`. The aggregate pom fixes it (one classloader). |
| Coordinator can't boot, complains about event listener | prism not loaded — `etc/event-listener.properties` hard-wires `event-listener.name=prism`. prism must be in `plugin.bundles`. |
| `Could not transfer artifact … Network is unreachable` for `0.NNN-SNAPSHOT` | Stale local m2. `source ~/.localrc && mfi`. Devservers can't reach `maven.thefacebook.com`. |
| Queries run on the coordinator (no worker activity) | Missing `node-scheduler.include-coordinator=false` / `native-execution-enabled=true`. |
| `Native execution not supported for …` | Plan uses an operator the C++ worker lacks. Rewrite, or re-enable `node-scheduler.include-coordinator=true` for Java fallback. |
| Worker doesn't register | Check `worker-etc/config.properties` `discovery.uri` = coordinator port, and `node.environment` matches the coordinator. |
| `Address already in use` :8082/:8081 | `presto-local-dev stop`, or `lsof -i :8082` then kill. |
| `etc-local/` shows in `sl status` | It shouldn't — it's in `presto-facebook-trunk/.gitignore`. If it appears, confirm you're under that trunk and the ignore is intact. |
| `Sidecar monitoring port is NOT set. TW_PORT_sidecar_monitoring` | Benign devserver warning — ignore. |
