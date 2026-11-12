# T3 Code over Omnigent

T3 Code is a UI over the Omnigent sessions on the hub candidates. Omnigent still
owns every session: harness, TUI, policies, watcher wakeups, the Google Chat
bridge. T3 Code reaches it through `bin/omnigent-opencode`
(`services/omnigent-opencode`), an OpenCode 2 server whose sessions are
Omnigent sessions, registered as T3 Code's OpenCode provider "Omnigent". Each
Omnigent agent (`claude-native-ui`, `codex-native-ui`) is a model group in it.
See "The provider" below, and the service's README for its design.

## One server per host, every window on all of them

Each host runs one T3 Code server whose projects are all of its workspaces'
directories: T3 Code's own model, in which a server owns its machine's projects,
threads, files and terminals, and a client connects to every server and shows
them as one (`~/repos/t3code/docs/internals/remote.md`). Each workspace gets a
browser window: a Chrome `--app` window on a hostname of its own (on the Mac in
Google Chrome Beta, a browser of their own, on Linux in a Chrome profile of its own), served by its host's server and, at work, linked to every other
devserver's, so every window lists, searches (Cmd+K) and starts threads on every
host. Only where its new threads start is per window (see "A window's
project"). T3 Code's desktop app is one instance with one window (`t3 app PATH`
only switches that window's project), so the windows are Chrome's.

A thread stays on the server it started on: T3 Code moves a draft between
machines (Cmd+Shift+H) but neither moves nor forks a sent thread to another
server, and its fork always stays on the source thread's server. The Omnigent
session behind a thread likewise runs on that host.

## Work: devservers and the Mac

- `bin/t3-ensure` (run by `init.sh` on the candidates) installs the `t3` CLI,
  registers the "Omnigent" provider in the shared settings (see "Settings in the
  dotfiles"), to which it links `~/.t3-workspaces/checkouts`', removes the
  ACP providers `bin/omnigent-acp` used to be, turns off T3
  Code's built-in Codex and Claude (they bypass Omnigent; re-enabling one in
  Settings sticks), adds every `~/checkoutN` as a project, named after its host
  (`checkout1 · cco`, so the windows can tell CCO's from FTW's), installs the
  provider's runtime (`uv sync` of `services/omnigent-opencode`), and enables `t3@checkouts` (`systemd/t3@.service`,
  `bin/t3-workspace-serve`) once. It also retires the server per checkout this
  replaced: every enabled `t3@checkoutN` is disabled and stopped, and its state
  in `~/.t3-workspaces/checkoutN` left. It restarts a running server only
  when it runs a `t3` other than the installed one.
