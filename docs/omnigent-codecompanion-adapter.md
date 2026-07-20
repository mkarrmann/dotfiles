# Dotfiles Omnigent adapter — plan (IMPLEMENTED)

What to add to `nvim/lua/plugins/codecompanion.lua` to launch a native Omnigent
chat, now that the plugin ships the `omnigent` adapter family (see
`~/repos/codecompanion.nvim/.codecompanion/omnigent-native-progress.md`).

This is the last mile to a **working foreground chat**. It's small: an adapter
definition + a launch keymap. Everything else is parity/polish.

> **STATUS: done + all follow-ups landed (2026-07-15).** `config.adapters.omnigent`
> (agent `polly`, `OMNIGENT_URL`, `host="auto"`, create-time `labels`) and the
> `<leader>aM` launcher are in `nvim/lua/plugins/codecompanion.lua`. Since the
> original plan, ALL remaining milestones shipped:
> - The `apply()` stop_stream leak is **fixed** (outgoing omnigent session's stream
>   is torn down symmetric to the ACP disconnect).
> - **M3 resume UX**: `omnigent_continue()` picker on `<leader>amc` (cwd-scoped,
>   `<A-c>` toggles all workspaces) + in-chat `/omnigent_resume` / `/omnigent_session`
>   / `/omnigent_children`.
> - **M4 wakeups**: `background_updates=true` — background/wakeup turns render while
>   idle, with a non-visible-chat toast on `CodeCompanionChatOmnigentWakeup`.
> - **Track C parity**: winbar session pill + statusline + context-% now light up
>   for omnigent via the shared `lib/codecompanion-session.lua` resolver +
>   `CodeCompanionOmnigentUsage` feed; doctor/reap have omnigent arms.
> - **Native harnesses + Codex Goal**: the agent picker admits the exact built-in
>   `claude-native-ui`/`claude-native` and `codex-native-ui`/`codex-native`
>   pairs. `/goal` creates or attaches a Codex-native session and provides
>   create/view/edit/pause/resume/clear controls through asynchronous REST calls.
>
> See `~/repos/codecompanion.nvim/.codecompanion/omnigent-native-progress.md` for
> the full per-milestone status and the manual GUI verification checklist (the only
> thing left is eyeballing the buffer rendering).

## 1. Adapter definition

Add an `omnigent` family alongside `http` and `acp` in the `adapters = { ... }`
block:

```lua
adapters = {
  http = { ... },   -- unchanged
  acp  = { ... },   -- unchanged
  omnigent = {
    omnigent = function()
      -- IMPORTANT: extend the family's builtin "default" via the family module,
      -- NOT extend("omnigent", ...). The generic builtin lives at
      -- adapters/omnigent/default.lua; extending "omnigent" would recurse back
      -- into this function, and the top-level extend() would misroute "default"
      -- to the http family. Calling the family module directly avoids both.
      return require("codecompanion.adapters.omnigent").extend("default", {
        name = "omnigent",
        formatted_name = "Omnigent",
        url = vim.env.OMNIGENT_URL or "http://127.0.0.1:6767",
        defaults = {
          -- SDK agents and the built-in claude-native-ui/codex-native-ui agents
          -- normalize output and Omnigent policy elicitations onto the session
          -- stream. Native agents use their vendor rules and tool surface.
          agent = "polly",
          host = "auto",       -- fail-closed FQDN match to this machine
          workspace = "auto",  -- cwd, but only when the resolved host is local
        },
        opts = {
          background_updates = false, -- flip on when M4 (wakeups) lands
        },
      })
    end,
  },
}
```

Resolution sanity check: `adapter = "omnigent"` → `config.adapters.omnigent.omnigent`
(this function) → `adapter_type` returns `"omnigent"` → the omnigent family
resolves it → the function extends the builtin `default.lua` with these overrides.

## 2. Launch keymap

`tab_chat_set_adapter` already handles a non-broker/non-dvsc adapter by falling
through to `tab_chat_open_or_toggle({ adapter = name })`, so no changes to the
launch machinery are needed — just a keymap in the `keys = { ... }` table:

```lua
{ "<leader>aM", function() tab_chat_set_adapter("omnigent", { clear = true }) end,
  desc = "CodeCompanion Chat (Omnigent)" },
```

(`<leader>aM` is a suggestion — the `aM`/`aN` slots look free next to your
`aG`/`aC`/`aO` agent launchers.)

