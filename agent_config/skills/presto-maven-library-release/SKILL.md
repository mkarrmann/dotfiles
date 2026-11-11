---
name: presto-maven-library-release
description: Use when releasing a new version of a standalone Presto Maven library that lives on GitHub (facebookexternal/presto-configerator-client, presto-smc-client, presto-capella-client and similar) to Meta's Maven Proxy (maven.thefacebook.com, repository id fb-nexus-releases) with maven-release-plugin, and then bumping it in presto-facebook-trunk. Covers release:prepare / release:perform, which steps the agent can and cannot run, IPv6, the client-certificate + PUBLISH requirement behind 403s, retrying a failed deploy, and verification. Trigger keywords: release presto-configerator-client, new version of presto-smc-client, mvn release:perform, deploy to nexus, 403 Forbidden maven.thefacebook.com, fb-nexus-releases. Not for buck-built jars (see presto-deps-all) or Presto server packages (see presto-deploy).
---

# Releasing a standalone Presto Maven library

## Overview

A few Presto client libraries are separate GitHub repos (mostly under `facebookexternal/`) that are released only to Meta's internal Maven Proxy, `maven.thefacebook.com` (it still answers on the old Nexus paths). There is no Jenkins or GitHub Actions job for them; a person releases them from a local clone with `maven-release-plugin` (wiki "Presto Internal/Releasing/Release Presto Libraries" names `presto-configerator-client` as fb-nexus-only). presto-facebook-trunk then pins the new version in its root `pom.xml`.

The repos inherit from `com.facebook.airlift:airbase`, which sets `tagNameFormat=@{project.version}` (tags are plain `1.14`). Their poms set `releaseProfiles=fb-nexus-release` (plain `maven-deploy-plugin`, tests skipped) and deploy to `fb-nexus-releases` = `https://maven.thefacebook.com/nexus/content/repositories/releases`.

## Who runs what

| Step | Agent session | User's own shell |
|---|---|---|
| `git fetch` / `git push` to GitHub | **Blocked**: fwdproxy answers `CONNECT tunnel failed, response 403` for the agent identity | Works (git uses `fwdproxy:8080`) |
| `mvn test`, `release:prepare` (local only) | Works, with the IPv6 flag below | Works |
| `release:perform` (upload) | Possible only with a client certificate; ask the user before uploading | Works with a client certificate |
| Bump the pom in fbsource, build, submit | Works | Works |

So hand the user the exact `git` commands, and keep everything else in the agent session.

## Procedure

Run from the library clone (e.g. `~/checkout3/presto-configerator-client`).

