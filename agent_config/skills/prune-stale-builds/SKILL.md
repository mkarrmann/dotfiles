---
name: prune-stale-builds
description: >-
  Use when the devserver disk is filling up or Matt asks to free disk space,
  clean up builds, or find what is using space; also after a Presto trunk
  version bump. Covers ~/bin/prune-stale-builds, which deletes Maven artifacts
  from older Presto 0.NNN-SNAPSHOT versions in ~/.m2, the per-checkout m2
  repos and the out-of-tree build roots under /data/users/$USER/builds,
  without touching current builds. Also lists what it deliberately leaves
  alone (buck-out, caches) and why deleting whole build directories is wrong.
  Trigger keywords: disk space, disk full, df, du, out of space, No space left
  on device, clean up builds, ~/.m2, m2-repo, builds dir, stale snapshots.
---

# Prune stale builds

## Why the disk fills

Each workspace (`~/checkout1`–`~/checkout4`, `~/wt/<label>`) builds Presto
Java into its own out-of-tree roots and Maven repository (`_checkout_suffix`
in `~/.localrc`):

| Workspace | Build roots | Maven repository |
|---|---|---|
| `~/checkout1` | `$BUILD_ROOT/presto-trunk`, `presto-facebook-trunk` | `~/.m2/repository` |
| `~/checkoutN`, `~/wt/<label>` | `$BUILD_ROOT/presto-{,facebook-}trunk-<name>` | `$BUILD_ROOT/m2-repo-<name>` |

`BUILD_ROOT` is `/data/users/$USER/builds`. Every trunk version bump
(`0.300-SNAPSHOT` → `0.301-SNAPSHOT`) leaves the old version's jars and
plugin directories behind in every one of these, about 30G per Maven
repository per bump plus the build roots, and nothing else removes them. On
2026-10-27 this had grown to roughly 600G and the disk was at 99%.

**Do not delete whole build roots or Maven repositories.** All of them are in
use; they refill to the same size on the next `mpi`/`mfi` and cost a cold
rebuild. Only the old versions are reclaimable.

## The script

```bash
~/bin/prune-stale-builds        # report what is stale, delete nothing
~/bin/prune-stale-builds -v     # also list every path
~/bin/prune-stale-builds --yes  # delete
```

Source: `~/dotfiles/bin/prune-stale-builds`; tests:
`~/dotfiles/tests/test_prune_stale_builds.py`.

For each workspace it reads the version from `presto-trunk/pom.xml` and
`presto-facebook-trunk/pom.xml` and treats as stale only what is **strictly
older**:

- Maven repository: `com/facebook/presto/**/0.NNN-SNAPSHOT` directories
  (nested group ids such as `com/facebook/presto/router` included). Releases
  like `0.174` and other groups are never touched.
- Build roots: any file or directory whose name contains `-0.NNN-SNAPSHOT`,
  e.g. `presto-tpch-0.299-SNAPSHOT.jar` or the plugin directory
  `presto-tpch-0.299-SNAPSHOT/`.

Safety properties, all covered by the tests:

- Newer versions are kept, so a checkout parked on an older revision keeps
  the build it will return to.
- A workspace with a running Maven build (found via `/proc`, by
  `-Dmaven.repo.local` / `-Dout-of-tree-build-root`, or `~/.m2` when neither
  is given) is skipped whole. Rerun after the build finishes.
- Build roots and repositories with no matching workspace (a removed
  `~/wt/<label>`) are listed, never deleted. `presto-local-dev-*` and other
  directories are ignored.

## How to use it

1. Run it without `--yes` and show Matt the report. Deleting needs his
   go-ahead; a report is free.
2. Run with `--yes` once he agrees, then `df -h /` to show the result.
3. If a workspace was skipped for a running build, say so and offer to rerun
   later rather than waiting on it.

## What it leaves alone, and the other big consumers

Report these with sizes and let Matt decide; none is safe to delete blindly.

- **buck-out** (hundreds of GB): Eden redirections into
  `/data/users/$USER/scratch/<checkout>/edenfsZredirectionsZbuck-out`. `eden
  du --fast` sizes them. `buck2 clean` frees everything but kills that
  checkout's daemon and makes the next build cold, so it is not "stale only".
- **Caches** under `~/.cache` (`dvsc`, `cppls`, `jdtls`, `JetBrains`, `uv`):
  rebuilt on demand, at the cost of reindexing.
- **Unknown data directories** under `/data/users/$USER`: ask what they are.

Use parallel `du` (`xargs -P`) with a `timeout`; a serial `du` over
`/data/users/$USER` takes minutes and walks into Eden mounts.
