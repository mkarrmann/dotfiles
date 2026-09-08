Not really just dotfiles per se. Any setup scripts and files I want to share across all my machines.

# Mac and Linux

Dotfile git repo.

First run init.sh to symlink dotfiles.

## Desktop and work setup

`init.sh` and `sync.sh` share `bin/dotfiles-profile`. It automatically selects
`work` for a hostname ending in `.facebook.com` or a machine with `x2ssh` on
PATH (the work Mac). Other machines use `desktop`; installing Omnigent alone
does not change that choice. Check the result with `~/dotfiles/bin/dotfiles-profile`.

Both profiles configure the shell, terminal, editor, and ordinary tools.
Desktop setup fetches the CodeCompanion fork from GitHub and skips internal
plugins/MCPs and the Omnigent hub infrastructure. Work setup retains the
devserver bootstrap sources, work MCPs, Linux services, and Mac client jobs.
On Linux it discovers routing before running dependent Omnigent setup; failure
skips those dependent steps, while declared Neovim sessions can still start.

Both profiles install the GitHub CLI into `~/.local/bin` (`bin/gh-ensure`).
Desktop `init.sh` also installs the AWS CLI and, once `aws login` has been run
on that machine, the AWS Agent Toolkit: the `aws-mcp` server and AWS's default
`aws-*` skills, written into every detected agent (`bin/aws-agent-toolkit-ensure`).
Credentials never live in dotfiles; before login the toolkit step skips with
instructions, and re-running after setup refreshes the skills.

Desktop `init.sh` also installs Omnigent if absent. On Linux, `sync.sh` stages
`systemd/desktop/omnigent-host.service`; `init.sh` enables and restarts it.
The unit runs the standard `omnigent host --server '' --non-interactive`,
which manages a local server and execution host with state under `~/.omnigent`.
Systemd starts it at login (at boot too if user lingering is enabled).
Personal Macs use the native background `omnigent start` lifecycle instead.
Desktop `omnigent claude`, `omnigent codex`, and other commands pass straight
through to the native CLI and use your local agent credentials; an explicit
`--server URL` still selects a remote server. Unlike the work wrapper,
desktop does not inject `OMNIGENT_URL` as a command-line server.

On Debian/Ubuntu amd64 desktops, `init.sh` also installs the Omnigent GUI if
the `omnigent-desktop-electron` package is absent. Its first-install version,
download URL, and SHA-256 are pinned in `omnigent_config/desktop-app.env`;
update all three together when choosing a new release. Existing installations
are left untouched, including older versions; upgrades remain explicit.
The installer uses an interactive sudo/apt prompt. Unattended runs print the
command to run later without downloading or installing anything. To install
only the GUI, run `./bin/omnigent-desktop-app-ensure` in a terminal.
`sync.sh` also links `omnigent_config/omnigent-desktop-electron.desktop` over the package's
launcher entry to pass a Chromium flag that avoids a first-launch crash under sway with
fractional scaling (details in that file).

Linux desktops also get a systemd-coredump size cap (`systemd/desktop/coredump-size-cap.conf`,
installed to `/etc/systemd/coredump.conf.d/` by `bin/coredump-size-cap-ensure`) so a crashing
Electron app cannot fill the root filesystem with a 30 GB core. Needs sudo, so `init.sh` prints
the command when run non-interactively.
Other architectures and distributions skip this package installer. Devservers
and Macs skip it entirely, and `sync.sh` never invokes it. On first launch,
select `http://127.0.0.1:6767`; the app's settings stay machine-local.

Override detection for one invocation with `DOTFILES_PROFILE=work ./init.sh`
(or `desktop`). To persist an exception, put the single word `work`, `desktop`,
or `auto` in `~/.config/dotfiles/profile` (under `$XDG_CONFIG_HOME` if set).
The environment variable wins over that file. Existing devservers and the work
Mac should need no override.

`sync.sh` applies profile-specific config; `init.sh` also installs tools and
converges services. Desktop sync removes previously managed internal MCP
registrations and work Claude plugin settings while preserving unrelated
personal entries. Codex's explicit local MCP overrides still win.
Desktop sync only stages the service link. Desktop init also stops and disables
the work units declared in `systemd/desktop/disabled-units.list`, then hands
the local host role to systemd. It retires only units linked to this checkout;
unrelated custom units are preserved. Host units can switch between the two
managed profiles; a custom host unit is reported as a conflict. Re-running
desktop init restarts the host and can interrupt active Omnigent sessions.
For just this setup, run `bin/omnigent-desktop-ensure` (or `--stage` to only
stage the unit). Installed packages and session history are retained.

Regression checks use temporary homes and stubbed installers/service managers:

```sh
python3 -m unittest discover -s tests -p test_dotfiles_profiles.py
python3 -m unittest discover -s tests -p test_codex_config.py
python3 -m unittest discover -s tests -p test_omnigent_desktop.py
python3 -m unittest discover -s tests -p test_omnigent_desktop_app.py
```

## Sway window orchestration (Linux desktops)

`bin-linux/startup-windows` builds and repairs the sway session's workspace
layout. It is the Linux counterpart of `bin-macos/startup-windows` (AeroSpace),
and sway starts it once per session via the `exec` line at the bottom of
`sway_config`. Rebuild on demand with `$mod+Shift+r`, or run
`~/bin/startup-windows` directly; `--dry-run` prints the plan without touching
anything.

