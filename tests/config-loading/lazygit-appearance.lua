-- 真实 Snacks / Lazygit / delta；只在隔离 HOME 的临时 Git 仓库中运行。
local config, data = vim.fn.stdpath("config"), vim.fn.stdpath("data")
vim.opt.rtp:prepend(config)
vim.opt.rtp:append(data .. "/lazy/snacks.nvim")
vim.opt.rtp:prepend(data .. "/lazy/catppuccin")
require("snacks")
require("config.lazygit-appearance").setup()
require("config.lazygit-appearance").setup()
assert(#vim.api.nvim_get_autocmds({ group = "dotfiles_lazygit_appearance" }) == 1, "duplicate theme hook")
require("catppuccin").setup({
  flavour = "auto",
  background = { light = "latte", dark = "macchiato" },
  auto_integrations = false,
})

local root = vim.fn.tempname()
vim.fn.mkdir(root .. "/repo", "p")
vim.fn.mkdir(root .. "/bin", "p")
vim.fn.system({ "git", "init", "--quiet", root .. "/repo" })
assert(vim.v.shell_error == 0, "git init failed")
vim.fn.writefile({ "appearance test" }, root .. "/repo/sample.txt")
local delta = vim.fn.exepath("delta")
local receipt = root .. "/delta.args"
vim.fn.writefile({
  "#!/bin/sh",
  "printf '%s\\n' \"$*\" >> " .. vim.fn.shellescape(receipt),
  "exec " .. vim.fn.shellescape(delta) .. ' "$@"',
}, root .. "/bin/delta")
vim.fn.setfperm(root .. "/bin/delta", "rwx------")
local previous_path = vim.env.PATH
vim.env.PATH = root .. "/bin:" .. previous_path
local term

local function wait_for(appearance)
  assert(
    vim.wait(5000, function()
      if vim.fn.filereadable(receipt) == 0 then
        return false
      end
      local lines = vim.fn.readfile(receipt)
      return (lines[#lines] or ""):find("--" .. appearance, 1, true) ~= nil
    end, 20),
    "delta did not select " .. appearance
  )
end

local ok, err = pcall(function()
  local opts = {
    cwd = root .. "/repo",
    interactive = false,
    config = {
      git = { autoFetch = false },
      update = { method = "never" },
      disableStartupPopups = true,
    },
  }
  vim.o.background = "dark"
  vim.cmd.colorscheme("catppuccin-nvim")
  term = Snacks.lazygit(opts)
  wait_for("dark")
  local channel = vim.bo[term.buf].channel
  vim.o.background = "light"
  wait_for("light")
  assert(vim.bo[term.buf].channel == channel, "appearance change replaced the process")

  term:hide()
  vim.o.background = "dark"
  wait_for("dark")
  assert(Snacks.lazygit(opts) == term, "showing the terminal replaced it")
  assert(vim.bo[term.buf].channel == channel, "restoring the terminal replaced the process")

  local generated = table.concat(vim.fn.readfile(vim.fn.stdpath("cache") .. "/lazygit-theme.yml"), "\n")
  assert(generated:find('editPreset: "nvim%-remote"'), "editor integration was lost")

  -- 退出后验证亮色启动，不能只覆盖复用进程的通知路径。
  vim.fn.jobstop(channel)
  term:close()
  vim.o.background = "light"
  vim.fn.delete(receipt)
  term = Snacks.lazygit(opts)
  wait_for("light")
end)

if term and term:buf_valid() then
  vim.fn.jobstop(vim.bo[term.buf].channel)
  term:close()
end
vim.env.PATH = previous_path
vim.fn.delete(root, "rf")
if not ok then
  print(err)
  vim.cmd("cquit 1")
end
vim.cmd("qa!")
