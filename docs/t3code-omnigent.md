# T3 Code over Omnigent

T3 Code is a UI over the Omnigent sessions on the hub candidates. Omnigent still
owns every session: harness, TUI, policies, watcher wakeups, the Google Chat
bridge. T3 Code reaches it through `bin/omnigent-acp`, an ACP agent registered
as T3 Code's "Local command" ACP provider.

## One server per checkout

T3 Code's desktop app is single-window and has no way to open scoped to a
project, so each workspace instead gets a browser window on a T3 Code server
scoped to that workspace's checkout (other projects, like `~/dotfiles`, can be
added per window):

- `bin/t3-ensure` (run by `init.sh` on the candidates) installs the `t3` CLI,
  registers the "Omnigent · Claude" (`claude-native-ui`) and "Omnigent · Codex"
  (`codex-native-ui`) providers in `~/.t3-workspaces/checkoutN`, turns off T3
  Code's built-in Codex and Claude (they bypass Omnigent; re-enabling one in
  Settings sticks), adds `~/checkoutN` as a project, warms uv's cache
  for the shim, and enables `t3@checkoutN` (`systemd/t3@.service`,
  `bin/t3-workspace-serve`) once. It never restarts a running server; after
  `t3 update`, restart the instances to pick up the new version.
- `~/checkoutN` listens on `127.0.0.1:T3_BASE_PORT+N`; `bin-macos/nvs-tunnels`
  forwards it to the Mac on the candidate's `T3_*_TUNNEL_BASE_PORT+N`
  (`omnigent_config/topology.env`). A tunnel picks new forwards up when it
  next restarts.
- `bin-macos/workspaces` gives each devserver workspace a `t3Url` on its own
  `*.t3.localhost` hostname, and `bin-macos/startup-windows` opens it as a Chrome
  app window in a Chrome profile of its own (`~/.t3-workspace-chrome/<name>`).
  The profile is how the script tells the windows apart; they share a title.
- Each shim creates sessions on its own host (`--host auto`) in the checkout.

## Pairing

Each window's Chrome profile pairs with its server without an extra login:
`nvs-tunnels`' remote command runs `bin/t3-pairing-tokens` inside the ET session
it already authenticated, which mints a 12-hour one-time token per running
server; `nvs-tunnels` saves them under `~/.local/state/t3-pairing/HOST/` (not
shown in the tunnel window). Before `startup-windows` creates a devserver's first
window it waits for that server to answer through the tunnel and for its token,
then opens every window it creates on the server's `/pair` URL while a token is
available. A window it finds on an error page (opened before its tunnel was up)
is replaced. A window that still asks to pair needs a fresh token: reconnect the
tunnel, close the window, and rerun `startup-windows`.

## Linux desktop

The desktop runs one server, with no tunnel, and a window per workspace scoped
to that workspace's project:

- One server, `t3@desktop` on `127.0.0.1:3780` (`T3_SERVER` and `T3_PORT` in
  `bin-linux/startup-windows`), whose projects are the workdirs of the table's
  `ws|t3` rows. A server per workspace scoped each window too, but split the
  threads so that no window saw another's. `--print-layout` lists the server as
  `t3Server`/`t3Port` and the rows under `t3`; `bin/t3-ensure` (run by `init.sh`)
  enables `t3@desktop` and adds each row's workdir as a project, and
  `bin/t3-workspace-serve` reads its port from the same layout.
- `startup-windows` opens a row's window only while `t3@desktop` is enabled (the
  row is otherwise inert): a Chrome `--app` window on
  `http://<slot>.t3.localhost:3780/` in the profile `~/.t3-workspace-chrome/<slot>`.
  sway shows nothing that tells these windows apart (their app_ids are Chrome's
  per-app ids, and they share a title), so a window's profile, read from its
  browser's command line, is its identity and the mark `sw:<ws>:t3` its claim;
  it is the last window of its workspace's stack. Before opening a window the
  script waits up to 30 s for the server, and it replaces a window left on
  Chrome's error page once the server answers.
- Each window is scoped to its row's project as `startup-windows` opens it:
  its new threads start there, and its sidebar lists only that project. T3 Code
  (0.1.6) has no URL or setting for either; both are client state in the page's
  localStorage, per profile and per origin, so per window.
  `bin-linux/t3-window-scope` reaches the new window through Chrome's DevTools
  port (the launch adds `--remote-debugging-port=0`: loopback only, the same
  trust as the Omnigent app's), writes `projectOrder` and the sidebar scope
  (`t3code:ui-state:v1`, `apps/web/src/uiStateStore.ts`) and separate project
  grouping (`t3code:client-settings:v1`; it makes a scope key
  `<environmentId>:<path>`), and leaves the window on a draft in the project,
  which the app opens itself (Ctrl+Shift+N from `/new`). If that fails, the run
  warns and the window stays, but may not keep to the project. A window the
  script holds or adopts is left as it is; to scope one opened before this,
  close it and rerun (`$mod+Shift+r`).
- What follows the scope: the composer the window opens on, Ctrl+Shift+N
  (`chat.newLocal`), and a Shift-click on the sidebar's New thread button, all
  of which start in the project of the thread or draft on screen. What does
  not: the New thread button and Ctrl+N (`chat.new`) open a project picker
  while the server has more than one project; `/` opens a draft in the most
  recently active project (`routes/_chat.index.tsx`); and opening a thread
  outside the scope (a link, the command palette) widens the sidebar back to
  all projects (`Sidebar.tsx`), until the project is picked again in its
  filter.
- Pairing is local: the script mints a 5-minute token with `t3 pair`
  (`T3CODE_HOME=~/.t3-workspaces/desktop`) and opens a new window on
  `/pair#token=<token>`. The profile stays signed in for about 30 days; a window
  that asks to pair again needs closing and a rerun (`$mod+Shift+r`).
- The T3 Code desktop app (`bin/t3-desktop-app-ensure`) runs its own server on
  `~/.t3`; to see the same threads there, pair it with `t3@desktop`
  (`t3 pair --base-dir ~/.t3-workspaces/desktop` prints an `App link`).

Gotchas:

- T3 Code finds `~/.local/bin` only through a login-shell PATH probe that gives up
  after 5 s (`apps/server/src/os-jank.ts`), and the shim's `uv` lives there, so
  `systemd/t3@.service` sets PATH itself.
- Provider processes are not in the unit: T3 Code runs each in a transient scope
  under `app-t3code-agents-<hash>.slice`, so they outlive a restart and the next
  start stops the leftovers.
- Chrome rewrites its process title, so a browser's `/proc/<pid>/cmdline` is one
  space-joined string with a single trailing NUL, not one argument per NUL;
  `chrome_user_data_dir` in `bin-linux/sway-windows-lib.sh` reads it that way.
- `t3 serve` prints a one-time pairing QR code and token to its journal at
  startup (`journalctl --user -u t3@desktop`).
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
