# T3 Code over Omnigent

T3 Code is a UI over the Omnigent sessions on the hub candidates. Omnigent still
owns every session: harness, TUI, policies, watcher wakeups, the Google Chat
bridge. T3 Code reaches it through `bin/omnigent-acp`, an ACP agent registered
as T3 Code's "Local command" ACP provider.

## One server per checkout

T3 Code's desktop app is single-window and has no way to open scoped to a
project, so each workspace instead gets a browser window on a T3 Code server
whose only project is that workspace's checkout:

- `bin/t3-ensure` (run by `init.sh` on the candidates) installs the `t3` CLI,
  registers the "Omnigent · Claude" (`claude-native-ui`) and "Omnigent · Codex"
  (`codex-native-ui`) providers in `~/.t3-workspaces/checkoutN`, warms uv's cache
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

Each window's Chrome profile pairs with its server once, with no extra login:
`nvs-tunnels`' remote command runs `bin/t3-pairing-tokens` inside the ET session
it already authenticated, which mints a 12-hour one-time token per running
server; `nvs-tunnels` saves them under `~/.local/state/t3-pairing/HOST/` (not
shown in the tunnel window). When `startup-windows` creates a window for a
profile without `~/.t3-workspace-chrome/<name>/.t3-paired`, it opens it on the
server's `/pair` URL with that token, then marks the profile. To pair a profile
again (its session expired), delete that marker and its window, reconnect the
tunnel, and rerun `startup-windows`.

## Limits

- Forks and side chats start a new Omnigent session seeded with a summary: T3 Code's
  ACP registry adapter never asks an ACP agent to fork.
- Stop on a native session sends Escape, which the agent can survive.
- Turns started outside T3 Code (wakeups, other clients) do not appear in it.
- `omnigent-acp --t3-tools` gives the Claude TUI T3 Code's tools, but only with
  the token from launch; the providers above do not enable it.
