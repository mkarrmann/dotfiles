# T3 Code over Omnigent

T3 Code is a UI over the Omnigent sessions on the hub candidates. Omnigent still
owns every session: harness, TUI, policies, watcher wakeups, the Google Chat
bridge. T3 Code reaches it through `bin/omnigent-acp`, an ACP agent registered
as T3 Code's "Local command" ACP provider.

## Pieces

- `bin/t3-ensure` (run by `init.sh` on the candidates): installs the `t3` CLI,
  pins the service to `127.0.0.1:$T3_SERVER_PORT`, registers the
  "Omnigent · Claude" (`claude-native-ui`) and "Omnigent · Codex"
  (`codex-native-ui`) providers in `~/.t3/userdata/settings.json`, warms uv's
  cache for the shim, and installs `t3code.service` once. It never restarts a
  running service; upgrades go through `t3 update` or the app's update button.
- `bin-macos/nvs-tunnels`: forwards `T3_PRIMARY_TUNNEL_PORT` / `T3_STANDBY_TUNNEL_PORT`
  (`omnigent_config/topology.env`) to each candidate's T3 Code server, next to
  the Omnigent forward. A tunnel picks it up when it next restarts.
- Each shim creates sessions on its own host (`--host auto`) in the thread's
  folder, so a thread on the standby runs there.

## Pair the Mac (once per devserver)

On the devserver: `t3 pair --ttl 30m`. On the Mac, with the tunnel up, open the
printed app link with the address rewritten to the local tunnel port, e.g.
`http://127.0.0.1:23773/pair#token=…` for the standby, in **Settings →
Environments → Add environment**. The app keeps the connection afterwards.

## Limits

- Forks and side chats start a new Omnigent session seeded with a summary: T3 Code's
  ACP registry adapter never asks an ACP agent to fork.
- Stop on a native session sends Escape, which the agent can survive.
- Turns started outside T3 Code (wakeups, other clients) do not appear in it.
- `omnigent-acp --t3-tools` gives the Claude TUI T3 Code's tools, but only with
  the token from launch; the providers above do not enable it.
