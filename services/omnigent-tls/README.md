# Omnigent TLS front door

A local caddy listener that gives the Omnigent desktop app an HTTP/2 origin. It
reverse proxies to `mac_proxy` and changes nothing server-side; the hub and the
omnigent repo are untouched.

## Why

The desktop app is an Electron shell that loads its SPA from the hub, so every
window is a document on one origin sharing one `defaultSession` — and therefore
one Chromium socket pool. Chromium allows 6 sockets per origin on HTTP/1.1, and
the app holds one for the entire life of every conversation stream.

The app knows this and sizes itself accordingly: `maxLiveConversations` returns
3 on HTTP/1.1 and 30 on a multiplexed transport, coordinated across windows
with `navigator.locks`. But when a window finds every slot held by other
windows and has nothing of its own to reclaim, it opens its active conversation
over budget rather than showing a dead pane. With 8 windows that means 8 streams
against 6 sockets: the pool saturates, every other request — message POSTs
included — queues behind streams that never end, and only restarting the app
clears it.

Measured before this existed: 14 established sockets to the hub, being 8 update
WebSockets (separate pool, 255 limit, never the problem) plus exactly 6 HTTP,
holding the same ephemeral ports for 35 minutes.

Chromium never negotiates cleartext h2, so lifting the ceiling requires TLS.

## Shape

```
Omnigent.app --> https://localhost:6443  (caddy, TLS + h2)
                        |
                        v
                 127.0.0.1:6767  (mac_proxy)  <-- CLI and health probes
                        |
                   ET tunnel --> hub
```

`mac_proxy` keeps `OMNIGENT_PORT`, so `bin/omnigent`, `omnigent-server-url`,
and every dotfiles health probe are unaffected. Rollback is re-pointing the app
(`Server > Change Server…`, or pick the old URL from `recent_servers`) — this
unit can keep running.

After the cutover the app holds one multiplexed connection instead of six
capped ones, and live streams move freely between roughly 9 and 22.

## Operating it

Supervised by `launchd/com.mkarrmann.omnigent-tls.plist` (KeepAlive), installed
by `sync.sh`. `bin-macos/omnigent-tls-ensure` installs caddy and reports CA
trust; `init.sh` runs it at login.

The Caddyfile sets `admin off`, which disables the two caddy subcommands that
use the admin API. Use these instead:

```bash
# trust the local CA (what `caddy trust` would do)
bin-macos/omnigent-tls-ensure --trust

# apply an edit to the Caddyfile (what `caddy reload` would do)
launchctl kickstart -k gui/$UID/com.mkarrmann.omnigent-tls
```

Re-trust is only needed if caddy regenerates its CA — a wiped data directory,
or root expiry in 2036.

## Checking it

```bash
bin-macos/omnigent-conn-check   # which transport is live, and the budget
bin/omnigent-stream-check       # live stream count, run on the hub
```

`omnigent-conn-check` is the one that can tell whether the app is on h2 or has
silently rolled back to plain HTTP — the hub cannot, because the Caddyfile
preserves `Host: 127.0.0.1:6767` upstream so both paths look identical to it.

Healthy looks like one non-WebSocket connection. Six means the app is on
HTTP/1.1 and wedged.

## Trade-off to watch

The budget going 3 → 30 moves load onto the tunnel: `mac_proxy` opens one
upstream TCP per stream. Peak observed after cutover was 25 concurrent tunnel
connections, against a ceiling of 6 before. `mac_proxy` caches its `/health`
verdict per candidate for 1s and single-flights concurrent probes, which keeps
a burst of stream opens from doubling that.
