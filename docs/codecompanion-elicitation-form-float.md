# Design doc: dedicated form-float renderer for CodeCompanion elicitation

**Status:** Proposed — NOT implemented (as of 2026-06-14)
**Author:** mkarrmann (with Claude)
**Date:** 2026-06-14
**Audience:** Future me, or any agent picking this up cold.

---

## TL;DR

`lib/codecompanion-elicitation.lua` patches codecompanion.nvim's ACP `Connection`
to handle the UNSTABLE `elicitation/create` request — the carrier for dm-core's
structured `ask_user_question` tool. Today the renderer multiplexes one question
across N sequential `vim.ui.select` / `vim.ui.input` calls. That design causes
three UX problems (free-form answers, label truncation, wrong-tab placement),
which have **stop-gap fixes already shipped** (see "Already shipped" below).

**Proposal**: replace the render layer with a single dedicated **floating form
window** (built on `nui.nvim`, already a dependency) that shows all questions at
once — full width control, per-field free-form input, correct placement, single
submit. This is the durable fix for all three problems, which share one root
cause (stateless one-question-at-a-time pickers).

**This doc is a proposal only. No form-float code exists yet.** The wire/patch
layer and the stop-gap fixes are real and in the tree; the renderer rewrite
described in "Proposed design" is not.

---

## Background: current architecture

`lib/codecompanion-elicitation.lua` (~450 lines) has two layers:

### Wire / patch layer (KEEP — orthogonal to rendering)
- `patch()` — idempotent monkey-patch of `codecompanion.acp.Connection`:
  1. **Dispatch**: wraps `handle_incoming_request_or_notification` to short-circuit
     `elicitation/create` → `M._handle_elicitation_create`.
  2. **Capability advertisement**: wraps `prepare_adapter` to splice
     `clientCapabilities.elicitation.form = {}` into the `initialize` body. Without
     this, the wrapper suppresses `ask_user_question` entirely.
- `_handle_elicitation_create(conn, msg)` — validates `mode == "form"`, calls
  `focus_owning_tab`, `open_plan_file`, `announce_preamble`, then `ask_schema`, and
  replies `{action="accept", content=...}` or `{action="cancel"}`.
- `focus_owning_tab` / `open_plan_file` / `announce_preamble` — placement + preamble.

### Render layer (REPLACE — ~210 lines)
- `ask_schema` → iterates properties (required-first ordering) → `ask_property`
- `ask_property` → dispatches by JSON-schema type to one of:
  `pick_one` / `pick_one_or_input` / `pick_many` / `ask_input` / boolean picker /
  number input — each a separate `vim.ui.select` / `vim.ui.input` call chained by
  callbacks.

### The wrapper contract (server side, for reference)
The agent side lives in `dvsc-core-acp/packages/acp-wrapper/src/elicitation.ts`
(landed in **D106593967**). `buildElicitationRequest` maps dm-core's
`questions[]` → an `ElicitationSchema`: single-select → `string + enum`,
multi-select → `array` with `items.enum`. Property key = `question.header` or
`q1`/`q2`/…. `elicitationResponseToDecision` maps the response back to a
`ToolCallDecision`.

---

## The three problems

| # | Problem | Root cause |
|---|---------|------------|
| a | No way to give a free-form answer — enum questions box you into the listed options. | `ask_property` sends `string+enum` straight to a closed `vim.ui.select`. |
| b | Long option labels get truncated. | The snacks `select` layout preset (`width 0.5, max_width 100`) clips long sentence-options; the list window doesn't wrap. |
| c | The picker/plan-split opens in whatever tab is focused, not the tab that owns the chat. | The handler drove `vim.ui` in the focused tab without re-focusing the owning tab. |

All three are symptoms of the same thing: **one logical question is spread across
several independent, stateless UI calls**, so there's no single surface to control
width/wrapping, no place for a persistent free-form field, and no single anchor
for placement.

---

## Already shipped (stop-gaps — keep until the form-float lands)

These are in the tree today and are the *interim* fixes:

- **(a)** Free-form escape hatch via sentinel options: `pick_one_or_input` appends
  `✎ Other (type a custom response)…`; `pick_many` has an `✎ add a custom value…`
  entry (`codecompanion-elicitation.lua:68`, `:92`, `:110`).
- **(b)** Snacks `select` layout widened to `width=0.7 / max_width=140`, placed at
  the correct key path `picker.sources.select` (`nvim/lua/plugins/overrides.lua:188`).
  NOTE: the first attempt placed it at `picker.select`, which snacks never reads
  (`picker/config/init.lua` get() resolves per-source overrides from
  `global.sources[opts.source]`) — it was a silent no-op. Fixed.
- **(c)** `focus_owning_tab(conn)` walks tabpages, matches
  `buf_get_chat(bufnr).acp_connection == conn`, and re-focuses that tab before any
  UI opens (`codecompanion-elicitation.lua:316`).

### Confirmed: the (a) free-form approach is ACP-sound

Verified against `elicitation.ts` (D106593967): `elicitationResponseToDecision`
passes `content[key]` through `stringifyContent` into `ToolCallDecision.answers`
with **no validation against the property `enum`**. A typed out-of-enum value
round-trips verbatim into dm-core's `ask_user_question` result, so the agent reads
it exactly like a picked option.

