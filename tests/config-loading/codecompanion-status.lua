-- 原生 CodeCompanion 窗口与 ACP 通知回归；仅替换代理连接，不联网或读取个人凭据。
local api = vim.api
local config_dir, data = vim.fn.stdpath("config"), vim.fn.stdpath("data")
for _, plugin in ipairs({ "codecompanion.nvim", "plenary.nvim", "nvim-treesitter" }) do
  vim.opt.rtp:append(data .. "/lazy/" .. plugin)
end
vim.o.columns, vim.o.lines = 240, 60
-- 确定的主题色夹具；验证生产模块取色于主题，而非内置固定色值。
api.nvim_set_hl(0, "Normal", { fg = 0xcdd6f4, bg = 0x1e1e2e })
api.nvim_set_hl(0, "WinBar", { bg = 0x181825 })
api.nvim_set_hl(0, "Comment", { fg = 0x6c7086 })
api.nvim_set_hl(0, "Special", { fg = 0x89b4fa })
api.nvim_set_hl(0, "NonText", { fg = 0x45475a })

local Connection = require("codecompanion.acp")
require("codecompanion.interactions.chat.helpers").create_acp_connection = function(chat, callback)
  local connection = Connection.new({ adapter = chat.adapter, chat = chat })
  connection.session_id = "fixture-" .. chat.bufnr
  connection._state.handle = { kill = function() end }
  chat.acp_connection = connection
  if callback then
    callback()
  end
end

local spec = dofile(config_dir .. "/lua/plugins/codecompanion.lua")
spec[1].config(nil, spec[1].opts)
local status = require("config.codecompanion-status")
local handle = Connection.handle_incoming_request_or_notification
status.setup()
assert(Connection.handle_incoming_request_or_notification == handle, "setup wrapped the ACP handler twice")

local cc = require("codecompanion")
local source_win = api.nvim_get_current_win()
vim.wo[source_win].winbar = "existing source winbar"
local chat = assert(cc.chat())
local win = chat.ui.winnr
api.nvim_win_set_width(win, 140)

local function rendered_bar(target)
  target = target or win
  return api.nvim_eval_statusline(vim.wo[target].winbar, {
    winid = target,
    use_winbar = true,
    maxwidth = api.nvim_win_get_width(target),
    highlights = true,
  })
end

local function bar(target)
  return rendered_bar(target).str
end

local function centered(target)
  local rendered = rendered_bar(target)
  local left = #rendered.str:match("^ *")
  local right = #rendered.str:match(" *$")
  assert(left > 0 and right > 0 and math.abs(left - right) <= 1, "winbar content was not centered")
  assert(rendered.width == api.nvim_win_get_width(target or win), "centered winbar did not fill the window")
end

