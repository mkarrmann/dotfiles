-- Headless test for lib.tscontext-perf.
-- nvim --headless -u NONE --cmd "set rtp+=$HOME/dotfiles/nvim" -c "lua require('lib.test.tscontext-perf-spec').run()" -c "qa!"

local M = {}

local PLUGIN_GROUP = "treesitter_context_update"
local OUR_GROUP = "tscontext_perf"
local EVENT = "DiagnosticChanged"

local function assert_eq(actual, expected, label)
	if actual ~= expected then
		error(string.format("%s: expected %s, got %s", label, vim.inspect(expected), vim.inspect(actual)))
	end
end

local function autocmd_count(group)
	local ok, autocmds = pcall(vim.api.nvim_get_autocmds, { group = group, event = EVENT })
	if not ok then
		return 0
	end
	return #autocmds
end

-- Stand in for the plugin's own subscription and record what it receives.
local function fake_plugin()
	local seen = {}
	vim.api.nvim_create_autocmd(EVENT, {
		group = vim.api.nvim_create_augroup(PLUGIN_GROUP, { clear = true }),
		callback = function(args)
			table.insert(seen, args.event)
		end,
	})
	return seen
end

local function fresh()
	pcall(vim.api.nvim_del_augroup_by_name, OUR_GROUP)
	package.loaded["lib.tscontext-perf"] = nil
	local perf = require("lib.tscontext-perf")
	perf.DEBOUNCE_MS = 10
	return perf
end

local function emit()
	vim.api.nvim_exec_autocmds(EVENT, { modeline = false })
end

function M.run()
	local ok, err = xpcall(function()
		-- A storm of diagnostics collapses into a single deferred refresh.
		do
			local seen = fake_plugin()
			local perf = fresh()
			assert_eq(perf.setup(), true, "setup adopts the plugin subscription")
			assert_eq(autocmd_count(PLUGIN_GROUP), 0, "plugin subscription removed")
			assert_eq(autocmd_count(OUR_GROUP), 1, "debounced subscription installed")

			for _ = 1, 30 do
				emit()
			end
			assert_eq(#seen, 0, "no synchronous refresh while diagnostics churn")

			vim.wait(300, function()
				return #seen > 0
			end)
			assert_eq(#seen, 1, "30 publishes collapse into 1 refresh")
			assert_eq(seen[1], EVENT, "plugin callback sees a DiagnosticChanged update")
		end

		-- The refresh still fires for a lone diagnostics publish.
		do
			local seen = fake_plugin()
			local perf = fresh()
			assert_eq(perf.setup(), true, "setup")
			emit()
			vim.wait(300, function()
				return #seen > 0
			end)
			assert_eq(#seen, 1, "single publish still refreshes")
		end

		-- setup() is idempotent.
		do
			fake_plugin()
			local perf = fresh()
			assert_eq(perf.setup(), true, "first setup")
			assert_eq(perf.setup(), true, "second setup is a no-op")
			assert_eq(autocmd_count(OUR_GROUP), 1, "no duplicate debounced subscription")
		end

		-- If the plugin re-registers (TSContextToggle / a later setup call),
		-- the next flush re-adopts instead of leaving two live subscriptions.
		do
			local seen = fake_plugin()
			local perf = fresh()
			assert_eq(perf.setup(), true, "setup")
			local reregistered = fake_plugin()
			assert_eq(autocmd_count(PLUGIN_GROUP), 1, "plugin re-registered")

			-- The re-registered subscription is live again, so this publish also
			-- reaches it synchronously; the deferred flush then re-adopts it.
			emit()
			assert_eq(#reregistered, 1, "live plugin subscription still fires directly")
			vim.wait(300, function()
				return autocmd_count(PLUGIN_GROUP) == 0
			end)
			assert_eq(autocmd_count(PLUGIN_GROUP), 0, "re-registered subscription re-adopted")
			assert_eq(#reregistered, 2, "flush routed to the current plugin callback")
			assert_eq(#seen, 0, "stale callback is not invoked")
		end

		-- teardown() hands the subscription back, restoring synchronous refresh.
		do
			local seen = fake_plugin()
			local perf = fresh()
			assert_eq(perf.setup(), true, "setup")
			assert_eq(perf.teardown(), true, "teardown reports restored")
			assert_eq(autocmd_count(OUR_GROUP), 0, "debounced subscription removed")
			assert_eq(autocmd_count(PLUGIN_GROUP), 1, "plugin subscription handed back")

			emit()
			assert_eq(#seen, 1, "refresh is synchronous again after teardown")
			assert_eq(perf.teardown(), false, "teardown is idempotent")
		end

		-- Absent the plugin, setup reports failure rather than silently doing nothing.
		do
			pcall(vim.api.nvim_del_augroup_by_name, PLUGIN_GROUP)
			local perf = fresh()
			assert_eq(perf.setup(), false, "setup fails loudly when the plugin is absent")
			assert_eq(autocmd_count(OUR_GROUP), 0, "no subscription installed on failure")
		end
	end, debug.traceback)

	pcall(vim.api.nvim_del_augroup_by_name, PLUGIN_GROUP)
	pcall(vim.api.nvim_del_augroup_by_name, OUR_GROUP)

	if not ok then
		error(err)
	end
	print("tscontext-perf-spec: all checks passed")
end

return M
