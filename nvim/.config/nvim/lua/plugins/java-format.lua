-- 保留 JDTLS 格式化，仅调整本地声明的排版规则。
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