- Upgrading: bump `T3_CLI_VERSION` and every `T3_CLI_SHA256` in
  `bin/t3-ensure` (from the release's `SHA256SUMS`), then run `init.sh` on
  each devserver. `t3-ensure` downloads the pinned archive with curl, checks
  it, links `~/.local/bin/t3` to it and restarts `t3@checkouts` onto it. An
  older `t3` is replaced, a newer one left alone. `t3 update` does not work
  on a devserver: its own fetch goes neither through the proxy nor with the
  client certificate `~/.curlrc` gives curl, so it fails on `getaddrinfo
  ENOTFOUND github.com`, `with-proxy` or not. The previous version stays in
  `~/.t3/runtime/versions/` for a rollback.
- `t3@checkouts` listens on `127.0.0.1:T3_PORT`. `bin-macos/nvs-tunnels`
  forwards it to the Mac once per candidate, on `T3_PRIMARY_TUNNEL_PORT`
  or `T3_STANDBY_TUNNEL_PORT` (`omnigent_config/topology.env`); every
  window shares the forwards, its own host's as the page's server and the
  other's as a linked one. A tunnel picks a new forward up
  only when it restarts; with T3 Code windows on, `startup-windows` rebuilds a
  tunnel that lacks it, as it does one that lacks its Omnigent forward.
- `bin-macos/workspaces` gives each devserver workspace a `t3Url` on its host's
  port at a hostname of its own, `<origin>.t3.localhost`, and
  `bin-macos/startup-windows` opens it as an `--app` window of Google Chrome
  Beta (`T3_BROWSER_APP`; `open -na "Google Chrome Beta" --args
  --profile-directory=Default --app=URL`). A browser of their own, rather than
  your Chrome, is what lets `bin-macos/t3-browser-shortcuts` (run by `sync.sh`)
  move the shortcuts Chrome reserves and a page cannot override (Cmd+N, Cmd+T,
  Cmd+W, ...) off T3 Code's without taking them from your browsing; its own
  bundle id keeps macOS from handing it your links and Dock clicks, which
  sank the instance per window before it (`~/.t3-workspace-chrome/<name>`, which
  `startup-windows` now quits). The script tells the windows apart (they share a title) by
  their origin, which `bin/t3-window-scope --list-windows` reads through
  AppleScript; the hostname keeps each window's cookies and localStorage its
  own. AppleScript runs page JavaScript only with View > Developer > Allow
  JavaScript from Apple Events on in that browser. A browser can open a link
  only in itself, so `t3code/link-forwarder`, an extension loaded in Chrome
  Beta, hands every http(s) tab a window opens (other than a `*.t3.localhost`
  one) to `bin-macos/t3-open-in-chrome`, its native messaging host, which opens
  it in your Chrome's work profile (`--profile-directory=Default`) and closes
  the tab; `sync.sh` registers the host (`t3-open-in-chrome --install`).
- A window `startup-windows` opens starts its new threads in its workspace's
  checkout and links every other devserver's server
  (`bin/t3-window-scope @chrome ORIGIN ~/checkoutN --link http://127.0.0.1:PORT LABEL`,
  the label the prefix of that host's nvs sessions, CCO or FTW): the Mac knows a
  checkout only by its place under the devserver's home, and `~/checkoutN` is
  the one live project whose root ends in `/checkoutN` on the window's own
  server. `~/.local/state/t3-windows/<browser>/<name>` (`Google-Chrome-Beta`)
  records the servers a window's
  origin is set up with by environment id (`.t3-servers/ENVIRONMENT_ID`): its
  own once a window was paired and scoped, the other once linked. A run reopens,
  paired, a found window whose origin is not set up with its own server (a run
  that failed, an origin paired with an earlier server), and links a set-up window into a server it does not list yet in
  place (`--link-only`, which leaves its page). A server whose state was reset
  has a new id and is set up again. A window it
  finds is left as it is. A failure warns and the window stays.
- The provider creates sessions on its own host (`OMNIGENT_OPENCODE_HOST`,
  default `auto`) in the thread's project.

## The Mac's own server: workspace 1

Workspace 1 is the Mac's own (`1|local|~|...|~/dotfiles` in `bin-macos/workspaces`),
so its window is on a T3 Code server on the Mac: `t3@local` on
`127.0.0.1:T3_PORT`, run by `launchd/com.mkarrmann.t3-local.plist` (loaded by
`sync.sh`; `bin/t3-workspace-serve local`), whose project is `~/dotfiles`. The
Mac is an Omnigent host, so its provider's sessions run here.
`bin/t3-ensure` (run by `init.sh`, and by hand) registers the provider and adds
the project. The window is at `http://local.t3.localhost:T3_PORT/`, pairs with a
token `startup-windows` mints on the spot (`t3 pair` with
`T3CODE_HOME=~/.t3-workspaces/local`), links both devservers' servers, and
every devserver window links it ("Mac").

## Pairing at work

A pairing token is good once, and every window pairs with each devserver's
server, so each host keeps a pool: with T3 Code windows on, `startup-windows`
starts each tunnel as `nvs-tunnels HOST --t3-pairing ...`, whose remote command
runs `bin/t3-pairing-tokens COUNT` inside the ET session it already authenticated,
which mints COUNT 12-hour tokens from `t3@checkouts` (none while it is not
running), twice the Mac's devserver windows so reopened windows have some too.
`nvs-tunnels` pools them, a file each, in `~/.local/state/t3-pairing/HOST/` (not
shown in the tunnel window), and empties that pool when a session starts, as
its tokens came from the session before; `startup-windows` drops tokens past
11 hours. `startup-windows` never waits on Duo: before a host's first window or link it
checks once that its server answers through the tunnel (giving the Mac's own
server 15 s at login), and when it does not, leaves that host's windows (and any
window linking it) for later, listing them in
`~/.local/state/startup-windows-pending`. Each tunnel runs
`startup-windows --deferred` on connecting, whenever you approve Duo; that run
opens or links only those windows, arranges only their workspaces and sweeps
nothing (logged to `~/.local/state/startup-windows-logs/deferred.log`). The
Omnigent windows wait the same way while the hub does not answer. A window it creates opens on
`/pair` with a token of its own host's, and links each other host with a token
of that host's; a new origin waits up to 30 s for each, while an origin that
paired before (its session cookie lasts about 30 days) takes one only when one
is there. A window it finds on an error page (opened before its tunnel was up)
is replaced. A window that still asks to pair, or does not list another host's
threads, needs fresh tokens: reconnect the tunnel, close the window, and rerun
`startup-windows`.

## Enabling at work

The work side is on (`STARTUP_T3_WINDOWS=1`, the default in
`bin-macos/startup-windows`; `STARTUP_T3_WINDOWS=0` turns it off). Turning it on
took:

1. On each candidate, the next `init.sh` starts `t3@checkouts` with every
   `~/checkoutN` as a project and retires the old `t3@checkoutN` servers (their
   `~/.t3-workspaces/checkoutN` stays; delete it by hand once unneeded). Check
   with `systemctl --user list-units 't3@*'`.
2. On the Mac, `brew install --cask google-chrome@beta`, open it once to get
   through its first run, and turn on View > Developer > Allow JavaScript from
   Apple Events. Then run `~/dotfiles/sync.sh`, so `~/bin/t3-window-scope`
   exists, `t3-browser-shortcuts` frees Cmd+N and the like and the link
   forwarder's host is registered, and quit and reopen Chrome Beta to pick
   those up. In Chrome Beta's `chrome://extensions`, turn on Developer mode,
   Load unpacked `~/dotfiles/t3code/link-forwarder` (its id is
   `pdcoehmlpmfjiimeabommdgknpnjadca`, fixed by its key), and leave it on.
3. Run `~/bin/startup-windows`. It finds both tunnels
   without their new forward or token pool and restarts them (expect Duo), then
   opens each window, paired with both servers and scoped as it is created; that
   first run takes a few seconds more per window. With
   `STARTUP_T3_WINDOWS=0` the tunnels mint no tokens.

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

## Settings in the dotfiles

T3 Code has two kinds of settings, kept two ways:

- **Server settings** (providers, models, compaction, the environment theme):
  each server's `userdata/settings.json` is a symlink, made by `bin/t3-ensure`,
  to `t3code/settings.json`, which every server at work, the Mac's and the
  desktop's share. T3 Code saves through the link and reloads the file when it
  changes (since 0.1.6), and writes only what differs from its defaults, so a
  change in any window's server settings shows up as a dotfiles diff on that
  host, to commit and pull to the others like any other. The Omnigent
  provider is registered by bare name (`omnigent-opencode`, found on PATH via
  `~/bin`), as one file cannot hold each host's home. Being shared, an
  environment theme or icon set on one server is every server's.
- **Client settings** (appearance, fonts, diff layout, notifications): a window
  keeps them in its page's localStorage and the desktop app in
  `~/.t3/userdata/client-settings.json`; neither can be a symlink. `bin/t3-client-settings`
  syncs them with `t3code/client-settings.json`, which holds only what differs
  from 0.1.6's defaults (`t3code/client-settings.defaults.json`; regenerate with
  `t3code/generate-client-defaults VERSION` when the pin moves). Each run takes
  what changed in any window or the desktop app since its last run, writes the
  file, and puts the result in every window (reloading the ones it changed);
  the desktop app only while it is not running. Both `startup-windows` run it
  after their T3 Code windows are up; run `~/bin/t3-client-settings` to sync
  sooner. Two windows that changed one setting differently stop the sync until
  `--prefer NAME` picks one (a Mac window's origin's first label, a Linux
  window's profile directory name, or `desktop`).

## A window's project

T3 Code (0.1.6) has no URL or setting for the project a window's new threads
start in; it is client state in the page's localStorage, per profile and per
origin, so per window. `bin/t3-window-scope` reaches a new window through its
browser's DevTools port (`--remote-debugging-port=0`: loopback only, so web
pages cannot reach it, local processes can, as they can the Omnigent app's
port on the desktop and the hub's on the Mac). It first saves each `--link`
server in the window by opening T3 Code's pairing link for it,
`/pair?host=URL&label=LABEL#token=TOKEN` (the token from stdin), and waits until
the window's saved connections (IndexedDB `t3code:connection-runtime`) name that
server's environment. It then writes `projectOrder` with the project first,
keyed `ENVIRONMENT_ID:ROOT` as the same root (`~/checkout1`) is a project on
both devservers (`getProjectOrderKey`, `packages/client-runtime/src/state/projectGrouping.ts`),
clears any sidebar scope (`t3code:ui-state:v1`, `apps/web/src/uiStateStore.ts`),
and leaves the window on a draft in the project, which the app opens itself (`chat.newLocal`, mod+shift+n, from `/new`;
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

- The links and the scoping depend on T3 Code's client internals (its pairing
  link, the IndexedDB where it saves connections, the localStorage keys above),
  which differ between 0.1.6 and later releases. After an upgrade, rerun
  `tests/test-t3-window-scope-e2e.sh`, which checks them against the installed
  `t3` and Chrome.

- T3 Code finds `~/.local/bin` and `~/bin` only through a login-shell PATH probe
  that gives up after 5 s (`apps/server/src/os-jank.ts`), so
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

## The provider

`bin/omnigent-opencode` runs `services/omnigent-opencode`'s virtualenv (made
by `bin/t3-ensure`). T3 Code starts one per server on first use and stops it 30
seconds after its last thread closes; threads resume by id
(`ses_<Omnigent session id>`), and what Omnigent cannot tell it (T3 Code's
message ids, rollbacks) is kept in `~/.local/state/omnigent-opencode/state.db`.
Env, set in the provider's environment in T3 Code's settings: `OMNIGENT_URL`
(default `http://127.0.0.1:6767`), `OMNIGENT_OPENCODE_AGENTS` (default
`claude-native-ui,codex-native-ui`), `OMNIGENT_OPENCODE_HOST`,
`OMNIGENT_OPENCODE_LOG` (a log file), `OMNIGENT_OPENCODE_DEBUG`.