local function highlight_at(rendered, needle)
  local offset = assert(rendered.str:find(needle, 1, true)) - 1
  local result
  for _, highlight in ipairs(rendered.highlights) do
    if highlight.start > offset then
      break
    end
    result = highlight.groups[#highlight.groups]
  end
  return result
end

local function contains(actual, expected)
  assert(actual:find(expected, 1, true), "missing `" .. expected .. "` in `" .. actual .. "`")
end

local function options(model, effort, mode)
  return {
    { id = "model", category = "model", type = "select", currentValue = model, options = {} },
    { id = "reasoning_effort", category = "thought_level", type = "select", currentValue = effort, options = {} },
    { id = "mode", category = "mode", type = "select", currentValue = mode, options = {} },
  }
end

local function notify(connection, update, session)
  connection:handle_incoming_request_or_notification({
    method = "session/update",
    params = { sessionId = session or connection.session_id, update = update },
  })
end

local function configure(connection, model, effort, mode)
  notify(connection, { sessionUpdate = "config_option_update", configOptions = options(model, effort, mode) })
end

local function report(connection, used, size, session)
  notify(connection, { sessionUpdate = "usage_update", used = used, size = size }, session)
end

local checks = 0
local function check(label, callback)
  callback()
  checks = checks + 1
  print("PASS: " .. label)
end

check("chat winbar waits for reported configuration and capacity", function()
  assert(vim.wo[win].winbar == status.winbar, "chat did not install its winbar")
  contains(bar(), "待连接")
  contains(bar(), "待上报")
  assert(not bar():find("0.0%%"), "unknown usage was shown as zero")
end)

assert(
  vim.wait(1000, function()
    return chat.acp_connection ~= nil
  end),
  "fixture ACP connection was not initialized"
)
local connection = chat.acp_connection
check("model, reasoning and permission follow ACP configuration", function()
  configure(connection, "gpt-6-astra", "max", "workspace-write")
  contains(bar(), "模型 gpt-6-astra")
  contains(bar(), "推理 max")
  contains(bar(), "权限 工作区")
end)

check("labels, values and separators use distinct theme highlights and remain centered", function()
  centered()
  local rendered = rendered_bar()
  assert(highlight_at(rendered, "模型") == "DotfilesCodexStatusLabel")
  assert(highlight_at(rendered, "gpt-6-astra") == "DotfilesCodexStatusValue")
  assert(highlight_at(rendered, "│") == "DotfilesCodexStatusSeparator")
  local label = api.nvim_get_hl(0, { name = "DotfilesCodexStatusLabel", link = false })
  local value = api.nvim_get_hl(0, { name = "DotfilesCodexStatusValue", link = false })
  assert(label.fg ~= value.fg and not label.bold and value.bold, "labels and values are not visually distinct")
end)

check("switching themes rebuilds the label and value highlights", function()
  api.nvim_set_hl(0, "Comment", { fg = 0x777777 })
  api.nvim_set_hl(0, "Special", { fg = 0xabcdef })
  api.nvim_exec_autocmds("ColorScheme", { pattern = "codecompanion-fixture" })
  assert(api.nvim_get_hl(0, { name = "DotfilesCodexStatusLabel", link = false }).fg == 0x777777)
  assert(api.nvim_get_hl(0, { name = "DotfilesCodexStatusValue", link = false }).fg == 0xabcdef)
end)

check("zero usage and percentage use the reported capacity", function()
  report(connection, 0, 272000)
  contains(bar(), "0/272k 0.0%")
  assert(chat.tokens == 0, "upstream usage handler was bypassed")
end)

check("reported context updates while the chat window is inactive", function()
  api.nvim_set_current_win(source_win)
  report(connection, 23665, 272000)
  contains(bar(), "23.7k/272k 8.7%")
  assert(chat.tokens == 23665, "upstream token state was not updated")
  assert(vim.wo[source_win].winbar == "existing source winbar", "source winbar was changed")
end)

check("compaction may decrease usage and values are not clamped to 100 percent", function()
  report(connection, 10000, 272000)
  contains(bar(), "10k/272k 3.7%")
  report(connection, 300000, 272000)
  contains(bar(), "300k/272k 110.3%")
end)

check("missing or zero capacity remains unknown without division by zero", function()
  report(connection, 50, 0)
  contains(bar(), "50/待上报 待上报")
  report(connection, 60, nil)
  contains(bar(), "60/待上报 待上报")
end)

check("invalid usage and foreign-session updates do not corrupt displayed state", function()
  local before = bar()
  for _, invalid in ipairs({ -1, math.huge, 0 / 0, "unknown" }) do
    report(connection, invalid, 272000)
    assert(bar() == before, "invalid usage reached the winbar")
  end
  report(connection, 999, 1000, "another-session")
  notify(connection, {
    sessionUpdate = "config_option_update",
    configOptions = options("foreign-model", "low", "agent-full-access"),
  }, "another-session")
  assert(bar() == before, "foreign-session state reached the winbar")
end)

check("switching models invalidates old capacity until a fresh report", function()
  report(connection, 23665, 272000)
  configure(connection, "gpt-6.1-sol", "medium", "workspace-write")
  contains(bar(), "gpt-6.1-sol")
  contains(bar(), "待上报")
  assert(not bar():find("272k", 1, true), "old model capacity was reused")
  configure(connection, "gpt-6-astra", "max", "workspace-write")
  contains(bar(), "待上报")
  assert(not bar():find("272k", 1, true), "returning to the old model revived stale usage")
  configure(connection, "gpt-6.1-sol", "medium", "workspace-write")
  report(connection, 42000, 200000)
  configure(connection, "gpt-6.1-sol", "high", "read-only")
  contains(bar(), "high")
  contains(bar(), "42k/200k 21.0%")
  contains(bar(), "只读")
end)

check("model changes invalidate usage even when no winbar redraw occurs between updates", function()
  configure(connection, "gpt-6-astra", "max", "workspace-write")
  report(connection, 23665, 272000)
  configure(connection, "gpt-6.1-sol", "medium", "workspace-write")
  configure(connection, "gpt-6-astra", "max", "workspace-write")
  contains(bar(), "待上报")
  assert(not bar():find("272k", 1, true), "rapid model changes revived stale usage before redraw")
  configure(connection, "gpt-6.1-sol", "high", "read-only")
  report(connection, 42000, 200000)
end)

check("client model requests invalidate usage before the next winbar render", function()
  local request = connection.send_rpc_request
  connection.send_rpc_request = function(_, method, params)
    assert(method == "session/set_config_option" and params.configId == "model", "unexpected client request")
    return { configOptions = options(params.value, "high", "read-only") }
  end
  for _, model in ipairs({ "gpt-6-astra", "gpt-6.1-sol" }) do
    assert(connection:set_model(model), "client could not set the model")
    chat:update_metadata()
  end
  connection.send_rpc_request = request
  contains(bar(), "待上报")
  assert(not bar():find("200k", 1, true), "client model changes retained stale usage")
  report(connection, 42000, 200000)
end)

check("restoring another session does not retain the previous session's usage", function()
  connection:use_session("restored-session")
  configure(connection, "gpt-6.1-sol", "high", "read-only")
  contains(bar(), "待上报")
  assert(not bar():find("42k", 1, true), "previous session usage leaked")
  report(connection, 1000, 10000, "fixture-" .. chat.bufnr)
  contains(bar(), "待上报")
  report(connection, 1000, 10000)
  contains(bar(), "1k/10k 10.0%")
end)

check("narrow windows retain all four values in compact form", function()
  api.nvim_win_set_width(win, 58)
  contains(bar(), "gpt-6.1-sol | high | 1k/10k 10.0% | 只读")
  centered()
end)

check("model names are literal text rather than evaluated statusline syntax", function()
  api.nvim_win_set_width(win, 170)
  configure(connection, "gpt%{1+1}%#ErrorMsg#\ncustom", "high", "read-only")
  contains(bar(), "gpt%{1+1}%#ErrorMsg# custom")
  assert(highlight_at(rendered_bar(), "%#ErrorMsg#") == "DotfilesCodexStatusValue", "model text injected a highlight")
  configure(connection, "gpt-6.1-sol", "high", "read-only")
end)

check("separate chats and hidden tabs keep independent connection state", function()
  local first_tab = api.nvim_get_current_tabpage()
  vim.cmd("tabnew")
  local second = assert(cc.chat())
  assert(
    vim.wait(1000, function()
      return second.acp_connection ~= nil
    end),
    "second fixture ACP connection was not initialized"
  )
  local second_win = second.ui.winnr
  api.nvim_win_set_width(second_win, 140)
  configure(second.acp_connection, "second-model", "low", "agent-full-access")
  report(second.acp_connection, 20, 100)
  contains(bar(second_win), "second-model")
  contains(bar(second_win), "20/100 20.0%")
  contains(bar(second_win), "完全访问")
  api.nvim_set_current_tabpage(first_tab)
  contains(bar(), "gpt-6.1-sol")
  assert(not bar():find("second-model", 1, true), "another chat's model leaked")
  second:close()
end)

check("hiding and reopening chat preserves the bar; source reuse clears it", function()
  chat.ui:hide()
  chat.ui:open()
  win = chat.ui.winnr
  contains(bar(), "gpt-6.1-sol")
  local normal = api.nvim_create_buf(true, false)
  api.nvim_set_current_win(win)
  api.nvim_win_set_buf(win, normal)
  assert(vim.wo[win].winbar ~= status.winbar, "chat winbar leaked into a normal buffer")
  chat:close()
end)

print("CodeCompanion status checks passed: " .. checks)
vim.cmd("qa!")
