# Review source fixture contract

These files are synthetic version 1 `DiffSnapshot` payloads. They contain no
recorded comment text, paths, URLs, hosts, user names, or production IDs.

The production adapter is expected to normalize read-only results from:

- `jf diff-properties D<number>` for lifecycle, author, and latest version;
- `meta phabricator.diff comments --number=D<number> --output=json
  --no-color --latest-version --skip-author --unresolved-only
  --no-suggestions` for published review comments;
- a fixed `jf graphql` signalview query for aggregate state and stable failure
  names on the latest version.

The contract was shape-checked on 2026-07-19 with Jellyfish release
`20260716-050544` and `meta` release `20260716-214220`. The watcher never uses
the detail-fetching or deferred-test mutation paths from the `ci-signals`
skill.
