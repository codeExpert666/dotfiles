-- LazyVim 的 Sidekick extra 提供终端与状态集成；这里只使用独立 Codex CLI。
local function codex(action, opts)
  local cli = require("sidekick.cli")
  if action == "prompt" then
    return cli.prompt({
      cb = function(_, text)
        if text then
          codex("send", { text = text })
        end
      end,
    })
  end

  -- Sidekick 默认优先复用任意已连接会话；切换 :tcd 后只复用当前目录。
  -- 没有当前目录会话时显式创建；裸工具没有 cwd，不能用 cwd 过滤器启动。
  local filter = { name = "codex" }
  if action ~= "select" then
    local State, Config = require("sidekick.cli.state"), require("sidekick.config")
    if #State.get({ name = "codex", cwd = true }) == 0 then
      if action == "hide" or action == "close" then
        return
      end
      local tool = Config.get_tool("codex")
      if vim.fn.executable(tool.cmd[1]) == 0 or (Config.cli.mux.enabled and vim.fn.executable("tmux") == 0) then
        vim.notify("Sidekick requires codex and tmux on Neovim's PATH; see docs/sidekick.md", vim.log.levels.ERROR)
        return
      end
      -- 创建终端会切换窗口并退出可视模式，先保存本次选区上下文。
      if action == "send" and not opts.text then
        local message, text = cli.render(opts)
        if not text or message == "" then
          return vim.notify("Nothing to send to Codex.", vim.log.levels.WARN)
        end
        opts = vim.tbl_extend("force", opts, { text = text })
      end
      local state = State.attach({ tool = tool }, { show = true, focus = true })
      if action == "toggle" or action == "focus" then
        return state
      end
    end
    filter.cwd = true
  end
  return cli[action](vim.tbl_extend("force", opts or {}, { filter = filter }))
end

return {
  {
    "folke/sidekick.nvim",
    opts = {
      nes = { enabled = false },
      copilot = { status = { enabled = false } },
      cli = {
        mux = { enabled = true, backend = "tmux", create = "terminal" },
        -- 本机包装器按次覆盖桌面 MCP，已需 embedded 模式；显式选择以免回退警告。
        tools = {
          codex = {
            cmd = { "codex", "--no-daemon" },
            -- 交给 Codex 滚动正文；Sidekick 的整屏回看会把输入框一起滚走。
            native_scroll = true,
          },
        },
        win = {
          layout = "right",
          split = { width = 0.3 },
          config = function(terminal)
            -- 只调整 Sidekick 自建会话，避免 tmux 的默认 Ctrl-b 前缀吞掉 CLI 按键。
            if terminal.mux_backend == "tmux" and terminal.mux_session then
              for _, option in ipairs({ "prefix", "prefix2" }) do
                vim.list_extend(terminal.tool.cmd, {
                  ";",
                  "set-option",
                  "-t",
                  terminal.mux_session,
                  option,
                  "None",
                })
              end
            end
          end,
          -- 保留 Codex 的历史、光标移动和控制键；Ctrl-. 统一切换焦点。
          keys = {
            buffers = false,
            files = false,
            prompt = false,
            hide_ctrl_dot = false,
            hide_ctrl_z = false,
            nav_left = false,
            nav_down = false,
            nav_up = false,
            nav_right = false,
          },
        },
      },
    },
    config = function(_, opts)
      -- extra 的 opts 会注册 NES 开关；CLI 专用配置不暴露这个入口。
      pcall(vim.keymap.del, "n", "<leader>uN")
      LazyVim.cmp.actions.ai_nes = nil
      require("sidekick").setup(opts)
      require("config.sidekick-mouse").setup()
    end,
    keys = {
      { "<tab>", false, mode = "n" },
      { "<leader>a", false, mode = { "n", "v" } },
      {
        "<c-.>",
        function()
          codex("focus")
        end,
        mode = { "n", "t", "i", "x" },
        desc = "Codex CLI: Toggle Focus",
      },
      {
        "<leader>aa",
        function()
          codex("toggle")
        end,
        desc = "Codex CLI: Toggle Terminal",
      },
      {
        "<leader>as",
        function()
          codex("select")
        end,
        desc = "Codex CLI: Select Session",
      },
      {
        "<leader>ad",
        function()
          codex("close")
        end,
        desc = "Codex CLI: Detach Terminal (Keep Running)",
      },
      {
        "<leader>ah",
        function()
          codex("hide")
        end,
        desc = "Codex CLI: Hide Terminal",
      },
      {
        "<leader>at",
        function()
          codex("send", { msg = "{position}", submit = false })
        end,
        desc = "Codex CLI: Add Cursor Location",
      },
      {
        "<leader>at",
        function()
          codex("send", { msg = "{position}\n{selection}", submit = false })
        end,
        mode = "x",
        desc = "Codex CLI: Add Selection and Location",
      },
      {
        "<leader>af",
        function()
          codex("send", { msg = "{file}", submit = false })
        end,
        desc = "Codex CLI: Add File Reference",
      },
      {
        "<leader>av",
        function()
          codex("send", { msg = "{position}\n{selection}", submit = false })
        end,
        mode = "x",
        desc = "Codex CLI: Add Selection and Location",
      },
      {
        "<leader>ap",
        function()
          codex("prompt")
        end,
        mode = { "n", "x" },
        desc = "Codex CLI: Add Prompt or Context",
      },
    },
  },
  {
    "folke/snacks.nvim",
    optional = true,
    -- 不接管通用 picker 的 Alt-a；从编辑器显式添加 Codex 上下文。
    opts = { picker = { win = { input = { keys = { ["<a-a>"] = false } } } } },
  },
  {
    "folke/which-key.nvim",
    optional = true,
    opts = { spec = { { "<leader>a", group = "AI (Codex CLI)", mode = { "n", "x" } } } },
  },
}
