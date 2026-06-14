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
- For **native sidecar mode** (`sidecar` / `PRESTO_LOCAL_DEV_SIDECAR=1`): the `presto-native-sidecar-plugin` must be built into the local m2. It is in `_oss_modules` (so `source ~/.localrc && mpi` builds it) and in `presto-build`'s `OSS_MODULES` (so `presto-build` builds it).

**Related skills:** `presto-build` (build), `presto-gateway-deploy` (deploy gateway), `presto-e2e-test` (remote E2E).

## Quick Reference

| Task | Command |
|------|---------|
| Start coordinator (Java) | `presto-local-dev coordinator` |
| Start native worker (C++) | `presto-local-dev worker` |
| Start gateway (Java) | `presto-local-dev gateway` |
| Start coordinator + worker | `presto-local-dev all` |
| Start coordinator + worker in **native sidecar mode** | `presto-local-dev sidecar` |
| Stop everything | `presto-local-dev stop` |
| Status (nodes + activeWorkers) | `presto-local-dev status` |
| Run a query against the local coordinator | `presto-local-dev query "<SQL>"` |

Ports are env-overridable: `PRESTO_LOCAL_DEV_COORDINATOR_PORT`, `PRESTO_LOCAL_DEV_GATEWAY_PORT`, `PRESTO_LOCAL_DEV_WORKER_PORT` (and `PRESTO_LOCAL_DEV_WORKER_FB303_PORT`). The script JVM-overrides `-Dhttp-server.http.port`/`-Ddiscovery.uri` so the in-tree config (port 8080) is untouched — needed on ODs where port 8080 is taken.

**Concurrent checkouts work with zero config.** Ports, pidfiles, and logs are all derived from the checkout index (`checkout1`→1, `checkout4`→4, …) the script already computes for build isolation. Each checkout gets a 10-port block at `base + (index-1)*10`: coordinator `8082`, gateway `8081`, worker `7777`, worker-fb303 `10101` for checkout1, then `+10` per checkout (checkout4 → `8112`/`8111`/`7807`/`10131`). checkout1 keeps the historical ports byte-for-byte. Because pidfiles/logs are suffixed (`-checkoutN`) and the `pkill` orphan-fallback patterns are scoped to the per-checkout build root / `--etc_dir`, `stop` and `status` only ever touch their own checkout's processes. `status` prints the resolved checkout, index, and ports. Before launching, `start` fails fast (with a clear message) if the resolved port is already held by a foreign process — the orphan-squatting-a-port case.

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

**Persistent local tweaks: `etc-local/config.local.properties`.** The base-derived `config.properties` is regenerated every `coordinator` start (so it stays in sync with the tracked `etc/config.properties`), but any keys you put in `config.local.properties` (gitignored, auto-created, never overwritten) are merged in last and **win** — so your edits survive restarts without drifting from base. Put overrides there, not in the generated `config.properties`. The worker has the identical mechanism in `etc-local/worker-config.local.properties` (its `worker-etc/` is recreated every `worker` start, so direct edits there are lost — use the overlay). No per-key env vars: any config key is just a line in the matching overlay.

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

## Native coordinator-sidecar mode (opt-in)

By default the coordinator does **not** understand native (C++) session properties — e.g. `SET SESSION native_spill_io_stats_key_suffix=…` fails with `INVALID_SESSION_PROPERTY: Unknown session property`. Those properties are defined in the C++ worker, and the coordinator only learns them from a **sidecar**: a worker that announces `sidecar=true`, which the coordinator queries (`/v1/properties/session`, `/v1/functions`) via the `presto-native-sidecar-plugin`.

**When to use it:** any time you're testing native-only session properties, functions, or types end-to-end (the spill-IoStats feature being the motivating case). For plain native-execution query testing you don't need it — leave it off.

**How to enable:**
```bash
presto-local-dev sidecar                          # coordinator + sidecar worker
# or, equivalently, for individual subcommands:
PRESTO_LOCAL_DEV_SIDECAR=1 presto-local-dev all
PRESTO_LOCAL_DEV_SIDECAR=1 presto-local-dev coordinator   # then worker, same env
```
The flag must be set for **both** the coordinator and worker starts (the `sidecar` subcommand does both in one go). A **single worker doubles as the sidecar and the compute worker** (the "cluster of only sidecar workers" topology), so no second process is needed — `activeWorkers ≥ 1` still gates queries.

**Running a query with a native session property:**
```bash
PRESTO_LOCAL_DEV_HEADERS='X-Presto-Session: native_spill_io_stats_key_suffix=foo' \
  presto-local-dev query "SELECT count(*) FROM nation"
```
(`PRESTO_LOCAL_DEV_HEADERS` is a `;`-separated list of extra HTTP headers passed to `/v1/statement`.)

