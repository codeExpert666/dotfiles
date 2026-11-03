-- Go 内置 ftplugin 的 noremap 也覆盖 Select 模式，会把参数占位符中的 [] 当成跳转。
-- 只移除 Select 模式映射，保留普通、可视和操作符等待模式的 Go 分节跳转。
for _, key in ipairs({ "[[", "[]", "][", "]]" }) do
  pcall(vim.keymap.del, "s", key, { buffer = true })
end
