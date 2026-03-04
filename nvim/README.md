# Neovim Environment System

This directory contains an extensible environment detection and configuration system for Neovim that automatically applies workarounds for different infrastructures.

## Features

- **Automatic environment detection** - Detects Meta, and can be extended for other environments
- **Environment-specific workarounds** - Applies fixes only when needed
- **Clean architecture** - All environment logic centralized in `lib/env.lua`
- **Shell integration** - Optional wrapper script for shell-level fixes

## How it works

1. **Environment Detection** (`lib/env.lua`)
   - Automatically detects the current environment (Meta, default, etc.)
   - Applies environment-specific configurations
   - Currently detects Meta by checking for proxy and certificate variables

2. **Shell Wrapper** (`../bin/nvim-env-wrapper`)
   - Clears conflicting environment variables before starting Neovim
   - Only activated when needed (Meta environment detected)

3. **Automatic Setup** (`env-setup.sh`)
   - Source this from your shell RC file
   - Automatically sets up the `nvim` alias when at Meta

## Meta-specific workarounds

When Meta environment is detected:
- Disables Lazy.nvim auto-update (GitHub blocked by proxy)
- Disables Lazy.nvim auto-install
- Reduces git timeout for faster failures
- Clears conflicting SSL certificate variables
- Prepends `/usr/lib/nvim` to runtimepath for bundled treesitter parsers

## Setup

Add to your `.localrc`, `.bashrc`, or `.zshrc`:
```bash
source ~/dotfiles/nvim/env-setup.sh
```

## Commands

- `:EnvInfo` - Show current environment and active workarounds

## Extending for new environments

To add support for a new environment, edit `lib/env.lua`:

1. Add detection logic in `M.detect()`
2. Add a new config in `M.configs`
3. Optionally update the shell wrapper (`../bin/nvim-env-wrapper`)

Example:
```lua
-- In M.detect()
if vim.env.COMPANY_SPECIFIC_VAR then
  return "company"
end

-- In M.configs
company = {
  name = "Company Name",
  setup_env = function()
    -- Environment variable fixes
  end,
  lazy_overrides = function()
    -- Lazy.nvim configuration changes
  end,
  setup = function()
    -- Additional setup
  end,
}
```

## Files

- `lib/env.lua` - Core environment detection and configuration
- `env-setup.sh` - Shell integration script
- `../bin/nvim-env-wrapper` - Shell wrapper for environment fixes
- `config/local.lua` - Calls the environment setup