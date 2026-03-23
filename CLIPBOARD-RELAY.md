# Clipboard Relay: Reverse Tunnel Approach

## Problem

Neovim runs as a headless server (`nvim --headless --listen PORT`) on the devvm,
with a remote UI client (`nvim --server localhost:PORT --remote-ui`) on the Mac
connected through ET tunnels. Clipboard integration is broken because:

1. The headless server has **no terminal** (nohup with stdio redirected to a log).
2. OSC 52 escape sequences require a terminal to reach Ghostty.
3. On Neovim 0.11.6, the built-in OSC 52 provider uses `nvim_chan_send(2, ...)`
   which writes to the server's TUI channel — nonexistent on a headless server.
4. The `--remote-ui` RPC protocol has no clipboard channel (proposed but not
   yet implemented upstream).

Copy (yank → Mac clipboard) is the hard problem. Paste (Mac → Neovim) works
via Cmd+V — Ghostty sends clipboard content as bracketed paste input.

## This Solution: Reverse Tunnel + Push

Architecture:

```
Mac (Ghostty)                    ET tunnel                    Devvm (headless nvim)
┌─────────────────┐                                    ┌──────────────────────┐
│ nvs-clip-listen  │◄── reverse tunnel ◄── nc ◄────────│ clipboard-relay.lua  │
│ (port 8765)      │    -r 8765:8765    localhost:8765  │ vim.g.clipboard copy │
│ pipes to pbcopy  │                                    │ fires on every yank  │
└─────────────────┘                                    └──────────────────────┘
```

**Server side** (`clipboard-relay.lua`): A custom `vim.g.clipboard` provider.
On yank, it spawns `nc -w 1 localhost 8765` via `vim.fn.jobstart` (async, never
blocks Neovim) and sends the text over stdin. If the tunnel is down, nc fails
silently — the yank still succeeds in the register.

**Mac side** (`nvs-clip-listen`): A loop that accepts one TCP connection at a
time and pipes the received text to `pbcopy`. Idempotent — exits immediately if
a listener is already running on the port.

**Tunnel** (`nvs-tunnels`): Adds `-r 8765:8765` to the x2ssh command, creating
a reverse ET tunnel so `localhost:8765` on the devvm reaches the Mac listener.
The listener is auto-started before the ET connection is established.

### Files

| File | Type | Purpose |
|---|---|---|
| `nvim/lua/lib/clipboard-relay.lua` | New | Server-side clipboard provider (nc push) |
| `bin-macos/nvs-clip-listen` | New | Mac-side listener (nc loop → pbcopy) |
| `bin-macos/nvs-tunnels` | Modified | Adds reverse tunnel + auto-starts listener |
| `bin/nvs` | Modified | Loads clipboard-relay on headless server start |
| `nvim/lua/config/options.lua` | Modified | Removes broken OSC 52 provider config |

### Paste behavior

- `p` / `P`: Pastes from Neovim's unnamed register (always works).
- `"+p`: Calls the paste function, which returns the last yanked content.
  This works for local yank/paste but does NOT reflect the current Mac clipboard.
- **Cmd+V**: Ghostty sends the Mac clipboard as bracketed paste input. This is
  the correct way to paste from the Mac clipboard into Neovim.

### Deployment

1. Sync dotfiles to devvms (clipboard-relay.lua + bin/nvs changes).
2. Run `init.sh` on Mac (links nvs-clip-listen into PATH).
3. Restart tunnel sessions (workspace T) — starts listener + reverse tunnel.
4. Restart headless nvim servers (or let them restart naturally).

### Caveat

The `-r` flag for x2ssh reverse tunnels is untested. Standard ET supports
`-r PORT:PORT`, but x2ssh is a Meta wrapper that may use different syntax. If
the reverse tunnel fails, this is the only line to adjust.

---

## Alternative Solution: Long-Poll via RPC (commit 505caa4)

Architecture:

```
Mac (Ghostty)                                              Devvm (headless nvim)
┌────────────────────┐     existing forward tunnel    ┌──────────────────────┐
│ _clip_sync loop    │──── nvim --remote-expr ────────│ nvs-clipboard.lua    │
│ in bin-macos/nvs   │     "v:lua._clip_wait(seq)"    │ vim.g._clip* globals │
│ emits OSC 52 to    │◄─── blocks via vim.wait() ◄───│ _clip_wait() blocks  │
│ /dev/tty → Ghostty │     until yank or 30s timeout  │ until _clip_seq      │
└────────────────────┘                                │ changes              │
                                                      └──────────────────────┘
```

**Server side** (`nvs-clipboard.lua`): On yank, stores text in `vim.g._clip*`
globals and increments `vim.g._clip_seq`. Exposes `_G._clip_wait(last_seq)`
which calls `vim.wait(30000, ...)` to block until a new yank or timeout.

**Mac side** (`bin-macos/nvs`): A background `_clip_sync` function that calls
`nvim --remote-expr "v:lua._clip_wait($last_seq)"`. This RPC call blocks
server-side until data is available, then returns `seq:base64_content`. The Mac
side decodes it and writes an OSC 52 escape sequence to `/dev/tty`.

This is a **long-poll** pattern — not active polling. Each RPC call blocks
for up to 30 seconds waiting for a yank event, so there's at most one process
spawn per yank (plus one per 30s timeout).

---

## Comparison

| Dimension | Reverse tunnel (this commit) | Long-poll (505caa4) |
|---|---|---|
| **Extra infrastructure** | Reverse ET tunnel + Mac listener | None — uses existing forward tunnel |
| **Deployment complexity** | Must verify x2ssh `-r` flag works | Just dotfiles sync |
| **Copy mechanism** | True push: nc on yank → tunnel → pbcopy | Long-poll: RPC blocks until yank, returns data |
| **Process spawning** | `nc` per yank (async, lightweight) | `nvim --remote-expr` per yank + per 30s timeout |
| **Server-side cost** | None | `vim.wait()` blocks with 200ms internal poll |
| **Mac-side cost** | Persistent listener process | Background watcher process |
| **Encoding** | Raw text over TCP | Base64 (avoids shell newline stripping) |
| **Failure mode** | nc fails silently if tunnel down | `--remote-expr` fails, retries after 1s |
| **Latency** | Essentially zero (TCP push) | Essentially zero (vim.wait returns immediately on yank) |
| **Moving parts** | 3 (relay module, listener, tunnel) | 2 (clipboard module, watcher in nvs) |

### Reverse tunnel strengths
- True event-driven push — no server-side blocking or internal polling.
- No process spawning overhead on the Mac side (listener is persistent).
- Architecturally clean: the server pushes clipboard data; the Mac receives it.

### Long-poll strengths
- No reverse tunnel — eliminates the untested x2ssh `-r` flag entirely.
- No extra listener process — self-contained within the existing nvs script.
- Simpler deployment — fewer files, no tunnel configuration changes.
- Base64 encoding handles edge cases (trailing newlines in clipboard content).

### Shared between both
- Both remove the broken OSC 52 provider from options.lua.
- Both load their clipboard module via `-c` in `bin/nvs` headless startup.
- Both rely on Cmd+V for Mac → Neovim paste (correct, not a hack).
- Both fail gracefully when the transport is down (yank still works locally).

### Recommendation

If x2ssh `-r` works without issues, the reverse tunnel approach is cleaner at
runtime (zero server-side overhead, true push). If x2ssh reverse tunnels are
problematic, the long-poll approach is the pragmatic choice — it works entirely
within the existing tunnel infrastructure.

A reasonable strategy: try the reverse tunnel first; fall back to long-poll if
the x2ssh flag doesn't cooperate.
