# Metamate 3 feature requests (drafts)

Drafts for Matt to post in the agentcloud group (or as Knots). Each is generic:
it helps anyone with more than one checkout per machine, not only this setup.

---

## 1. List a dev server's real checkouts in the Folder picker

**Problem.** On a dev server, *Folder* offers only `fbsource`, `configerator`
and `www` under `/data/users/<me>`. The code says why:
"A dev server announces no checkouts (fbcode `node/daemon/host_facts.rs`,
`read_checkouts`), so the folders on offer are the standard checkouts"
(`nest/apps/agentcloud/lib/nodes/devserverFolder.ts`). Many of us keep several
working copies (`~/checkout1/fbsource` … `~/checkout4/fbsource`) to run parallel
stacks; Claude Code, Codex and Muse load their project config from the folder
they start in, so a chat in the wrong copy runs on the wrong stack.

**Ask.** Have the dev server node announce its Sapling/Eden working copies
(e.g. from `eden list`, or `.hg`/`.sl` roots one or two levels under `$HOME`),
and offer them in *Folder*, labeled by path (`checkout2/fbsource`).

**Done when.** Picking a dev server lists every working copy on it; picking one
starts the chat there with its `CLAUDE.md`/skills/hooks. The three standard
names still appear.

**Related.** "Start a coding chat in any folder" (D133391417, T297990583) lets
you type a path; this makes the common case one click and needs no typing.

---

## 2. New chat remembers the machine and folder per tab (or accepts them in the link)

**Problem.** The default machine is one per account (Settings → Launch
defaults → Node, synced), or per Space with "Your Space defaults". A folder
pick lasts one chat ("One folder for the next session"). Someone who keeps one
browser window per working copy has to re-pick machine and folder on every new
chat, and a Space is the wrong unit: a Space is shared team context, while a
working copy is personal placement.

**Ask (either).**
- *New chat* in a tab starts on the machine and folder that tab last used.
- `/new` accepts `node` and `folder` (e.g.
  `/new?node=devvm20365.cco0.facebook.com&folder=/home/me/checkout2/fbsource`),
  so a window or bookmark can carry its placement.

**Done when.** A window used for one working copy keeps starting chats there; a
link with `node`+`folder` opens a composer already placed there.

---

## 3. Open a file from the transcript in my own editor

**Problem.** With *Code path links*, a repo-relative path opens CodeHub; an
absolute path on the node is not linked at all. People who edit in Neovim,
Emacs or a local IDE cannot jump from a session's file mention to the file on
the machine the session runs on.

**Ask.** A setting: *Open files with* → a URL template using `{node_host}`,
`{abs_path}`, `{line}` (e.g. `myeditor://open?host={node_host}&path={abs_path}&line={line}`),
applied to file mentions, edit pills and diffs, including absolute paths on the
session's node.

**Done when.** With a template set, clicking a file mention opens the template
URL with the session's node and the file's absolute path and line.

---

## 4. CI events for unpublished drafts

**Problem.** "Phabricator also sends no CI results for an unpublished draft"
(`meta signals.subscription explain --topic wake-yourself`). A draft-first
workflow (submit drafts, let agents fix CI, publish when green) gets no CI wake;
only the 10-minute safety poll on platform-armed watches can catch it.

**Ask.** Either emit CI events for the author's own drafts, or give
agent-created diff watches the same periodic check platform-armed ones have.

---

## 5. Hosted Codex honors the person's own hooks

**Problem.** The seeded per-session `CODEX_HOME` copies `~/.codex/hooks.json`,
but Codex keys hook trust by the file's path
(`[hooks.state."<path>:pre_tool_use:0:0"]`), so the copy is untrusted and Codex
skips it silently. Personal PreToolUse guardrails therefore never run in hosted
Codex, while they do in hosted Claude Code.

**Ask.** Carry the trust over when seeding (rewrite the `hooks.state` keys to
the seeded path), or link `hooks.json` at a stable path.
