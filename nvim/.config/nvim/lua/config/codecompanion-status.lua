-- CodeCompanion v20.0.1：补充 ACP usage_update 中尚未保留的 size，用于聊天 winbar。
-- 更新插件时复核通知入口；上游提供完整用量元数据后可移除此通知包装。
local M = {}
M.winbar = [[%{%v:lua.require'config.codecompanion-status'.render()%}]]

local usage = setmetatable({}, { __mode = "k" })
local initialized, redraw_pending = false, false
local modes = {
  ["read-only"] = "只读",
  ["workspace-write"] = "工作区",
  agent = "自动审批",
  ["agent-full-access"] = "完全访问",
}

local function highlights()
  local normal = vim.api.nvim_get_hl(0, { name = "Normal", link = false })
  local bar = vim.api.nvim_get_hl(0, { name = "WinBar", link = false })
  local comment = vim.api.nvim_get_hl(0, { name = "Comment", link = false })
  local accent = vim.api.nvim_get_hl(0, { name = "Special", link = false })
  local separator = vim.api.nvim_get_hl(0, { name = "NonText", link = false })
  local background = bar.bg or normal.bg
  vim.api.nvim_set_hl(0, "DotfilesCodexStatusLabel", {
    fg = comment.fg or normal.fg,
    bg = background,
    bold = false,
    italic = false,
  })
  vim.api.nvim_set_hl(0, "DotfilesCodexStatusValue", {
    fg = accent.fg or normal.fg,
    bg = background,
    bold = true,
  })
  vim.api.nvim_set_hl(0, "DotfilesCodexStatusSeparator", { fg = separator.fg or comment.fg, bg = background })
end

local function span(group, value)
  -- winbar 会再次解析返回的格式串；代理提供的文本及百分号必须保持为字面量。
  return "%#DotfilesCodexStatus" .. group .. "#" .. value:gsub("%%", "%%%%")
end

local function redraw()
  if redraw_pending then
    return
  end
  redraw_pending = true
  vim.schedule(function()
    redraw_pending = false
    -- 无界面会话无需刷新屏幕；退出时也不再触碰已释放的窗口与 tabline。
    if vim.v.exiting == vim.NIL and #vim.api.nvim_list_uis() > 0 then
      vim.api.nvim__redraw({ winbar = true, flush = true })
    end
  end)
end

local function values(connection)
  local result = {}
  if connection and connection.session_id then
    for _, option in ipairs(connection:get_config_options()) do
      if option.type == "select" and type(option.currentValue) == "string" then
        result[option.category or option.id] = option.currentValue
      end
    end
  end
  return result
end

local function count(value)
  if value == nil then
    return "待上报"
  end
  local unit, divisor = "", 1
  if value >= 1000000 then
    unit, divisor = "m", 1000000
  elseif value >= 1000 then
    unit, divisor = "k", 1000
  end
  if divisor == 1 then
    return string.format("%.0f", value)
  end
  return string.format("%.1f", value / divisor):gsub("%.0$", "") .. unit
end

local function valid_count(value)
  return type(value) == "number" and value >= 0 and value < math.huge and value == math.floor(value)
end

local function text(value, fallback)
  return type(value) == "string" and value ~= "" and value:gsub("%c", " ") or fallback
end

function M.render()
  local win = tonumber(vim.g.statusline_winid) or vim.api.nvim_get_current_win()
  if not vim.api.nvim_win_is_valid(win) then
    return ""
  end
  local buf = vim.api.nvim_win_get_buf(win)
  if vim.bo[buf].filetype ~= "codecompanion" then
    return ""
  end
  local chat = require("codecompanion").buf_get_chat(buf)
  if not chat or chat.adapter.type ~= "acp" then
    return ""
  end

  local connection = chat.acp_connection
  local options = values(connection)
  local model = text(options.model, "待连接")
  local effort = text(options.thought_level, "待上报")
  local mode = text(modes[options.mode] or options.mode, "待上报")
  local current = connection and usage[connection]
  -- 用量属于具体会话及模型；恢复其他会话或切换模型后等待新的通知。
  if current and (current.session ~= connection.session_id or current.model ~= options.model) then
    usage[connection] = nil
    current = nil
  end
  local used, size = current and current.used, current and current.size
  local percent = used and size and string.format("%.1f%%", used / size * 100) or "待上报"
  local context = count(used) .. "/" .. count(size) .. " " .. percent
  local full = string.format("模型 %s │ 推理 %s │ 上下文 %s │ 权限 %s", model, effort, context, mode)
  local labeled = vim.fn.strdisplaywidth(full) <= vim.api.nvim_win_get_width(win)
  local fields = {}
  for _, field in ipairs({ { "模型", model }, { "推理", effort }, { "上下文", context }, { "权限", mode } }) do
    fields[#fields + 1] = (labeled and span("Label", field[1]) .. " " or "") .. span("Value", field[2])
  end
  -- 窄窗口省略字段名；字段顺序不变，仍保留用量、容量和比例。
  local separator = span("Separator", labeled and " │ " or " | ")
  return span("Label", "") .. "%=" .. table.concat(fields, separator) .. span("Label", "") .. "%="
end

function M.setup()
  if initialized then
    return
  end
  initialized = true
  local Connection = require("codecompanion.acp")
  local handle = Connection.handle_incoming_request_or_notification
  Connection.handle_incoming_request_or_notification = function(self, notification)
    local params = type(notification) == "table" and notification.params
    local update = type(params) == "table" and params.update
    local matched = type(notification) == "table"
      and notification.method == "session/update"
      and notification.id == nil
      and type(update) == "table"
      and params.sessionId ~= nil
      and params.sessionId == self.session_id
    if matched and update.sessionUpdate == "usage_update" and valid_count(update.used) then
      usage[self] = {
        session = params.sessionId,
        model = values(self).model,
        used = update.used,
        size = valid_count(update.size) and update.size > 0 and update.size or nil,
      }
    end
    local result = handle(self, notification)
    if matched and (update.sessionUpdate == "usage_update" or update.sessionUpdate == "config_option_update") then
      redraw()
    end
    return result
  end

  local group = vim.api.nvim_create_augroup("dotfiles_codecompanion_status", { clear = true })
  highlights()
  vim.api.nvim_create_autocmd("ColorScheme", {
    group = group,
    callback = function()
      highlights()
      redraw()
    end,
  })
  vim.api.nvim_create_autocmd("User", {
    group = group,
    pattern = {
      "CodeCompanionChatModel",
      "CodeCompanionChatACPConfigChanged",
      "CodeCompanionACPSessionPost",
      "CodeCompanionACPChatRestored",
    },
    callback = redraw,
  })
  vim.api.nvim_create_autocmd("BufWinEnter", {
    group = group,
    callback = function()
      if vim.bo.filetype ~= "codecompanion" and vim.wo.winbar == M.winbar then
        vim.cmd("setlocal winbar<")
      end
    end,
  })
end

return M
