-- 在 LazyVim 的默认 LSP 配置上，为 Zsh 文件启用 Shuck 语言服务器。
-- 格式化读取 Shuck 自身的配置：全局默认位于 ~/.config/shuck/shuck.toml，项目配置优先。
return {
  {
    "neovim/nvim-lspconfig",
    opts = {
      servers = {
        -- shuck 是 nvim-lspconfig 的服务配置名，默认通过 shuck server 启动。
        shuck = {
          -- 不交给 Mason 安装；使用 PATH 中已安装的 shuck。
          mason = false,
          -- 只匹配 zsh 文件类型；sh/bash 由 bash.lua 中的 bashls 处理。
          filetypes = { "zsh" },
        },
      },
    },
  },
}