1. **Get the merged code** (user's shell): `git -C <clone> fetch origin`. Then check out `master`, fast-forward to `origin/master`, and confirm the change is in it (`git merge-base --is-ancestor <commit> HEAD`) and that the pom is at `X-SNAPSHOT`. `release:prepare` will release `X` and move to `X+1-SNAPSHOT` (it drops `-SNAPSHOT`, then increments the last number). Pass `-DreleaseVersion=X -DdevelopmentVersion=X+1-SNAPSHOT` to be explicit.

2. **Run the tests first.** The release build runs `clean verify -DskipTests`, so it does not test:
   ```bash
   MAVEN_OPTS="-Djava.net.preferIPv6Addresses=true" mvn -B clean test
   ```

3. **Prepare locally, without pushing.** The pom's `<scm>` may still point at the old `git@github.com:prestodb/...` SSH URL, so never let the plugin push:
   ```bash
   export MAVEN_OPTS="-Djava.net.preferIPv6Addresses=true"
   mvn -B release:clean release:prepare -DpushChanges=false \
     -DreleaseVersion=X -DdevelopmentVersion=X+1-SNAPSHOT \
     -Darguments="-Djava.net.preferIPv6Addresses=true"
   ```
   This makes the commits `[maven-release-plugin] prepare release X` and `... prepare for next development iteration`, plus tag `X`. It also writes `release.properties`, which `release:perform` needs.

4. **Push before uploading** (user's shell): `git -C <clone> push origin master --follow-tags`. If GitHub rejects it because master moved, nothing has been published yet: reset, refetch, and prepare again.

5. **Upload** with a client certificate (see Gotchas):
   ```bash
   PEM=/var/facebook/credentials/$USER/x509/$USER.pem
   P12=$(mktemp -d)/maven-client.p12
   openssl pkcs12 -export -in "$PEM" -inkey "$PEM" -name maven-client -out "$P12" -passout pass:changeit
   MAVEN_OPTS="-Djava.net.preferIPv6Addresses=true -Djavax.net.ssl.keyStore=$P12 -Djavax.net.ssl.keyStoreType=PKCS12 -Djavax.net.ssl.keyStorePassword=changeit" \
     mvn -B release:perform -DlocalCheckout=true -Darguments="-Djava.net.preferIPv6Addresses=true"
   ```
   `-DlocalCheckout=true` builds from the local tag instead of cloning through the pom's `<scm>` URL. The forked Maven inherits `MAVEN_OPTS`, so the certificate reaches the JVM that uploads.

6. **Verify** (anonymous GET, no proxy needed):
   ```bash
   curl -sS -o /dev/null -w '%{http_code}\n' \
     https://maven.thefacebook.com/nexus/content/repositories/releases/<group/path>/<artifact>/X/<artifact>-X.pom
   ```
   Expect `200`. Check the `.jar` too.

7. **Bump presto-facebook-trunk** in a separate fbsource diff and build it with `source ~/.localrc && mfi` (see presto-build). Precedent: D85725998 (presto-smc-client 1.21 → 1.22), whose test plan linked the library PR and a `presto-build` run.

## Where the versions are pinned

| Library | Coordinates | Pin |
|---|---|---|
| presto-configerator-client | `com.facebook.presto.configerator:presto-configerator-client` | literal `<version>` in `fbcode/github/presto-facebook-trunk/pom.xml` (~line 1603) |
| presto-smc-client | `com.facebook.presto:presto-smc-client` | property `dep.presto-smc-client.version` in the same pom (~line 80) |

`fbcode/github/presto-capella-client/pom.xml` also pins presto-configerator-client (1.12 as of 2026-11). The Buck copy under `fbcode/third-party-java/com/facebook/presto/configerator/` (1.10) is not used by the Maven build.

## Gotchas

| Symptom | Cause | Fix |
|---|---|---|
| `Network is unreachable` resolving plugins, while curl to the same URL works | JVM tries IPv4; the agent shell doesn't load `~/.localrc`'s `MAVEN_OPTS` | `MAVEN_OPTS=-Djava.net.preferIPv6Addresses=true` (and in `-Darguments`) |
| `403 Forbidden` on the first upload, nothing uploaded | Since Maven Proxy v335 (2026-10-22) uploads must present a corp client certificate whose identity has PUBLISH on `MAVEN_PROXY:maven_proxy_prod`. Without a certificate the proxy sees the edge's identity and refuses even users who are on the list. The shared `deployment` password alone no longer works. | Build the PKCS12 keystore (non-empty password, or Java sends no certificate) and pass it in `MAVEN_OPTS` as in step 5. Check the grant with `meta amp.acl check-access --type=MAVEN_PROXY --name=maven_proxy_prod --action=PUBLISH --unixname=$USER`. Details and announcement: presto-deps-all, "Uploads need a client certificate". |
| `401` | Expired or missing certificate | `fixcerts`, rebuild the keystore |
| `400 Bad Request` on upload | That version already exists (releases can't be overwritten), e.g. after a partial deploy | Check which artifacts exist; release the next version |
| `release:perform` failed | `release.properties` and `target/checkout` stay behind | Fix the cause and rerun `release:perform -DlocalCheckout=true`; no need to prepare again unless the version is burned |
| Plugin push fails or goes to the wrong org | `<scm>` points at `git@github.com:prestodb/...` | `-DpushChanges=false`, push by hand, and `-DlocalCheckout=true` |

## Worked example: presto-configerator-client 1.14 (2026-11-11)

PR #16 (qualify the TW job handle so Configerator canaries reach the gateway) merged as `25dca96`. Tests passed (43), `release:prepare` made `0738fea` / `945ddfc` and tag `1.14`, and the user pushed them. Two `release:perform` runs then got `403` with nothing uploaded: one from the agent session and one from the user's shell, both without a client certificate, although the user had PUBLISH (`ACCESS_GRANTED`, deciding accessor their own USER id). The fix is step 5's keystore. After the upload, the pin bump in presto-facebook-trunk and a gateway release through Conveyor are what make gateway canaries work (memory note `presto-gateway-no-config-canary`).
