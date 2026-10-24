-- 文件树和搜索统一使用 Snacks；目录合并暂时依赖固定的 PR #2907。
return {
  {
    "folke/snacks.nvim",
    url = "https://github.com/Dawe257/snacks.nvim.git",
    branch = "explorer/compact-folders",
    commit = "0e163b4982b0d48eb2853383b0ffb04d49c0625a",
    -- opts 会与 LazyVim 已有的 Snacks 配置合并。
    opts = function(_, opts)
      local compact = require("config.snacks-compact")
      compact.setup()
      return vim.tbl_deep_extend("force", opts, {
        picker = {
          -- hidden：显示点文件；ignored：包含被忽略的文件和构建产物。
          sources = {
            explorer = {
              hidden = true,
              ignored = true,
              compact_folders = true,
              transform = compact.transform,
            },
            files = { hidden = true, ignored = true },
            grep = { hidden = true, ignored = true },
          },
        },
      })
    end,
  },
}
