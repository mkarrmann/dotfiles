# Presto Local Dev

## Overview

Run Presto components (Java coordinator, Java gateway, **C++ Prestissimo native worker**) locally on a devserver for development and testing.

**Key script:** `~/.claude/skills/presto-local-dev/presto-local-dev` (Java coordinator + gateway only — the native worker is launched separately, see below).

**Prerequisites:**
- Java modules must be built first. Use `presto-build` (full build) or `source ~/.localrc && mfi` (FB trunk install). **Critical**: the local `~/.m2/repository` snapshots must match the version in the current checkout's `pom.xml` (e.g. `0.298-SNAPSHOT`). If `mfi` is mid-flight or stale, the coordinator will fail at startup with `Could not transfer artifact ... Network is unreachable` because the devserver has no direct internet access.
- x509 credentials at `/var/facebook/credentials/$USER/x509/$USER.pem`
- For the native worker: a Buck build of `fbcode//fb_presto_cpp:main`. Cached builds finish in seconds; cold builds take a long time.

**Related skills:**
- `presto-build` — Build Java/C++ modules
- `presto-gateway-deploy` — Deploy gateway to test TW cluster
- `presto-e2e-test` — E2E tests against remote clusters

## Quick Reference

| Task | Command |
|------|---------|
| Start coordinator (Java) | `presto-local-dev coordinator` |
| Start gateway (Java)     | `presto-local-dev gateway` |
| Start both Java services | `presto-local-dev all` |
| Stop all                 | `presto-local-dev stop` |
| Show status              | `presto-local-dev status` |
| Build native worker      | `buck build fbcode//fb_presto_cpp:main` |
| Run native worker        | `<binary> --etc_dir=/path/to/fbcode/fb_presto_cpp/etc` (see below) |

## Architecture

```
                        ┌─────────────────┐
  curl / presto CLI ──> │  Gateway (:8081) │ (optional — can hit coordinator directly)
                        └───────┬─────────┘
                                │ routes to
                        ┌───────▼──────────┐
                        │ Java Coordinator │ ── schedules tasks ──> ┌────────────────────────┐
                        │ (:8082)          │                        │ Prestissimo C++ worker │
                        └──────────────────┘ <── task status ──     │ (HTTP port from        │
                                                                    │  fb_presto_cpp/etc/    │
                                                                    │  config.properties:    │
                                                                    │  http-server.http.port)│
                                                                    └────────────────────────┘
```

- **Coordinator** (`presto-facebook-main`): Runs `com.facebook.presto.facebook.PrestoFacebook` on port 8082. Config from `presto-facebook-main/etc/config.properties`.
- **Gateway** (`presto-gateway`): Runs `com.facebook.presto.gateway.LocalPrestoGateway` on port 8081. Config from `presto-gateway/etc/config.properties`. Routes to coordinator at `localhost:8082`. **Optional** — for most testing you can skip the gateway and hit the coordinator directly at `:8082`.
- **Ports are env-var overridable**: export `PRESTO_LOCAL_DEV_COORDINATOR_PORT` and/or `PRESTO_LOCAL_DEV_GATEWAY_PORT` before invoking `presto-local-dev`. Defaults are 8082/8081 respectively. The script JVM-overrides `-Dhttp-server.http.port` and `-Ddiscovery.uri` so the in-tree `etc/config.properties` (which ships with port 8080) stays untouched — necessary on ODs where `etserver` or similar root services hold port 8080.
- **Prestissimo native worker** (`fb_presto_cpp`): Runs `FacebookPrestoMain` (target `fbcode//fb_presto_cpp:main`). Reads its config from `--etc_dir`. Self-registers with the coordinator via the `discovery.uri` in its `config.properties`.

The gateway must be started **after** the coordinator, since it probes the coordinator on startup. The native worker can be started any time after the coordinator (it retries discovery).

## Components

### Coordinator

Main class: `com.facebook.presto.facebook.PrestoFacebook` (in `presto-facebook-main/src/test/`)

Config: `presto-facebook-main/etc/config.properties`

The coordinator connects to real Meta infrastructure (configerator, warehouse, prism metastore) via the CLF sidecar. It registers with environment from `etc/config.properties` (`node.environment`).

### Gateway

Main class: `com.facebook.presto.gateway.LocalPrestoGateway` (in `presto-gateway/src/test/`)

Config: `presto-gateway/etc/config.properties`

`LocalPrestoGateway` extends `PrestoGateway` and overrides:
- Coordinator source: hardcoded to `localhost:8082`
- Environment config: uses a single test environment
- Admission control: disabled (no-op)

The gateway connects to real infrastructure for routing decisions (NSSR for namespace resolution, configerator for resource groups, Global Tetris Router for Tetris routing).

### Prestissimo native worker

Buck target: `fbcode//fb_presto_cpp:main` (`cpp_bolt_binary`). The compiled binary is the value of the `--show-full-json-output` map after `buck build`, typically under `buck-out/v2/art/fbcode/<hash>/fb_presto_cpp/__main__/main`.

