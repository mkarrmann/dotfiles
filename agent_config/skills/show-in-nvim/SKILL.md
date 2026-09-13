---
name: show-in-nvim
description: Reveal source locations in Matt's running Neovim session instead of only pasting them into chat. Use whenever he asks to show, open, reveal, jump to, pull up, or "take me to" code, a file, a function, a definition, a call site, or a line — and when reporting review findings, bugs, or a set of locations he will want to walk through. Also use to clear previously pushed annotations. Trigger keywords: show me, show me the code, open it, open in nvim, reveal, jump to, pull up, take me there, where is it, put it in my editor, quickfix, location list, annotate.
---

# Show code in Neovim

`nvim-show` opens a concrete source location in the Neovim session that owns it.
Prefer it over describing a location in prose: when Matt asks _where_ something
is, put it in front of him.

## Commands

```bash
nvim-show FILE [LINE [COL]] [--text NOTE]   # jump to one location
nvim-show --list [PATH|-]                   # JSON array -> location list
nvim-show --clear                           # drop this session's annotations
```

Options: `--focus`, `--session NAME`, `--title LABEL`, `--no-jump`, `--agent KEY`.

Add `--focus` when he asks to be taken there ("show me", "open it", "pull it
up"): it brings the editor's window forward where the platform can (on the
Linux desktop, the terminal tab beside the Omnigent window). Leave it off for
background annotation he did not ask to look at yet, such as a review's
findings while he is still reading your summary.

Always resolve the concrete path and line from context and run the command —
never print it for Matt to run himself. Paths may be relative; they are resolved
against the process working directory.

## One jump

```bash
nvim-show fbcode/velox/exec/HashProbe.cpp 412 9
```

Add `--text` to leave one inline note at that line:

```bash
nvim-show fbcode/velox/exec/HashProbe.cpp 412 9 --text "the spill path starts here"
```

## Several locations

Use `--list` for anything he will want to walk through — all the call sites, the
findings from a review, every place a pattern appears. Each entry's `text`
becomes both the location-list entry and inline virtual text at that line.

```bash
nvim-show --list - <<'JSON'
[
  {"file": "fbcode/velox/exec/HashProbe.cpp", "line": 412, "col": 9, "text": "unchecked index"},
  {"file": "fbcode/velox/exec/HashBuild.cpp", "line": 88, "col": 3, "text": "same assumption"}
]
JSON
```

It is a **location list**, not the quickfix list, so it is scoped to your tab and
does not clobber another agent's results. The list window opens automatically, so
the entry count is visible; Matt walks it with `]l` / `[l`, and `<leader>xL` opens
it in Trouble. A single jump opens no list window.

Give a run of related pushes a shared `--title` so the tab and list are labelled
usefully, e.g. `--title "review: spill path"`.

## Tabs, and cleaning up

Each agent session owns exactly one tabpage, keyed automatically by
`$CC_SESSION_ID`, or by the Omnigent session id under an Omnigent runner.
Everything you show lands in that one tab; a concurrently running agent gets
its own. Repeated single jumps append to your location list,
so the history stays walkable. `--list` replaces it.

Run `nvim-show --clear` when a set of annotations is stale — after he says the
findings are addressed, or before pushing an unrelated set. It clears only your
own annotations and list.

## Which Neovim it picks

Usually you do not have to care — just run the command. It resolves the target
itself and tells you which one it used. Two cases need you to do something.

**Nothing is running.** It says `cannot determine which Neovim to use`. Ask Matt
to start one, or for an address to use.

**Several are running and none is implied.** It lists them and stops rather than
guessing. Pass `--server ADDR` with the one he names.

The full order, for when you need to reason about it. Steps marked _(nvs)_ apply
only on machines running `nvs` headless servers — Matt's devservers; the step
marked _(desktop)_ only on his Linux desktop. Each switches itself off elsewhere.

Named or derived; if unreachable this errors rather than redirecting:

1. `--server ADDR` — a socket path or `host:port`
2. `--session NAME` _(nvs)_
3. `$NVIM` — you are running in a terminal buffer inside Neovim
4. the machine's `nvim-show-resolver`. On the desktop that is the Neovim on the
   same sway workspace as the Omnigent window showing your session — the
   editor physically beside the conversation, whatever directory either is in.
   It reports `session … is not open in any Omnigent window` when he has
   navigated away, and the fallbacks below take over _(desktop)_
5. longest workdir-prefix match of the shown path against this host's nvs
   session list, so a file under `~/checkout2` opens in that checkout's
   editor _(nvs)_
6. the same match against the working directory _(nvs)_

Fallbacks; each is skipped when it is not reachable:

7. the target you last resolved to — so `--clear`, which carries no path, and
   later jumps outside any checkout still land in your own tab
8. `$NVIM_SHOW_SERVER`
9. `$NVS_TARGET_SESSION` _(nvs)_
10. the sole running Neovim, found from its default server socket

On a devserver, rule 5 means a path under a checkout always reaches that
checkout's editor without configuration. The first push of a path outside every
checkout — `~/dotfiles`, say — has nothing to key on, so ask Matt for a session
rather than guessing one from the list it prints; after that, rule 7 carries the
rest of the conversation.

On the desktop, rule 4 needs your session to be open in an Omnigent window. If
it reports that it is not, and nothing is remembered (rule 7), ask him to open
the session in the window he wants to work beside rather than passing a socket.

Without `--focus` there is no window focus: the tab lights up in his tabline and
he switches to it. Say what you pushed and where, e.g. "opened
`HashProbe.cpp:412` in CCO-checkout2" or "… in the ws 2 terminal".

## Notes

- Same machine only. `nvim-show` reaches Neovim instances local to wherever it
  runs; it cannot cross to another host.
- Editing `show-in-nvim.lua` does not affect a Neovim that has already used it —
  Lua caches the module. Restart it, or clear `package.loaded`.
- Implementation: `~/dotfiles/bin/nvim-show`,
  `~/dotfiles/nvim/lua/lib/show-in-nvim.lua`, and on the desktop
  `~/dotfiles/bin-linux/nvim-show-resolver`.
