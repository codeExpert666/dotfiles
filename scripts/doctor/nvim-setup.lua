-- 在复制的 init.lua 执行前加载，关闭自动安装、更新、配置变更检测和本地插件配置。
-- 离线会话禁用 Taplo，并收集启动错误；其余编辑器能力基于配置副本进行检查。
vim.opt.rtp:prepend(vim.fn.stdpath("data") .. "/lazy/lazy.nvim")
local lazy = require("lazy")
local setup = lazy.setup
vim.g.dotfiles_doctor_errors = {}
-- 临时会话接管通知接口，收集错误级别的通知供运行检查报告。
---@diagnostic disable-next-line: duplicate-set-field
vim.notify = function(message, level)
  if level == vim.log.levels.ERROR then
    local errors = vim.g.dotfiles_doctor_errors
    errors[#errors + 1] = tostring(message)
    vim.g.dotfiles_doctor_errors = errors
  end
end
lazy.setup = function(opts)
  opts.install = { missing = false }
  opts.checker = { enabled = false }
  opts.change_detection = { enabled = false }
  opts.readme = { enabled = false }
  opts.rocks = { enabled = false }
  opts.pkg = { enabled = false }
  opts.local_spec = false
  local spec = opts.spec
  assert(type(spec) == "table", "doctor requires a plugin spec list")
  vim.list_extend(spec, {
    { "mason-org/mason-lspconfig.nvim", enabled = false },
    {
      "mason-org/mason.nvim",
      config = function(_, settings)
        settings.ensure_installed = {}
        require("mason").setup(settings)
      end,
    },
    {
      "neovim/nvim-lspconfig",
      opts = function(_, settings)
        for _, server in pairs(settings.servers) do
          if type(server) == "table" then
            server.mason = false
          end
        end
        -- 当前支持的 Taplo 构建会先初始化远程模式定义目录，再应用工作区设置。
        -- 为保持离线探测，此处禁用 Taplo；运行检查会将其挂载验证记为跳过。
        -- 上游实现：https://github.com/tamasfe/taplo/blob/master/crates/taplo-lsp/src/world.rs
        if settings.servers.taplo then
          settings.servers.taplo.enabled = false
        end
      end,
    },
    {
      "nvim-treesitter/nvim-treesitter",
      opts = function(_, settings)
        settings.ensure_installed = {}
        settings.auto_install = false
      end,
    },
  })
  return setup(opts)
end