What T3 Code gets:

- **Steering.** A message sent while the agent works goes into the running turn
  (codex-native `turn/steer`, Claude's own input queue) without interrupting
  it; T3 Code shows it delivered when the harness takes it.
- **Stop** interrupts the turn (Escape on Claude, which the agent can survive).
- **Turns started outside T3 Code** (a watcher's wakeup, a background task's
  notification, a message from the Omnigent app or the TUI) show in the thread
  as turns the agent started; with the daemon, as the reply to an "Agent"
  message `[Omnigent] <the input>`, also after T3 Code has let go of the thread.
- **Subagents.** Claude's Agent tool and Codex's spawn_agent show as T3 Code
  subagent calls, each with a child thread following the child Omnigent
  session; a background subagent's result wakes the parent with its report.
- **Approvals** follow the thread's runtime mode: outside full access (and in
  plan mode) the provider turns on an Omnigent session policy
  (`t3-code-runtime-mode`, the built-in `ask_on_os_tools`) and answers its asks
  by T3 Code's rules, so "allow for this session" works on both harnesses.
  Your own Omnigent policies (`no_new_environments`, ...) always ask, even in
  full access. See the service README.
- **Questions** (Claude's AskUserQuestion, Codex's requestUserInput) are T3 Code
  forms.
- **Fork** from any turn keeps the history before it (an Omnigent fork, on the
  same host); **rollback** is a fork at the boundary that the thread then
  continues in.
