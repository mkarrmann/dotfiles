# Dotfiles Omnigent adapter — plan (IMPLEMENTED)

What to add to `nvim/lua/plugins/codecompanion.lua` to launch a native Omnigent
chat, now that the plugin ships the `omnigent` adapter family (see
`~/repos/codecompanion.nvim/.codecompanion/omnigent-native-progress.md`).

This is the last mile to a **working foreground chat**. It's small: an adapter
definition + a launch keymap. Everything else is parity/polish.

> **STATUS: done.** `config.adapters.omnigent` (agent `polly`, `OMNIGENT_URL`,
> `host="auto"`, create-time `labels`) and the `<leader>aM` launcher are in
> `nvim/lua/plugins/codecompanion.lua`. Resolved decisions below (agent = `polly`,
> keymap = `<leader>aM`, labels = implemented). **One outstanding fix:** the
> in-place adapter-swap in `tab_chat_set_adapter`'s `apply()` must
> `stop_stream()` an outgoing omnigent session (it only disconnects ACP today),
> or the SSE stream leaks when swapping away from an omnigent chat. Remaining
> parity work (winbar pill / context-%) is tracked as "C. Lib parity" in
> `omnigent-native-roadmap.md`.

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
          -- Use a claude-sdk agent: it streams output_text + emits elicitations.
          -- claude-native-ui (terminal harness) completes turns but streams NO
          -- text to CodeCompanion (its output goes to the tmux terminal).
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

## What works immediately

- One-chat-per-tab, read-only buffer + input queue (the handler now fires
  `CodeCompanionRequestStarted/Finished`, which the queue advances on).
- Streaming assistant text, cancel (posts an interrupt; the durable session
  survives), `Chat:close` tears down only the local SSE stream.
- `/model` picker once a session advertises `model_options`.
- Existing ACP/broker adapters are untouched.

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
- **Async REST**: not needed. `create`/`post` return in <1s (the agent's thinking
  happens over the async SSE stream), so there's no editor freeze like the ACP
  `session/new` case — the `async_utils.sync` wrap you applied to `_submit_acp`
  isn't required here.

## Open questions

1. `polly` as the default agent, or a different claude-sdk agent / a picker?
2. Keymap letter (`<leader>aM`?), and do you want a broker-style fresh-vs-resume
   split, or just a single launcher for now?
3. Want the attribution-labels tweak in this pass, or defer?
