# SKILL.md Curation Principles

What belongs in SKILL.md vs the CLI's `--help` output.

## SKILL.md is for

- **Preferred workflows**: steer toward the best approach (e.g., ghtml over format subcommands, `get` over `comments list`)
- **Gotchas not obvious from --help**: e.g., `comments add` creates unanchored comments invisible in the UI
- **One good example per concept**: don't show five variations when one with a parenthetical note suffices
- **Conventions**: temp file paths (`/tmp/gmux*`), output format preferences
- **Connecting the dots**: when to use one subcommand vs another, which format to prefer and why

## SKILL.md is NOT for

- **Flag listings**: don't repeat what `-h` already shows — use prose or point to `--help`
- **Every variation of a command**: if `--match-case` and `--as-markdown` are just extra flags, mention them in a parenthetical, don't give each its own line
- **Redundant code blocks**: if you're repeating the same prefix (`meta google.docs get <DOC>`) on every line, write prose instead
- **Exhaustive API docs**: the CLI has `--help` for that

## Rules of thumb

- **Prose over code blocks** when just listing flags or options
- **One example beats five** — consolidate with parenthetical notes like `(also --as-markdown)`
- **Steer, don't document** — recommend the best path, don't neutrally list all paths
- **Note caveats** — if something is lossy, verbose, invisible in the UI, or rarely needed, say so
- **Brevity matters** — SKILL.md goes into Claude's context window; every line costs tokens
