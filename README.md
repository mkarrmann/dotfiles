Not really just dotfiles per se. Any setup scripts and files I want to share across all my machines.

# Mac and Linux

Dotfile git repo.

First run init.sh to symlink dotfiles.

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