For Codex Goal, launch `<leader>aA`, select `codex-native-ui`, and run `/goal` in
the new chat. `/goal` also works before the first prompt: it creates the bound
native session without posting a dummy message, then opens create/view/edit/
pause/resume/clear controls.

## What works immediately

- One-chat-per-tab, read-only buffer + input queue (the handler now fires
  `CodeCompanionRequestStarted/Finished`, which the queue advances on).
- Streaming assistant text, cancel (posts an interrupt; the durable session
  survives), `Chat:close` tears down only the local SSE stream.
- `/model` picker once a session advertises `model_options`.
- Existing ACP/broker adapters are untouched.

## Temporary SDK Codex workarounds

> **HACK STATUS (2026-07-20): active for Omnigent 0.5.1.** The local Omnigent
> `main` checkout at `0beca0bb814b99601adf35fc3a271f5a8b3f8f6b` still has
> the same defect. This section is a removal checklist, not a supported design
> to preserve indefinitely.

### Managed-host session cwd is dropped by several headless spawn paths

CodeCompanion already sends the correct absolute `workspace` in
`POST /v1/sessions`. The server validates it, the host launches a dedicated
runner with `OMNIGENT_RUNNER_WORKSPACE=<workspace>`, and the runner passes that
runtime cwd into `_resolve_harness_config`. The observed failure was Codex, but
the break is a broader gap inside Omnigent:

1. `omnigent/runner/app.py::_build_spawn_env_from_spec` receives `cwd`, but its
   built-in `claude-sdk`, `codex`, `cursor`, and `copilot` branches call their
   spawn-env builders without it. The `pi`, `kimi`, and plugin-builder branches
   do pass `cwd`.
2. Those four builders have no `cwd` parameter and never set their respective
   `HARNESS_CLAUDE_SDK_CWD`, `HARNESS_CODEX_CWD`, `HARNESS_CURSOR_CWD`, or
   `HARNESS_COPILOT_CWD` variable.
3. `omnigent/inner/codex_harness.py` reads only `HARNESS_CODEX_CWD` for the
   executor's explicit cwd. It otherwise receives the agent's raw
   `HARNESS_CODEX_OS_ENV`, whose cwd is the placeholder `.`.
4. `CodexExecutor` launches app-server and sends thread/start that relative
   cwd from the runner process's inherited directory (`$HOME` on the managed
   host), not from the session workspace.

The other affected harnesses have the equivalent fallback shape: their explicit
cwd env var is absent and their serialized `os_env.cwd` is still `.`. Omnigent
correctly resolves the same runner workspace for `sys_os_*` resources and native
terminal harnesses. Therefore the upstream bug is not Codex-only; this
dotfiles workaround is Codex-specific because Codex is the harness investigated
and its external launcher gives us a contained interception point.

The current built-in headless harnesses fall into three groups:

- **Affected by the dropped runtime cwd:** `claude-sdk`, `codex`, `cursor`, and
  `copilot`. Installed 0.5.1 produces no corresponding `HARNESS_*_CWD` for all
  four while serializing `os_env.cwd` as `.`.
- **Already recover the workspace:** `pi` and `kimi` receive `cwd` in their
  spawn-env builder; `qwen`, `goose`, `hermes`, and generic `acp` explicitly
  fall back to `OMNIGENT_RUNNER_WORKSPACE` inside their harness wrapper.
- **Not the same cwd contract:** native terminal harnesses launch directly in
  the runner workspace. `openai-agents` and `antigravity` do not expose the
  same workspace-backed CLI/native-tool surface.

The dotfiles workaround deliberately crosses three configuration layers:

- `systemd/omnigent-host.service` pins `HARNESS_CODEX_PATH=%h/bin/codex` so
  Omnigent cannot bypass the dotfiles launcher by discovering
  `/usr/local/bin/codex` directly.
- `omnigent_config/agents/codex/config.yaml` allowlists only the non-secret
  `OMNIGENT_RUNNER_WORKSPACE` variable through CodexExecutor's environment
  scrub.
- `bin/codex` validates that value and `cd -P`s before executing app-server.
  The raw `os_env.cwd: .` then resolves relative to the correct process cwd.

### Native alternatives checked

There is no equivalent supported per-session configuration in Omnigent today:

- `HARNESS_CODEX_CWD` is supported but static in the host environment. The
  host daemon starts before any session workspace exists, and systemd does not
  substitute one environment variable into another per child runner.
- `os_env.cwd` is a static agent boundary/placeholder. A single registered
  agent cannot set it to a different absolute checkout for every session.
  `cwd: ${OMNIGENT_RUNNER_WORKSPACE}` is not an escape hatch: both
  `parse(..., expand_env=True)` and `expand_env=False` leave that field
  literal, as `_parse_os_env` does not expand cwd values.
