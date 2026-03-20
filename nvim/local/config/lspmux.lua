-- Override fb-pyright-ls@meta to use lspmux for LSP multiplexing.
--
-- When the lspmux server is running, all nvim instances share a single Pyright
-- process, saving ~10GB of memory per additional instance. Falls back to the
-- default meta.nvim behavior when lspmux is unavailable.
--
-- Must be required before vim.lsp.enable().

local M = {}

local function lspmux_available()
  if vim.fn.executable("lspmux") ~= 1 then
    return false
  end
  local sock = "/tmp/lspmux-" .. (vim.env.USER or "unknown") .. ".sock"
  return vim.uv.fs_stat(sock) ~= nil
end

function M.setup()
  if not lspmux_available() then
    return
  end

  local wrapper = vim.fn.expand("~/.local/share/lspmux/fb-pyright-ls.sh")
  if vim.fn.filereadable(wrapper) ~= 1 then
    return
  end

  vim.lsp.config("fb-pyright-ls@meta", {
    cmd = { "lspmux", "client", "--server-path", wrapper },
    cmd_cwd = nil,
    cmd_env = nil,
  })
end

M.setup()

return M
