-- Headless smoke test for lib.codecompanion-elicitation.
-- Run with:
--   nvim --headless -u NONE \
--     --cmd "set rtp+=$HOME/dotfiles/nvim" \
--     -c "lua require('lib.test.codecompanion-elicitation-spec').run()" \
--     -c "qa!"

local M = {}

local function assertEq(actual, expected, label)
	if not vim.deep_equal(actual, expected) then
		error(string.format("%s: expected %s, got %s",
			label, vim.inspect(expected), vim.inspect(actual)))
	end
end

local function assert_callable(value, label)
	if type(value) ~= "function" then
		error(string.format("%s: expected function, got %s", label, type(value)))
	end
end

-- Stub out vim.ui so the renderer can be driven deterministically.
-- Each pending answer is queued; the renderer consumes them in order
-- via vim.ui.select / vim.ui.input invocations. Returning nil from the
-- queue triggers the cancel path.
local function install_ui_stub(answers)
	local idx = 0
	local function next_answer()
		idx = idx + 1
		return answers[idx]
	end
	local orig = { select = vim.ui.select, input = vim.ui.input }
	vim.ui.select = function(items, _opts, callback)
		local a = next_answer()
		if a == nil then
			callback(nil)
		elseif type(a) == "number" then
			-- Index-by-number lets the test pick a specific entry by position.
			callback(items[a], a)
		else
			-- String matches by value.
			for i, item in ipairs(items) do
				if item == a then
					callback(item, i)
					return
				end
			end
			callback(nil)
		end
	end
	vim.ui.input = function(_opts, callback)
		callback(next_answer())
	end
	return function() vim.ui = orig end
end

