-- 基于 LazyVim/starter 修改：新增 <leader>ba 删除全部已列出缓冲区的快捷键。
-- Keymaps are automatically loaded on the VeryLazy event
-- Default keymaps that are always set: https://github.com/LazyVim/LazyVim/blob/main/lua/lazyvim/config/keymaps.lua
-- Add any additional keymaps here

-- 普通模式下按 <leader>ba 删除所有已列出的缓冲区。
-- Snacks 会处理未保存的内容，并在删除缓冲区时保留现有窗口布局。
vim.keymap.set("n", "<leader>ba", function()
  Snacks.bufdelete.all()
end, { desc = "Delete All Buffers" })
