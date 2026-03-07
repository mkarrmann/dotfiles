-- Environment detection and configuration
-- Provides environment-specific overrides for different infrastructures

local M = {}

-- Detect current environment
function M.detect()
  -- Meta detection
  if vim.env.THRIFT_TLS_CL_CERT_PATH or vim.env.http_proxy == "http://fwdproxy:8080" then
    return "meta"
  end

  -- Add more environment detections here as needed
  -- if vim.env.SOME_COMPANY_VAR then
  --   return "company"
  -- end

  return "default"
end

-- Environment-specific configurations
M.configs = {
  meta = {
    name = "Meta",

    -- Shell environment fixes
    setup_env = function()
      -- Clear conflicting certificate variables that cause OpenSSL segfaults
      vim.env.THRIFT_TLS_CL_CERT_PATH = nil
      vim.env.THRIFT_TLS_CL_KEY_PATH = nil
      vim.env.CLAUDE_CODE_CLIENT_CERT = nil
      vim.env.CODEX_OTEL_CLIENT_CERT = nil
      vim.env.OTEL_EXPORTER_OTLP_METRICS_CLIENT_CERTIFICATE = nil
    end,

    -- Lazy.nvim overrides
    lazy_overrides = function()
      local ok, Config = pcall(require, "lazy.core.config")
      if ok and Config.options then
        -- Disable automatic checking due to proxy blocking GitHub
        if Config.options.checker then
          Config.options.checker.enabled = false
        end

        -- Don't auto-install missing plugins
        if Config.options.install then
          Config.options.install.missing = false
        end

        -- Reduce timeout for blocked git operations
        if Config.options.git then
          Config.options.git.timeout = 10
        end
      end
    end,

    -- Additional setup
    setup = function()
      -- Meta's Neovim package puts bundled treesitter parsers in /usr/lib/nvim/parser/
      -- but doesn't add /usr/lib/nvim to the runtimepath
      vim.opt.rtp:prepend("/usr/lib/nvim")

      -- Could add more Meta-specific setup here
    end,
  },

  -- Default (no special environment)
  default = {
    name = "Default",
    setup_env = function() end,
    lazy_overrides = function() end,
    setup = function() end,
  },
}

-- Apply environment-specific configuration
function M.setup()
  local env = M.detect()
  local config = M.configs[env] or M.configs.default

  -- Apply environment fixes first
  if config.setup_env then
    config.setup_env()
  end

  -- Apply Lazy overrides
  if config.lazy_overrides then
    config.lazy_overrides()
  end

  -- Run additional setup
  if config.setup then
    config.setup()
  end

  -- Environment info available via :EnvInfo command

  M.current_env = env
  M.current_config = config

  -- Create user commands
  M.create_commands()
end

-- Get current environment name
function M.get_env()
  return M.current_env or "unknown"
end

-- Create user command to check environment
function M.create_commands()
  vim.api.nvim_create_user_command("EnvInfo", function()
    local env = M.get_env()
    local config = M.current_config or M.configs.default
    local msg = string.format("Environment: %s", config.name)

    if env == "meta" then
      msg = msg .. "\n  • Lazy auto-update: disabled"
      msg = msg .. "\n  • Lazy auto-install: disabled"
      msg = msg .. "\n  • Git timeout: reduced"
      msg = msg .. "\n  • SSL certificates: cleared"
    end

    vim.notify(msg, vim.log.levels.INFO)
  end, { desc = "Show current environment and workarounds" })
end

return M