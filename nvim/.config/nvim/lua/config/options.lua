-- Options are automatically loaded before lazy.nvim startup
-- Default options that are always set: https://github.com/LazyVim/LazyVim/blob/main/lua/lazyvim/config/options.lua
-- 基于 LazyVim/starter 修改：以下为本仓库的行号、剪贴板和自动格式化设置。

-- Ghostty SSH：显式声明 OSC52，让 which-key 跳过 * / + 的剪贴板预览。
-- Neovim 自动检测 OSC52 时不会设置 g:clipboard，which-key 因而无法识别它。
-- 保留 LazyVim 的 clipboard 选项和用户已声明的 provider；本地会话沿用原生剪贴板。
if vim.env.SSH_CONNECTION and vim.env.TERM == "xterm-ghostty" and vim.g.clipboard == nil then
  local osc52 = require("vim.ui.clipboard.osc52")
  vim.g.clipboard = {
    name = "OSC 52",
    copy = {
      ["+"] = osc52.copy("+"),
      ["*"] = osc52.copy("*"),
    },
    paste = {
      ["+"] = osc52.paste("+"),
      ["*"] = osc52.paste("*"),
    },
  }
end

-- 使用绝对行号
vim.opt.relativenumber = false

-- 启用 LazyVim 的保存时自动格式化，对所有已配置格式化器的文件类型生效。
-- Bash/POSIX Shell 的 shfmt 参数由 lua/plugins/formatting.lua 统一设置。
-- Zsh 通过 Shuck LSP 格式化，规则来自 ~/.config/shuck/shuck.toml 或项目配置。
vim.g.autoformat = true

-- 保留英文拼写检查，忽略中日韩字符
vim.opt.spelllang:append("cjk")

-- 将本机 Zsh 登录环境文件识别为 Zsh
vim.filetype.add({
  filename = {
    ["local.zprofile"] = "zsh",
  },
})
