-- Clipboard relay for nvs headless server mode.
-- Stores yank content in vim.g variables so the local clipboard watcher
-- (bin-macos/nvs) can fetch via --remote-expr and emit OSC 52 to the terminal.
--
-- Content is base64-encoded server-side to avoid trailing-newline stripping
-- when the local watcher fetches it via shell command substitution.
--
-- _clip_wait() provides a push-based (long-poll) interface: the caller blocks
-- until a yank happens or the timeout expires, avoiding repeated polling.

local function copy(lines, regtype)
  vim.g._clip = table.concat(lines, '\n')
  vim.g._clip_b64 = vim.base64.encode(table.concat(lines, '\n'))
  vim.g._clip_regtype = regtype
  vim.g._clip_seq = (vim.g._clip_seq or 0) + 1
end

vim.g.clipboard = {
  name = 'nvs-relay',
  copy = {
    ['+'] = copy,
    ['*'] = copy,
  },
  paste = {
    ['+'] = function() return { vim.split(vim.g._clip or '', '\n'), vim.g._clip_regtype or 'v' } end,
    ['*'] = function() return { vim.split(vim.g._clip or '', '\n'), vim.g._clip_regtype or 'v' } end,
  },
}

function _G._clip_wait(last_seq)
  vim.wait(30000, function() return (vim.g._clip_seq or 0) ~= last_seq end, 200)
  local seq = vim.g._clip_seq or 0
  if seq ~= last_seq then
    return seq .. ':' .. (vim.g._clip_b64 or '')
  end
  return ''
end
