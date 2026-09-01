-- HACK: debounce nvim-treesitter-context's DiagnosticChanged subscription.
--
-- WHAT IT DOES
-- Steals the plugin's own DiagnosticChanged autocmd, deletes the registration,
-- and re-invokes the captured callback on a DEBOUNCE_MS trailing timer. The
-- plugin's callback is reused verbatim rather than reimplemented, so its guards
-- and force-highlight behaviour are preserved. Only the timing changes. Every
-- other refresh trigger (CursorMoved, WinScrolled, BufEnter, ...) is untouched
-- and still repaints synchronously.
--
-- WHY
-- pyrefly@meta publishes diagnostics on roughly every other keystroke -- 76
-- notifications per 120 keypresses, measured. Each one drove a full
-- treesitter-context update, which on a 2500-line Python buffer blocked the
-- main loop for ~40ms per keypress. Typing an fbcode Python file was visibly
-- laggy while `:enew` was not. Measured on the devserver, no network in path:
--
--   :enew scratch buffer          p50   0.5ms
--   PlanBuilder.cpp  (2878 lines) p50   6.0ms
--   *.py             (2572 lines) p50  45-100ms   p95 105-287ms   max 308ms
--   *.py with this hack           p50   7-10ms    p95  23ms
--
-- WHY THIS IS A HACK, NOT A FIX
--   1. It depends on three of the plugin's private details: the augroup name
--      `treesitter_context_update`, the fact that its DiagnosticChanged
--      callback is safe to call directly, and the fact that its `au_update`
--      reads only `args.event` and `args.match` (we synthesise the args table).
--      None of these are a supported interface.
--   2. The defect is upstream -- either treesitter-context doing unbounded work
--      on a high-frequency event, or pyrefly@meta publishing that often. This
--      file compensates for someone else's bug from inside personal dotfiles.
--   3. The mechanism is NOT understood. See below.
--
-- WHAT IS NOT EXPLAINED
-- Direct instrumentation attributed only ~2% of the cost: across 120
-- keystrokes, `context.get` took 109ms total (35 calls) and `render.open`
-- 0.6ms (2 calls), against ~5.9s of measured latency. Also ruled out by
-- measurement: diagnostic count (typing inside a comment with 0 diagnostics
-- present is equally slow), semantic tokens, inlay hints, `update_in_insert`
-- (already false), diagnostic virtual_text/signs/underline rendering,
-- URI->buffer churn, and completion. Removing the other three
-- DiagnosticChanged listeners (satellite, trouble, nvim.diagnostic.status)
-- changed nothing.
--
-- So the remaining ~98% is somewhere in this subscription's path and has not
-- been located. What is solid is the correlation, reproduced across five
-- independent runs: debounce or remove this subscription and p50 goes 45ms ->
-- ~7ms. This code rests on that evidence, not on an explanation. An accurate
-- upstream bug report is not possible until the gap is closed.
--
-- LIMITATIONS
--   - Diagnostic highlighting inside the 1-5 sticky header lines lags by up to
--     DEBOUNCE_MS after you stop typing. Nothing else is deferred.
--   - DEBOUNCE_MS is not derived from anything; it is a guess that measured
--     well. Without the mechanism there is no principled value.
--   - If the plugin renames the augroup, setup() returns false and the wiring
--     in plugins/overrides.lua warns. It degrades to the old slow behaviour
--     rather than breaking, but it does degrade.
--
-- REMOVE THIS WHEN
-- The missing 98% is identified and fixed upstream, or treesitter-context
-- debounces DiagnosticChanged itself, or pyrefly@meta stops publishing per
-- keystroke. The supported alternative, if carrying this stops being worth it,
-- is the plugin's own `on_attach` hook to skip large buffers entirely -- fewer
-- moving parts, but it drops the sticky header on big files.
--
-- Full investigation, method, and raw numbers:
--   ~/dotfiles/docs/nvim-typing-latency-investigation.md
-- Reproduce:  ~/bin/nvim-keystroke-bench --compare <file>

local M = {}

M.DEBOUNCE_MS = 250

local PLUGIN_GROUP = "treesitter_context_update"
local OUR_GROUP = "tscontext_perf"
local EVENT = "DiagnosticChanged"

local state = {
	patched = false,
	callback = nil,
	timer = nil,
}

local function plugin_autocmd()
	local ok, autocmds = pcall(vim.api.nvim_get_autocmds, { group = PLUGIN_GROUP, event = EVENT })
	if not ok then
		return nil
	end
	return autocmds[1]
end

-- Take ownership of the plugin's subscription. Re-runs on every flush because
-- the plugin recreates its whole augroup in enable(), which TSContextToggle and
-- any later setup() call go through.
local function adopt()
	local autocmd = plugin_autocmd()
	if not autocmd or type(autocmd.callback) ~= "function" then
		return false
	end
	state.callback = autocmd.callback
	pcall(vim.api.nvim_del_autocmd, autocmd.id)
	return true
end

local function cancel()
	local timer = state.timer
	state.timer = nil
	if timer and not timer:is_closing() then
		timer:stop()
		timer:close()
	end
end

function M.flush()
	state.timer = nil
	adopt()
	if type(state.callback) ~= "function" then
		return
	end
	-- Synthesised args: au_update reads only .event and .match.
	pcall(state.callback, { event = EVENT, buf = vim.api.nvim_get_current_buf() })
end

---@return boolean patched
function M.setup()
	if state.patched then
		return true
	end
	if not adopt() then
		return false
	end

	vim.api.nvim_create_autocmd(EVENT, {
		group = vim.api.nvim_create_augroup(OUR_GROUP, { clear = true }),
		desc = "Debounced treesitter-context refresh (HACK, see lib.tscontext-perf)",
		callback = function()
			cancel()
			state.timer = vim.defer_fn(M.flush, M.DEBOUNCE_MS)
		end,
	})

	state.patched = true
	return true
end

-- Hand the subscription back to the plugin. Exists so the hack is reversible at
-- runtime and so `nvim-keystroke-bench --compare` can A/B it in one session.
---@return boolean restored
function M.teardown()
	if not state.patched then
		return false
	end
	cancel()
	pcall(vim.api.nvim_del_augroup_by_name, OUR_GROUP)
	if type(state.callback) == "function" and not plugin_autocmd() then
		vim.api.nvim_create_autocmd(EVENT, {
			group = vim.api.nvim_create_augroup(PLUGIN_GROUP, { clear = false }),
			callback = state.callback,
		})
	end
	state.patched = false
	return true
end

-- Test seam.
function M._state()
	return state
end

return M
