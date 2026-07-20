# Omnigent-backed Neovim tabpage status

Status: **IMPLEMENTED** (2026-07-19)

## Summary

Replace the Claude Code hook-driven tabpage status integration with a
transport-neutral tab status model fed by CodeCompanion's native Omnigent
session stream.

The important architectural choice is to reuse CodeCompanion's existing
Omnigent SSE subscription. The native adapter already owns stream lifecycle,
reconnection, replay deduplication, foreground/background arbitration, durable
session identity, and elicitation state. Neovim should project those normalized
updates into tab-local presentation state rather than open a second subscription
to the same Omnigent session.

The intended flow is:

```text
Omnigent /v1/sessions/{id}/stream
  -> CodeCompanion Omnigent SSE parser and reducer
  -> CodeCompanionOmnigentLifecycle User event
  -> lib/omnigent-tab-state.lua
  -> lib/agent-tabline.lua
```

This preserves the useful behavior of the old tabline:

- show when an agent is working;
- show when an agent needs input;
- show an unread completion on a background tab;
- clear unread state when the tab is viewed;

while removing the dependency on Claude Code hooks, `NVIM_TAB_HANDLE`, and the
Claude-specific state globals.

## Implementation outcome

The implementation follows the architecture above with a deliberately small
public lifecycle contract:

- CodeCompanion's Omnigent session emits only status-bearing updates through a
  persistent `on_lifecycle` callback. Content, reasoning, tool, and usage
  updates do not fan out as Vim autocmds.
- The Omnigent chat handler publishes those updates as
  `CodeCompanionOmnigentLifecycle`, including the owning buffer and the
  state-folded session snapshot.
- `lib/omnigent-tab-state.lua` projects the lifecycle into semantic tab-local
  state and uses `cc_tab_owner` as the sole chat-to-tab mapping.
- `lib/agent-tabline.lua` renders true tabpages, manual names, pointed
  separators, and transport-neutral activity markers.
- The old Claude notification hook and `claude_state` renderer were removed.
  Non-status tab actions still used by `claude-agent-manager` were retained in
  `lib/agent-tab-actions.lua`.

The custom renderer was retained. Bufferline is not installed in the active
configuration, and its default listed-buffer behavior is incompatible with
tabs that contain only unlisted terminal or CodeCompanion buffers. Keeping the
semantic state in a separate module leaves a future bufferline tabs-mode switch
possible without changing lifecycle handling.

## Motivation

The current tabline was introduced when Claude Code sessions moved from tmux
windows into Neovim tabpages. Its state comes from
`claude_config/hooks/nvim-notify.sh`, which sends remote expressions back into
the parent Neovim instance. `nvim/lua/lib/claude-tab-state.lua` stores glyphs
directly in `vim.t.claude_state`:

- `⚙` working;
- `!` needs input;
- `✓` completed but unread;
- `~` completed and viewed.

That mechanism is now attached to the wrong lifecycle. Omnigent is the durable
session owner, and CodeCompanion's native Omnigent adapter is the primary editor
client. Status should therefore be derived from Omnigent response and
elicitation events.

The custom renderer also displaced LazyVim's `bufferline.nvim` UI. The status
model should be separated from rendering so the richer behavior can survive a
return to a better-looking tab UI.

## Goals

1. Drive tab status from Omnigent session events, including CodeCompanion chats.
2. Support foreground turns and externally-triggered background/wakeup turns.
3. Represent pending Omnigent elicitations as "needs input."
4. Mark completion or failure unread only when it was not already viewed.
5. Preserve manual tab names and the `<leader><Tab>r` rename workflow.
6. Keep state collection independent of the chosen tabline renderer.
7. Avoid duplicate Omnigent streams, polling, and session-to-tab heuristics.
8. Handle persistent headless `nvs` sessions and hidden CodeCompanion chats.

## Non-goals

- Reimplement the Omnigent SSE client in dotfiles.
- Make the tabline a general Omnigent dashboard for sessions not attached to
  this Neovim process.