| WS | Contents |
|----|----------|
| 1  | local terminal (bare nvim) + Chrome + Omnigent |
| 2  | nvim in `~/dotfiles` + Chrome + Omnigent |
| 3  | nvim in `~/dev/orchest` + Chrome + Omnigent |
| 4  | nvim in `~/repos/omnigent` + Chrome + Omnigent |
| 5  | nvim in `~/work/presto` + Chrome + Omnigent |
| 6  | nvim in `~/gatech` + Chrome + Omnigent |
| 9  | second-monitor dashboard (Obsidian) |
| Z  | overflow / stray sweep (`$mod+z`) |

Each workspace is one tabbed container ordered terminal, Chrome, Omnigent.
Workspace-to-monitor pinning is machine-local (`~/.config/sway/config.d/`, see
`sway_config.local.example`), so the dashboard lands on the second monitor
without the script doing any monitor arithmetic of its own — unlike
`bin-macos/arrange-ws11`, which has to resolve the display itself.

Edit the `WORKSPACES` table to change the layout. Terminals run `nvim`
directly in the repo (the Mac's `nvs` exists to keep buffers alive on a remote
devserver across SSH drops, which does not apply locally). A machine that
needs a different layout can drop a `~/.config/sway-windows/layout.sh` that
reassigns `WORKSPACES` / `DASHBOARD_PANES` / `CHROME_CMD`, rather than editing
the repo table.

The script is idempotent and self-healing: a re-run adopts what is already
there, returns displaced windows to their workspace, and sweeps anything
unclaimed to `Z`. It is much smaller than the Mac original because sway
supplies what AeroSpace does not:

- Terminals are identified by an app_id set at launch (`ghostty --class=...`),
  so they need no title matching or creation-order polling. The class must be a
  valid GTK application id — ghostty silently falls back to its default
  otherwise, so `startup-windows` validates every slot before launching.
- Chrome and Omnigent cannot carry a per-window identity (Chrome's second
  window inherits the first's `--class`), so they are claimed by sway marks.
  Marks are globally unique in sway, which makes double-claiming impossible and
  lets a re-run read back the previous run's claims.
- Layout is `layout tabbed`, which is idempotent and absorbs late-arriving
  windows, so there is no flatten-and-rebuild pass.

Two Linux-specific constraints are load-bearing:

- Omnigent is launched through its **desktop entry**, never
  `/opt/Omnigent/omnigent-desktop-electron` directly, so it inherits
  `--disable-features=WaylandFractionalScaleV1` from
  `omnigent_config/omnigent-desktop-electron.desktop`. Without that flag a
  first launch on a fractionally scaled output dies with SIGTRAP.
- Additional Omnigent windows need `wtype` (`sudo apt install wtype`). The app
  is single-instance, a deep link reuses an existing window, and a second
  launch only focuses one, so the Server ▸ New Window accelerator
  (Ctrl+Shift+N) is synthesized — the same thing the Mac script does by
  clicking that menu item via AppleScript. Without `wtype` you get one Omnigent
  window and a warning per additional slot.

Regression checks. The first needs no compositor; the second starts a private
headless sway (pinned to `WLR_BACKENDS=headless`, stub apps only, under its own
D-Bus session and runtime dir where `dbus-run-session` exists) and exercises
the real orchestration: dry run, fresh build, idempotent re-run, self-healing,
stray sweep, floating repair, `--no-dashboard`, retired slots, prefix and
`DASHBOARD_WS` overrides, dashboard claims. It never touches the live session
and skips itself if sway, ghostty, or flock is missing.

```sh
./tests/test-sway-windows.sh
./tests/test-sway-windows-e2e.sh
```

## Omnigent topology

`omnigent_config/topology.env` declares the central Omnigent hub shared by the
Mac and every devserver. Linux bootstrap runs an execution host everywhere but
starts the server, prod-network proxy, and Google Chat mobile bridge only on
that hub. The Mac reaches it through the ET tunnel.

The Google Chat space/identity policy is tracked in
`omnigent_config/google-chat.env`; `init.sh` materializes its owner-only runtime
env on the hub. `~/.omnigent/config.yaml` remains local because its host ID is
the unique identity of that physical machine. Bootstrap reconciles its server
routing and ACP declarations without copying that identity.

Agent session naming helpers:

- Claude: managed by Agent Manager (`cn`, `cr`, etc.).
- Codex: lightweight named-session sync is available via shell functions:
  - `con <name> [prompt...]`
  - `cor <name> [prompt...]`
  - `cof <name> [prompt...]`
  - `codex_name <name>`
  - `cols`

Codex name/session mappings are stored in `~/.codex/agents.tsv` (machine-local).

`bin/codex` is the launcher every codex entry point goes through. It picks the
real binary in order: `/usr/local/bin/codex` (Meta's provisioned install, which
owns auth on devservers and the work Mac), `/opt/homebrew/bin/codex`, the
npm global prefix (`$NPM_CONFIG_PREFIX`, else `~/.npm-global`), then PATH. On
machines without a provisioned install this lands on the same install that
codex's own `npm install -g @openai/codex` self-update writes, so accepting an
in-app upgrade actually takes effect. Set `CODEX_LAUNCHER_CANDIDATES` (a
colon-separated list) for layouts the default order misses.

Then download vim relative line numbers from https://www.vim.org/scripts/script.php?script_id=2351

Follow instructions to install, run:

vim RltvNmbr.vba.gz
:so %

# Mac

On mac, run `cp -r CatchMouse.app/ ~/Applications/` to install CatchMouse.

Also install Karabiner-Elements from online and hammerspoon from brew.

# Windows

Press `Windows key` + `r` to open `Run`
Enter `shell:startup`
Copy `.exe` files to this directory
