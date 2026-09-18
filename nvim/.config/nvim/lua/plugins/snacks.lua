-- 设置 Snacks 文件浏览器、文件名搜索和内容搜索的显示范围。
return {
  {
    "folke/snacks.nvim",
    -- opts 会与 LazyVim 已有的 Snacks 配置合并。
    opts = {
      -- 文件浏览器也使用 picker，因此与搜索功能在此统一配置。
      picker = {
        -- 按来源分别配置，键名必须是 sources（复数），否则以下设置不会被读取。
        -- hidden = true：包含以 . 开头的文件和目录，例如 .gitignore、.config/。
        -- ignored = true：包含被 .gitignore 等规则忽略的文件，也会纳入依赖目录和构建产物。
        sources = {
          -- 文件浏览器（默认 Space e）：显示隐藏项和被忽略的项目。
          explorer = { hidden = true, ignored = true },
          -- 文件名搜索（默认 Space Space）：搜索范围包含隐藏项和被忽略的文件。
          files = { hidden = true, ignored = true },
          -- 内容搜索（默认 Space /）：同时搜索隐藏文件和被忽略文件中的文本。
          grep = { hidden = true, ignored = true },
        },
      },
    },
  },
}
