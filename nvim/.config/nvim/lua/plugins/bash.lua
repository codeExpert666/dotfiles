-- 在 LazyVim 的默认 LSP 配置上启用 Bash 补全、跳转和诊断。
return {
  {
    "neovim/nvim-lspconfig",
    opts = {
      servers = {
        -- bashls 是 bash-language-server 的配置名；空表沿用默认设置。
        -- LazyVim 通过 Mason 安装服务，默认匹配 sh/bash 文件。
        bashls = {},
      },
    },
  },
}
