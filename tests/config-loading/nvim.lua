-- 配置加载测试的离线探针：验证发现、语法和基础设置，不启动插件管理器或下载依赖。
local ok, err = pcall(function()
  local config = vim.fn.stdpath("config")
  assert(config == vim.env.HOME .. "/.config/nvim", "unexpected Neovim config directory")
  assert(vim.fn.filereadable(config .. "/init.lua") == 1, "missing init.lua")
  for _, path in ipairs(vim.fn.globpath(config, "**/*.lua", false, true)) do
    assert(loadfile(path))
  end
  dofile(config .. "/lua/config/options.lua")
  assert(vim.o.relativenumber == false, "relative line numbers are enabled")
  assert(vim.g.autoformat == true, "automatic formatting is disabled")
  local extras = vim.json.decode(table.concat(vim.fn.readfile(config .. "/lazyvim.json"), "\n"))
  assert(vim.tbl_contains(extras.extras, "lazyvim.plugins.extras.lang.toml"), "missing TOML extra")
  local lock = vim.json.decode(table.concat(vim.fn.readfile(config .. "/lazy-lock.json"), "\n"))
  assert(lock.LazyVim and lock["lazy.nvim"], "missing LazyVim or lazy.nvim lock entry")
end)
if not ok then
  print(err)
  vim.cmd("cquit 1")
end
vim.cmd("qa!")