- **Model and effort** switch live; **plan mode** is codex-native's plan
  collaboration mode and, on Claude, the approval policy with edits denied;
  **/compact** compacts the Omnigent session; **images** are sent inline.
- **Titles** and other text T3 Code generates come from a one-shot `claude -p`
  (`OMNIGENT_OPENCODE_TEXTGEN_MODEL`, or the whole command in
  `OMNIGENT_OPENCODE_TEXTGEN_COMMAND`).

## Limits

- Switching a claude-native session's model makes Claude Code save it as your
  default model in `~/.claude/settings.json` (Omnigent types `/model` in its
  terminal). This is Omnigent's switch, not the provider's; it was so with the
  ACP providers too.
- A fork stays on its host: T3 Code forks only within a server. `omnigent-acp`'s
  `/fork-to HOST` slash command is not carried over; fork to another host from
  the Omnigent app.
- Codex is less covered than Claude: its subagents and its file edits under the
  approval policy are untested, and a declined Codex request ends the turn.
- A denial by the approval policy reaches the model as the policy's reason, not
  T3 Code's note.
- T3 Code lets go of a thread 30 minutes after its last turn
  (`ProviderSessionManager.ts`, `DEFAULT_IDLE_TIMEOUT_MS`) and stops the
  provider 30 seconds after its last thread. The provider's daemon
  (`omnigent-opencode daemon`, run by launchd or systemd wherever a T3 Code
  server runs; see the service README's "Daemon") follows each session anyway,
  for a week after its last activity, and puts every turn started outside T3
  Code to T3 Code as an "Agent" message (`[Omnigent] ...`) whose turn shows the
  agent's work. Without the daemon (it is not running, or T3 Code started the
  provider before it was), such a turn is not shown live.
- Opening a thread whose Omnigent session is gone fails instead of quietly
  starting a fresh session.
- Each edit's diff shows in its tool call; T3 Code's checkpoint diffs and git
  panes need a git or jj repository, which the Sapling checkouts are not.
- Threads from the ACP providers (`omnigent-claude`, `omnigent-codex`) keep
  their history in T3 Code but cannot be continued there; their Omnigent
  sessions continue in the Omnigent app.