- `os_env.sandbox.env_passthrough` can allow an existing variable through; it
  cannot assign `HARNESS_CODEX_CWD` from `OMNIGENT_RUNNER_WORKSPACE`.
- `SessionCreateRequest` exposes `workspace` but no per-session environment or
  harness-cwd override. `executor.config` has no supported Codex cwd field.
- A community harness plugin cannot override the built-in `codex` harness id;
  inventing a second custom harness would reproduce core Codex integration and
  be a larger workaround.
- `codex-native-ui` starts in the proper workspace, but it is a terminal-backed
  native session rather than the streamed SDK chat surface used here.

### Upstream fix and cleanup

The upstream fix should make runtime cwd part of the built-in headless-harness
contract, not patch Codex alone:

1. Add `cwd: Path | None` to the affected built-in spawn-env builders.
2. Pass the already-resolved `cwd` from `_build_spawn_env_from_spec`.
3. Set each harness's `HARNESS_*_CWD` when present, with parametrized managed-host
   regression tests for agents declaring `os_env.cwd: .`.

For Codex specifically, that means calling
`_build_codex_spawn_env(spec, cwd=cwd, workdir=workdir)` and emitting
`HARNESS_CODEX_CWD=str(cwd)`.

After a released Omnigent version passes a live check that the actual Codex
app-server `/proc/<pid>/cwd` and persisted `sys_os_shell.cwd` both equal the
session workspace **without the workaround**:

1. Remove the `OMNIGENT_RUNNER_WORKSPACE` block and test-only
   `OMNIGENT_CODEX_REAL_PATH` seam from `bin/codex`, then remove the
   workspace-specific wrapper test file.
2. Remove `OMNIGENT_RUNNER_WORKSPACE` from the Codex agent's
   `env_passthrough`.
3. Remove `HARNESS_CODEX_PATH` from `omnigent-host.service` and from
   `OMNIGENT_RUNNER_ENV_PASSTHROUGH` unless another launcher requirement still
   needs it.
4. Re-run `omnigent-agents-ensure`, restart the host, and repeat the live CWD
   check in both numbered checkouts and concurrent sessions.

The adjacent `HARNESS_CODEX_DISABLE_NATIVE_TOOLS=1` and
`HARNESS_TURN_TIMEOUT_S=1800` settings address a separate issue: native Codex
tool calls are not emitted as Omnigent progress or durable tool items, which
caused the false 240-second idle-watchdog failure. Remove those only after
Omnigent forwards native tool activity and cancellation correctly; they are
not part of the CWD cleanup.

## What will NOT show yet (parity work, non-blocking)

- **Winbar session pill + context-%** — `overrides.lua` `cc_session_id` and
  `codecompanion-stats`/`-chatinfo` read `chat.acp_connection`/`acp_session_id`.
  Omnigent chats use `chat.omnigent_session`/`omnigent_session_id`. Needs a shared
  `lib/codecompanion-session.session_id(chat)` resolver + an omnigent feed for
  `chatinfo.pin` and a `session.usage` → stats feed.
- Resume/fork pickers (broker-specific), elicitation rendering (ACP-bound) —
  later milestones.

## Options to decide

- **Agent(s)**: hardcode `polly`, or add a picker (like your dvsc one) listing
  omnigent agents filtered to `claude-sdk` harness + `model_options`. Start
  hardcoded; add a picker later.
- **Devserver**: set `OMNIGENT_URL` to the Mac-server tunnel when nvim runs on a
  devserver; `host = "auto"` will then resolve the *devserver* host (bind tools to
  where nvim is, not where the server is).
- **Attribution labels** (optional, needs a 1-line plugin tweak): to stamp nvim
  identity onto sessions for Orchest mapping (your `build_client_metadata`
  pattern), add `defaults.labels = { ... }` here and have `session:create` copy
  `d.labels` into the create body (currently it copies model_override /
  reasoning_effort / harness_override only).
- **Async REST**: foreground `create`/`post` remain synchronous because model work
  happens over SSE. Codex Goal GET/mutations use the asynchronous client path
  because those calls may wake an offline native runner.

## Open questions

1. `polly` as the default agent, or a different claude-sdk agent / a picker?
2. Keymap letter (`<leader>aM`?), and do you want a broker-style fresh-vs-resume
   split, or just a single launcher for now?
3. Want the attribution-labels tweak in this pass, or defer?