Source entry point: `fbcode/fb_presto_cpp/FacebookPrestoMain.cpp`. Accepts:
- `--etc_dir <path>` (default `.`) — directory containing `config.properties`, `node.properties`, and `catalog/*.properties`.
- `--presto_worker_fb303_port <int>` (default 10101) — Thrift fb303 counter export port.

Default etc dir for FB devserver use: `fbcode/fb_presto_cpp/etc/`. It contains `config.properties`, `node.properties`, and a `catalog/` subdirectory with one `<connector>.properties` per catalog (e.g. `prism.properties`, `hive.properties`).

The native worker self-registers with the coordinator using the `discovery.uri` in its `config.properties` (point this at `http://127.0.0.1:8082` for the local coordinator).

#### Coordinator-side requirements for routing to Prestissimo

For all execution to land on the native worker (and not on the Java coordinator's in-process worker), the coordinator's `presto-facebook-main/etc/config.properties` must set:

```
node-scheduler.include-coordinator=false
native-execution-enabled=true
optimizer.optimize-hash-generation=false
regex-library=RE2J
offset-clause-enabled=true
inline-sql-functions=false
use-alternative-function-signatures=true
```

(The first line stops the coordinator from acting as a worker; the rest are the standard "Prestissimo-mode" planner settings copied from `NativeQueryRunnerUtils.getNativeWorkerSystemProperties()`.)

If you're using Thrift internal communication (the user's `config.properties` already enables it), Prestissimo supports it natively — no extra worker config needed.

#### Launch command

```bash
buck build fbcode//fb_presto_cpp:main --show-full-json-output
# → grab the binary path from the JSON output
BIN=$(buck build fbcode//fb_presto_cpp:main --show-full-json-output 2>/dev/null \
      | python3 -c 'import json,sys;print(json.loads(sys.stdin.read())["fbcode//fb_presto_cpp:main"])')

THRIFT_TLS_CL_KEY_PATH=/var/facebook/credentials/$USER/x509/$USER.pem \
THRIFT_TLS_CL_CERT_PATH=/var/facebook/credentials/$USER/x509/$USER.pem \
"$BIN" --etc_dir=/data/users/$USER/fbsource/fbcode/fb_presto_cpp/etc 2>&1 \
      | tee /tmp/presto-local-prestissimo.log
```

Log to grep for register-with-coordinator success: `Announcement` / `discoveryUri` / `nodeId`.

## Running via Maven

The script uses `mvn exec:java` with `classpathScope=test` to run test classes with the full dependency classpath. Key JVM flags:

| Flag | Purpose |
|------|---------|
| `-Dio.netty.native.detectNativeLibraryDuplicates=false` | Avoids Netty native lib conflict between netty-epoll and hadoop JARs |
| `-Djdk.attach.allowAttachSelf=true` | Required for JMX agent |
| `-Dconfigerator.timeout=5s` | Avoid hanging on configerator |
| `-Dws.client-proxy.local-enabled=false` | Disable local WS client proxy |
| `--add-opens=...` (17 entries — see wiki) | Java 17+ reflection access for java.io, java.lang[.ref/.reflect], java.net, java.nio, java.security, javax.security.auth[.login], java.text, java.util[.concurrent[.atomic]/.regex], jdk.internal.loader, sun.security.action, sun.security.krb5 |

Environment variables:
- `THRIFT_TLS_CL_KEY_PATH` / `THRIFT_TLS_CL_CERT_PATH` — x509 cert for Thrift TLS connections

## Running via IntelliJ

Both components can also be run from IntelliJ with the following run configurations:

