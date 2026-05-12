-- Snacks dashboard integration for Agent Manager
-- Adds agent status side panes and a keybinding to launch the full TUI.

local dashboard_bin = vim.fn.expand("~/.claude/agent-manager/bin/dashboard.py")

return {
	{
		"snacks.nvim",
		enabled = false,
		opts = {
			dashboard = {
				width = 60,
				pane_gap = 4,
				preset = {
					keys = {
						{ icon = " ", key = "f", desc = "Find File", action = ":lua Snacks.dashboard.pick('files')" },
						{ icon = " ", key = "n", desc = "New File", action = ":ene | startinsert" },
						{ icon = " ", key = "g", desc = "Find Text", action = ":lua Snacks.dashboard.pick('live_grep')" },
						{ icon = " ", key = "r", desc = "Recent Files", action = ":lua Snacks.dashboard.pick('oldfiles')" },
						{ icon = " ", key = "c", desc = "Config", action = ":lua Snacks.dashboard.pick('files', {cwd = vim.fn.stdpath('config')})" },
						{ icon = " ", key = "s", desc = "Restore Session", section = "session" },
						{ icon = " ", key = "x", desc = "Lazy Extras", action = ":LazyExtras" },
						{ icon = "󰒲 ", key = "l", desc = "Lazy", action = ":Lazy" },
						{ icon = " ", key = "q", desc = "Quit", action = ":qa" },
						{
							icon = "🤖 ",
							key = "a",
							desc = "Agent Dashboard",
							action = function()
								vim.cmd("terminal python3 " .. dashboard_bin)
								vim.cmd("startinsert")
							end,
						},
					},
				},
				sections = {
					-- Pane 1 (left): Local agents
					{
						pane = 1,
						section = "terminal",
						cmd = "python3 " .. dashboard_bin .. " --summary --local",
						height = 18,
						ttl = 30,
						padding = { 1, 0 },
						enabled = function()
							return vim.fn.filereadable(dashboard_bin) == 1
						end,
					},

					-- Pane 2 (center): Standard LazyVim dashboard
					{ pane = 2, section = "header" },
					{ pane = 2, section = "keys", gap = 1, padding = 1 },
					{ pane = 2, section = "startup" },

					-- Pane 3 (right): Remote agents
					{
						pane = 3,
						section = "terminal",
						cmd = "python3 " .. dashboard_bin .. " --summary --remote",
						height = 18,
						ttl = 30,
						padding = { 1, 0 },
						enabled = function()
							return vim.fn.filereadable(dashboard_bin) == 1
						end,
					},
				},
			},
		},
	},
}
