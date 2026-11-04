-- JDTLS 使用缓冲区缩进选项格式化；覆盖 LazyVim 默认的两空格，仅作用于 Java。
vim.opt_local.expandtab = true
vim.opt_local.tabstop = 4
vim.opt_local.shiftwidth = 4
vim.opt_local.softtabstop = 4

vim.b.undo_ftplugin = (vim.b.undo_ftplugin or "")
  .. " | setlocal expandtab< tabstop< shiftwidth< softtabstop<"
