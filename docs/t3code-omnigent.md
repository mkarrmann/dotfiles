# T3 Code over Omnigent

T3 Code is a UI over the Omnigent sessions on the hub candidates. Omnigent still
owns every session: harness, TUI, policies, watcher wakeups, the Google Chat
bridge. T3 Code reaches it through `bin/omnigent-acp`, an ACP agent registered
as T3 Code's "Local command" ACP provider.

## One server per host

Each host runs one T3 Code server whose projects are all of its workspaces'
directories, and each workspace gets a browser window on it: a Chrome `--app`
window on a hostname and in a Chrome profile of its own. A server per workspace
kept each window to its own directory, but it also split the threads, so that
no window saw another's. Now every window lists every project and every thread,
and only where its new threads start is per window (see "A window's project").
T3 Code's desktop app is single-window and has no way to open on a project, so
the windows are Chrome's.

## Work: devservers and the Mac

- `bin/t3-ensure` (run by `init.sh` on the candidates) installs the `t3` CLI,
  registers the "Omnigent · Claude" (`claude-native-ui`) and "Omnigent · Codex"
  (`codex-native-ui`) providers in `~/.t3-workspaces/checkouts`, turns off T3
  Code's built-in Codex and Claude (they bypass Omnigent; re-enabling one in
  Settings sticks), adds every `~/checkoutN` as a project, warms uv's cache for
  the shim, and enables `t3@checkouts` (`systemd/t3@.service`,
  `bin/t3-workspace-serve`) once. It also retires the server per checkout this
  replaced: every enabled `t3@checkoutN` is disabled and stopped, and its state
  in `~/.t3-workspaces/checkoutN` left. It never restarts a running server;
  after `t3 update`, restart `t3@checkouts` to pick up the new version.
- `t3@checkouts` listens on `127.0.0.1:T3_BASE_PORT`. `bin-macos/nvs-tunnels`
  forwards it to the Mac once per candidate, on `T3_PRIMARY_TUNNEL_BASE_PORT`
  or `T3_STANDBY_TUNNEL_BASE_PORT` (`omnigent_config/topology.env`); every
  window on that devserver shares the forward. A tunnel picks a new forward up
  only when it restarts; with T3 Code windows on, `startup-windows` rebuilds a
  tunnel that lacks it, as it does one that lacks its Omnigent forward.
- `bin-macos/workspaces` gives each devserver workspace a `t3Url` on its host's
  port at a hostname of its own, `<origin>.t3.localhost`, and
  `bin-macos/startup-windows` opens it as a Chrome `--app` window in a profile of
  its own, `~/.t3-workspace-chrome/<name>`. The profile, read from the owning
  browser's command line, is how the script tells the windows apart (they share
  a title); the hostname keeps each window's cookies and localStorage its own.
- A window `startup-windows` opens starts its new threads in its workspace's
  checkout (`bin/t3-window-scope PROFILE ORIGIN ~/checkoutN`): the Mac knows a
  checkout only by its place under the devserver's home, and `~/checkoutN` is
  the one live project whose root ends in `/checkoutN`. A window it finds is
  left as it is. The DevTools port the helper needs is on the launch that starts
  the profile's browser (`--no-startup-window --remote-debugging-port=0`): on
  macOS a later `open -na ... --app=` hands its window to the running browser,
  which ignores new flags, and a browser outlives its last window. So a profile
  whose browser was started without the port warns instead of being scoped
  until that browser is quit. A failure warns and the window stays.
- Each shim creates sessions on its own host (`--host auto`) in the checkout.

## Pairing at work

A pairing token is good once, so each window still pairs with one of its own:
`nvs-tunnels`' remote command runs `bin/t3-pairing-tokens` inside the ET session
it already authenticated, which mints a 12-hour token per `~/checkoutN` from
`t3@checkouts` (none while it is not running); `nvs-tunnels` saves them as
`~/.local/state/t3-pairing/HOST/checkoutN` (not shown in the tunnel window).
Before `startup-windows` creates a devserver's first window it waits for the
server to answer through the tunnel and for that checkout's token, then opens
every window it creates on `/pair` while a token is available. A window it finds
on an error page (opened before its tunnel was up) is replaced. A window that
still asks to pair needs a fresh token: reconnect the tunnel, close the window,
and rerun `startup-windows`.

## Enabling at work

The work side is off (`STARTUP_T3_WINDOWS=0` in `bin-macos/startup-windows`).
To turn it on:

1. On each candidate, the next `init.sh` starts `t3@checkouts` with every
   `~/checkoutN` as a project and retires the old `t3@checkoutN` servers (their
   `~/.t3-workspaces/checkoutN` stays; delete it by hand once unneeded). Check
   with `systemctl --user list-units 't3@*'`.
2. On the Mac, run `~/dotfiles/sync.sh` so `~/bin/t3-window-scope` exists, and
   quit every T3 Code window from the old design (Cmd+Q in it: closing the
   window leaves its browser running without a DevTools port).
3. Run `STARTUP_T3_WINDOWS=1 ~/bin/startup-windows`. It finds both tunnels
   without their new forward and restarts them (expect Duo), then opens each
   window, paired and scoped as it is created; that first run takes a few
   seconds more per window. To keep the windows, set the default of
   `STARTUP_T3_WINDOWS` in `bin-macos/startup-windows` to 1.

