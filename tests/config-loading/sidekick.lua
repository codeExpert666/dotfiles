-- 真实 Sidekick 终端与上下文；用 cat 代替模型，不读取凭据、不连接宿主 tmux。
local api = vim.api
local config, data = vim.fn.stdpath("config"), vim.fn.stdpath("data")
vim.opt.rtp:prepend(config)
vim.opt.rtp:append(data .. "/lazy/sidekick.nvim")
vim.o.columns, vim.o.lines = 200, 50
vim.g.mapleader = " "

local spec = dofile(config .. "/lua/plugins/sidekick.lua")[1]
local opts = vim.deepcopy(spec.opts)
opts.cli.mux.enabled = false
opts.cli.tools.codex.cmd = { "/bin/sh", "-c", "printf 'READY\\n1\\n2\\n3\\n4\\n5\\n'; exec cat" }
require("sidekick").setup(opts)
local Session = require("sidekick.cli.session")
-- 将后端限定为本次 Neovim 的终端，不发现或连接其他正在运行的会话。
Session.did_setup = true
Session.register("terminal", require("sidekick.cli.terminal"))
local State, Cli = require("sidekick.cli.state"), require("sidekick.cli")

for _, key in ipairs(spec.keys) do
  if type(key[2]) == "function" then
    vim.keymap.set(key.mode or "n", key[1], key[2], { desc = key.desc })
  end
end

local function press(lhs, mode)
  local key = vim.fn.maparg(lhs, mode or "n", false, true)
  assert(type(key.callback) == "function", "missing mapping: " .. lhs)
  key.callback()
end

local function wait(check, message)
  assert(vim.wait(5000, check, 20), message)
end

local function current()
  return State.get({ name = "codex", cwd = true, attached = true })[1]
end

local function contents(terminal)
  return table.concat(api.nvim_buf_get_lines(terminal.buf, 0, -1, false), "\n")
end

local root = vim.fn.tempname()
for _, name in ipairs({ "project", "worktree" }) do
  vim.fn.mkdir(root .. "/" .. name, "p")
  vim.fn.writefile({ "first_" .. name, "second_" .. name, "outside_selection" }, root .. "/" .. name .. "/sample.txt")
end

local ok, err = pcall(function()
  vim.cmd.cd(root .. "/project")
  vim.cmd.edit("sample.txt")
  local source = api.nvim_get_current_win()
  press("<leader>aa")
  wait(function()
    return current() and contents(current().terminal):find("READY", 1, true)
  end, "terminal did not start")
  local first = current()
  local terminal, job = first.terminal, first.terminal.job
  assert(first.session.cwd == vim.fs.normalize(root .. "/project"), "wrong project cwd")

  press("<c-.>", "t")
  wait(function()
    return api.nvim_get_current_win() == source
  end, "focus did not return to source")
  assert(terminal:is_open(), "focus switch hid the terminal")
  press("<c-.>")
  wait(function()
    return terminal:is_focused()
  end, "focus did not return to terminal")
  terminal:blur()
  assert(Cli.render("{file}") == "@sample.txt", "file reference differs")
  press("<leader>af")
  wait(function()
    return contents(terminal):find("@sample.txt", 1, true)
  end, "file reference was not delivered to the terminal")
  terminal:blur()
  vim.cmd("normal! ggVj")
  local selected = Cli.render("{position}\n{selection}")
  assert(selected:find("@sample.txt :L1-L2", 1, true), "selection has no location")
  assert(selected:find("first_project\nsecond_project", 1, true), "selection body differs")
  assert(not selected:find("outside_selection", 1, true), "selection included an extra line")
  press("<leader>av", "x")
  wait(function()
    return contents(terminal):find("second_project", 1, true)
  end, "selection did not reach terminal input")

  terminal:blur()
  press("<leader>ah")
  wait(function()
    return not terminal:is_open()
  end, "terminal did not hide")
  assert(terminal:is_running(), "hiding stopped the process")
  press("<leader>aa")
  wait(function()
    return terminal:is_open()
  end, "terminal did not reopen")
  assert(current().terminal.job == job, "reopening replaced the process")

  -- 切换目录时，已连接的旧会话不能抢走新项目的上下文。
  terminal:blur()
  vim.cmd.cd(root .. "/worktree")
  vim.cmd.edit("sample.txt")
  vim.cmd("normal! ggVj")
  press("<leader>av", "x")
  wait(function()
    return current() and current().terminal.job ~= job
  end, "changing cwd reused the previous project's process")
  local second = current()
  assert(second.session.cwd == vim.fs.normalize(root .. "/worktree"), "wrong worktree cwd")
  wait(function()
    return contents(second.terminal):find("second_worktree", 1, true)
  end, "opening a new terminal lost the visual selection")
  assert(terminal:is_running(), "switching projects stopped the previous session")
  second.terminal:blur()
  vim.cmd.cd(root .. "/project")
  press("<leader>aa")
  wait(function()
    return current() and current().terminal.job == job
  end, "returning to the project did not reuse its process")

  -- 缺少依赖时报告明确错误，不把上下文发到另一个目录的会话。
  terminal:blur()
  vim.cmd.cd(root)
  local executable, notify = vim.fn.executable, vim.notify
  local warnings = {}
  vim.notify = function(message, level)
    warnings[#warnings + 1] = { message = message, level = level }
  end
  require("sidekick.config").cli.mux.enabled = true
  vim.fn.executable = function(command)
    return command == "tmux" and 0 or executable(command)
  end
  press("<leader>aa")
  assert(not current(), "missing tmux unexpectedly started a session")
  require("sidekick.config").cli.mux.enabled = false
  require("sidekick.config").cli.tools.codex.cmd = { root .. "/missing-codex" }
  vim.fn.executable = executable
  press("<leader>aa")
  vim.notify = notify
  assert(not current(), "missing Codex unexpectedly started a session")
  assert(#warnings == 2 and warnings[1].level == vim.log.levels.ERROR, "missing dependency errors were not reported")
  assert(terminal:is_running(), "dependency failure stopped the previous project's process")
end)

for _, state in ipairs(State.get({ name = "codex", attached = true })) do
  if state.terminal then
    state.terminal:close()
  end
end
vim.fn.delete(root, "rf")
if not ok then
  print(err)
  vim.cmd("cquit 1")
end
print("Sidekick: project routing, context, focus and hidden-process reuse passed")
vim.cmd("qa!")
