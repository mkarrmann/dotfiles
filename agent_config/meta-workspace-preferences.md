# Meta Workspace Layout

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
