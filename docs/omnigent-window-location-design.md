# Per-window location for the Omnigent desktop app

Status: **In progress.** The origins are live on this Mac and the source of
truth exists; the window placement and manifest rendering are not built yet.
See Verification for exactly which claims are observed versus still reasoned.

## Summary

Give each AeroSpace workspace's Omnigent window its own **origin**, so the
app's existing per-window memory of "which host and which checkout" stops
being shared across all eight windows.

Location (devserver + checkout) becomes a property of the _window_ — the
physical place the work happens. Projects stay orthogonal and mean "what
work", so the same project can be run from any location.

The single source of truth for the workspace table is built at the same time,
because this adds a fourth and fifth consumer of a mapping currently written
down three times.

## The problem

Starting a conversation in the ws6 window requires manually setting the host
to `devvm20365` and the workspace to `/home/mkarrmann/checkout3`. Both fields
default to whatever was last chosen **in any window**, so the ws6 window
routinely opens pointing at another workspace's checkout. The wrong value is
sticky and invisible: it did not come from anything on screen.

## Root cause

The composer's memory is app-global by design:

`web/src/lib/hostPreferences.ts:1`

```ts
// Persisted, app-global preference for which host the new-session landing
// composer starts on.
const STORAGE_KEY = "omnigent:last-host-choice";
```

The workspace side is the same shape — `omnigent:recent-workspaces`, a
`Record<host_id, string[]>` (`web/src/hooks/useRecentWorkspaces.ts:6`).

Both live in `localStorage`. There is no `session.fromPartition` anywhere in
`web/electron/src`, so every window shares `session.defaultSession`. Windows
do carry per-window state (`main.js:537-549`: `{origin, ephemeral, badgeCount,
browserRegistry}`) but no window id, no profile, and no location. There are no
CLI flags, and no `?host=` / `?workspace=` query parameters.

So there is no per-window configuration to set. **The only per-window axis the
app already has is `origin`** — and `localStorage` is partitioned by origin.

## Design

### Distinct hostname per workspace

Eight loopback hostnames, one per managed workspace, all resolving to
`127.0.0.1` and all served by the existing Caddy front door on 6443:

| ws  | hostname                           | host              | workspace          |
| --- | ---------------------------------- | ----------------- | ------------------ |
| 1   | `local.omnigent.localhost`         | MacBook-Pro.local | `/Users/mkarrmann` |
| 2   | `cco-checkout1.omnigent.localhost` | devvm20365.cco0   | `~/checkout1`      |
| 3   | `ftw-checkout1.omnigent.localhost` | devvm36111.ftw0   | `~/checkout1`      |
| 4   | `cco-checkout2.omnigent.localhost` | devvm20365.cco0   | `~/checkout2`      |
| 5   | `ftw-checkout2.omnigent.localhost` | devvm36111.ftw0   | `~/checkout2`      |
| 6   | `cco-checkout3.omnigent.localhost` | devvm20365.cco0   | `~/checkout3`      |
| 7   | `ftw-checkout3.omnigent.localhost` | devvm36111.ftw0   | `~/checkout3`      |
| 8   | `cco-checkout4.omnigent.localhost` | devvm20365.cco0   | `~/checkout4`      |

An origin is `scheme://host:port`, so **distinct names on one port are distinct
origins**. No new listeners are needed: the addresses join the existing site
block, keeping one `tls internal` directive, one h2 config, and one upstream —
Caddy issues each name a certificate from the CA already trusted for 6443.

Names, not `127.0.0.x`, for two reasons. The deep-link scheme rule is an exact
hostname set:

`web/electron/src/url.js:26`

```js
const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);
```

`localhost:PORT` therefore infers `http` and would miss the TLS listener
entirely. A non-listed name infers `https` and works. And because multi-server
mode prefixes notification titles with the firing origin's host
(`main.js:2673-2678`), a _descriptive_ name turns that unavoidable cost into
useful attribution: `[cco-checkout2] Session finished`.

**No `/etc/hosts` entries and no sudo.** macOS resolves `*.localhost` to
loopback natively, verified on this machine:

```
$ python3 -c "import socket; print(socket.getaddrinfo('cco-checkout3.omnigent.localhost', 443)[0][4])"
('127.0.0.1', 443)
```

No new certificate trust step either: `tls internal` issues from the CA already
trusted for 6443. Confirmed after the rollout — `curl` validates every new name
with no `-k`, and the chain reads `Caddy Local Authority - ECC Intermediate`.

### Placing each window on its origin

