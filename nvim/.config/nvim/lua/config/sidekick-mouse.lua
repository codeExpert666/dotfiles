local M = {}

function M.setup()
  local wheel = { [vim.keycode("<ScrollWheelUp>")] = 64, [vim.keycode("<ScrollWheelDown>")] = 65 }
  vim.on_key(function(key)
    local button = wheel[key]
    if not button then
      return
    end
    local mouse = vim.fn.getmousepos()
    if mouse.winid == 0 or mouse.winid == vim.api.nvim_get_current_win() or mouse.line == 0 then
      return
    end
    local id = vim.w[mouse.winid].sidekick_session_id
    local terminal = id and require("sidekick.cli.terminal").get(id)
    if
      not terminal
      or terminal.tool.name ~= "codex"
      or not terminal.tool.native_scroll
      or vim.api.nvim_win_get_buf(mouse.winid) ~= terminal.buf
      or not terminal:is_running()
    then
      return
    end
    -- 非活动终端的滚轮会被 Nvim 当作缓冲区滚动；直接发送 SGR 鼠标事件，保留编辑焦点。
    local sent =
      pcall(vim.api.nvim_chan_send, terminal.job, ("\27[<%d;%d;%dM"):format(button, mouse.wincol, mouse.winrow))
    if sent then
      return ""
    end
  end, vim.api.nvim_create_namespace("dotfiles.sidekick_mouse"))
end

return M
