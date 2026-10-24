-- Codex 通过 ACP 提供代理能力；CodeCompanion 负责聊天、上下文和改动审阅。
local function codex_adapter(name, mode)
  return function()
    return require("codecompanion.adapters").extend("codex", {
      name = name,
      formatted_name = name == "codex" and "Codex" or "Codex Inline",
      defaults = {
        auth_method = "chat-gpt",
        session_config_options = { mode = mode },
      },
    })
  end
end

return {
  {
    "olimorris/codecompanion.nvim",
    tag = "v20.0.1",
    cmd = { "CodeCompanion", "CodeCompanionChat", "CodeCompanionActions", "CodeCompanionCodeReview" },
    dependencies = { "nvim-lua/plenary.nvim", "mason-org/mason.nvim", "nvim-treesitter/nvim-treesitter" },
    config = function(_, opts)
      require("codecompanion").setup(opts)
      require("config.codecompanion-status").setup()
    end,
    opts = {
      adapters = {
        acp = {
          codex = codex_adapter("codex", "workspace-write"),
          -- 局部编辑从只读模式开始；插件仅批准对缓冲区临时副本的修改。
          codex_inline = codex_adapter("codex_inline", "read-only"),
        },
      },
      interactions = {
        chat = { adapter = "codex" },
        inline = { adapter = "codex_inline" },
        background = { chat = { opts = { enabled = false } } },
      },
      -- AGENTS.md、skills 和 MCP 由 Codex 加载，避免再次自动注入同一套规则。
      rules = { opts = { chat = { enabled = false } } },
      display = {
        chat = {
          window = {
            layout = "vertical",
            position = "right",
            width = 0.4,
            pertab = true,
            opts = { winbar = require("config.codecompanion-status").winbar },
          },
        },
        action_palette = { provider = "snacks" },
      },
      opts = { language = "Chinese" },
    },
    keys = {
      { "<leader>aa", "<cmd>CodeCompanionChat Toggle<cr>", desc = "Toggle Codex Chat" },
      { "<leader>an", "<cmd>CodeCompanionChat<cr>", desc = "New Codex Chat" },
      { "<leader>av", ":CodeCompanionChat Add<cr>", mode = "x", desc = "Add Selection to Codex" },
      { "<leader>ai", "<cmd>CodeCompanion<cr>", desc = "Codex Inline Edit" },
      { "<leader>ai", ":CodeCompanion<cr>", mode = "x", desc = "Codex Inline Edit Selection" },
      { "<leader>ap", "<cmd>CodeCompanionActions<cr>", mode = { "n", "x" }, desc = "Codex Actions" },
      { "<leader>ar", "<cmd>CodeCompanionCodeReview<cr>", desc = "Review Codex Changes" },
      { "<leader>ac", "<cmd>CodeCompanionCodeReview Comment<cr>", desc = "Comment on Code" },
      { "<leader>ac", ":CodeCompanionCodeReview Comment<cr>", mode = "x", desc = "Comment on Selection" },
    },
  },
  {
    "mason-org/mason.nvim",
    opts = { ensure_installed = { "codex-acp" } },
  },
  {
    "folke/which-key.nvim",
    optional = true,
    opts = { spec = { { "<leader>a", group = "ai", mode = { "n", "x" } } } },
  },
}