The `--add-opens` set below matches the [IntelliJ on Devserver (Presto)](https://www.internalfb.com/wiki/Presto_Internal/Presto_Development_Guide/Intellij_Idea_+_Devserver/) wiki. Without the full set, you'll hit `InaccessibleObjectException` at runtime for reflection outside `java.lang`.

**Coordinator:**
- Main class: `com.facebook.presto.facebook.PrestoFacebook`
- Module: `presto-facebook-main`
- JDK: temurin-17
- VM options:
  ```
  -ea
  -Xmx2G
  -XX:+ExitOnOutOfMemoryError
  -Djdk.attach.allowAttachSelf=true
  -Duser.timezone=America/Bahia_Banderas
  -Dconfigerator.timeout=5s
  -Dprism.directory-listing-timeout=3m
  -Dws.metadata.max-retry-time=2m
  -Dws.metadata.max-backoff-time=15s
  -Dws.client-proxy.local-enabled=false
  -Dws.client-proxy.use-environment-tier=false
  -Dws.thrift.client.write-timeout=15s
  -Dws.thrift.client.read-timeout=15s
  -Dws.thrift.client.receive-timeout=15s
  -Dprism.otherRegion=nebraska
  -Dclient-proxy.tier=ws.freeproxy.vll.client_proxy
  -Dconfig=etc/config.properties
  -Dlog.levels-file=etc/log.properties
  --add-opens=java.base/java.io=ALL-UNNAMED
  --add-opens=java.base/java.lang=ALL-UNNAMED
  --add-opens=java.base/java.lang.ref=ALL-UNNAMED
  --add-opens=java.base/java.lang.reflect=ALL-UNNAMED
  --add-opens=java.base/java.net=ALL-UNNAMED
  --add-opens=java.base/java.nio=ALL-UNNAMED
  --add-opens=java.base/java.security=ALL-UNNAMED
  --add-opens=java.base/javax.security.auth=ALL-UNNAMED
  --add-opens=java.base/javax.security.auth.login=ALL-UNNAMED
  --add-opens=java.base/java.text=ALL-UNNAMED
  --add-opens=java.base/java.util=ALL-UNNAMED
  --add-opens=java.base/java.util.concurrent=ALL-UNNAMED
  --add-opens=java.base/java.util.concurrent.atomic=ALL-UNNAMED
  --add-opens=java.base/java.util.regex=ALL-UNNAMED
  --add-opens=java.base/jdk.internal.loader=ALL-UNNAMED
  --add-opens=java.base/sun.security.action=ALL-UNNAMED
  --add-opens=java.security.jgss/sun.security.krb5=ALL-UNNAMED
  ```
- Env: `THRIFT_TLS_CL_KEY_PATH=/var/facebook/credentials/$USER/x509/$USER.pem;THRIFT_TLS_CL_CERT_PATH=/var/facebook/credentials/$USER/x509/$USER.pem`

**Gateway:**
- Main class: `com.facebook.presto.gateway.LocalPrestoGateway`
- Module: `presto-gateway`
- JDK: temurin-17
- VM options: same `-ea -Xmx2G -XX:+ExitOnOutOfMemoryError -Djdk.attach.allowAttachSelf=true -Duser.timezone=America/Bahia_Banderas -Dconfigerator.timeout=5s -Dws.client-proxy.local-enabled=false -Dws.client-proxy.use-environment-tier=false` plus the full `--add-opens` set above (omit the coordinator-only flags: `-Dconfig`, `-Dlog.levels-file`, `-Dprism.*`, `-Dws.metadata.*`, `-Dws.thrift.client.*`, `-Dclient-proxy.tier`).
- Env: same as coordinator

## Sending Test Queries

Once both components are running:

```bash
# Via curl
curl -s --noproxy '*' http://localhost:8081/v1/statement \
  -X POST \
  -H "X-Presto-User: $USER" \
  -H "X-Presto-Catalog: prism" \
  -H "X-Presto-Schema: di" \
  -d "SELECT 1"

# Via presto CLI
presto --server localhost:8081 --catalog prism --schema di --execute "SELECT 1"
```

## Logs

| Component | Log file |
|-----------|----------|
| Coordinator | `/tmp/presto-local-coordinator.log` |
| Gateway | `/tmp/presto-local-gateway.log` |

Logs include full Maven output and application stderr. Search for `ERROR` or specific class names for debugging.

## Common Issues

| Problem | Fix |
|---------|-----|
| `Address already in use` on port 8081/8082 | Kill the existing process: `lsof -i :8081` then `kill <pid>` |
| `Multiple resources found for libnetty_transport_native_epoll` | The script passes `-Dio.netty.native.detectNativeLibraryDuplicates=false` automatically |
| `Can not attach to current VM` | Missing `-Djdk.attach.allowAttachSelf=true` |
| `No Presto clusters found` | Expected if coordinator environment doesn't match gateway's expected clusters |
| `Connection refused: localhost:8082` | Coordinator isn't running — start it first |
| Coordinator startup is slow | CLF sidecar extraction + configerator init takes ~60-90s |
| Gateway startup is slow | Similar CLF/configerator overhead, ~60-90s |
| Coordinator fails immediately with `Could not transfer artifact … Network is unreachable` for `0.NNN-SNAPSHOT` POMs | The local `~/.m2/repository` snapshots are out of date relative to the version in `pom.xml`. Devservers can't reach `maven.thefacebook.com`. Run `source ~/.localrc && mfi` to refresh. Build cannot proceed without the missing snapshots. |
| Native worker doesn't register with coordinator | Check `discovery.uri` in `fb_presto_cpp/etc/config.properties` matches the coordinator's `http-server.http.port` (8082). Also confirm the coordinator's `node.environment` matches `fb_presto_cpp/etc/node.properties`. |
| Queries succeed but execute on the coordinator (no native worker activity) | Coordinator config is missing `node-scheduler.include-coordinator=false` and/or `native-execution-enabled=true`. See "Prestissimo native worker" section. |
| Queries fail with `Native execution not supported for ...` | The query plan uses an operator the C++ worker doesn't implement. Either rewrite the query or fall through to Java workers (re-enable `node-scheduler.include-coordinator=true`). |
