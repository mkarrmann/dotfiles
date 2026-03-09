return {
	{
		"pittcat/codex.nvim",
		keys = {
			{ "<leader>ao", "<cmd>CodexToggle<cr>", desc = "Toggle Codex" },
			{ "<leader>aO", "<cmd>CodexOpen<cr>", desc = "Open Codex" },
			{ "<leader>ac", "<cmd>CodexResume<cr>", desc = "Resume Codex Session" },
			{ "<leader>aC", "<cmd>CodexResumeLast<cr>", desc = "Resume Last Codex Session" },
			{ "<leader>ak", "<cmd>CodexFork<cr>", desc = "Fork Codex Session" },
			{ "<leader>aK", "<cmd>CodexForkLast<cr>", desc = "Fork Last Codex Session" },
			{ "<leader>am", "<cmd>CodexName<cr>", desc = "Name Codex Session" },
			{ "<leader>aM", ":'<,'>CodexSendReference<cr>", mode = "v", desc = "Send reference to Codex" },
		},
		opts = {
			terminal = {
				provider = "auto",
				direction = "vertical",
				position = "right",
				size = 0.35,
			},
			terminal_bridge = {
				auto_attach = true,
				path_format = "rel",
				path_prefix = "@",
				selection_mode = "reference",
			},
			extra_args = {
				"--no-alt-screen",
			},
			env = {
				EDITOR = vim.fn.expand("~/bin/nvim-edit-in-tab"),
			},
		},
		init = function()
			local agent_session = require("lib.agent-session")

			local function first_positional_arg_with_index(args)
				for idx, arg in ipairs(args or {}) do
					local trimmed = tostring(arg or ""):gsub("^%s+", ""):gsub("%s+$", "")
					if trimmed ~= "" and trimmed:sub(1, 1) ~= "-" then
						return trimmed, idx
					end
				end
				return nil, nil
			end

			local function run_codex_with_subcommand(subcommand, args)
				local ok_lazy, lazy = pcall(require, "lazy")
				if ok_lazy then
					lazy.load({ plugins = { "pittcat/codex.nvim" } })
				end

				local ok_codex, codex = pcall(require, "codex")
				local ok_utils, utils = pcall(require, "codex.utils")
				local ok_term, terminal = pcall(require, "codex.terminal")

				if not (ok_codex and ok_utils and ok_term) then
					vim.notify("codex.nvim is not available", vim.log.levels.ERROR)
					return
				end

				local cfg = codex.state.opts or {}
				local tcfg = cfg.terminal or {}
				local bridge_cfg = cfg.terminal_bridge or {}
				local cwd = utils.get_cwd(cfg.cwd_provider)
				local cmd_args = vim.deepcopy(args or {})

				local label_name = nil
				local known_sid = nil
				local target_idx = nil
				local renamed = false
				if subcommand == "resume" then
					local target
					target, target_idx = first_positional_arg_with_index(cmd_args)
					if target then
						if agent_session.is_uuid(target) then
							local mapped_name, mapped_cwd = agent_session.codex_name_for_sid(target)
							label_name = mapped_name or ("codex:" .. target:sub(1, 8))
							known_sid = target
							if mapped_cwd and mapped_cwd ~= "" then
								cwd = mapped_cwd
							end
						else
							local sid, mapped_cwd = agent_session.codex_sid_for_name(target)
							label_name = target
							known_sid = sid
							if mapped_cwd and mapped_cwd ~= "" then
								cwd = mapped_cwd
							end
						end
					end
					if label_name then
						renamed = agent_session.rename_tmux_window(label_name)
					end
				elseif subcommand == "fork" then
					local target
					target, target_idx = first_positional_arg_with_index(cmd_args)
					if target and agent_session.is_uuid(target) then
						local mapped_name, mapped_cwd = agent_session.codex_name_for_sid(target)
						label_name = mapped_name or ("codex:" .. target:sub(1, 8))
						known_sid = target
						if mapped_cwd and mapped_cwd ~= "" then
							cwd = mapped_cwd
						end
					elseif target then
						label_name = target
						local sid, mapped_cwd = agent_session.codex_sid_for_name(target)
						known_sid = sid
						if mapped_cwd and mapped_cwd ~= "" then
							cwd = mapped_cwd
						end
					end
					if label_name then
						renamed = agent_session.rename_tmux_window(label_name)
					end
				end
				if known_sid and target_idx and cmd_args[target_idx] then
					cmd_args[target_idx] = known_sid
				end

				local cmd = { cfg.bin or "codex", subcommand }
				for _, extra in ipairs(cfg.extra_args or {}) do
					table.insert(cmd, extra)
				end
				for _, arg in ipairs(cmd_args) do
					table.insert(cmd, arg)
				end

				local baseline_sid = agent_session.latest_codex_sid()

				terminal.run(cmd, {
					direction = tcfg.direction or "horizontal",
					size = tcfg.size or 15,
					position = tcfg.position,
					provider = tcfg.provider or "native",
					auto_insert = tcfg.auto_insert_mode ~= false,
					fix_display_corruption = tcfg.fix_display_corruption == true,
					reuse = tcfg.reuse ~= false,
					cwd = cwd,
					env = cfg.env or {},
					alert_on_exit = cfg.alert_on_exit == true,
					alert_on_idle = cfg.alert_on_idle == true,
					notification = cfg.notification,
					terminal_bridge_auto_attach = bridge_cfg.auto_attach ~= false,
				})

				if label_name and renamed then
					if subcommand == "fork" then
						agent_session.capture_new_codex_sid(label_name, cwd, baseline_sid)
					elseif known_sid then
						agent_session.upsert_codex_session(label_name, known_sid, cwd)
					else
						agent_session.capture_new_codex_sid(label_name, cwd, baseline_sid)
					end
				end
			end

			vim.api.nvim_create_user_command("CodexAdd", function()
				vim.cmd("CodexSendPath")
			end, { desc = "Add current file path to Codex" })
			vim.api.nvim_create_user_command("CodexSend", function()
				vim.cmd("'<,'>CodexSendSelection")
			end, { range = true, desc = "Send visual selection to Codex" })
			vim.api.nvim_create_user_command("CodexTreeAdd", function()
				vim.cmd("CodexSendPath")
			end, { desc = "Add current tree file path to Codex" })
			vim.api.nvim_create_user_command("CodexResume", function(opts)
				run_codex_with_subcommand("resume", opts.fargs)
			end, { nargs = "*", desc = "Resume Codex session (opens picker without args)" })
			vim.api.nvim_create_user_command("CodexResumeLast", function()
				run_codex_with_subcommand("resume", { "--last" })
			end, { desc = "Resume most recent Codex session" })
			vim.api.nvim_create_user_command("CodexFork", function(opts)
				run_codex_with_subcommand("fork", opts.fargs)
			end, { nargs = "*", desc = "Fork Codex session (opens picker without args)" })
			vim.api.nvim_create_user_command("CodexForkLast", function()
				run_codex_with_subcommand("fork", { "--last" })
			end, { desc = "Fork most recent Codex session" })
			vim.api.nvim_create_user_command("CodexName", function(opts)
				local function apply_name(raw_name)
					local name = tostring(raw_name or ""):gsub("^%s+", ""):gsub("%s+$", "")
					if name == "" then
						vim.notify("Usage: CodexName <name>", vim.log.levels.WARN)
						return
					end
					local sid = agent_session.latest_codex_sid()
					local cwd = vim.fn.getcwd()
					if sid and agent_session.is_uuid(sid) then
						agent_session.upsert_codex_session(name, sid, cwd)
					end
					agent_session.rename_tmux_window(name)
					vim.notify("Codex window named '" .. name .. "'")
				end

				if opts.args and opts.args ~= "" then
					apply_name(opts.args)
					return
				end

				vim.ui.input({ prompt = "Codex session name: " }, function(input)
					if input and input ~= "" then
						apply_name(input)
					end
				end)
			end, { nargs = "*", desc = "Name current Codex window and map it to latest Codex session" })
		end,
	},
}