## Linux desktop

The desktop runs one server, with no tunnel, and a window per workspace whose
new threads start in that workspace's project:

- One server, `t3@desktop` on `127.0.0.1:3780` (`T3_SERVER` and `T3_PORT` in
  `bin-linux/startup-windows`), whose projects are the workdirs of the table's
  `ws|t3` rows. `--print-layout` lists the server as `t3Server`/`t3Port` and the
  rows under `t3`; `bin/t3-ensure` (run by `init.sh`) enables `t3@desktop` and
  adds each row's workdir as a project, and `bin/t3-workspace-serve` reads its
  port from the same layout.
- `startup-windows` opens a row's window only while `t3@desktop` is enabled (the
  row is otherwise inert): a Chrome `--app` window on
  `http://<slot>.t3.localhost:3780/` in the profile `~/.t3-workspace-chrome/<slot>`.
  sway shows nothing that tells these windows apart (their app_ids are Chrome's
  per-app ids, and they share a title), so a window's profile, read from its
  browser's command line, is its identity and the mark `sw:<ws>:t3` its claim;
  it is the last window of its workspace's stack. Before opening a window the
  script waits up to 30 s for the server, and it replaces a window left on
  Chrome's error page once the server answers.
- A window it opens starts its new threads in its row's project
  (`bin/t3-window-scope PROFILE ORIGIN WORKDIR`, the launch adding
  `--remote-debugging-port=0`). If that fails, the run warns and the window
  stays. A window the script holds or adopts is left as it is; to scope one
  opened before this, close it and rerun (`$mod+Shift+r`).
- Pairing is local: the script mints a 5-minute token with `t3 pair`
  (`T3CODE_HOME=~/.t3-workspaces/desktop`) and opens a new window on
  `/pair#token=<token>`. The profile stays signed in for about 30 days; a window
  that asks to pair again needs closing and a rerun (`$mod+Shift+r`).
- The T3 Code desktop app (`bin/t3-desktop-app-ensure`) runs its own server on
  `~/.t3`; to see the same threads there, pair it with `t3@desktop`
  (`t3 pair --base-dir ~/.t3-workspaces/desktop` prints an `App link`).

## A window's project

T3 Code (0.1.6) has no URL or setting for the project a window's new threads
start in; it is client state in the page's localStorage, per profile and per
origin, so per window. `bin/t3-window-scope` reaches a new window through its
browser's DevTools port (`--remote-debugging-port=0`: loopback only, so web
pages cannot reach it, local processes can, as they can the Omnigent app's
port on the desktop and the hub's on the Mac), writes `projectOrder` with the
project first and clears any sidebar scope (`t3code:ui-state:v1`,
`apps/web/src/uiStateStore.ts`), and leaves the window on a draft in the
project, which the app opens itself (`chat.newLocal`, mod+shift+n, from `/new`;
the helper presses Cmd where the page's `navigator.platform` is a Mac's, as T3
Code reads mod, and Ctrl elsewhere).

- What starts in the window's project: the composer the window opens on,
  `chat.newLocal` (Ctrl+Shift+N, Cmd+Shift+N on the Mac), and a Shift-click on
  the sidebar's New thread button, all of which start in the project of the
  thread or draft on screen.
- What does not: the New thread button and `chat.new` (Ctrl+N, Cmd+N) open a
  project picker while the server has more than one project; `/` opens a draft
  in the most recently active project (`routes/_chat.index.tsx`); and after
  opening another project's thread, those follow that thread's project.

Gotchas:

- T3 Code finds `~/.local/bin` only through a login-shell PATH probe that gives up
  after 5 s (`apps/server/src/os-jank.ts`), and the shim's `uv` lives there, so
  `systemd/t3@.service` sets PATH itself.
- Provider processes are not in the unit: T3 Code runs each in a transient scope
  under `app-t3code-agents-<hash>.slice`, so they outlive a restart and the next
  start stops the leftovers.
- Chrome rewrites its process title on Linux, so a browser's `/proc/<pid>/cmdline`
  is one space-joined string with a single trailing NUL, not one argument per
  NUL; `chrome_user_data_dir` in `bin-linux/sway-windows-lib.sh` reads it that
  way.
- `t3 serve` prints a one-time pairing QR code and token to its journal at
  startup (`journalctl --user -u t3@desktop`, `-u t3@checkouts` at work).
- The startup WARN that the host "blocks the sandbox T3's browser runs in" and
  asks for `sudo env "PATH=$PATH" t3 browser setup` concerns only T3 Code's
  built-in browser tabs and HTML previews, not these windows.

## Limits

- Forks and side chats start a new Omnigent session seeded with a summary: T3 Code's
  ACP registry adapter never asks an ACP agent to fork.
- Stop on a native session sends Escape, which the agent can survive.
- Turns started outside T3 Code (wakeups, other clients) do not appear in it.
- `omnigent-acp --t3-tools` gives the Claude TUI T3 Code's tools, but only with
  the token from launch; the providers above do not enable it.
