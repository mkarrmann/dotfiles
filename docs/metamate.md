# Working on Metamate 3

Metamate 3 (agentcloud; the `metamate`/`mm` TUI, the web app at
agentcloud.internalmeta.com, `agentcloudctl`) is replacing the Omnigent hub at
work. Metamate owns sessions, harnesses, the inbox, mobile and Slack; this
setup adds only what it lacks for working across several checkouts. Nothing
here wraps Metamate's chat or calls its orchestrator directly: everything goes
through `agentcloudctl` and the Skills SDK, its supported scripting surfaces
(fbcode/agentcloud/INTEGRATING.md).

## Facts this rests on (checked 2026-11-24)

- Both devservers and the Mac are Metamate nodes (`agentcloudctl node list`):
  `devvm20365.cco0.facebook.com`, `devvm36111.ftw0.facebook.com`. A node takes
  many sessions at once.
- The web composer cannot start a chat in `~/checkout2..4`: its Folder field is
  hardcoded to `fbsource`/`configerator`/`www` under `/data/users/$USER`
  (`nest/apps/agentcloud/lib/nodes/devserverFolder.ts`), and its default is a
  `/tmp/agentcloud-*-workspace/<id>` scratch folder. The machine default is one
  per account, or per Space with the `space-session-defaults` experiment;
  nothing defaults per window.
- `agentcloudctl create --node-id H --workspace P --startup-folder project`
  does start a hosted CLI in `P`, from a shell or from inside a session (the
  security guardrail allowed it), and the thread is an ordinary inbox entry.
- Hosted Claude Code runs as Matt with his login `PATH`, `AGENTCLOUD_SESSION_ID`
  set, and a per-session `CLAUDE_CONFIG_DIR` that links everything in
  `~/.claude` except `skills/`. So rules, `CLAUDE.md`, MCP servers, plugins and
  user hooks apply; `~/.claude/skills` does not; `~/checkoutN/.claude/skills`
  does (found by walking up from the workspace).
- A `PreToolUse` deny from a hook blocks the call in a hosted session (it ran
  with `permission_mode: auto`).
- `nvim-show` from a hosted session reaches the checkout's nvs Neovim
  (`localhost:<port>` answered `v:servername`).
- Codex children need `--node`: `agentcloudctl spawn|run -s "$AGENTCLOUD_SESSION_ID"
  --harness codex --node H --workspace P`.
- Metamate's own agent and Codex/Muse take skills only from Metamate settings
  (spec 2783, knobs on): `meta skills.sdk.home-live add` / `create` makes a
  private catalog skill, `install --harness=agentcloud` installs it, and an
  edit needs `revise --files-json-file` plus `set-stable` (a new revision is
  not served until it is stable).

## Pieces

| Piece | What it does |
|---|---|
| `bin/mm-checkout` | `list`: each place's threads, commit and uncommitted count; `new PLACE\|auto`: start a thread in that checkout. Places come from `bin-macos/workspaces`. |
| `agent_config/skills/metamate-checkouts` | Teaches any agent (in a chat, on the phone, in Slack) to place work with `mm-checkout`. |
| `bin/metamate-skills-sync` + `agent_config/metamate-skills.list` | Keeps the listed skills installed in Metamate settings (create, install, revise, set-stable, uninstall). State in `~/.local/state/metamate-skills/state.json`. |
| `sync.sh` workspace skill links | Global skills are also linked into `~/checkoutN/.claude/skills`, where hosted Claude Code finds them. A local session lists a skill present at both levels once. |
| `claude_config/hooks/guardrails.py` | `PreToolUse` hook applying `no_new_environments`, `no_foreground_wait` and `destructive_commands` (Omnigent's `blast_radius` deny tier, plus `sl`/`hg` pushes) to Claude sessions Omnigent does not front. Abstains when `$OMNIGENT` is set. |
| Orchest `plugin-metamate-bridge` | Orchest workspace/task attention from `agentcloudctl fleet --watch` (in `~/repos/orchest`). |

## Using it

- Start work in a checkout from any Metamate chat: "start a thread in cco:2 to
  …", or `~/dotfiles/bin/mm-checkout new cco:2 --prompt "…"` from a shell.
- See what is where: `~/dotfiles/bin/mm-checkout list`.
- After editing a listed skill: `~/dotfiles/bin/metamate-skills-sync`.

## Known gaps

- New chats from the web composer still start in `/tmp` scratch; per-window
  checkout defaults need Metamate changes (see
  [metamate-upstream-requests.md](metamate-upstream-requests.md)).
- No guardrails for hosted Codex (its copied `hooks.json` loses path-keyed
  trust) or Muse (its prepared home drops `~/.config/muse`), nor for
  Metamate's own agent (only checked-in tenant policy reaches it).
- Draft-diff CI: Phabricator sends no CI events for drafts; Metamate's own
  watches catch them only through the 10-minute safety poll on watches it arms
  itself. Untested end to end.

## Verifying

```sh
python3 -m unittest discover -s ~/dotfiles/tests -p 'test_mm_checkout.py'
python3 -m unittest discover -s ~/dotfiles/tests -p 'test_metamate_skills_sync.py'
python3 -m unittest discover -s ~/dotfiles/tests -p 'test_guardrails_hook.py'
~/dotfiles/bin/mm-checkout list
```
