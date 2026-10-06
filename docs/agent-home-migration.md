# Moving from the Omnigent hub to Agent Home 2.0

**Status:** Preparation. Omnigent stays primary; nothing here changes how it
runs today.

**Goal:** When Agent Home 2.0 (AH2) becomes the daily driver, the switch should
be a configuration change, not a rewrite. Keep the parts of this setup that
are ours -- rules and skills, the watcher, the guardrail policies, the nvim
workflow -- and drop the plumbing AH2 provides: the hub and its failover,
snapshots, upgrade fencing, the TLS proxy, and the Google Chat bridge.

[agenthome-passthrough-design.md](agenthome-passthrough-design.md) proposed
bridging sessions through AgentCloud's client plane. AH2 does not run on
AgentCloud -- it runs on dm-core -- so that design does not describe the
system this document targets.

## What AH2 is, as it bears on this setup

Verified against source and the live CLI on 2026-10-06.

- **Execution is on our hosts.** Each devserver runs `devmate-bridge` and
  `dm-core-server` as systemd user units (`WorkingDirectory=/home/%u/fbsource`).
  www (`www/flib/intern/dm_core/`) drives them over Thrift. A session runs as
  us, with our `$HOME`, in the directory it is given.
- **Harnesses:** `BaseAgent` in
  `xplat/vscode/modules/dm-core/src/shared/types/agent-types.ts` -- Native
  (Devmate), Claude (Agent SDK), Codex (app-server), MetaCode, Muse, Mare, Pi.
  The model is chosen separately; Claude and GPT models are both available.
- **User config loads.** The Claude harness passes
  `settingSources: ['project', 'local', 'user']` and leaves user MCP servers
  in place (`extension-host/casdk/ClaudeAgentQueryOptions.ts:400-419`), so
  `~/.claude` settings, hooks, skills, CLAUDE.md, and `~/.claude.json` MCP
  servers -- including `watch` -- apply. It forces
  `permissionMode: 'acceptEdits'` (:431). Codex uses `~/.codex`; dm-core sets
  `CODEX_HOME` only in workflow mode (`codex/CodexJsonRpcClient.ts:243-273`).
- **Placement defaults fight this setup.** Only leased kinds are auto-picked
  (`isDmHqAutoPickableKind`: od, dsv2, sc), and a busy fleet falls back to
  reserving a new OD (`DEFAULT_BUSY_FLEET_FALLBACK = 'new_od'`). Under GK
  `dm_hq_fresh_checkout`, a new session may `sl goto getstablerev()` on a clean
  checkout with no drafts (`src/api/checkoutFreshen.ts`).
- **Agent commands use an agent network identity** (`3p-bind-mount -c devmate`).
  Maven is unaffected here: `~/.m2/settings.xml` mirrors `*` to the internal
  Nexus.
- **Session API** (`meta ah.session --help`): `message` ("queued or started
  depending on session state", risk LOW / AUTONOMOUS), `whoami`, `list`,
  `inspect`, `read`, `ask`/`reply`, `answer`, `fork`, `move`, `stop`, and
  `create` with orchestrated children that report back to their parent.

## Done

- **The watcher can wake AH2 sessions.** Watches addressed to
  `agenthome:<agent_id>` are delivered with `meta ah.session message`; every
  other id takes the Omnigent path unchanged. A capability handshake keeps the
  MCP surface from accepting such a watch until the running worker can wake
  it. See the watcher README, "Agent Home sessions".
- **Skills say how an AH2 session addresses itself:** `phabricator-diff-watch`,
  `watch-anything`, and `waiting-without-polling`.

## To enable the watcher for AH2

An operator action, not something to do during source changes. On the active
hub:

```bash
systemctl --user restart omnigent-watcher omnigent-server
curl --noproxy '*' -s http://127.0.0.1:6767/v1/watches/capabilities
# expect {"session_kinds":["agenthome","omnigent"]}
```

Restarting `omnigent-server` interrupts the streams of open Omnigent sessions;
do it when they are quiet. No schema change is involved, and reverting the
source with the same restarts goes back to Omnigent-only.

## Open before switching

1. **Guardrail policies.** `no_new_environments` and `no_foreground_wait` exist
   only as Omnigent policy modules. AH2 has no user policy layer, but it does
   load Claude settings hooks, and Codex now uses the canonical `~/.codex`.
   Factor the decision logic out of `omnigent_config/policy_modules/` into
   harness-neutral functions with thin Omnigent-policy and hook wrappers, then
   confirm the hooks fire under AH2's Claude and Codex harnesses.
2. **nvim.** The CodeCompanion adapter targets Omnigent's API. Whether
   dm-core's local HTTP/SSE API can back an equivalent adapter is unexamined.
3. **The watcher without the hub.** Registration still goes through the
   Omnigent server's mounted API, and commands run on the hub. Retiring the
   hub means running the watcher API standalone on one host.
4. **Session identity under Codex.** `meta ah.session whoami` reads
   `AH_SESSION_ID`, `DM_SESSION_ID`, then `AGENT_SESSION_ID`; dm-core sets the
   last for Claude but injects nothing for Codex unless the caller passes
   `extraEnv`. Confirm what an AH2 Codex session's `whoami` prints.
5. **Messaging a session that is no longer live.** `message` targets live
   sessions. The watcher treats an off-host session as suspended, not gone;
   whether a message revives it from the stored transcript is untested.
6. **Placement settings,** per user: "Always use my devserver", the default
   harness, and start links with an explicit directory, for example
   `internalfb.com/ah/new?dir=~/checkout2/fbsource/fbcode/github/presto-facebook-trunk`.
7. **Omnigent-specific skills.** `omnigent-sessions`, `omnigent-models`, and
   `omnigent-visual-output` mean nothing outside Omnigent; gate them on the
   environment once AH2 sessions are routine.