- Restore Claude Code hook compatibility indefinitely.
- Persist unread state across a Neovim server restart. Tabpages themselves do
  not survive that boundary today.
- Change CodeCompanion's transcript rendering, queue, or elicitation UI.

## Existing building blocks

### Native Omnigent adapter

`nvim/lua/plugins/codecompanion.lua` configures the native Omnigent adapter with:

- durable `conv_*` sessions;
- `background_updates = true`;
- a persistent session SSE stream;
- heartbeat and reconnect handling;
- labels identifying the originating `nvs` session and optional tab name.

### Normalized event reducer

The CodeCompanion fork's `lua/codecompanion/omnigent/events.lua` reduces raw SSE
frames into stable updates including:

```text
turn_started
message_delta
elicitation
elicitation_resolved
turn_completed
turn_failed
turn_cancelled
interrupted
error
status
usage
```

`lua/codecompanion/omnigent/session.lua` applies each update to local session
state before routing it to either the foreground handler or background observer.
At that point `session.status`, `pending_elicitations`, usage, and model metadata
are current.

### Foreground and background routing

The foreground handler emits `CodeCompanionRequestStarted` and
`CodeCompanionRequestFinished`. The background observer currently emits:

- `CodeCompanionChatOmnigentWakeup` on the first visible content of a background
  turn;
- `CodeCompanionChatOmnigentBackgroundTurn` after a background turn completes.

These events cover much of the desired behavior but are not a complete status
contract. In particular, there is no public event covering all elicitation,
failure, cancellation, and stream lifecycle transitions.

### Tab ownership

The CodeCompanion chat buffer is stamped with:

```lua
vim.b[chat_bufnr].cc_tab_owner
```

This is already the canonical mapping used by `lib/codecompanion-queue.lua`.
The one-chat-per-tab invariant means there is no need to infer ownership from
session labels, the current window, a buffer name, or an Omnigent workspace.

## Implemented architecture

### 1. Expose normalized Omnigent lifecycle updates

Add a persistent lifecycle callback to the CodeCompanion Omnigent session
runtime. It must run after `Session:_apply_state(update)` and before the update is
routed to the foreground handler or background observer.

The handler installs the callback with chat context when it creates or attaches
the session. Unlike the foreground `on_update` callback, this lifecycle callback
must remain installed after a request completes so it continues to observe
background turns.

The callback fires one Neovim event:

```lua
vim.api.nvim_exec_autocmds("User", {
  pattern = "CodeCompanionOmnigentLifecycle",
  data = {
    bufnr = chat.bufnr,
    session_id = session.session_id,
      kind = update.kind,
      response_id = update.response_id,
      active_response_id = session.reducer.current_response_id,
      status = session.status,
    pending_elicitations = vim.tbl_count(session.pending_elicitations or {}),
    error = update.error,
  },
})
```

The event contains normalized state, not the complete raw SSE payload.
This keeps consumers independent of wire-format changes and avoids exposing
message or tool content unnecessarily.

Only `turn_started`, elicitation, terminal, status, interruption, and stream
error updates enter this channel. This avoids dispatching an autocmd for every
streamed text chunk.

Transport failures that terminate a foreground request should also produce a
terminal lifecycle update, or the tab-state module should consume the already
paired `CodeCompanionRequestFinished` error event as a fallback.

### 2. Store semantic tab state

Create `nvim/lua/lib/omnigent-tab-state.lua`. Do not encode presentation glyphs
as state. Store phase and unread status separately:

```lua
vim.t.agent_status = {
  phase = "idle", -- idle | running | waiting | failed
  unread = false,
  session_id = nil,
  response_id = nil,
}
```

The separation matters because "unread" is orthogonal to session phase. For
example, a failed background turn is both `failed` and `unread`, while an
elicitation remains `waiting` even after the user enters the tab.

The module owns:

- mapping a chat buffer to `cc_tab_owner`;
- applying lifecycle transitions;
- clearing state on chat teardown or adapter replacement;
- clearing only `unread` on `TabEnter`;
- invalid-tab and stale-event checks;
- requesting a tabline redraw after meaningful changes.