`startup-windows` currently creates Omnigent windows by driving the
**Server ▸ New Window** menu item over AppleScript
(`bin-macos/startup-windows:206-211`). Replace that with an OS-level deep
link, which is both simpler and more robust than synthetic UI events:

```
open "omnigent://cco-checkout3.omnigent.localhost:6443/c/<session_id>"
```

The grammar accepts only `/c/<session_id>` (`deepLink.js:26`), so the link
needs a target session. Rather than a fleet of placeholder sessions, resolve
it dynamically: **the most recent non-archived session whose `host_id` and
`workspace` match that row**, which lands the window on your last piece of
work in that checkout. When a location has no sessions yet, create one.

Window targeting is deterministic because each origin has exactly one window:

`web/electron/src/deepLink.js:117`

```js
const pick = onOrigin.find(({ i }) => i === focusedIndex) ?? onOrigin[0];
```

The first link to each origin raises a native consent dialog — pinning an
origin is a privilege grant. Eight one-time dialogs; afterwards
`chooseDeepLinkStrategy` returns `open-known` and placement is automatic.

### What this buys

Each window's `localStorage` is now its own. The app's existing behaviour —
restore the last host choice if that host is online, auto-seed the workspace
from that host's recents — becomes **per window** with no app change. Set each
window's location once; it holds.

## Single source of truth

The workspace table is currently written three times: the macOS
`WORKSPACES` array (`bin-macos/startup-windows:95-125`), the hand-maintained
`orchest_plugins.json` attribution map (`:65-96`), and the Linux table that
`bin-linux/startup-windows --print-layout` emits for
`bin-linux/orchest-plugins-render`. This design adds two more consumers —
the hostname/origin table and the Caddy address list — so the duplication is
resolved now rather than multiplied.

`docs/workspace-layout-refactor.md` proposed exactly this in 2026-05 and was
never implemented; the Linux side is the only part that achieved derivation.

**One declarative table** — workspace number, devserver FQDN, checkout path,
nvs session name, origin hostname — read by a small `bin-macos/workspaces`
emitter that prints JSON, mirroring the existing `--print-layout` precedent so
both platforms are consumed the same way. It lives in `bin-macos/` rather than
the `bin/workspaces` this doc first proposed: the table is the macOS layout,
with devservers and checkouts in it, and its Linux counterpart is already
`bin-linux/startup-windows --print-layout`. A shared `bin/` name would imply
one table serves both desktops, which is not true. Generated from it:

1. the macOS `WORKSPACES` rows (ghostty / chrome / omnigent triples);
2. `orchest_plugins.json` attribution, which stops being hand-maintained and
   becomes rendered like its Linux counterpart;
3. the reverse map, workspace → (host, checkout), which nothing produces today.

The `/etc/hosts` block is gone from this list — it is not needed at all.

**The Caddy address list is checked by a test rather than generated.** The
launchd job loads `services/omnigent-tls/Caddyfile` directly from the repo, and
that file is tracked, hand-edited, and mostly explanatory prose. Generating it
would turn a documented config into build output to derive nine address lines
that change approximately never. Instead `tests/test-workspaces.sh` asserts the
Caddyfile serves exactly the origins the table declares, at the declared TLS
port, and fails loudly naming the offending origin when they diverge. Drift is
the risk generation was there to remove; the test removes it at a fraction of
the cost.

This is a deliberate departure from `docs/workspace-layout-refactor.md` §7.4,
which rejected a generator-based source of truth as over-engineered — but
conditioned that rejection on there being only two consumers, both able to
source shell. That premise no longer holds: `orchest_plugins.json` is neither
shell nor Lua, which is exactly why `bin-linux/orchest-plugins-render` already
exists. So generation stands for the manifest, and a test covers Caddy.

The rendering follows `bin-linux/orchest-plugins-render`: write only when the
output changes, and fail loudly if a sentinel is missing rather than emit a
silently empty mapping.

## Projects

Projects must carry **no location**. A project's stored config can set
`hostId` and `workspace`, and if it does it _wins_ over the window's default:

`web/src/shell/projectPrefill.ts:10-16`

```
 * A project's stored session defaults, as the composer consumes them. This is
 * the ONLY project-driven prefill source: a set field seeds the composer, and
 * an absent field falls through to the composer's generic defaults (last host /
 * recent workspace / last-used agent).
 * `undefined` means the config is still loading for a project that has one — the
 * machine WAITS in that case so a generic default can't win the race.
```

