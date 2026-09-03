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

## Diff ownership and CI follow-up

- Submitting a diff or stack makes you the owner of its CI follow-up, and that ownership is **pre-authorized** — it is the one exception to "DO NOT amend or rebase existing commits unless I explicitly ask." In the same turn as the `jf submit` that prints the Phabricator URLs, subscribe via the `phabricator-diff-watch` skill; then monitor and act on CI failures and AI-reviewer comments, amending the affected diffs until signals are green or you have a reason to push back. Never ask whether to watch and never offer it as an option — just report what you found and fixed. This covers only diffs this session created and still owns; it authorizes no other amend, and no publishing, landing, or reviewer changes. Subscribing is a notification subscription, not a live-environment operation, so the Live Environment Safety gate does not apply to it.
