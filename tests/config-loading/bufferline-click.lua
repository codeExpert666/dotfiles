-- 真实 bufferline 鼠标点击与 Sidekick 终端；cat 代替模型，禁用宿主 tmux 发现。
local api = vim.api
local config, data = vim.fn.stdpath("config"), vim.fn.stdpath("data")
vim.opt.rtp:prepend(config)
for _, plugin in ipairs({ "sidekick.nvim", "bufferline.nvim" }) do
  vim.opt.rtp:append(data .. "/lazy/" .. plugin)
end
vim.o.columns, vim.o.lines, vim.o.mouse, vim.o.hidden = 200, 50, "a", true
local opts = vim.deepcopy(dofile(config .. "/lua/plugins/sidekick.lua")[1].opts)
opts.cli.mux.enabled, opts.cli.watch = false, false
opts.cli.tools.codex.cmd = { "cat" }
require("sidekick").setup(opts)
local Session = require("sidekick.cli.session")
Session.did_setup = true
Session.register("terminal", require("sidekick.cli.terminal"))
local bufferline_opts = { options = { show_buffer_icons = false, diagnostics = false } }
dofile(config .. "/lua/plugins/bufferline.lua")[1].opts(nil, bufferline_opts)
require("bufferline").setup(bufferline_opts)

local terminal, task, resume
local function finish(err)
  if terminal then
    terminal:close()
  end
  if err then
    print(err)
    vim.cmd("cquit 1")
  end
  print("Bufferline: terminal-mode mouse click, editor routing, tab isolation and process preservation passed")
  vim.cmd("qa!")
end

local function pause()
  vim.defer_fn(resume, 100)
  coroutine.yield()
end

local function click(buf)
  _G.___bufferline_private.handle_click(buf, 1, "l")
  pause()
end

local function file(name)
  local buf = api.nvim_create_buf(true, false)
  api.nvim_buf_set_name(buf, name)
  return buf
end

task = coroutine.create(function()
  local first = api.nvim_get_current_buf()
  api.nvim_buf_set_name(first, "first.txt")
  local target, hidden = file("target.txt"), file("hidden.txt")
  local editor = api.nvim_get_current_win()
  terminal = require("sidekick.cli.state").attach(
    { tool = require("sidekick.config").get_tool("codex") },
    { show = true, focus = true }
  ).terminal
  pause()
  local job = terminal.job
  local function intact()
    assert(api.nvim_win_get_buf(terminal.win) == terminal.buf, "file replaced the terminal display")
    assert(terminal.job == job and terminal:is_running(), "click restarted or stopped the terminal")
  end

  -- 真实终端输入模式下，向渲染出的目标标签发送鼠标按下/松开事件。
  assert(vim.fn.mode() == "t", "terminal did not enter input mode")
  vim.cmd("redraw!")
  local tabline = api.nvim_eval_statusline(vim.o.tabline, { use_tabline = true, maxwidth = vim.o.columns }).str
  local col = assert(tabline:find("target.txt", 1, true), "target tab was not rendered")
  api.nvim_input_mouse("left", "press", "", 0, 0, col)
  api.nvim_input_mouse("left", "release", "", 0, 0, col)
  pause()
  assert(api.nvim_get_current_win() == editor and api.nvim_get_current_buf() == target, "mouse missed editor")
  assert(vim.fn.mode() == "n", "file click left editor in terminal/insert mode")
  intact()

  api.nvim_buf_set_lines(target, 0, -1, false, { "unsaved text" })
  terminal:focus()
  pause()
  click(first)
  assert(vim.bo[target].modified, "click discarded unsaved changes")
  assert(api.nvim_buf_get_lines(target, 0, 1, false)[1] == "unsaved text", "unsaved content changed")

  local other = api.nvim_open_win(target, true, { split = "above", win = editor })
  api.nvim_set_current_win(editor)
  terminal:focus()
  click(target)
  assert(api.nvim_get_current_win() == other, "existing target window was not reused")
  terminal:focus()
  click(hidden)
  assert(api.nvim_get_current_win() == other, "most recent editor was not reused")
  api.nvim_set_current_win(editor)
  click(hidden)
  assert(api.nvim_get_current_win() == editor, "ordinary editor click switched windows")
  intact()

  -- 其他 tab 中同名目标与最近编辑窗口不能影响当前 tab 的选择。
  vim.cmd.tabnew()
  local foreign = api.nvim_get_current_win()
  api.nvim_win_set_buf(foreign, target)
  vim.cmd.tabprevious()
  terminal:focus()
  click(target)
  assert(api.nvim_get_current_win() == editor, "routing escaped the current tab")
  intact()

  -- 最近窗口已关闭时，回退到仍存在的编辑窗口。
  api.nvim_set_current_win(other)
  terminal:focus()
  api.nvim_win_close(other, true)
  click(hidden)
  assert(api.nvim_get_current_win() == editor, "closed recent window prevented fallback")

  -- 工具窗口、浮窗、固定缓冲区和其他终端即使显示目标，也不能被选中。
  local tool_buf = api.nvim_create_buf(false, true)
  local tool = api.nvim_open_win(tool_buf, true, { split = "above", win = editor })
  local floating = api.nvim_open_win(target, false, { relative = "editor", row = 3, col = 3, width = 20, height = 4 })
  vim.wo[editor].winfixbuf = true
  api.nvim_win_set_buf(tool, target)
  vim.w[tool].sidekick_session_id = "another-terminal"
  terminal:focus()
  click(target)
  local created = api.nvim_get_current_win()
  assert(created ~= editor and created ~= tool and created ~= floating and created ~= terminal.win)
  assert(api.nvim_get_current_buf() == target, "missing editor fallback did not open target")
  assert(api.nvim_win_get_buf(tool) == target, "other terminal was replaced")
  intact()
  api.nvim_win_close(created, true)
  api.nvim_win_close(floating, true)
  api.nvim_win_close(tool, true)
  vim.wo[editor].winfixbuf = false

  -- 仅剩 Sidekick 时仍创建编辑分屏，并保留同一个终端。
  terminal:focus()
  api.nvim_win_close(editor, true)
  click(hidden)
  assert(api.nvim_get_current_win() ~= terminal.win and api.nvim_get_current_buf() == hidden)
  intact()
  terminal:focus()
  pause()
  terminal:blur()
  pause()
  assert(api.nvim_get_current_buf() == hidden, "focus toggle did not return to the new editor")
  terminal:hide()
  terminal:show()
  intact()
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
  finish("Bufferline click regression timed out")
end, 10000)
