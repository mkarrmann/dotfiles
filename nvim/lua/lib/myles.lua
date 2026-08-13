-- Myles file picker with a stall watchdog.
--
-- Telescope's async_job_finder streams `myles --list` until the process closes
-- stdout, and the myles client has no timeout of its own. A wedged daemon is
-- therefore indistinguishable from a query that simply matched nothing: the
-- picker sits at zero results forever, silently. This wraps the stock meta.nvim
-- extension so an in-flight search reports its elapsed time in the prompt title
-- and, past a stall threshold, offers an in-picker daemon restart.

local M = {}
local repo_context = require("lib.repo-context")

local PID_FILE = vim.fn.expand("~/.local/share/myles/pid")
local TICK_MS = 250
-- Sub-second searches are the norm; don't flicker the title for those.
local SHOW_ELAPSED_MS = 1000
-- Deliberately above a cold daemon's per-repo indexing cost (daemon_scm_state_init_ms
-- has been logged at ~16s for a fresh fbsource checkout) so the hint is a suggestion
-- rather than a verdict: a slow first query outlives it without anything being broken.
local STALL_MS = 10000

local function daemon_pid()
	local ok, lines = pcall(vim.fn.readfile, PID_FILE)
	if not ok or not lines or not lines[1] then
		return nil
	end
	local pid = tonumber(vim.trim(lines[1]))
	if pid and vim.uv.fs_stat("/proc/" .. pid) then
		return pid
	end
	return nil
end

function M.restart(on_done)
	vim.notify("Restarting Myles daemon…", vim.log.levels.WARN)
	local before = daemon_pid()
	vim.system({ "myles", "restart" }, { text = true }, function(res)
		vim.schedule(function()
			-- `myles restart` kills the old daemon, then loses a race to any client
			-- that respawns one first and exits non-zero on the daemon lock. A fresh
			-- live pid means the restart achieved what was asked regardless.
			local after = daemon_pid()
			if res.code == 0 or (after ~= nil and after ~= before) then
				vim.notify("Myles daemon restarted (pid " .. tostring(after) .. ")", vim.log.levels.INFO)
				if on_done then
					on_done()
				end
			else
				vim.notify(
					"myles restart failed (exit " .. res.code .. ")\n" .. (res.stderr or ""),
					vim.log.levels.ERROR
				)
			end
		end)
	end)
end

local function attach_watchdog(prompt_bufnr, map)
	local action_state = require("telescope.actions.state")
	local picker = action_state.get_current_picker(prompt_bufnr)
	if not picker then
		return
	end

	local base_title = picker.prompt_title or "Find Files Using Myles"
	-- default_text seeds the prompt before this attaches, so that first search would
	-- otherwise never be timed — exactly the search that follows a restart.
	local started_at = (picker.default_text or "") ~= "" and vim.uv.now() or nil
	local shown_title

	local function set_title(text)
		if shown_title == text then
			return
		end
		shown_title = text
		pcall(function()
			picker.prompt_border:change_title(text)
		end)
	end

	-- Fires when the myles process closes stdout, which is the only reliable
	-- "search finished" signal: an empty result set looks the same as a hang.
	picker:register_completion_callback(function()
		started_at = nil
		set_title(base_title)
	end)

	vim.api.nvim_buf_attach(prompt_bufnr, false, {
		on_lines = function()
			if picker.closed then
				return true
			end
			started_at = vim.uv.now()
			return false
		end,
	})

	local timer = vim.uv.new_timer()
	timer:start(
		TICK_MS,
		TICK_MS,
		vim.schedule_wrap(function()
			if picker.closed or not vim.api.nvim_buf_is_valid(prompt_bufnr) then
				timer:stop()
				if not timer:is_closing() then
					timer:close()
				end
				return
			end
			if not started_at then
				return
			end

			local elapsed = vim.uv.now() - started_at
			local has_results = picker.manager ~= nil and picker.manager:num_results() > 0
			if elapsed < SHOW_ELAPSED_MS then
				set_title(base_title)
			elseif elapsed < STALL_MS or has_results then
				set_title(string.format("%s — %.1fs", base_title, elapsed / 1000))
			else
				set_title(string.format("Myles %ds — <C-g> restarts the daemon", math.floor(elapsed / 1000)))
			end
		end)
	)

	map({ "i", "n" }, "<C-g>", function()
		local prompt = picker:_get_prompt()
		-- Reopening beats Picker:refresh here: closing the picker is what tears down
		-- the wedged job (and with it the orphaned client), and the reopened picker
		-- gets a finder that is not carrying any state from the dead daemon.
		require("telescope.actions").close(prompt_bufnr)
		M.restart(function()
			M.pick({ default_text = prompt })
		end)
	end)
end

function M.pick(opts)
	require("telescope").extensions.myles.myles(opts or {})
end

function M.setup()
	local extension = require("telescope").extensions.myles
	if not extension._repo_context_original then
		extension._repo_context_original = extension.myles
		extension.myles = function(opts)
			opts = opts or {}
			local function pick(cwd)
				local picker_opts = vim.tbl_deep_extend("force", opts, {
					cwd = cwd,
					attach_mappings = function(prompt_bufnr, map)
						attach_watchdog(prompt_bufnr, map)
						return true
					end,
				})
				extension._repo_context_original(picker_opts)
			end

			if opts.cwd then
				pick(opts.cwd)
			else
				repo_context.with_context(function(context)
					pick(context.workdir)
				end)
			end
		end
	end

	vim.api.nvim_create_user_command("MylesRestart", function()
		M.restart()
	end, { desc = "Restart the Myles daemon" })
end

return M