### 3. State transitions

| Omnigent update | Result |
| --- | --- |
| session ready/restored | Attach session id and seed from the session snapshot |
| `turn_started` | `phase=running`, `unread=false` |
| `elicitation` | `phase=waiting` |
| `elicitation_resolved` | `waiting` if other elicitations remain; otherwise `running` when a response is active, else `idle` |
| `turn_completed` | `phase=idle`; set `unread=true` only if the owner tab is not current |
| `turn_failed` or `error` | `phase=failed`; set `unread=true` only if the owner tab is not current |
| `interrupted` or `turn_cancelled` | `phase=idle`, `unread=false` |
| new turn after failure/completion | Clear the previous terminal/unread state |
| `TabEnter` | Clear `unread`; do not clear `waiting` |
| chat closed/adapter replaced | Clear the tab's session association and status |

Terminal updates should be matched against the current response id where one is
available. A late terminal event from an older request must not clear a newer
running state. This mirrors the request-id protection already used by
`lib/codecompanion-queue.lua`.

### 4. Presentation mapping

The renderer maps semantic state to text and highlights:

| State | Suggested marker | Meaning |
| --- | --- | --- |
| running | `⚙` | Agent is processing |
| waiting | `!` | An Omnigent elicitation needs input |
| idle + unread | `✓` | A background completion has not been viewed |
| failed + unread | `×` | A background failure has not been viewed |
| idle, viewed | none | No attention required |

Rename the current Claude-specific highlight groups to transport-neutral names,
for example `TablineAgentRunning`, `TablineAgentWaiting`,
`TablineAgentUnread`, and `TablineAgentFailed`.

## Tabline presentation options

### Considered: bufferline in tabs mode

Re-enable `akinsho/bufferline.nvim` and configure it in tabpage mode rather than
its default buffer mode. Verify against the pinned version that its formatter can:

- resolve the actual tabpage represented by each element;
- display `N:name` using `vim.t.tab_name`;
- append the marker for that tabpage;
- update marker highlights without rebuilding plugin state;
- remain visible with terminal and unlisted CodeCompanion buffers.

Do not re-enable vanilla buffer mode. The prior failure was explicit: most of
these tabs contain unlisted terminal or CodeCompanion buffers, so bufferline
considered the listed-buffer count too small and hid the bar.

### Chosen: retain and restyle the custom renderer

The small tabpage renderer was retained and restyled to match LazyVim's tabs:

- selected-tab color;
- slanted or padded separators;
- consistent tab width;
- transport-neutral status highlights.

The status module must not depend on which option is selected. This allows a UI
change without touching lifecycle logic.

## Session and tab correlation

For CodeCompanion, `cc_tab_owner` is authoritative. It is local, direct, and
already maintained through chat open/close behavior.

Existing Omnigent labels such as `orchest.nvim_session` and `orchest.tab` remain
useful for external UIs, but they should not drive local tab status:

- tab names are optional and mutable;
- names are not necessarily unique;
- create-time labels do not follow later tab renames;
- workspace and host cannot distinguish multiple chats in one checkout.

If a future non-CodeCompanion Omnigent client needs to target Neovim tabs, add a
stable random tab UUID such as `omnigent.nvim_tab_id` and stamp it onto the
session at creation. That is a separate extension and is not required here.

## Migration record

### Phase 1: lifecycle contract (complete)

1. Add the persistent normalized lifecycle callback/event to the CodeCompanion
   fork.
2. Cover foreground, background, elicitation, terminal, and transport-error
   paths in the Omnigent test suite.
3. Preserve the existing `RequestStarted`, `RequestFinished`, wakeup, and
   background-turn events for current consumers.

### Phase 2: tab-state projection (complete)

