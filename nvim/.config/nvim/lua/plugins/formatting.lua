-- 在 LazyVim 的默认格式化配置上，让 Bash/POSIX Shell 遵循仓库的 shfmt -ci -sr 约定。
return {
  {
    "stevearc/conform.nvim",
    opts = {
      formatters = {
        shfmt = {
          -- 完整覆盖参数，避免 Conform 根据 expandtab/shiftwidth 改用空格缩进。
          args = {
            -- 当前文件名用于识别脚本类型；占位符由 Conform 替换。
            "-filename",
            "$FILENAME",
            -- 0 表示使用 Tab 缩进，与 shfmt 的默认值一致。
            "-i",
            "0",
            -- case 分支标签缩进一级，分支内的命令继续缩进。
            "-ci",
            -- 重定向符号与目标之间留空格，例如 > file、2> error.log。
            "-sr",
          },
        },
      },
    },
  },
}
