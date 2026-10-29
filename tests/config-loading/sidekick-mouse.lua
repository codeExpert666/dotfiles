-- 在真实事件循环中验证活动/非活动终端的滚轮；模拟 TUI，不启动 Codex 或宿主 tmux。
local api = vim.api
local config, data = vim.fn.stdpath("config"), vim.fn.stdpath("data")
vim.opt.rtp:prepend(config)
vim.opt.rtp:append(data .. "/lazy/sidekick.nvim")
vim.o.columns, vim.o.lines, vim.o.mouse = 200, 50, "a"

local root = vim.fn.tempname()
vim.fn.mkdir(root, "p")
local packets = root .. "/packets"
vim.fn.writefile({
  "import os, sys, tty",
  "tty.setraw(0)",
  "log = open(sys.argv[1], 'ab', buffering=0)",
  "os.write(1, b'\\x1b[?1049h\\x1b[?1003h\\x1b[?1006h\\x1b[2J\\x1b[H' + b'REPLY\\r\\n' * 20 + b'INPUTBOX')",
  "while True:",
  "    chunk = os.read(0, 4096)",
  "    if not chunk: break",
  "    log.write(chunk)",
}, root .. "/tui.py")

local opts = vim.deepcopy(dofile(config .. "/lua/plugins/sidekick.lua")[1].opts)
opts.cli.mux.enabled = false
opts.cli.tools.codex.cmd = { vim.fn.exepath("python3"), root .. "/tui.py", packets }
require("sidekick").setup(opts)
require("config.sidekick-mouse").setup()
-- 重复初始化不应重复发送滚轮事件。
require("config.sidekick-mouse").setup()
local Session = require("sidekick.cli.session")
Session.did_setup = true
Session.register("terminal", require("sidekick.cli.terminal"))

local terminal, task, resume
local function finish(err)
  if terminal then
    terminal:close()
  end
  vim.fn.delete(root, "rf")
  if err then
    print(err)
    vim.cmd("cquit 1")
  end
  print("Sidekick mouse: focused and hovered scrolling, coordinates, editor modes and ordinary scrolling passed")
  vim.cmd("qa!")
end

local function pause()
  vim.defer_fn(resume, 150)
  coroutine.yield()
end

local function received()
  local raw = vim.fn.filereadable(packets) == 1 and table.concat(vim.fn.readfile(packets), "\n") or ""
  local events = {}
  -- Nvim 可能在滚轮前发送指针移动（35）；这里只比较上下滚轮。
  for event in raw:gmatch("\27%[<6[45];%d+;%d+M") do
    events[#events + 1] = event
  end
  return table.concat(events)
end

local function wheel(win, direction)
  local pos = api.nvim_win_get_position(win)
  api.nvim_input_mouse("wheel", direction, "", 0, pos[1] + 4, pos[2] + 4)
  pause()
end

task = coroutine.create(function()
  local lines = {}
  for i = 1, 200 do
    lines[i] = ("editor line %d"):format(i)
  end
  api.nvim_buf_set_lines(0, 0, -1, false, lines)
  local editor, editor_buf = api.nvim_get_current_win(), api.nvim_get_current_buf()
  terminal = require("sidekick.cli.state").attach(
    { tool = require("sidekick.config").get_tool("codex") },
    { show = true, focus = true }
  ).terminal
  for _ = 1, 30 do
    pause()
    local text = table.concat(api.nvim_buf_get_lines(terminal.buf, 0, -1, false), "\n")
    if text:find("INPUTBOX", 1, true) then
      break
    end
  end
  assert(terminal.tool.native_scroll, "Codex native scrolling disabled")
  assert(vim.fn.mode() == "t", "terminal did not enter input mode")
  wheel(terminal.win, "up")
  assert(received() == "\27[<64;5;5M", "focused wheel was lost or duplicated: " .. vim.inspect(received()))

  terminal:blur()
  pause()
  local before = api.nvim_win_call(terminal.win, vim.fn.winsaveview)
  local expected = received()
  for _, mode in ipairs({ "n", "i", "v" }) do
    if mode ~= "n" then
      api.nvim_input(mode)
      pause()
    end
    for _, direction in ipairs({ "up", "down" }) do
      wheel(terminal.win, direction)
      expected = expected .. ("\27[<%d;5;5M"):format(direction == "up" and 64 or 65)
      assert(received() == expected, "hovered wheel was lost, duplicated or had wrong coordinates in " .. mode)
      assert(api.nvim_get_current_win() == editor and vim.fn.mode() == mode, "hovered wheel changed editor focus/mode")
      assert(api.nvim_win_get_buf(terminal.win) == terminal.buf, "hovered wheel replaced terminal buffer")
      local after = api.nvim_win_call(terminal.win, vim.fn.winsaveview)
      assert(after.topline == before.topline, "hovered wheel scrolled terminal buffer")
    end
    api.nvim_input(vim.keycode("<Esc>"))
    pause()
  end
  assert(vim.deep_equal(api.nvim_buf_get_lines(editor_buf, 0, -1, false), lines), "wheel inserted text in editor")

  wheel(editor, "down")
  assert(vim.fn.line("w0") > 1, "ordinary editor scrolling was intercepted")
  assert(received() == expected, "editor wheel was forwarded to Codex")

  -- 别的工具不应被当作 Codex 接收 SGR 数据。
  terminal.tool.name = "other"
  wheel(terminal.win, "up")
  assert(received() == expected, "wheel was forwarded to another tool")
  finish()
end)
resume = function()
  local ok, err = coroutine.resume(task)
  if not ok then
    finish(err)
  end
end
vim.schedule(resume)
vim.defer_fn(function()
  finish("Sidekick mouse regression timed out")
end, 10000)