**What the script changes in sidecar mode** (all generated, nothing tracked is touched):
- Adds the `presto-native-sidecar-plugin` pom to `plugin.bundles`. It registers as a `CoordinatorPlugin` (the PluginManager scans each bundle for both `Plugin` and `CoordinatorPlugin` service files), exposing the native session-property / function-namespace / type / plan-checker / expression-optimizer factories.
- Adds coordinator keys to the overlay: `coordinator-sidecar-enabled=true`, `presto.default-namespace=native.default`, `exclude-invalid-worker-session-properties=true`, and flips `inline-sql-functions=true` (it is `false` in the default overlay).
- Writes the five provider files (canonical values from `presto-docs/.../native-sidecar-plugin.rst`). The factories are only instantiated when these exist:

  | File | Contents | Where |
  |------|----------|-------|
  | `function-namespace/native.properties` | `function-namespace-manager.name=native`, `function-implementation-type=CPP`, `supported-function-languages=CPP` | `etc-local/function-namespace/` (our `function-namespace.config-dir`) |
  | `session-property-providers/native-worker.properties` | `session-property-provider.name=native-worker` | `$RUN_DIR/etc/` (cwd-relative default) |
  | `type-managers/native.properties` | `type-manager.name=native` | `$RUN_DIR/etc/` |
  | `plan-checker-providers/native.properties` | `plan-checker-provider.name=native` | `$RUN_DIR/etc/` |
  | `expression-manager/native.properties` | `expression-manager-factory.name=native` | `$RUN_DIR/etc/` |

  The four `$RUN_DIR/etc/` files coexist with the "run-dir has no `etc/`" prism trick because the script creates **only those provider subdirs** under `$RUN_DIR/etc/` — never `query-prerequisites.properties` or `access-control.properties`, so those managers still no-op (absence is the off switch).
- Launches the worker with `native-sidecar=true` and `presto.default-namespace=native.default` in its `worker-etc/config.properties`.

**Caveat for the spill-IoStats feature specifically:** sidecar mode makes `native_spill_io_stats_key_suffix` *accepted*, but the feature keys off warehouse/WS-FileSystem IoStats; local-disk spill to `/tmp` may not emit those keys, so the stat columns can still be empty locally. Sidecar mode unblocks the *property*, not necessarily the *signal*. To get the signal, spill to Warm Storage — see below.

## Spilling to Warm Storage (to exercise spill IoStats)

The spill-IoStats keys (`wsReadBytes` / `wsWriteBytes`) are emitted **only by the velox Warm Storage FileSystem** — never by local disk. velox picks the spill FileSystem purely from the spill path prefix: `/tmp…` → `LocalFileSystem` (no `ws*` IoStats); `ws://…` → `WarmStorageFileSystem` (`fb_velox/warm_storage/WSFile`), which writes `wsWriteBytes.<REGION>` / `wsReadBytes.<REGION>` into the `SpillStats::ioStats` object that `Operator::recordSpillStats()` reads. So **local-disk spill can never produce the signal** — you must point the worker's spill path at Warm Storage.

The WS velox FileSystem is already registered at worker startup (`FacebookPrestoBase::registerWarmStorageFilesystem`, from `storage_oncall_name`/`storage_user_name`/`storage_service_name` — all `presto` in the worker etc). Enabling WS spill is just a config change.

**How:** point the worker's spill path at a `ws://` location the worker's identity can write to. This is just a worker config key, so set it in the gitignored `etc-local/worker-config.local.properties` overlay (any key there overrides the generated worker config and persists across restarts):
```
# etc-local/worker-config.local.properties
experimental.spiller-spill-path=ws://ws.dw.<cluster>/namespace/<ns>/<you>/spill
```
Leave it unset to keep the default `/tmp` (local disk). A bad/inaccessible `ws://` path does **not** crash the worker at startup — it fails when a query actually spills.

**You also have to force a spill** (the worker etc has `system-memory-gb=200`, `query.max-memory-per-node=200GB`, so nothing spills by default). Enable spill and apply memory pressure via session properties, e.g.:
```bash
PRESTO_LOCAL_DEV_HEADERS='X-Presto-Session: spill_enabled=true' \
  presto-local-dev query "SELECT l_orderkey, count(*) FROM lineitem GROUP BY 1"
```
(use a `tpch`/`sf*` schema large enough to exceed the per-node limit you set; you may also lower `query.max-memory-per-node` via `config.local.properties` and per-operator native spill session props).

**What to look for:** in the operator runtime stats, spill keys are region-scoped — `wsWriteBytes.<REGION>` (e.g. `wsWriteBytes.ATN`, `wsWriteBytes.UNKNOWN`). With `native_spill_io_stats_key_suffix=.spill` set, your stack appends the suffix → `wsWriteBytes.ATN.spill`, which is what prism's `PrismOperatorStatisticsEvent` surfaces as the spill columns. That suffixed-vs-bare distinction is the whole point of the stack.

**Access caveat (the real blocker):** the `ws://` namespace must be writable by the configured identity from this devserver. The only in-repo WS test path (`ws://ws.dw.atn5dw2/namespace/testing/presto/…`) lives in a **DISABLED** test ("until we maintain a test directory on ws cluster"), so a stable writable test namespace isn't guaranteed. Private/warehouse paths may also need a token via `ws.token-path`. If WS write access can't be obtained on the devserver, the velox unit-test path (a fake/local FileSystem that emits IoStats in-process) is the fallback for verifying the suffix mechanism.

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
| `start` aborts with "port … already in use" | A foreign/orphaned process holds this checkout's port. `presto-local-dev stop` (this checkout), inspect with `ss -ltnp \| grep ':<port>'` and kill the owner, or override via `PRESTO_LOCAL_DEV_*_PORT`. Each checkout uses a distinct port block, so this is usually a leftover from a prior run. |
| `etc-local/` shows in `sl status` | It shouldn't — it's in `presto-facebook-trunk/.gitignore`. If it appears, confirm you're under that trunk and the ignore is intact. |
| `INVALID_SESSION_PROPERTY: Unknown session property native_*` | The coordinator has no native sidecar. Start in sidecar mode (`presto-local-dev sidecar` or `PRESTO_LOCAL_DEV_SIDECAR=1`) — see [Native coordinator-sidecar mode](#native-coordinator-sidecar-mode-opt-in). |
| `Sidecar monitoring port is NOT set. TW_PORT_sidecar_monitoring` | Benign devserver warning — ignore. |
