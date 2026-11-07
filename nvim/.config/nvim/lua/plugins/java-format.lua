-- 保留 JDTLS 格式化，只覆盖 else 的换行风格。
return {
  {
    "mfussenegger/nvim-jdtls",
    opts = function(_, opts)
      opts.settings = vim.tbl_deep_extend("force", opts.settings or {}, {
        java = {
          format = {
            settings = {
              url = vim.fn.stdpath("config") .. "/java-formatter.xml",
              profile = "Personal",
            },
          },
        },
      })
    end,
  },
}
