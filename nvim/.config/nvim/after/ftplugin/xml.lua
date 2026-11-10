-- XML（含 Mapper 与 pom.xml）默认使用四空格；LemMinX 沿用缓冲区缩进选项。
vim.opt_local.expandtab = true
vim.opt_local.tabstop = 4
vim.opt_local.shiftwidth = 4
vim.opt_local.softtabstop = 4

vim.b.undo_ftplugin = (vim.b.undo_ftplugin or "") .. " | setlocal expandtab< tabstop< shiftwidth< softtabstop<"