function M.run()
	local elicit = require("lib.codecompanion-elicitation")

	assert_callable(elicit.patch, "patch")
	assert_callable(elicit._internal.ask_property, "ask_property")
	assert_callable(elicit._internal.ask_schema, "ask_schema")
	assert_callable(elicit._handle_elicitation_create, "_handle_elicitation_create")

	-- ─── ask_schema: single-select string + enum ────────────────────
	do
		local restore = install_ui_stub({ "REST" })
		local out
		elicit._internal.ask_schema({
			properties = {
				api = { type = "string", title = "Which API?", enum = { "REST", "GraphQL" } },
			},
			required = { "api" },
		}, function(c) out = c end)
		restore()
		assertEq(out, { api = "REST" }, "single-select picks string from enum")
	end

	-- ─── ask_schema: cancellation ───────────────────────────────────
	do
		local restore = install_ui_stub({ nil })
		local out = "sentinel"
		elicit._internal.ask_schema({
			properties = {
				api = { type = "string", title = "Which API?", enum = { "a", "b" } },
			},
			required = { "api" },
		}, function(c) out = c end)
		restore()
		assertEq(out, nil, "cancel on a picker returns nil content")
	end

	-- ─── ask_schema: boolean ────────────────────────────────────────
	do
		local restore = install_ui_stub({ "true" })
		local out
		elicit._internal.ask_schema({
			properties = { yn = { type = "boolean", title = "Yes or no?" } },
			required = { "yn" },
		}, function(c) out = c end)
		restore()
		assertEq(out, { yn = true }, "boolean picker yields true")
	end

	-- ─── ask_schema: integer with parse ─────────────────────────────
	do
		local restore = install_ui_stub({ "42" })
		local out
		elicit._internal.ask_schema({
			properties = { n = { type = "integer", title = "How many?" } },
			required = { "n" },
		}, function(c) out = c end)
		restore()
		assertEq(out, { n = 42 }, "integer parses input")
	end

	-- ─── ask_schema: array (multi-select) ───────────────────────────
	-- Sequence: pick "rust" → step → pick "ts" → step → pick "[done]"
	do
		local restore = install_ui_stub({ "rust", "ts", "[done — submit selections]" })
		local out
		elicit._internal.ask_schema({
			properties = {
				langs = {
					type = "array",
					title = "Languages",
					items = { type = "string", enum = { "rust", "ts", "python" } },
				},
			},
			required = { "langs" },
		}, function(c) out = c end)
		restore()
		-- Multi-select returns an array; order matches selection order.
		assertEq(out, { langs = { "rust", "ts" } }, "multi-select collects picks until done")
	end

	-- ─── ask_schema: respects required order, then unordered ───────
	do
		local restore = install_ui_stub({ "x", "y" })
		local out
		elicit._internal.ask_schema({
			properties = {
				second = { type = "string", title = "Second?", enum = { "y" } },
				first  = { type = "string", title = "First?",  enum = { "x" } },
			},
			required = { "first", "second" },
		}, function(c) out = c end)
		restore()
		assertEq(out, { first = "x", second = "y" }, "required[] order drives prompt order")
	end

	-- ─── _handle_elicitation_create: end-to-end via fake Connection ─
	do
		local sent = {}
		local fake_conn = {
			send_result = function(self, id, result)
				sent[#sent + 1] = { kind = "result", id = id, result = result }
			end,
			send_error = function(self, id, message, code)
				sent[#sent + 1] = { kind = "error", id = id, message = message, code = code }
			end,
		}
		local restore = install_ui_stub({ "REST" })
		elicit._handle_elicitation_create(fake_conn, {
			id = 7,
			method = "elicitation/create",
			params = {
				mode = "form",
				message = "Pick a stack",
				requestedSchema = {
					properties = {
						api = { type = "string", title = "API style?", enum = { "REST", "GraphQL" } },
					},
					required = { "api" },
				},
			},
		})
		-- vim.schedule defers; flush.
		vim.wait(200, function() return #sent > 0 end)
		restore()
		if #sent ~= 1 then
			error("expected exactly one wire response, got " .. tostring(#sent))
		end
		local got = sent[1]
		assertEq(got.kind, "result", "wire response kind")
		assertEq(got.id, 7, "wire response id")
		assertEq(got.result, { action = "accept", content = { api = "REST" } },
			"wire response body")
	end

	-- ─── patch(): prepare_adapter wrap advertises elicitation cap ──
	-- Regression guard: an earlier version wrapped
	-- connect_and_authenticate and mutated self.adapter_modified.parameters,
	-- but adapter_modified is {} at that point — the cap never landed in
	-- INITIALIZE. Verify the cap now flows through prepare_adapter's
	-- return value (which is the table assigned to adapter_modified and
	-- then sent as the INITIALIZE body).
	do
		package.loaded["codecompanion.acp"] = nil
		local fake_connection = {
			prepare_adapter = function(self)
				return vim.deepcopy(self.adapter)
			end,
			handle_incoming_request_or_notification = function(_self, _msg) end,
		}
		package.loaded["codecompanion.acp"] = fake_connection

		-- Force re-installation; the module's `_patched` flag would
		-- otherwise short-circuit a second patch() call.
		package.loaded["lib.codecompanion-elicitation"] = nil
		local fresh = require("lib.codecompanion-elicitation")
		fresh.patch()

		local conn = setmetatable({
			adapter = {
				parameters = {
					protocolVersion = 1,
					clientCapabilities = { fs = { readTextFile = true } },
				},
			},
		}, { __index = fake_connection })

		local got = conn:prepare_adapter()
		assertEq(got.parameters.clientCapabilities.fs,
			{ readTextFile = true },
			"prepare_adapter preserves existing capabilities")
		local form = got.parameters.clientCapabilities.elicitation
			and got.parameters.clientCapabilities.elicitation.form
		if form == nil then
			error("prepare_adapter did not splice elicitation.form into parameters")
		end
		-- Clean up so the real codecompanion plugin can be loaded normally
		-- in any subsequent test run within this nvim instance.
		package.loaded["codecompanion.acp"] = nil
		package.loaded["lib.codecompanion-elicitation"] = nil
	end

	-- ─── _handle_elicitation_create: rejects non-form mode ──────────
	do
		local sent = {}
		local fake_conn = {
			send_result = function(self, id, result)
				sent[#sent + 1] = { kind = "result", id = id, result = result }
			end,
			send_error = function(self, id, message, code)
				sent[#sent + 1] = { kind = "error", id = id, message = message, code = code }
			end,
		}
		elicit._handle_elicitation_create(fake_conn, {
			id = 8,
			method = "elicitation/create",
			params = { mode = "url" },
		})
		if #sent ~= 1 then
			error("expected error reply for non-form mode")
		end
		assertEq(sent[1].kind, "error", "non-form mode returns error")
	end

	print("OK: lib.codecompanion-elicitation spec passed")
end

return M
