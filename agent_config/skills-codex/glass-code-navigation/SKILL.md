---
name: glass-code-navigation
description: >-
  Semantic code navigation across fbsource with the `glass` CLI (Glean's Glass service): where a symbol is defined, every reference to it, its callers and callees, and the symbols a file declares, without grep's false positives. Use when asked where something is defined, who calls or uses a function or class, what a file declares, or to trace a call chain in C++, Python, Rust, Thrift, Buck/Starlark, or JavaScript/Flow. Not for Java/Kotlin (no index) or for code that has not landed.
---

# Code navigation with glass

`glass` queries Glean's prebuilt index of fbsource over the network: no build, no
server to start, about 5 s per call. A reference is a real use of that symbol,
not a text match, so it answers "who calls X" where `search_files`, `rg`, or `bgs`
would also return comments, strings, and unrelated symbols of the same name.

## Workflow

Everything except `search` and `list` takes a symbol ID, so start with one of those:

```bash
glass search -r fbsource GleanConfig -l python          # name -> symbol IDs (prefix; -e exact, -i ignore case)
glass search -r fbsource -s folly::Optional -l cpp      # -s: scoped name
glass list fbsource/fbcode/glean/client/py3/__init__.py  # symbols a file declares, with IDs
glass describe fbsource/py/fbcode/glean.client.py3.GleanConfig          # location, kind, signature, source
glass find-references fbsource/py/fbcode/glean.client.py3.GleanConfig   # every use site
glass call-hierarchy fbsource/py/fbcode/glean.client.py3.GleanClient.create_db  # callers and callees
```

Results carry repo-prefixed paths (`fbsource/fbcode/...`) with line:column
ranges. Pass `--color never` when parsing output.

## Gotchas

- Put the name before `-l`. It takes several values and swallows a trailing
  name: `invalid value 'X' for '--language'`.
- `list` needs the repo prefix: `fbsource/fbcode/...`, not `fbcode/...`
  (`No repository found for: fbcode`).
- Empty output with exit status 0 means the index has no match, not that the
  symbol does not exist. Fall back to text search.
- The index is built from landed fbsource, not your working copy. Symbols added,
  moved, or renamed locally are missing or stale, so read the file before
  trusting a location.

## Language coverage (checked 2026-09-22)

| Works | Partial | Does not work |
|---|---|---|
| C++, Python, Rust, Thrift, Buck/Starlark (`-l buck`), JavaScript/Flow | Go: `list` works, `search` finds nothing | Java, Kotlin: `No Glean dbs found for: fbsource.java.scip` |

`-l` also accepts other names (hack, typescript, objectivec, swift, ...), which
are untested. For Java, including Presto, use text search and let the build
(Maven or Buck) check the code.
