-- 获取个人 Markdown 规则文件的路径。
-- stdpath("config") 通常是 ~/.config/nvim；.. 用于拼接字符串。
local markdownlint_config = vim.fn.stdpath("config") .. "/.markdownlint.json"

return {
  -- 在支持 Kitty 图形协议的终端中直接渲染 Mermaid 和 Markdown 图片。
  {
    "folke/snacks.nvim",
    opts = {
      image = {
        enabled = true,
        doc = {
          -- 阅读时用图表替代源码；光标进入代码块时由 Snacks 显示源码。
          conceal = function(_, kind)
            return kind == "chart" or kind == "math"
          end,
        },
      },
    },
  },

  -- 配置诊断：检查 Markdown 并显示警告。
  {
    "mfussenegger/nvim-lint",
    opts = {
      linters = {
        ["markdownlint-cli2"] = {
          -- 在默认参数前加入配置文件路径。
          -- 保留默认的 "-"，继续读取缓冲区内容。
          prepend_args = { "--config", markdownlint_config },
        },
      },
    },
  },

  -- 配置修复：让 Conform 使用同一份规则。
  {
    "stevearc/conform.nvim",
    opts = {
      formatters = {
        ["markdownlint-cli2"] = {
          -- 保留默认的 "--fix" 和文件名参数。
          prepend_args = { "--config", markdownlint_config },
        },
      },
    },
  },
}
