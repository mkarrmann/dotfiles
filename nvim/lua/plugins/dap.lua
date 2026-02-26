return {
	{
		"mfussenegger/nvim-dap",
		dependencies = {
			"rcarriga/nvim-dap-ui",
			"nvim-neotest/nvim-nio",
		},
		keys = {
			{ "<leader>db", function() require("dap").toggle_breakpoint() end, desc = "Toggle breakpoint" },
			{ "<leader>dc", function() require("dap").continue() end, desc = "Continue" },
			{ "<leader>dn", function() require("dap").step_over() end, desc = "Step over" },
			{ "<leader>di", function() require("dap").step_into() end, desc = "Step into" },
			{ "<leader>do", function() require("dap").step_out() end, desc = "Step out" },
			{ "<leader>dx", function()
				require("dap").terminate()
				require("dap").clear_breakpoints()
				require("dapui").close()
			end, desc = "Terminate" },
			{ "<leader>dr", function() require("dap").repl.open() end, desc = "REPL" },
			{ "<leader>df", function() require("lib.fdb-dap").fdb_debug() end, desc = "fdb debug (Buck target)" },
		},
		config = function()
			local dap = require("dap")
			local dapui = require("dapui")
			dapui.setup()

			dap.listeners.after.event_initialized["dapui_config"] = function() dapui.open() end

			dap.adapters["lldb-dap"] = {
				type = "executable",
				command = "/opt/llvm/bin/lldb-dap",
			}

			dap.configurations.cpp = {
				{
					name = "Launch (manual binary path)",
					type = "lldb-dap",
					request = "launch",
					program = function()
						return vim.fn.input("Path to executable: ", vim.fn.getcwd() .. "/", "file")
					end,
					cwd = "${workspaceFolder}",
					stopOnEntry = false,
					sourceMap = {
						{ ".", vim.fn.expand("~/fbsource/fbcode") },
						{ ".", vim.fn.expand("~/fbsource") },
						{ "/home/engshare", vim.fn.expand("~/fbsource/fbcode") },
					},
				},
			}
			dap.configurations.c = dap.configurations.cpp
		end,
	},
}
