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

Override detection for one invocation with `DOTFILES_PROFILE=work ./init.sh`
(or `desktop`). To persist an exception, put the single word `work`, `desktop`,
or `auto` in `~/.config/dotfiles/profile` (under `$XDG_CONFIG_HOME` if set).
The environment variable wins over that file. Existing devservers and the work
Mac should need no override.

`sync.sh` applies profile-specific config; `init.sh` also installs tools and
converges services. Desktop sync removes previously managed internal MCP
registrations and work Claude plugin settings while preserving unrelated
personal entries. Codex's explicit local MCP overrides still win.
Neither profile switching nor desktop sync stops services or removes packages
left by an earlier work setup. Those need a deliberate, separate cleanup.

Regression checks use temporary homes and stubbed installers/service managers:

```sh
python3 -m unittest discover -s tests -p test_dotfiles_profiles.py
python3 -m unittest discover -s tests -p test_codex_config.py
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
