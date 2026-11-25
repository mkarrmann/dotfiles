---
name: metamate-checkouts
description: >-
  Start Metamate 3 work in a specific checkout of Matt's devservers (CCO or FTW, ~/checkout1-4) and see what each checkout holds. Use when he asks to start, put, run, move, or hand off work "in checkout2", "on FTW", "in cco:3", "in a free checkout", or asks which checkouts are busy or free, or when a subagent or child session must run in a particular checkout. Metamate's web New chat cannot pick ~/checkout2-4 or default per window; this is the supported way. Trigger keywords: checkout, cco, ftw, cco:2, ftw:3, free checkout, which checkout, start a thread in, new chat in, mm-checkout, place work.
---

# Placing Metamate work in a checkout

Matt works across fixed places: each devserver's `~/checkoutN` (the table in
`~/dotfiles/bin-macos/workspaces`). A place is named by its nvs session
(`CCO-checkout2`) or a short form (`cco:2`, `cco2`). Metamate's web composer can
only start a coding chat in `/tmp` scratch or `~/fbsource` (= checkout1), so a
thread that belongs in another checkout is created with `mm-checkout`, which
wraps Metamate's supported CLI (`agentcloudctl create --node-id … --workspace …`).
The result is an ordinary top-level thread in Matt's web inbox.

## See what each checkout holds

```sh
~/dotfiles/bin/mm-checkout list          # threads per checkout + commit + uncommitted count
~/dotfiles/bin/mm-checkout list --json
```

`busy` means a thread there is running or waiting on Matt; `free` means no such
thread and no uncommitted changes; `idle` is neither (uncommitted work, nobody
on it). Treat a checkout with uncommitted changes as someone's work in progress.

## Start a thread in a checkout

```sh
~/dotfiles/bin/mm-checkout new cco:2 --title "Fix flaky FooTest" --prompt "…the task, self-contained…"
~/dotfiles/bin/mm-checkout new auto --host ftw --prompt "…"     # first free FTW checkout
```

- Defaults: Claude Code in `<checkout>/fbsource`. Override with `--harness codex`,
  `--model`, `--effort`, `--repo configerator`.
- The prompt is the new thread's first message: write it so it stands alone
  (the new thread does not see this conversation). Include the goal, relevant
  diffs/files, and constraints.
- Reply with the place and the printed URL. Do not wait for the new thread to
  finish unless Matt asks; it runs on its own and shows in his inbox.
- Never pick a place that is `busy` or has uncommitted changes unless Matt named
  it explicitly.

## A child that must run in a checkout

For a subagent (renders under the current thread rather than as its own inbox
entry), spawn it with both the machine and the folder; a hosted harness without
`--node` is refused:

```sh
agentcloudctl spawn -s "$AGENTCLOUD_SESSION_ID" --harness codex \
  --node devvm36111.ftw0.facebook.com --workspace /home/mkarrmann/checkout3/fbsource -p "…"
```

`agentcloudctl run` takes the same flags and waits for the child's final text.
