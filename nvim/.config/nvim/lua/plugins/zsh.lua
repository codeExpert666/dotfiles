-- 使用 Zsh 自身的语法检查，通过 nvim-lint 显示诊断。
return {
  {
    "mfussenegger/nvim-lint",
    opts = {
      linters_by_ft = {
        -- 为 zsh 文件类型启用 nvim-lint 内置的 zsh 检查器。
        zsh = { "zsh" },
      },
      linters = {
        -- 其余设置（zsh 命令和诊断解析规则等）沿用内置定义。
        zsh = {
          -- 不传入缓冲区内容；nvim-lint 自动追加文件路径，检查磁盘上已保存的内容。
          stdin = false,
          -- 覆盖默认参数，移除用于标准输入的 /dev/stdin，与 stdin = false 配套。
          args = {
            -- 只解析语法，不执行被检查脚本中的命令（相当于 zsh -n）。
            "--no-exec",
            -- 禁用 RCS，跳过后续启动文件；系统级 zshenv 仍会读取。
            "--no-rcs",
            -- 同时禁用 GLOBAL_RCS，跳过后续全局启动文件。
            "--no-globalrcs",
          },
        },
      },
    },
  },
}