Leaving both unset is the whole discipline: the window supplies where, the
project supplies what. Nothing to build.

## Accepted costs

**The dock badge will read 8× the true unread count.** `updateBadge`
(`main.js:579-589`) de-duplicates per `origin` and sums across origins; eight
origins fronting one hub each report the same server-wide count. Today the
badge is correct. This is accepted permanently.

It is arguably an upstream bug — `main.js:544` states the intent as "two
windows on the same server ... must not be double-counted", and `origin` is a
proxy for server identity that breaks when several origins front one server.
Fixing it upstream stays available and blocks nothing.

**Eight near-identical entries** in the recent-servers list, and eight
one-time consent dialogs.

**More concurrent SSE to the hub.** The live-stream budget is origin-wide and
coordinated by `navigator.locks`, which are per-origin
(`web/src/store/streamSlots.ts:1-16`). Eight origins means eight independent
budgets instead of one shared one. Client-side this is _correct_ — the
connection pool it protects is also per-origin, and it independently relieves
the contention that motivated the h2 front door — but the hub sees more
concurrent streams. Expected to be immaterial for a single-user hub; worth
watching.

**Not a new cost:** notification duplication. The SPA notifies for any session
it is not actively viewing and nothing de-duplicates across windows, so eight
windows already produce this today. Origins change only the title prefix.

## Verification

Originally reasoned from source only. Status as of the 2026-09-09 rollout on
the Mac:

1. **The core claim** — set a window's host and workspace, quit and relaunch
   the app, confirm that window restores its own values and that a _different_
   window is unaffected. Everything else is worthless if this fails.
   **Partially confirmed.** The storage layer does partition: after pinning one
   window to `cco-checkout3.omnigent.localhost:6443`, the app's localStorage
   grew a second origin bucket holding its own `omnigent:session-workspace-state`,
   separate from the `localhost:6443` bucket that holds everything else. The
   baseline before the change was a single shared bucket whose one
   `omnigent:last-host-choice` read `c8c10fd6…` — the _Linux_ hub — which is the
   reported bug, measured. Restore-across-relaunch is still unverified.
2. Deep link places a window on its origin and infers `https`. **Confirmed** —
   `open omnigent://cco-checkout3.omnigent.localhost:6443/c/<id>` landed a
   window on the origin over TLS.
3. Each origin serves h2 (`getConnectionProtocol` picks the 30-stream budget).
   **Confirmed** — `curl -w '%{http_version}'` reports `2` on the new names.
4. Notification prefixes read `[cco-checkout3]`. Not yet observed.
5. Renderers are idempotent and fail loudly on a missing sentinel.

Inspect the storage partitions directly rather than trusting the UI:

```bash
strings ~/Library/Application\ Support/Omnigent/Local\ Storage/leveldb/* \
  | grep -oE "_https?://[a-z0-9.:-]+" | sort | uniq -c
```

Rollback is cheap and total: point the windows back at `localhost:6443` and
drop the extra addresses. No server state changes.

## Out of scope

**Relocating an existing session** to another checkout or devserver. Logged as
a follow-up. Three obstacles, none addressed here:

- `host_id` and `workspace` are fixed at create, tied by a DB check
  constraint, and **absent from `UpdateSessionRequest`**
  (`omnigent/server/schemas.py:2159`), which exposes `runner_id`, `title`,
  `labels`, `reasoning_effort`, `model_override`, `collaboration_mode`.
  `runner_id` is described as "the mutable session affinity primitive", so
  rebinding the runner is possible while the recorded location goes stale.
- Native harnesses keep their real state on the origin host's filesystem —
  bridge directories under `/tmp/omnigent-*/claude-native/<id>/` and
  transcripts under `~/.claude/projects/`. A host move orphans both.
- Checkout-to-checkout on one host still leaves cwd, worktrees, and
  uncommitted work behind.

Starting _new_ work on the same project in a different location needs none of
this and works the day this lands.

**A hotkey that creates a pre-bound session** (considered as an alternative).
Unnecessary once the composer's defaults are correct per window, and it would
have left the composer itself broken.

## Alternatives rejected

- **Projects as location.** Conflates where with what, and would have pinned
  each project to one checkout — the opposite of the stated goal.
- **Upstream `/p/<project>` deep links.** Clean, but gated on an upstream merge
  and a release clearing `OMNIGENT_MIN_VERSION`.
- **Per-workspace ports on `localhost`.** Blocked outright: `localhost` infers
  `http` (`url.js:26`), so deep links cannot reach a TLS listener.