1. Add `lib/omnigent-tab-state.lua` and its state-transition tests.
2. Replace `require("lib.claude-tab-state")` in `config/options.lua`.
3. Move the `TabEnter` unread handling into the new module.
4. Keep manual tab naming and `<leader><Tab>r` unchanged.
5. Initially render the new semantic state through the existing tabline so the
   behavior can be verified independently of visual changes.

### Phase 3: presentation (complete)

1. Audited bufferline against the active plugin/configuration constraints.
2. Kept and restyled the existing true-tabpage renderer.
3. Verified the renderer and lifecycle event in a persistent `nvs` server.

### Phase 4: cleanup (complete)

- remove `lib/claude-tab-state.lua`;
- remove the Claude-specific tab highlights and globals;
- remove the Claude `nvim-notify.sh` hook wiring if no other workflow uses it;
- remove the old Claude `TabEnter` state conversion;
- update comments that still describe the tabline as Claude-specific.

## Validation

### Automated tests

The pure state-transition test covers:

1. foreground start and completion on the current tab;
2. completion on a background tab becoming unread;
3. `TabEnter` clearing unread;
4. elicitation remaining visible after entering the tab;
5. resolution returning to running or idle;
6. background wakeup start and completion;
7. failure and cancellation;
8. stale response completion not clearing a newer request;
9. chat close clearing state;
10. invalid or already-closed tab handles being ignored.

The CodeCompanion Omnigent tests assert one lifecycle callback per relevant
normalized update, suppress content updates, and verify that
foreground/observer arbitration does not emit duplicates.

Validation completed on 2026-07-19:

- 119 Omnigent unit tests passed through a local MiniTest-compatible runner;
- the headless tab state and renderer specification passed;
- a live full-config CodeCompanion chat completed through Omnigent, produced one
  start and one completion, displayed unread state in a background tab, cleared
  it on `TabEnter`, and cleared the association on chat close;
- a restarted `CCO-main1` persistent Neovim server loaded the working
  CodeCompanion checkout and registered the lifecycle autocmd.

The repository's standard test target could not download `mini.nvim` because
GitHub access returned HTTP 403, so the same Omnigent test files were executed
in-process with compatible expectations instead.

### Manual smoke test

1. Open Omnigent CodeCompanion chats in two Neovim tabs.
2. Submit in one tab and switch away; confirm running then unread completion.
3. Enter the completed tab; confirm unread clears.
4. Trigger an Omnigent elicitation; confirm `!` remains until it is resolved.
5. Drive the same session from another client or wakeup; confirm the background
   observer updates the correct tab.
6. Interrupt and fail turns; confirm terminal states do not wedge as running.
7. Drop and restore the Omnigent stream; confirm no duplicate transition or
   permanent unread/running state.
8. Repeat through the Mac remote UI against both CCO and FTW `nvs` servers.

## Risks and mitigations

### Duplicate lifecycle events

Foreground and observer rendering share one session stream but alternate
ownership. Emit the lifecycle event once in the session router, not independently
from both renderers.

### Stream reconnect replay

The existing reducer and observer already handle content replay. The tab-state
consumer should make transitions idempotent by response id so replayed starts do
not create false unread changes.

### Elicitation precedence

`waiting` must outrank `running` and must be derived from the pending elicitation
count, not cleared merely because the tab was viewed.

### Persistent Neovim process

Configuration edits do not reload an existing headless `nvs` process. Rollout
and smoke-test instructions must include restarting the relevant `nvs@...`
service after installing the final configuration.

### Bufferline limitations

Treat bufferline integration as a presentation spike. Do not couple the status
model to undocumented bufferline internals; retain the custom renderer fallback.

## Decision summary

- **Use CodeCompanion's existing Omnigent SSE subscription.**
- **Expose normalized lifecycle state once at the session-router boundary.**
- **Map CodeCompanion chats to tabs with `cc_tab_owner`.**
- **Store semantic phase and unread state separately from glyphs.**
- **Keep rendering replaceable and evaluate bufferline tabs mode independently.**
- **Use the restyled custom renderer for true tabpages.**
- **Remove Claude hook integration after live Omnigent verification.**
