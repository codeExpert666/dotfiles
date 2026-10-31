local M = {}

function M.setup()
  vim.api.nvim_create_autocmd("ColorScheme", {
    group = vim.api.nvim_create_augroup("dotfiles_lazygit_appearance", { clear = true }),
    callback = function()
      vim.schedule(function()
        -- Neovim 会回答 OSC 11 查询，但不会主动通知已运行的内嵌程序。
        -- 仅向 Snacks 管理的 Lazygit 发送背景回复，保留进程及正在进行的操作。
        local terminal = package.loaded["snacks.terminal"]
        if not terminal then
          return
        end
        local color = vim.o.background == "light" and "ffff/ffff/ffff" or "0000/0000/0000"
        for _, term in ipairs(terminal.list()) do
          if type(term.cmd) == "table" and term.cmd[1] == "lazygit" and term:buf_valid() then
            local channel = vim.bo[term.buf].channel
            if channel > 0 then
              -- 退出与主题事件可能同时发生，已关闭的通道无需处理。
              pcall(vim.api.nvim_chan_send, channel, "\027]11;rgb:" .. color .. "\007")
            end
          end
        end
      end)
    end,
  })
end

return M
