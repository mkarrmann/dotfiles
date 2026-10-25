# Neovim Meta/Sapling integration: fix list

Status: done 2026-10-24 (see [Outcome](#outcome)). Written 2026-10-24.

This lists everything to fix in the Meta/Sapling layer of the Neovim config:
the smartlog panel, hg commands, gutter signs and inline blame, the diff
viewers, and the Meta tooling glue. It comes from five independent review
passes over the code at commit `7e589b7`. Most findings were reproduced by
running the real config headlessly against throwaway Sapling repos. Each
finding says how it was established:

- **repro**: a headless test showed the failure.
- **measured**: timings taken in `~/checkout1/fbsource`.
- **reasoning**: read from the code, not run. Usually because the trigger
  needs Phabricator, or is a race that's hard to stage.

Paths are relative to `nvim/`. Line numbers are as of `7e589b7`; they will
drift as fixes land, so the function names are the stable pointer.

Scope agreed: tiers 1–3 (T1 = loses data, silently does the wrong thing, or
affects everything; T2 = real but narrower bug; T3 = cleanup). Items under
"Ideas" are not scheduled.

## Outcome

Every T1–T3 item landed on `master` as its own commit, each with a spec that
failed before the fix where a spec was feasible. Run them all with
`~/dotfiles/tests/test-nvim-meta-hg.sh` (44 specs, about a minute; hermetic). Worked
serially in `~/dotfiles` rather than in the five worktrees the plan proposed;
G1, G3–G6 and G9's `termopen` bullet went to one helper agent working on
files nobody else touched.

| Item | Commit | Notes |
|---|---|---|
| 0 | `52ec269`, `71b839d`, `96b09ce`, `6c2c402`, `7cf760c` | Runner, then: fail on Lua errors; start specs after VimEnter (which-key maps nothing before it) with a fake-`hg` helper; unset `CODING_AGENT_METADATA` (Sapling ignores `-q` under it, so specs saw a different blame format); a startup check. |
| S1, S7 | `9d707f2` | S7's spawn errors are handled in the same rework. |
| S2 | `706b86e` | |
| S3 | `c943240` | |
| S4 | `24407fb` | |
| S5 | `6c81abe` | |
| S6 | `d580f15` | The shell half was fixed by C2 (no more string `:term` commands). Checked against the real fbsource smartlog: 576 commit lines parse identically. |
| S8 | `e6843d0` | Refocus refresh skipped within 5 s of the last one. |
| S9 | `9d7e848` | Kept bare `s`/`gx` (routed through the same handlers as `<CR>s`/`<CR>gx`); dropped `<CR>c`. |
| C1 | `d247136` | |
| C2 | `6a4b0ba` | Also the `run_cmd_and_exit` part of C10. |
| C3 | `70372ba` | |
| C4 | `5be0f1c` | |
| C5 | `6f3162e`, corrected in `c27073e` | A relative destination resolves against the repo root (as before), and completion now matches. `6f3162e` first resolved it against nvim's cwd, assuming the cwd is a repo root; nvs sessions run in the workspace root (`~/checkoutN`). |
| C6 | `caf23af` | |
| C7 | `202245b` | Also stopped closing a blame window from running `:buf N` in whatever window became current. |
| C8 | `5774183` | Checked with a fake `jf`; not run against Phabricator. |
| C9 | `45de1a4` | |
| C10 | `2d78d36` (+ C2, C4–C7) | |
| B1 | `e957134` | meta.nvim's spec now lives only in `local/plugins/meta.lua`. |
| B2 | `8f1d00a` | |
| B3 | `6fc2d5a` | |
| B4 | `b0a7867` | |
| B5 | `aa7bcdc` | |
| B6 | `2edf827` | |
| B7 | `2caa95c` | Blame is reused while the file's mtime and the working-copy parent (read from `.hg/dirstate`) are unchanged. `CmdlineLeave` sign refreshes now only follow `:` commands containing `!`. |
| B8 | `6507fd7` | |
| B9 | `798c6d4` | |
| B10 | `55ac780` | |
| B11 | `4d81dab` | |
| D1 | `7a61996` | |
| D2 | `5a7242c` | |
| D3, D8 | `6457be7` | |
| D4 | `f088912` | |
| D5 | `3a93163` | |
| D6 | `1d1afe4` | |
| D7 | `cdd75d1` | |
| D9 | `2e493b5` | |
| D10 | `c5c0ebe` | Title bar shows the requested base and its hash, e.g. `.^ 1a2b3c4d5e6f`. |
| D11 | `e35411e` | `apply_pair` went with D9. |
| Consolidation | `45c8e57` | Extracted the shared render sequence (`diff-opts.show`). Kept the keymaps separate: the two look sessions up differently and diff-tab has its own keys. |
| G1, G3, G4, G6 | `c2631b9`, `02d3375`, `b6ffc2a`, `a736708` | G3's effect on an actual Maven run and G4's eden prefetch weren't run. |
| G5 | `e6202b0`, reverted in `7bb4f71` | **Wrong item.** Each nvs session runs in a workspace root (`~/checkoutN`, see `~/.config/nvs/sessions.*`), and the picker already offered exactly that workspace's fbsource and configerator. Scanning `~/checkout*/*` put every checkout's repos in it, and a sticky pick overrode the buffer's repo. `e017898` adds a spec for the intended behavior. |
| G2 | `7ebbd6e` | |
| G7 | `ccd85bc` | |
| G8 | — | **Not fixable as described:** meta.nvim's own `init.lua` requires `meta.null-ls` (`meta/init.lua:113`), whose `init.lua` requires `null-ls.logger`, so none-ls loads with meta.nvim whatever none-ls's own event says. The dependency stays (it orders none-ls first). |
| G9 | `e36ed10`, `e335ce2` | |

An independent review of the combined change found regressions and gaps,
fixed afterwards:

| Finding | Commit |
|---|---|
| Diff split keys replaced LazyVim's per-buffer treesitter `]f`/`[f`/`]F`/`[F` on live buffers, ran the built-in key outside panes, and deleted them on close | `888e4a0` |
| Inline blame never finished when switching files faster than one `hg blame` (B7 killed and restarted it) | `ff3ae21` |
| `:HgInlineComment` refused inside `:HgDiffSplit` (working-copy splits have no commit diff) | `491854c` |
| Stale smartlog status block kept once the working copy was clean | `fd8319b` |
| Panel left empty when `hg ssl` failed before `hg sl` finished (S8) | `9160a53` |
| Diff panes still left `nofoldenable` on files cycled out of them (D1 was incomplete) | `b21d3b6` |
| A sticky `:MetaRepo` pick (G5) sent Buck commands for a file to another checkout | `0ca7db4`, reverted in `0a8c468` once G5 was reverted (the buffer's repo wins again, so it changed nothing) |
| Runner passed unit specs that failed to load | `93bcb8d` |
| Two blocking `hg log` calls to pin revisions (~0.6 s in fbsource) | `5825388` |
| Rebase spawn error left the panel busy forever | `07482ed` |
| `:HgBlame` restored options onto whatever buffer the window showed | `939ccaa` |

Its suspicion that gutter signs and `:HgRead` mishandle CRLF files was wrong
(`vim.system`'s text mode converts CRLF to LF); `34192ea` adds the coverage.

A second review (Codex, `gpt-6.1-sol`) found more, each reproduced and
fixed with a spec that failed before:

| Finding | Commit |
|---|---|
| Retargeting the smartlog left the old repo's commits up and actionable while the new one loaded (S1 stopped refreshes from blocking actions): `<CR>u` on its `@` ran `hg uncommit` in the new repo | `5f96fb4` |
| `:HgPublishDrafts` used a diff split's diff from any window in its tab, while `:HgInlineComment` uses it only from the split's panes | `a60a68f` |
| Diff split keys outside a pane replayed their usual meaning after the rest of the typeahead (`gqq` ran `q`, then `gq`) | `7944340` |
| CRLF files without a final newline weren't recognized as CRLF in diff panes | `0668c1b` |
| `:HgLineBlameCopyDiff` blamed the file on disk by buffer line number for lines that exist only in the buffer (pre-existing; B6 fixed only the ghost text) | `e91f9e0` |
| `:HgBlame` options saved per window: lost if another buffer was showing when the blame closed, and swapped between two buffers blamed in one window | `6a3f7f7` |
| `presto-maven` built `~/checkout1` from every workspace (the pre-existing TODO G4 left alone); it now builds the file's own repo with that workspace's build root and Maven repository | `4e064bf` |

Declined: it suggested binding inline comments to the Phabricator version a
split "displays" rather than the latest. A split shows a local commit, not a
version, and the latest version is the one that matches the commit you
submitted last, which is why C8 dropped the per-session version cache. The
case it found, a split left open across a resubmit, shows a commit that is
no longer current; reopen the split.

Known limit of the runner: plugins come from the real `~/.local/share/nvim`,
so a startup that installs a missing Mason package or treesitter parser
would write there, as your editor would.

Still to check by hand: C8 against Phabricator, G3 with a real in-editor
Maven run, G4's eden prefetch, and a `presto-maven` build from a
`~/checkout2` file.

## Contents

- [Outcome](#outcome)
- [0. Test infrastructure (do first)](#0-test-infrastructure-do-first)
- [S. Smartlog panel](#s-smartlog-panel)
- [C. Hg commands](#c-hg-commands)
- [B. Signs, inline blame, HgChanges](#b-signs-inline-blame-hgchanges)
- [D. Diff viewers](#d-diff-viewers)
- [G. Meta tooling glue](#g-meta-tooling-glue)
- [Decisions](#decisions)
- [Execution plan](#execution-plan)
- [Checked and not a problem](#checked-and-not-a-problem)

---

## 0. Test infrastructure (do first)

**Problem.** Headless `nvim` with the real config saves a persistence.nvim
session for its cwd on exit. Review runs overwrote the saved sessions for
`~/dotfiles/nvim` and `~/checkout2/fbsource` (both unrecoverable). Separately,
`~/.config/nvim` is per-file symlinks into `~/dotfiles`, so a headless run
always tests the main working copy, never a branch or worktree.

**Fix.** Check in `tests/test-nvim-meta-hg.sh` plus `tests/nvim-meta-hg/`
(fixtures, a small Lua assertion library, one spec file per area), in the
style of `tests/test-nvim-show.sh`:

- `XDG_STATE_HOME` points at a temp dir, so sessions and shada are never
  written. This removes the hazard rather than relying on every test calling
  `require("persistence").stop()`.
- A temp `XDG_CONFIG_HOME/nvim` mirrors the live symlink layout, pointing
  into the checkout under test:
  `init.lua → <root>/nvim_init.lua`, `lua/* → <root>/nvim/lua/*`,
  `lua/config/meta.lua → <root>/nvim/local/config/meta.lua`,
  `lua/plugins/meta.lua → <root>/nvim/local/plugins/meta.lua`,
  `lua/config/local.lua` containing `require("config.meta")`.
  Plugins still load from the real `~/.local/share/nvim` (read-only).
- Throwaway repos and cwd under `mktemp -d`, removed on exit; every nvim run
  under `timeout`.
- Specs fire `User VeryLazy` themselves (headless never does), since that's
  what loads `lib.meta-hg`.
- Optional fake `hg` first on `PATH` that passes through to the real one but
  can delay or fail chosen subcommands, for the race and failure tests.
- Port the four existing scratch tests from `/tmp/hgdiff-harness`
  (`t_names`, `t_entry`, `t_fetch`, `t_rename`).

Every fix below lands with a spec that fails before and passes after.

---

## S. Smartlog panel

`lua/lib/meta-hg.lua`, `ssl_utils.*`, `SSL_COMMANDS`, `HgSsl`.

### S1 (T1) Overlapping refreshes overwrite each other, across repos too — repro

- **Where:** `ssl_utils.refresh_buffer` (`SSL_STATE.blocked = true` at 1024;
  cleared in each completion path through ~1069), `ssl_utils.run_cmd`
  (1097–1132), rebase (1771–1774), `refresh_ssl` export (3840).
- **Problem:** one boolean `SSL_STATE.blocked` guards every refresh and
  command. Results aren't tied to the request that produced them, and the
  first operation to finish clears the flag for all. A slow refresh started
  before a change (or for another checkout) can finish last and overwrite the
  panel. Repro: panel showed the pre-commit `@` line after a newer refresh had
  already drawn the new commit; after retargeting to repo2 the panel was named
  for repo2 but listed repo1's commits. Actions then run
  `hg checkout/hide/rebase <hash>` with repo2's cwd and repo1's hashes. Also:
  `r` during a mutation clears the flag and lets a second mutation start, and
  `refresh_ssl` after `:SlPull` is skipped whenever any refresh is in flight.
- **Fix:** give each refresh a generation number and the repo root it was
  started for; drop results whose stamp doesn't match the latest. Track
  mutating commands with a separate busy counter. Refreshes then no longer
  need to block mutations, which also removes the ~10 s "SSL is updating,
  please wait..." lockout after entering the panel (the window that commit
  `c80f66a` left for later). ~30 lines.

### S2 (T2) Status virtual lines pile up on every refresh — repro

- **Where:** `annotate_hg_status`, `nvim_buf_set_extmark(bufnr, SSL_NS, ...,
  { virt_lines = ... })` at 843.
- **Problem:** nothing ever clears `SSL_NS`, and `nvim_buf_set_lines` moves
  old marks to the end of the replaced range instead of deleting them. After
  three refreshes there were 3 status blocks (9 virtual lines); in fbsource
  that's ~50 stale virtual lines per panel entry, mostly at the bottom.
- **Fix:** `nvim_buf_clear_namespace(bufnr, SSL_NS, 0, -1)` before placing the
  new mark. 1 line.

### S3 (T2) `:bd` on the panel leaves it without keymaps — repro

- **Where:** `HgSsl` reuse path, 1899–1930.
- **Problem:** `:bdelete` (LazyVim `<leader>bd`) unloads the buffer but keeps
  it valid, wiping buffer-local options and mappings. `HgSsl` sees a valid
  buffer, takes the reuse path, and never re-applies them: the panel redraws
  but `<CR>*`, `j/k`, `f`, `r`, `c` do nothing, and with `buftype=''` a `:w`
  would write a file called `hgssl [...]` into cwd.
- **Fix:** treat `not nvim_buf_is_loaded(bufnr)` as invalid (wipe and
  recreate), or move option and keymap setup into a function that runs on
  every open. ~5 lines.

### S4 (T2) Rebase failures other than merge conflicts are silent — repro

- **Where:** rebase action, 1786–1820.
- **Problem:** the "Rebase failed unexpectedly" fallback and the
  working-copy-changes check are nested inside
  `if string.find(obj.stderr, "hit merge conflicts")`. Rebasing onto a bad
  revision, a public commit, or a cycle shows only "Running: hg rebase ...".
- **Fix:** move the fallback out of the `if`, or reuse `ssl_utils.run_cmd`
  and keep only the conflict-specific message. ~3 lines.

### S5 (T2) Current commit with only hidden bookmarks vanishes; status annotation crashes — repro

- **Where:** `filter_hidden_bookmarks` (877–895); `annotate_hg_status`
  (757–779).
- **Problem:** the filter drops any commit whose bookmarks are all hidden,
  including the current one. The `@` line disappears (no highlight, `c`/`C`
  do nothing). With a dirty working copy, `current_commit_line_idx` stays 0
  and `sl_lines[0]:find("@")` throws. Real trigger: `sl goto fbsource/stable`
  plus uncommitted changes.
- **Fix:** never filter `commit.is_current`; return early from the annotation
  when the index is 0. 2 lines.

### S6 (T2, low) Commit-line parser matches inside messages; text reaches a shell — repro

- **Where:** `ssl_utils.parse_diff_line` (`commit_marker = "[@ox]  "`,
  636–647); callers that build shell strings for `run_cmd_and_exit`
  (1529–1531, 1611, 1647).
- **Problem:** the pattern isn't anchored, so a message line like
  `second  fox  commit` parses as a commit with hash `second`. Combined with
  string-built `:term` commands, a crafted title ran a shell command via `s`.
  Your real smartlog had no false positives, so this needs an odd title, but
  one false positive sends arbitrary text to checkout/hide/`jf submit`.
- **Fix:** anchor to the graph prefix, e.g. `^[│╷├─╯╭╮ ]*[@ox]  (%x+)`, and
  pass list commands (see C10). ~3 lines plus C10.

### S7 (T2, low) Errors before `blocked` is cleared leave the panel stuck — reasoning

- **Where:** `run_cmd` (1097–1099), `refresh_buffer` failure path
  (1032–1033).
- **Problem:** `vim.system` throws synchronously on ENOENT (missing binary,
  deleted cwd) after `blocked = true`; the `hg sl` failure path calls
  `vim.notify` from the luv callback before clearing the flag (safe today only
  because noice reschedules). `r` recovers.
- **Fix:** `pcall` the spawn and clear state first in every failure branch.
  Mostly subsumed by S1.

### S8 (T3) Refresh runs three commands one after another — measured

- **Where:** `refresh_buffer` (1027–1069); BufEnter refresh autocmd
  (2055–2066).
- **Problem:** `hg ssl` starts only after `hg sl` finishes, and `hg status`
  after that: ~11 s per refresh in checkout1 (`sl` 1.1 s, `ssl` 9.9 s,
  `status` 0.1 s). Every panel BufEnter starts one, and each `hg ssl` also
  queries Phabricator.
- **Fix:** run `ssl` and `status` concurrently with `sl`. After S1, skip the
  BufEnter refresh if the last one finished within a few seconds.

### S9 (T3) Cleanup

- `ssl_utils.get_ssl_file` (621) is dead.
- `<CR>c` (2027) is `<CR>w` without its guard; bare `s` and `gx` (2118, 2127)
  duplicate `<CR>s`/`<CR>gx` and skip `ssl_action`'s logging. Keep one of
  each.
- Rebase re-implements `run_cmd` by hand; cancelling the rebase prompt
  (1852) triggers a needless ~10 s refresh.
- `submit` (1682) uses `commit.diff and commit.diff or commit.hash`; every
  other submit uses the hash. On an obsolete `x` line, or a local bookmark
  parsed as `diff`, that can name a different commit. Use the hash.

### Ideas (not scheduled)

- After a refresh, put the cursor back on the same hash rather than the same
  row number.
- Resolve message and connector lines to the commit above, so `<CR>*` works
  anywhere in a commit block.
- Show `jf submit`'s output (D-number/URL) on success.

---

## C. Hg commands

`lua/lib/meta-hg.lua`.

### C1 (T1) `:HgCommit` commits the `HG:` template lines, and never aborts — repro

- **Where:** `commit_with_message`, 2140–2180. Also used by the `HgChanges`
  commit action (3028).
  ```lua
  vim.fn.system("hg --cwd ... debugcommitmessage >> " .. commit_filepath)
  ...
  if not vim.startswith(line, "HG:") then table.insert(lines, line) end
  ...
  local cmd = { "hg", "commit", "--logfile", commit_filepath }
  ```
- **Problem:** `--logfile` keeps `HG:` lines (only hg's own editor strips
  them), and the commit reads the file, not the filtered lines. The abort
  check only ignores `HG:` lines, but the template's other lines
  (`<Replace this line...>`, `Summary:`, ...) are never blank, so `:q!`
  without editing committed the bare template.
- **Fix:** on close, take the buffer lines, drop `HG:` lines, abort if what's
  left is blank or identical to the untouched template, and write the
  filtered lines to the logfile (or pass `-m`). ~10 lines.

### C2 (T1) Terminal commands close whatever window you're in when they finish — repro

- **Where:** `hg_utils.run_cmd_and_exit`, TermClose handler 320–330.
  ```lua
  callback = function()
    vim.cmd.close()
  ```
- **Problem:** closes the *current* window, not the terminal's. Switch tabs
  while `:HgAbsorb`, `:HgAmend`, `:HgCommitInteractive`, `:HgSuggest` or a
  smartlog action is running and your window is closed when it ends. It also
  ignores the exit status, so a failing command's output disappears
  instantly.
- **Fix:** close the terminal's own windows (`vim.fn.win_findbuf(term_buf)`);
  only auto-close on status 0, otherwise leave it open. ~8 lines.

### C3 (T2) `:HgStatus` keymaps `X` and `?` are broken; reopening errors — repro

- **Where:** `HgStatus`: `X = "reset"` (2735), `table.join` (2763),
  `nvim_buf_set_name(buf, "hg status")` (2723).
- **Problem:** in Sapling `hg reset [REV]` moves the working-copy parent, so
  `X` errors (`unknown revision 'sub/s.txt'`), or silently moves the commit
  if the path happens to resolve as a revision. `table.join` doesn't exist,
  so `?` throws. A second `:HgStatus` hits `E95` on the buffer name. Minor:
  `stdout = "No changes"` is computed then ignored; an error says "git
  status".
- **Fix:** `X` → `hg revert` with confirmation; `table.concat`; reuse or
  rename the existing buffer. ~5 lines.

### C4 (T2) `:HgRemove` closes the buffer even when the remove failed — repro

- **Where:** `HgRemove` (470–476), `HgResolve` (478–489).
- **Problem:** `silent !hg remove` then an unconditional
  `nvim_buf_delete(0, { force = true })`. With unsaved edits, `autowrite`
  makes `:!` write first, so the file is modified and `hg remove` refuses;
  the buffer still closes and nothing is reported. `HgResolve` likewise
  closes the buffer, and Sapling's `resolve --mark` accepts a file that still
  has `<<<<<<<` markers.
- **Fix:** run via `vim.system(list, { cwd })`; delete the buffer only on
  success, and show stderr on failure. `HgResolve` asks for confirmation if
  the buffer still contains conflict markers. ~10 lines.

### C5 (T2) `:HgMove` resolves the destination against the repo root — repro

- **Where:** `HgMove`, 456–463 (`hg --cwd <repo root> move <abs src> <args>`).
- **Problem:** `:HgMove moved.txt` from `repoA/sub/s.txt` lands at
  `repoA/moved.txt`, while `complete = "file"` completes relative to nvim's
  cwd. The buffer keeps the old (now deleted) name, so the next `:w`
  recreates it. `silent !` hides errors.
- **Fix:** resolve the destination against the current file's directory, run
  via `vim.system`, then rename the buffer and `:edit!` on success. ~8 lines.

### C6 (T2) `:HgRead` breaks when nvim's cwd is another checkout — repro

- **Where:** `setup`, `"%! hg cat %"` (3910–3914).
- **Problem:** runs hg in nvim's cwd; with cwd in repo B and the buffer in
  repo A, the buffer is replaced by Sapling's "not under root" error text
  (undoable).
- **Fix:** Lua callback: `vim.system({ "hg", "cat", abs }, { cwd =
  buf_repo_root() })`, replace lines only on success. ~8 lines.

### C7 (T2) `:HgBlame` twice loses your window options — repro

- **Where:** `HgBlame`, 369–376 (`BUF_OPTS_DICT[buf_nr] = preblame_opts`).
- **Problem:** the second call snapshots the already-changed options
  (`scrollbind`, `nowrap`, `nofoldenable`) over the originals, so closing both
  blame windows leaves them changed.
- **Fix:** snapshot only when `BUF_OPTS_DICT[buf_nr] == nil` (or close the
  existing blame window first). 2 lines.

### C8 (T2) Inline comments: stale diff version; drafts published on the wrong diff — reasoning

- **Where:** `version_id_cache` (2340, read at 2383–2386); `HgPublishDrafts`
  (2559–2583); `resolve_inline_comment_context` (2434).
- **Problem:** the version cache is never invalidated, so after `jf submit`
  in the same session, `:HgInlineComment` attaches drafts to the old version
  (line numbers refer to old content). `HgPublishDrafts` always uses `.`'s
  diff, but drafts made from a diff split of a lower stack commit go to that
  commit's diff, so they're never published.
- **Fix:** drop the cache (or a short TTL); have `HgPublishDrafts` prefer the
  current tab's diff session `commit.diff`, like
  `resolve_inline_comment_context`. ~6 lines. Not testable without
  Phabricator; verify by hand.

### C9 (T2) `:HgBrowse` crashes on its own error paths — repro

- **Where:** `makeHgBrowse`: `vim.log.level.ERROR` at 501, 505, 516;
  clipboard check at 537.
- **Problem:** `vim.log.level` is nil (should be `levels`), so every error
  path throws instead of notifying. `vim.g.clipboard or vim.opt.clipboard` is
  always truthy (`vim.opt.*` is an object), so the `'"'` register fallback is
  dead.
- **Fix:** three one-word edits; `vim.o.clipboard ~= ""`.

### C10 (T3) Replace shell-string `:!`/`:term` calls; deprecated APIs

- **Where:** `HgBlame` (359), `HgPrev`/`HgNext` (437–448),
  `HgMove`/`HgRemove`/`HgResolve`/`HgWrite` (456–610), `HgHistory` (566),
  `run_cmd_and_exit` (312: `vim.cmd("term " .. cmd)`).
- **Problem:** `:!` and `:term` expand `%`, `#`, `!` inside arguments
  (`shellescape` without `special`), `silent !` hides failures, and `:!`
  triggers autowrite as a side effect. This is what makes C4–C6 and S6
  possible.
- **Fix:** `vim.system(list, { cwd })` for one-shot commands;
  `vim.fn.jobstart(list, { term = true, cwd })` for interactive ones.
  Replace deprecated `nvim_buf_get_number` (242, 342, 375) with
  `nvim_get_current_buf`, and `nvim_buf_add_highlight` (585, 2711) with
  `vim.hl.range` or extmarks. ~40 lines touched.

---

## B. Signs, inline blame, HgChanges

`lua/lib/meta-hg.lua` (`HgChanges`, `setup_hg_signcolumn`,
`setup_line_blame`), plus the plugin specs for B1.

### B1 (T1) meta.nvim's own `meta.hg` runs alongside `lib.meta-hg` — repro

- **Where:** `lua/plugins/meta-lsp.lua:2-9` loads meta.nvim at startup and
  its `config` calls `require("meta")`;
  `/usr/share/fb-editor-support/nvim/lua/meta/init.lua:15` then runs
  `require("meta.hg").setup()` (the stock module). The alias
  `package.loaded["meta.hg"] = m` in `local/config/meta.lua:52` runs at
  `VeryLazy`, too late. `meta.setup()` also runs twice (meta-lsp `config`,
  and `local/config/meta.lua:56-59`), and `local/plugins/meta.lua:7` declares
  the meta.nvim spec a second time.
- **Problem:** both modules register sign autocmds (BufEnter, BufWritePost,
  FileChangedShellPost, CmdlineLeave) on the same `"hg_signcolumn"`
  namespace. The stock one runs `hg status <file>` without `cwd`, which fails
  in other checkouts and clears the shared namespace. Repro: signs blinked
  `0,0,3,3…` with cwd outside the repo, settled at 2 instead of 3 inside it,
  and were a steady 3 with the stock autocmds removed. Every event spawns hg
  twice.
- **Fix:** in the meta.nvim spec, set
  `package.preload["meta.hg"] = function() return require("lib.meta-hg") end`
  before `require("meta")`; remove the second `meta.setup()` and the
  duplicate spec. `lib.meta-hg.setup` merges `CONFIG` before its `_is_setup`
  guard, so the VeryLazy `setup({ ssl = { status = true } })` still applies.
  ~5 lines. Do this first in this area: the sign tests aren't meaningful
  until only one implementation runs.

### B2 (T1) `HgChanges` discard reloads buffers from the wrong checkout — repro

- **Where:** `X` action, 3080–3087.
  ```lua
  local abs = vim.fn.fnamemodify(path, ":p")
  local b = vim.fn.bufnr(abs)
  ... vim.cmd("edit!")
  ```
- **Problem:** `path` is repo-relative but `:p` resolves it against nvim's
  cwd. Discarding `fbcode/x.cpp` in checkout B with cwd in checkout A runs
  `edit!` on A's buffer, throwing away its unsaved edits. Repro: unsaved text
  in an unrelated repo's buffer was lost. `bufnr()` also prefix-matches.
- **Fix:** `vim.fs.joinpath(repo_root, path)` and an exact-name lookup over
  `nvim_list_bufs`. ~3 lines.

### B3 (T2) Gutter signs wrong at end of file and for pure deletions — repro

- **Where:** `vim.diff(old_content, table.concat(current_lines, "\n"), ...)`
  (3387–3391); `apply_signs` (3277–3295) with its "skip last hunk" special
  case; `]h`/`[h`/`]H`, which assume a trailing fake hunk.
- **Problem:** `hg cat` output ends in `\n`, the joined buffer doesn't, so
  almost every file gets a fake last-line hunk; the special case hides it
  along with any real change on the last line. For pure deletions `vim.diff`
  reports the line *after which* lines were removed, so `new_file_line - 2`
  is one too high. Repro on a 20-line file: modify last line → no sign;
  delete line 10 → mark on 8; delete line 1 → none; delete last line → marks
  on 18 and 19. `b:hg_diff` counts are off by one (read by `meta.statusline`).
  Appending at EOF leaves hunk navigation dead.
- **Fix:** append `"\n"` to the buffer text when `'eol'`/`'fixeol'` apply;
  put deletion marks at `start_b - 1` (row 0 when `start_b == 0`); delete the
  last-hunk special cases in signs and navigation. ~15 lines.

### B4 (T2) Async blame writes to one buffer using another's cursor — repro

- **Where:** `set_line_blame` (3548–3567), `handle_cursor_moved`
  (3606–3619), blame callback (3695–3714); `nvim_buf_get_extmarks(arg.buf,
  ...)` at 3699 has no validity guard.
- **Problem:** `set_line_blame(bufnr)` reads the cursor from window 0 and the
  cache key from `expand("%")`, then places the mark in `bufnr`. The 300 ms
  debounce and the async `hg blame` (0.8–5.9 s in fbsource) can fire after a
  buffer switch. Repro: "Invalid 'line': out of range" errors, and `f.txt`
  showing `small.txt`'s blame on the wrong line. The cache is keyed by
  cwd-relative path, so two checkouts with different `:lcd` collide, and
  `HgLineBlameCopyDiff` can copy the wrong diff. Deleting a buffer mid-blame
  (e.g. via `HgRemove`) raises "Invalid buffer id".
- **Fix:** key the cache by absolute path; in `set_line_blame` use a window
  that shows `bufnr` (or skip unless it's current); guard
  `nvim_buf_is_valid`. ~15 lines.

### B5 (T2) `HgLineBlameToggle` off doesn't stop blame — repro

- **Where:** `handle_cursor_moved` (3606–3616); toggle command (3720–3726).
- **Problem:** the CursorMoved handler never checks
  `CONFIG.line_blame.enable`; toggle only clears the current buffer's marks.
  After toggling off, moving the cursor shows blame again.
- **Fix:** early return when disabled; on re-enable, draw for the current
  buffer. ~3 lines.

### B6 (T2) Blame is wrong after unsaved edits — repro

- **Where:** cache lookup by line number (3556); cache fill (3674).
- **Problem:** the cache is indexed by line numbers in the file on disk and
  only refreshed on BufEnter or write. Inserting 3 lines at the top made line
  5 show line 8's blame. The post-insert-mode re-show and
  `HgLineBlameCopyDiff` are equally off.
- **Fix:** while the buffer is modified, map buffer lines to disk lines with
  `vim.diff(disk_lines, buf_lines, { result_type = "indices" })` and show
  nothing for new lines. ~25 lines. (Decision: remap rather than hide.)

### B7 (T2) Blame re-runs on every BufEnter with no reuse or cancellation — measured

- **Where:** blame autocmds (3570–3715); sign refresh on `CmdlineLeave`
  (3439), which fires on every `:` command and `/` search.
- **Problem:** every buffer or window enter spawns `hg blame -r wdir()
  --phabdiff` plus `hg status` and `hg cat`; on `Task.cpp` (4.4k lines) blame
  took 5.9 s cold, 0.8 s warm. Overlapping blames can finish out of order and
  overwrite newer results. This is also the window that makes B4 easy to hit.
- **Fix:** skip the BufEnter blame when the cached entry matches the file's
  mtime and the `.` hash; keep one job per buffer and `:kill()` superseded
  ones (or drop stale results via a generation counter). Drop `CmdlineLeave`
  if nothing needs it. ~20 lines.

### B8 (T2) Sign results can apply out of order or to newer text — reasoning

- **Where:** sign refresh, 3334–3427 (`current_lines` captured at 3335 before
  ~0.3 s of async hg work).
- **Problem:** no generation or changedtick check, so a slower older run can
  overwrite a newer one, or diff against text that's since changed.
- **Fix:** capture `nvim_buf_get_changedtick(bufnr)`; skip `apply_signs` and
  the cache write if it changed. ~4 lines.

### B9 (T2) `HgChanges` discard leaves added files on disk — repro

- **Where:** `X` action: tracked/untracked split (3052–3058), `hg revert`
  (3093–3095).
- **Problem:** `hg revert` un-adds an `A` file but leaves it on disk, so the
  picker says "Discarded 2 file(s)" and they reappear as `?`.
- **Fix:** after a successful revert, `rm -f` the paths whose status was `A`
  (for a rename destination this is right: revert already restored the
  source). ~3 lines.

### B10 (T2) `HgChanges` `d` with a stale list errors and leaves empty panes — repro

- **Where:** `d` action → `open_diff_split` (3157).
- **Problem:** `changed_files` can return `{}` (file reverted outside nvim);
  `open_diff_split` indexes `pairs[1]` and throws, leaving two empty windows.
- **Fix:** guard `#opts.files == 0` inside `open_diff_split` (covers every
  caller). 3 lines. Pairs with D5.

### B11 (T3) Cleanup

- `hg_diff_snacks_picker`, `hg_diff_current_commit_snacks_picker`
  (3770–3832) and the `CONFIG.picker == "snacks"` branches in `setup` can't
  run (`picker` defaults to `"telescope"` and isn't overridden). Delete
  (~70 lines).
- `nvim_buf_add_highlight` at 2835 → extmarks (see C10).

### Ideas (not scheduled)

- Signs for a copied/renamed-then-edited file (status `A` with a source)
  currently show nothing; diff against the source instead.

---

## D. Diff viewers

`lua/lib/diff-session.lua`, `lua/lib/diff-tab.lua` (CodeCompanion diff),
`lua/lib/diff-opts.lua`, the split code in `lua/lib/meta-hg.lua`
(~1150–1520), `:HgDiffSplit` in `local/config/meta.lua`. Several of these
are in code written on 2026-10-24.

### D1 (T1) Diff panes turn folding off globally — repro

- **Where:** `diff-opts.apply` (`diff-opts.lua:3-7`); same block in
  `diff-tab.lua:139-143`.
  ```lua
  vim.wo[win].foldenable = false
  vim.wo[win].relativenumber = true
  ```
- **Problem:** `vim.wo[win].x = v` sets the window's global value as well as
  the local one, so every window created afterwards inherits
  `foldenable=false` and `relativenumber`. Confirmed: after
  `vim.wo[w].foldenable = false`, a `:vsplit` + `:enew` window had
  `fen=false` and `vim.go.foldenable == false`. `:diffoff` never restores
  `foldenable`, so `:HgDiffSplit` leaves it off on your own window. The
  `WinNew` stripper in `lua/config/autocmds.lua` (~87–120) only resets
  scrollbind/cursorbind/diff.
- **Fix:** `vim.wo[win][0].x = v` (local scope) in both places. 2 lines.
  The `:HgDiffSplit` part goes away with D9.

### D2 (T1) Diffing an open file turns its swapfile off for good — repro

- **Where:** `diff_split_load_pair`, 1302–1304.
  ```lua
  new_buf = vim.fn.bufadd(real_path)
  vim.api.nvim_set_option_value("swapfile", false, { buf = new_buf })
  vim.fn.bufload(new_buf)
  ```
- **Problem:** when the file is already loaded (the usual case), this
  deletes its swapfile and leaves swap protection off for the buffer's
  lifetime.
- **Fix:** only set it when the buffer wasn't loaded before `bufload`.
  1 line.

### D3 (T2) Sessions sharing a live buffer hijack each other's keymaps — repro

- **Where:** `set_keymaps` (`diff-session.lua:41-63`), which captures the
  session in each closure; `cleanup` (`:160-170`), which deletes the maps
  from live buffers.
- **Problem:** the live pane is your real file buffer, so two sessions
  showing the same file share it. The last session to register owns
  `]f`/`[f`/`gf`/`gq`: `gq` in tab 1 closed tab 2's session, whose cleanup
  then removed the maps, leaving tab 1 with none. The maps also apply in your
  ordinary editing window on that file while any session is open.
- **Fix:** look the session up at key-press time from the current tab
  (`M.sessions[tab]`), and fall back to the key's normal behaviour when the
  current window isn't one of that session's panes. Only delete live-buffer
  maps when no other session references the buffer. ~20 lines.

### D4 (T2) CRLF files show every line changed against the working copy — repro

- **Where:** `split_file_lines`, 1191–1197.
- **Problem:** the old side keeps each line's `\r`, while the live buffer is
  loaded with `fileformat=dos`, which strips it. Changing one line of a CRLF
  file highlighted all lines.
- **Fix:** if every line ends in `\r`, strip it and set `fileformat=dos` on
  the scratch buffer. ~6 lines.

### D5 (T2) A failed `hg cat` looks like an added file; `]f` can get stuck — repro

- **Where:** `diff_fetch` failure path (contents stay `nil`, ~1224–1241);
  `old_lines or {}` in `diff_split_load_pair` (1299–1325); `diff_await`'s
  `vim.wait` return value ignored (1274); `show_pair` returns before updating
  `session.index` (`diff-session.lua:83-89`), and `cycle` computes from it.
- **Problem:** "fetch failed" and "file absent at this revision" both render
  as an empty pane, silently, and the empty buffer is cached for the session.
  A 60 s `diff_await` timeout blocks the UI then does the same. When both
  sides fail, `show_pair` never moves the index, so `]f` keeps landing on the
  previous file; if the first file fails, the split doesn't open.
- **Fix:** store a distinct error state in the cache; render a placeholder
  line such as `<failed to read foo.ts at abc123: ...>`; let `show_pair`
  advance the index and display it; check `vim.wait`'s result. ~25 lines.

### D6 (T2) The CodeCompanion diff leaves keymaps on real files — repro

- **Where:** `BufWinEnter` autocmd in `diff-tab.lua:348-356`;
  `remove_keymaps_from_buf` (`:264`) is never called.
- **Problem:** any real file opened inside the diff tab (`:e`, picker, LSP
  jump) gets buffer-local `?`, `gq`, `gf`, `gm`, `]f`, `[f`, `]F`, `[F` that
  outlive the session: `?` (search backward) opens the diff help and `gq`
  (format) does nothing.
- **Fix:** drop the autocmd (scratch buffers already get maps in
  `add_file`), or only map buffers listed in `state.file_data`. ~5 lines.

### D7 (T2) Reopening a diff in a tab splits from the old session's origin — repro

- **Where:** `open_diff_split`, 1334–1340.
- **Problem:** the old session's `on_close` focuses its own return window
  before the new one is captured, so the new split opens beside (and returns
  to) the old origin instead of where you invoked it.
- **Fix:** capture the current window first; if it's one of the old
  session's panes, use that session's return window instead. ~5 lines.

### D8 (T2) Keymaps re-set on every pair on each lazy load — measured

- **Where:** `diff_session.register(...)` at the end of
  `diff_split_load_pair` (1329) calls `set_keymaps` over all loaded pairs.
- **Problem:** quadratic: 87,108 `keymap.set` calls cycling 120 files;
  `register` costs 3 ms at 120 pairs, 33 ms at 1000.
- **Fix:** set maps only on the two buffers just created. ~5 lines (folds
  into D3's rework).

### D9 (T2) Fold `:HgDiffSplit` into the shared diff split — repro (leaks)

- **Where:** `local/config/meta.lua:134-180`.
- **Problem:** separate implementation: writes `hg cat -r .^` to a temp file
  and runs `diffsplit`. The temp buffer has no filetype, is modifiable, and
  stays listed after closing; it applies D1's leaky options to your own
  window.
- **Fix:** `open_diff_split({ files = { { file = rel } }, target = { hash =
  ".", is_current = true }, parent_rev = ".^", repo_root })` (~10 lines),
  delete the rest and `diff-opts.apply_pair`. UX change: opens two panes
  beside your window instead of turning your window into the live side
  (approved). Keep the `.^` base (see Decisions) and rename both commands'
  descriptions to name their base.

### D10 (T2) Working-copy cache is keyed by the literal `"."` — reasoning

- **Where:** `open_working_copy_diff_split`, HgChanges `d`, D9:
  `parent_rev = "."`; the cache in `diff_rev_cache` is keyed by that string.
- **Problem:** after an amend or commit with the session open, files not yet
  visited are fetched from the new `.` while visited ones show the old one.
- **Fix:** resolve `.`/`.^` to a hash when the session opens; fetch by hash,
  keep the friendly label in the winbar. ~5 lines.

### D11 (T3) Cleanup

- The `TabClosed` cleanup in `meta-hg.lua` (1493–1511) is redundant: the
  `WinClosed` handlers already run `on_close` when a tab closes, and this
  path never sets `closed`. Delete (~18 lines).
- `diff-session.close`'s default `tabclose` branch (`:186-193`) is unused
  (the only consumer always sets `on_close`).
- `diff-opts.apply_pair` becomes unused after D9.

### Consolidation (decide during implementation)

diff-session and diff-tab carry near-identical render code (diffoff → set
buffers → diffthis → options → winbar → top) and the same `]f`/`[f`/`gf`/`gq`
keymap handling; D1, D3 and D6 exist because each copy manages buffer-owned
keymaps and window options on its own. If fixing those leaves the same logic
duplicated in both, extract one `render_pair` and one keymap attach/detach
helper with buffer ownership (~0.5 day, ~150 lines out of diff-tab). Fully
merging the two session models isn't worth it: diff-tab's mutable file list,
turn/session modes and live refresh are a different lifecycle.

### Ideas (not scheduled)

- Land on the first hunk (`gg]c` when line 1 isn't a change) and remember
  each pair's cursor when revisiting.

---

## G. Meta tooling glue

`local/config/meta.lua`, `lua/lib/*`, `lua/plugins/*`.

### G1 (T1) Buck test/build/run crash on `-library`-only files — repro

- **Where:** `lua/lib/meta-buck.lua:55`.
  ```lua
  table.insert(bases, target:gsub("%-library$", ""))
  ```
- **Problem:** `gsub` returns two values, so this is
  `table.insert(t, str, 1)` → "bad argument #2 to 'insert'". Any file whose
  owners all end in `-library` (e.g. a `python_unittest` source) breaks
  `<leader>Bt`, `<leader>Bb`, `<leader>Br` and `:BuckRun`.
- **Fix:** `table.insert(bases, (target:gsub("%-library$", "")))`. 1 line.

### G2 (T2) `buck.run_target` wrapper is unreachable and adds nothing — repro

- **Where:** `local/config/meta.lua:94-132`.
- **Problem:** meta.nvim binds `<leader>Br` to the original function by value
  during `buck.setup()`, before the wrapper is installed, so the keymap never
  reaches it. From `:BuckRun` it only adds blocking `buck2 uquery` calls (4
  instead of 2): `lib.meta-buck` already adds the base target, so the wrapper
  always falls through to the original.
- **Fix:** delete it (~40 lines).

### G3 (T2) `java.lua` sets `JAVA_HOME=21` for all of nvim — repro (env), reasoning (Maven)

- **Where:** `lua/plugins/java.lua:123` (`vim.env.JAVA_HOME = JAVA_21`).
- **Problem:** once nvim-jdtls loads, every child process inherits JDK 21,
  including in-editor Maven runs (`<leader>mi`/`mt`/`mf` via
  `presto-maven`), while your shell (`~/.localrc:10`) uses 17. Repro: a child
  `sh -c 'echo $JAVA_HOME'` printed `.../impl/21`.
- **Fix:** pass `cmd_env = { JAVA_HOME = JAVA_21 }` in the jdtls config
  instead of mutating `vim.env`. 2 lines.

### G4 (T2) `presto-maven` prefetches before it `cd`s — reasoning

- **Where:** `lua/lib/presto-maven.lua:73-74`.
- **Problem:** `eden prefetch` defaults to the repo of the cwd. From
  `~/checkoutN` (not an eden mount) it fails silently; from
  `~/checkout2/fbsource` it prefetches checkout2 while building checkout1.
- **Fix:** move the `cd` first (or pass `--repo`). 1 line. Separate from the
  existing TODO about always building checkout1.

### G5 (T2) — withdrawn: based on a misreading of the workspace setup; see Outcome

#### Original finding (G5) `:MetaRepo`'s choice is overridden; no candidates from `~` — repro (first part)

- **Where:** `repo-context.current` (`lua/lib/repo-context.lua:94-99`);
  `candidate_parent`/`candidates` (`:128-153`).
- **Problem:** `current()` always prefers the focused buffer's repo, so
  after picking configerator the notify says "Active repository:
  configerator" but `<leader>p` and biggrep keep using fbsource.
  `candidates()` only scans the cwd repo's parent; from `~` it returns
  nothing, since `~/fbsource`/`~/configerator` are symlinks (`vim.fs.dir`
  reports `"link"`) and `~/checkoutN/*` isn't scanned.
- **Fix:** let an explicit pick win until you enter a buffer in a different
  repo (or drop the "for this tab" claim); scan `~/checkout*/*` and follow
  symlinks to directories. ~15 lines.

### G6 (T2) `:PyreflyCheckToggle` doesn't stop a running check — reasoning

- **Where:** `lua/lib/pyrefly-check.lua`: completion callback sets
  diagnostics (142) and re-runs pending checks (146–150) without checking
  `M.enabled`; toggle (159–169).
- **Problem:** toggling off during a 1–8 s check clears the namespace, then
  the callback sets the diagnostics again and any queued re-check runs.
- **Fix:** in the callback and the pending re-run, return early unless
  `M.enabled` (or the check was requested explicitly). 2 lines.

### G7 (T2) Without meta.nvim, hg keymaps and `:SlPull` still register and fail — repro

- **Where:** `local/config/meta.lua:194-228`.
- **Problem:** `<leader>hb`/`hB`/`ho`/`hh`/`hs`/`hS`/`hu`/`hc`/`hC` are mapped
  whether or not the commands exist (`E492`); `:HgDiffSplitWorkingSet` does
  nothing; `:SlPull` calls `require("lib.meta-hg").refresh_ssl()` unguarded.
- **Fix:** register them inside `if meta_hg then`. ~3 lines.

### G8 (T3) `none-ls` dependency makes meta.nvim load at startup — measured

- **Where:** `lua/plugins/meta-lsp.lua:7`
  (`dependencies = { "nvimtools/none-ls.nvim" }`), defeating none-ls's
  `event = "LazyFile"` in `local/plugins/meta.lua:6`.
- **Problem:** ~11.5 ms at startup (`null-ls` 7.4 ms, `plenary.async`
  4.1 ms). The rest of the Meta layer costs ~4 ms at startup and ~3 ms at
  VeryLazy, which is fine.
- **Fix:** drop the dependency or lazy-load meta.nvim. Check B1's preload
  still runs first.

### G9 (T3) Cleanup

- `vim.fn.termopen` (deprecated in 0.12) at `lua/lib/presto-maven.lua:57` →
  `vim.fn.jobstart(cmd, { term = true, ... })`.
- The global nvim-lspconfig format filter in `lua/plugins/meta-lsp.lua` is
  redundant with `lib/meta-format`; confirm and remove.

---

## Decisions

- **`<leader>hd` vs `<leader>hD` keep different bases.** `hd` (current file,
  vs `.^`) shows the current commit plus uncommitted edits; `hD` (all
  uncommitted files, vs `.`) shows uncommitted edits only. Both are useful.
  Make the base visible in the winbar (D9) and in the descriptions. No new
  combined command.
- **`:HgDiffSplit` folds into the shared split** and opens two panes beside
  your window (D9).
- **`:HgStatus` `X`** becomes `hg revert` with a confirmation (C3).
- **`:HgResolve`** asks for confirmation if conflict markers remain (C4).
- **Inline blame with unsaved edits** remaps lines through a diff rather than
  hiding (B6).
- **Terminal commands** stay open when they fail (C2).

## Execution plan

1. **Phase A (serial):** section 0, the hermetic suite, with the four
   existing tests ported. One commit.
2. **Phase B (parallel):** five fixer agents, one per area (S, C, B, D, G),
   each in its own git worktree under `/tmp/dotfiles-wt/` (pending your
   approval). One commit per item, each with a spec that fails before and
   passes after, run through the suite against that worktree. Order within
   areas: B1 before the other B items; D3/D8 together; C10 alongside C4–C6;
   B10's guard lands in D (inside `open_diff_split`).
3. **Phase C (serial):** apply the areas onto `master` one at a time,
   resolve conflicts, run the full suite after each; a fresh review of the
   combined diff; report back. Changes reach your editor on the next nvim
   start; running sessions are never touched.

Items needing a manual check because they can't be tested headlessly: C8
(Phabricator), G3's effect on Maven, G4 (eden).

## Checked and not a problem

- `debounce` fix (`270b5a5`) and duplicate line-blame handler fix (`257949f`)
  are complete for what they target.
- Merge commits: `hash^` and `status --change` both use p1, so the diff split
  agrees with itself. Root commits show files as added, which is correct.
- Duplicate fetches in the diff prefetch are harmless; closing a tab
  mid-prefetch stops fetching.
- `hg log -l 1` starts at `.` in Sapling, so browse and
  `get_current_diff_number` aren't tip bugs.
- `HgChanges` `X`/`a` already pass paths with spaces as separate arguments;
  `d` correctly ignores `base_revision`.
- Async callbacks in myles, pyrefly, meta-buck and `SlPull` schedule
  correctly; `checkout.lua` and the jdtls workspace logic are fine.
- Blocking calls in hg commands are fast enough in checkout1 (`status`
  0.1 s, `debugcommitmessage` 0.3 s) not to matter.
- Out of reach: `HgHunkRevert` delegates to meta.nvim
  (`meta/util/hg.lua:144`), which runs `sl diff` in nvim's cwd and reports "No
  hunk under cursor" from another checkout. Not our code.