Caveat (spec-purity): returning an out-of-enum value for an `enum` property is
technically non-conforming content. Harmless today; would break only if a future
wrapper starts validating `content` against `requestedSchema` before forwarding.
The spec-pure fix is server-side (emit a plain `string`, or add an explicit
"Other (specify)" affordance to the schema). The form-float keeps the same
verbatim-forward semantics — it just presents the custom field as a first-class row
instead of a magic sentinel.

---

## Proposed design: dedicated form float

Replace the render layer (`ask_schema`, `ask_property`, `pick_one`,
`pick_one_or_input`, `pick_many`, `ask_input`) with one form module backed by
`nui.nvim`. The wire/patch layer is **unchanged**.

### Module shape (recommend splitting for testability)

| Piece | Responsibility | Testable |
|-------|----------------|----------|
| **Model** (pure) | `schema → ordered fields` (reuse required-first ordering); per-field state (single idx / multi set / free text / number / bool / custom); `assemble_content` + required-validation + number parse/validate (moved out of `ask_property`). | Yes — unit-test directly, no UI. |
| **Render** (mostly pure) | fields → buffer lines + highlight ranges + a `line → (field, option)` map; width-aware wrapping (this is what kills (b) permanently). | Mostly. |
| **Window/keymaps** | `nui.Popup` (border, title "Devmate question", footer key-hints); nav (`<Tab>`/`j`/`k`), toggle (`<CR>`/`<Space>`), per-field custom-text entry (fixes (a) as a real row), submit, cancel; `VimResized` relayout; `WinClosed`/`BufLeave` → cancel; **exactly-once** resolve guard. | Hard — drive via simulated keymaps. |

### Field rendering
- single-select (`string+enum`): radio rows `( )` / `(•)`, plus an inline
  `Other: …` editable row.
- multi-select (`array+items.enum`): checkbox rows `[ ]` / `[x]`, plus an
  `+ add custom…` affordance.
- free `string`: editable input region.
- `boolean`: two-option radio.
- `integer`/`number`: input region, parsed + validated on submit.

### Submit / cancel semantics
- Submit assembles `content` exactly as `ask_schema` does today (key order
  preserved, all required fields present) → `{action="accept", content=...}`.
- Esc / `q` / window-closed / focus-loss → `{action="cancel"}`.
- A single-fire guard ensures `conn:send_result` is called **exactly once**.

### Plan-exit elicitations
Keep `open_plan_file` + `focus_owning_tab`. Open the plan in a split in the owning
tab; float the sign-off form over it. The plan stays editable (the agent re-reads
it on proceed).

---

## Effort estimate

Sizing in LOC + complexity + risk (not wall-clock).

**Unchanged:** wire/patch layer (~140 lines).
**Replaced:** ~210 lines of render layer.

| Component | New LOC | Complexity |
|-----------|---------|------------|
| Model | ~150–220 | Low |
| Render | ~120–180 | Medium |
| Window + keymaps | ~120–180 | **High** |
| Test rewrite (current spec is 361 lines, stubs `vim.ui.select/input` — that approach dies) | ~200–280 | Medium |

**Total new/changed:** ≈600–860 LOC, replacing ≈210. ~3–4× the render code, plus
the test rewrite.

**Overall: medium effort.** Most of it is mechanical (model + render); `nui` is
already installed so window boilerplate is small. The real risk concentrates in
window lifecycle.

### Where the risk actually is
1. **Exactly-once `conn:send_result`** — today's sequential pickers make this
   trivial; a float dismissed via `:q` / `<Esc>` / focus-loss / submit must resolve
   once and only once. #1 source of "agent hangs / double-reply" bugs.
2. **Cancel/close races** — `WinClosed`/`BufLeave` autocmds racing the submit keymap.
3. **Re-entrancy** — two overlapping elicitations need an explicit queue (today
   they serialize for free).

---

## What this supersedes / keeps

- **(b) snacks width bump → moot.** The float owns its width/wrapping and bypasses
  snacks. The `overrides.lua` override can be removed once the float lands
  (or kept harmlessly for other `vim.ui.select` callers).
- **(a) sentinel escape-hatch → replaced** by a first-class per-field custom-input
  row. Same verbatim-forward semantics (confirmed above).
- **(c) `focus_owning_tab` → still needed** (to place the plan split + float in the
  owning tab). Keep it.

---

## Scope reducers (if trimming)

- **Skip multi-select-in-form initially** — keep the existing re-entrant picker for
  `array` types; cuts model + render complexity (multi-select is the rarest
  `ask_user_question` shape).
- **Lean on `nui.Menu`** for option lists instead of hand-rolling the
  `line → option` map — trades some layout control for less render code.

---

## Open questions

1. Float-over-plan-split vs. side-by-side layout for plan-exit sign-off?
2. Keep the widened snacks `select` override for non-elicitation `vim.ui.select`
   callers, or revert it once the float lands?
3. Do we want fuzzy-filter for very long option lists (snacks gives this for free
   today; a hand-rolled float loses it)? Probably not needed for `ask_user_question`.
