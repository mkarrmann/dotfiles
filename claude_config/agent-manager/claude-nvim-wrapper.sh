#!/bin/bash
# Claude Code wrapper for Neovim's claudecode.nvim terminal_cmd.
# Derives session name from the current Neovim tab and exports NVIM_TAB_HANDLE
# so hooks can update the tab-state indicator in the tabline.

GENERIC_NAMES="nvim|bash|zsh|sh|fish|systemd"

# If no name is queued (e.g., from `cn`), derive one from the current tab name
if [ ! -f ~/.claude-next-name ] && [ -n "$NVIM" ]; then
  local_tab="${NVIM_TAB_HANDLE:-0}"
  tab_name=$(nvim --server "$NVIM" --remote-expr \
    "luaeval('(function() local ok,n=pcall(vim.api.nvim_tabpage_get_var,${local_tab},\"tab_name\"); return ok and n or \"\" end)()')" 2>/dev/null)
  if [ -n "$tab_name" ] && ! echo "$tab_name" | grep -qxE "$GENERIC_NAMES"; then
    echo "$tab_name" > ~/.claude-next-name
  fi
fi

# Rename the current Neovim tab to the agent name (if we have one)
if [ -n "$NVIM" ] && [ -f ~/.claude-next-name ]; then
  agent_name=$(cat ~/.claude-next-name)
  if [ -n "$NVIM_TAB_HANDLE" ]; then
    nvim --server "$NVIM" --remote-expr \
      "execute('lua _G._claude_rename_tab(${NVIM_TAB_HANDLE}, \"${agent_name}\")')" >/dev/null 2>&1
  else
    nvim --server "$NVIM" --remote-expr \
      "execute('lua _G._nvim_rename_current_tab(\"${agent_name}\")')" >/dev/null 2>&1
  fi
fi

# Export NVIM_TAB_HANDLE so hooks know which tab they belong to.
# Also publish the NVIM socket path so Python tools (watcher, dashboard) can reach Neovim.
if [ -n "$NVIM" ]; then
  if [ -z "$NVIM_TAB_HANDLE" ]; then
    NVIM_TAB_HANDLE=$(nvim --server "$NVIM" \
      --remote-expr "luaeval('vim.api.nvim_get_current_tabpage()')" 2>/dev/null)
  fi
  export NVIM_TAB_HANDLE
  mkdir -p ~/.claude/agent-manager
  echo "$NVIM" > ~/.claude/agent-manager/nvim-server
fi

exec claude --dangerously-skip-permissions "$@"
