local M = {}
local api = vim.api

local function is_editor(win)
  return win
    and api.nvim_win_is_valid(win)
    and api.nvim_win_get_tabpage(win) == api.nvim_get_current_tabpage()
    and api.nvim_win_get_config(win).relative == ""
    and not vim.w[win].sidekick_session_id
    and vim.bo[api.nvim_win_get_buf(win)].buftype == ""
    and not vim.wo[win].winfixbuf
end

function M.setup()
  local function remember_editor()
    local win = api.nvim_get_current_win()
    if is_editor(win) then
      vim.t.dotfiles_bufferline_editor = win
    end
  end
  api.nvim_create_autocmd({ "WinEnter", "BufEnter" }, {
    group = api.nvim_create_augroup("dotfiles_bufferline_click", { clear = true }),
    callback = remember_editor,
  })
  remember_editor()
end

-- Tabline 是全局的；从 Sidekick 点击文件时，先选择当前 tab 的编辑窗口。
-- 延迟执行以退出鼠标回调的 textlock，与 bufferline 默认字符串命令一致。
M.open = vim.schedule_wrap(function(buf)
  if not api.nvim_buf_is_valid(buf) then
    return
  end
  local current = api.nvim_get_current_win()
  if vim.w[current].sidekick_session_id then
    local target
    local wins = api.nvim_tabpage_list_wins(0)
    for _, win in ipairs(wins) do
      if is_editor(win) and api.nvim_win_get_buf(win) == buf then
        target = win
        break
      end
    end
    local recent = vim.t.dotfiles_bufferline_editor
    if not target and is_editor(recent) then
      target = recent
    end
    if not target then
      for _, win in ipairs(wins) do
        if is_editor(win) then
          target = win
          break
        end
      end
    end
    if target then
      api.nvim_set_current_win(target)
    else
      -- 只剩终端或工具窗口时，直接创建显示目标文件的编辑分屏。
      api.nvim_open_win(buf, true, { split = "left", win = current })
    end
    vim.cmd.stopinsert()
  end
  -- 不使用 bang，沿用 Neovim 对未保存内容的保护。
  vim.cmd.buffer(buf)
end)

return M
