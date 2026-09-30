# Meta Workspace

Rules for working inside a Meta workspace root — a directory holding `fbsource/`
and `configerator/` side by side. `sync.sh` symlinks this file to
`<workspace>/CLAUDE.md` and `<workspace>/AGENTS.md`, so it is present only on
machines that actually have a checkout and absent everywhere else.

- Each `~/checkoutN/` is a workspace root containing `fbsource/` and `configerator/` side by side (`~/checkout1/{fbsource,configerator}`, `~/checkout2/{fbsource,configerator}`, etc.). Editor and agent sessions normally start at the workspace root, not inside either repository.
- Derive sibling repository paths from the current workspace root. NEVER hardcode a specific `~/checkoutN`, and never assume a bare `~/configerator` or `~/fbsource`.
- Treat `~/checkoutN` as a workspace container, not a source-control or build root. For repository-specific commands such as `sl`, `jf`, `arc`, `buck`, and unscoped file discovery, explicitly use the repository implied by the task or current file by changing that command's working directory or passing a repository path. Do not change the session's global working directory merely to run a command.
- Confirm the process working directory before accessing checkout-specific files. If it identifies a workspace but not an active repository, use the task and current file to select `fbsource` or `configerator`; ask if the choice is materially ambiguous. If it does not identify a checkout, recover the editor/session workspace or ask rather than guessing.
- Before repository-specific work, apply that repository's own `AGENTS.md` instructions.
- `meta-rg` content searches may use explicit paths such as `fbsource/fbcode/...` or `configerator/source/...` from the workspace root. Run unscoped filename discovery (`meta-rg --files ...`) from the selected repository root so its search scope is unambiguous and efficient.

## Where work happens

- Do all work — edits, builds, tests, commits, package builds — in this workspace's own `fbsource/` and `configerator/`. If the task needs a different base revision, commit or rebase here, or move this checkout (`sl goto`) when that is safe for its working copy and any open editor; use your judgement.
- Other workspaces (`~/checkout2`, `~/checkout3`, …) belong to other sessions. Don't use them unless I ask.
- New environments need my approval (see "New working copies and build environments" in the global preferences). Here that means `sl worktree add`, `wt add`, `eden clone`, `fbclone`, or `buck2 --isolation-dir`. They are expensive on a devserver because each one starts cold:
  - Buck: a new daemon and `buck-out`, a full rebuild, and tens of GB of RAM per daemon.
  - Maven: `~/.localrc` derives build roots from the workspace, so a worktree at `~/wt/<label>` gets its own `$BUILD_ROOT/presto-trunk-<label>`, `presto-facebook-trunk-<label>`, and a whole new local repository `$BUILD_ROOT/m2-repo-<label>`: Presto Java rebuilds from scratch and `~/.m2` is duplicated. A worktree anywhere else falls back to checkout1's build roots and `~/.m2`, and overwrites checkout1's build output.
  - Past incidents: extra isolation daemons pushed swap to 128/128 GB, and leftover build directories filled the 1.6 TB root disk.
- Once I've approved, prefer `wt add <label>` (it puts the worktree under `~/wt/`, where the build roots above are set up) and remove the worktree, its build roots, and any daemon it started when the task is done.
- Omnigent-hosted sessions refuse these commands the first time, as a reminder (policy `no_new_environments` in `omnigent_config/policy_modules/`). Prefix the command with `OMNIGENT_NEW_ENV_APPROVED=1` only after I have explicitly approved that environment.

## Diff ownership and CI follow-up

- Submitting a diff or stack makes you the owner of its CI follow-up, and that ownership is **pre-authorized** — it is the one exception to "DO NOT amend or rebase existing commits unless I explicitly ask." In the same turn as the `jf submit` that prints the Phabricator URLs, arrange notifications using the `phabricator-diff-watch` skill; then monitor and act on CI failures and AI-reviewer comments, amending the affected diffs until signals are green or you have a reason to push back. Use the custom Meta watcher by default. Only substitute a concrete integration after verifying its required Meta event coverage, stack coverage, and lifetime as described in the skill; generic notification capability alone is insufficient. Avoid duplicate subscriptions and repeated model status checks. Never ask whether to watch and never offer it as an option — just report what you found and fixed. This covers only diffs this session created and still owns; it authorizes no other amend, and no publishing, landing, or reviewer changes. Subscribing is a notification subscription, not a live-environment operation, so the Live Environment Safety gate does not apply to it.
