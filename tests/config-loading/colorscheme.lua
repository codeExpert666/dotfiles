-- 使用已准备的真实 Catppuccin 缓存验证背景切换，不启动 Lazy 或联网安装插件。
local config, data = vim.fn.stdpath("config"), vim.fn.stdpath("data")
vim.opt.rtp:prepend(data .. "/lazy/catppuccin")
local specs = dofile(config .. "/lua/plugins/colorscheme.lua")
local opts = vim.deepcopy(specs[1].opts)
-- 插件集成由 LazyVim 加载；此探针只加载颜色插件。
opts.auto_integrations = false
require("catppuccin").setup(opts)

local function highlights()
  local result = {}
  for _, name in ipairs({ "Normal", "Comment", "StatusLine", "LineNr", "Visual" }) do
    result[name] = vim.api.nvim_get_hl(0, { name = name, link = false })
  end
  return result
end

local ok, err = pcall(function()
  vim.o.background = "dark"
  vim.cmd.colorscheme("catppuccin-macchiato")
  local baseline = highlights()

  vim.cmd.colorscheme(specs[2].opts.colorscheme)
  assert(vim.g.colors_name == "catppuccin-macchiato", "dark startup did not select Macchiato")
  assert(vim.deep_equal(highlights(), baseline), "automatic dark theme differs from the previous theme")

  -- 由 Neovim 真正重载 colorscheme；不手动调用颜色插件的切换函数。
  vim.o.background = "light"
  assert(vim.g.colors_name == "catppuccin-latte", "light background did not select Latte")
  assert(vim.api.nvim_get_hl(0, { name = "Normal" }).bg == 0xeff1f5, "Latte background differs")
  vim.o.background = "dark"
  assert(vim.g.colors_name == "catppuccin-macchiato", "switching back did not restore Macchiato")
  assert(vim.deep_equal(highlights(), baseline), "switching back changed the dark highlights")

  -- 另测亮色启动，然后往返切换，避免只覆盖深色启动路径。
  vim.o.background = "light"
  vim.cmd.colorscheme(specs[2].opts.colorscheme)
  assert(vim.g.colors_name == "catppuccin-latte", "light startup did not select Latte")
  vim.o.background = "dark"
  assert(vim.deep_equal(highlights(), baseline), "light startup did not restore the previous dark theme")
end)
if not ok then
  print(err)
  vim.cmd("cquit 1")
end
vim.cmd("qa!")
