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

  -- 模拟会话环境，检查 provider 选择及个人覆盖；不连接 SSH 或系统剪贴板。
  local options = config .. "/lua/config/options.lua"
  vim.env.SSH_CONNECTION = nil
  vim.env.TERM = "xterm-ghostty"
  vim.g.clipboard = nil
  vim.o.clipboard = "unnamedplus"
  dofile(options)
  assert(vim.g.clipboard == nil, "local clipboard provider was overridden")
  assert(vim.o.clipboard == "unnamedplus", "local clipboard synchronization changed")

  vim.env.SSH_CONNECTION = "192.0.2.1 12345 192.0.2.2 22"
  vim.env.TERM = "xterm-256color"
  vim.o.clipboard = ""
  dofile(options)
  assert(vim.g.clipboard == nil, "OSC52 was forced on an unrecognized SSH terminal")

  vim.env.TERM = "xterm-ghostty"
  vim.g.clipboard = "pbcopy"
  dofile(options)
  assert(vim.g.clipboard == "pbcopy", "explicit clipboard provider was overridden")

  vim.g.clipboard = nil
  dofile(options)
  assert(vim.g.clipboard.name == "OSC 52", "which-key cannot recognize the OSC52 provider")
  assert(vim.o.clipboard == "", "SSH clipboard synchronization changed")
  assert(vim.fn.has("clipboard") == 1, "OSC52 clipboard provider did not initialize")

  -- 使用 Neovim 的真实 provider；拦截终端传输并模拟回复，验证显式复制/粘贴仍可用。
  local lines = { "OSC52_DIAGNOSTIC_LINE_1", "第二行" }
  local encoded = vim.base64.encode(table.concat(lines, "\n"))
  local sent = {}
  local send = vim.api.nvim_ui_send
  vim.api.nvim_ui_send = function(sequence)
    sent[#sent + 1] = sequence
    local selector = sequence:match("^\027%]52;([cp]);%?")
    if selector then
      vim.schedule(function()
        vim.api.nvim_exec_autocmds("TermResponse", {
          data = { sequence = "\027]52;" .. selector .. ";" .. encoded .. "\027\\" },
        })
      end)
    end
  end
  vim.api.nvim_buf_set_lines(0, 0, -1, false, lines)
  vim.cmd("normal! yy")
  assert(#sent == 0, "ordinary yank unexpectedly accessed the terminal clipboard")
  for _, register in ipairs({ "+", "*" }) do
    local selector = register == "+" and "c" or "p"
    sent = {}
    vim.fn.setreg(register, lines, "v")
    assert(#sent == 1 and sent[1] == "\027]52;" .. selector .. ";" .. encoded .. "\027\\", "OSC52 copy failed")
    sent = {}
    local pasted = vim.fn.getreg(register, 1, true)
    assert(vim.deep_equal(pasted, lines), "OSC52 paste did not return the terminal contents")
    assert(#sent == 1 and sent[1] == "\027]52;" .. selector .. ";?\027\\", "OSC52 paste query failed")
  end
  vim.api.nvim_ui_send = send

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
