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

## 2. Let a browser tab keep a machine and folder for its new chats

**Post as:** `[Metamate 3] Feature request: Let a browser tab keep a machine and
folder for its new chats`, in the agentcloud group.

### Problem
- I keep one browser window per working copy: two dev servers, several
  checkouts each (`~/checkout1/fbsource` … `~/checkout4/fbsource`), each running
  its own stack. Each window should start new chats where its work lives.
- Today a new chat starts on the account-wide default machine (or the Space's,
  with *Your Space defaults*) and in a `/tmp` scratch folder. A Folder pick lasts
  one chat, and *New session* and its shortcut (Ctrl+0) always reset to Session
  Defaults (Client spec 306, non-goals). So every new chat needs the machine and
  the folder picked again, and a forgotten pick starts the chat in scratch or in
  the wrong checkout: Claude Code, Codex and Muse Code then load the wrong
  `CLAUDE.md`, skills and hooks and edit the wrong stack.
- A Space is the wrong unit for this: a Space is shared team context, a working
  copy is one person's placement, and several windows work in the same Space.

### How it works
- A **placement** is a machine plus a folder on it.
- **Opening a placed tab.** `/new?node=<node id>&workspace=<absolute path>`
  opens a blank chat on that machine, in that folder. This extends the existing
  `?node=` carry (spec 306 FR5) with the folder. (`workspace`, the create field
  it becomes, because `?folder=` already files a chat into a sidebar folder.)
- **⌘-click carries the folder too.** ⌘-click on *New session* from a hosted
  chat already carries the chat's machine; it should carry the chat's folder as
  well, so the new tab's chat starts where this one runs instead of in scratch.
- **The tab keeps it.** A tab opened with a placement keeps it: every new chat
  in that tab (*New session*, Ctrl+0, `/new`, a new pane) starts on that machine
  and in that folder. A machine or folder picked for one chat wins for that chat
  only. *Clear for this tab* in the composer returns the tab to Session Defaults.
- **Order.** Machine: the chat's own pick, then the tab's placement, then the
  Space default, then Session Defaults. Folder: the chat's own pick, then the
  tab's placement, then none (scratch).
- **Scope.** The machine applies to every harness; the folder to the hosted
  CLIs (Claude Code, Codex, Muse Code), as the Folder field does today.
- **Safe failures.** The folder must exist on the machine (`hosted.folder:
  "project"`): a missing one stops the chat with a message naming the machine
  and folder, and nothing is created. A placed machine that is not connected is
  shown as not connected; the chat is never moved to another machine silently.
- **Where it lives.** In the tab: it survives a reload, ends when the tab
  closes, and is not synced, so other tabs and devices are unaffected. A tab
  with no placement behaves exactly as today.
- *Nice to have:* the tab title names the placement (e.g. `checkout2 ·
  devvm20365`), so window managers and tab strips can tell windows apart.

### Done when
- Opening `/new?node=devvm1.example.com&workspace=/home/me/checkout2/fbsource`
  and sending a message starts the chat on devvm1 in that folder, with that
  folder's `CLAUDE.md`, skills and hooks loaded.
- In the same tab, Ctrl+0 or *New session* opens a blank chat already on devvm1
  and in that folder, also after reloading the tab.
- Another tab, and the same account on another device, are unaffected.
- A machine or folder picked for one chat applies to that chat only; the next
  new chat in the tab is placed again.
- ⌘-click on *New session* from a chat running in
  `/home/me/checkout3/fbsource` opens a tab whose blank chat is placed there.
- A missing folder stops the chat with a message and creates nothing; a
  disconnected machine is shown as such and not swapped.
- *Clear for this tab* returns the tab's new chats to Session Defaults.

### Not in this request
- Changing the machine or folder of a chat that has started.
- Account-wide or per-Space folder defaults.
- Listing a dev server's own checkouts in the Folder picker (request 1).

### Related
- Client spec 306 (`306-new-chat-node-binding`): the default node, the `?node=`
  carry (FR5, FR7, `lib/nodes/useCarriedNode.ts`), and the non-goal this request
  narrows to placed tabs.
- T297990583 / D133391417, start a chat in any folder: complementary. This
  request needs only that a non-standard absolute path can ride a create, which
  the server already honours (`resolve_workspace`: an absolute workspace is used
  as-is).
- T293232447 / D128643831, a Space's default node: per Space; this is per tab.
- For whoever builds it: `withPickedFolder` already sends a picked path as
  `workspace` with `hostedFolder: "project"`; the folder can be seeded through
  `pickPendingFolder` with `mustExist: true`, and `useSeedDefaultNode` decides the
  machine's precedence.

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
