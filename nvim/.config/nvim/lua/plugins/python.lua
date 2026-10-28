-- Python extra 配置 DAP 适配器；将调试工具也纳入 bootstrap 的依赖准备。
return {
  {
    "mason-org/mason.nvim",
    opts = { ensure_installed = { "debugpy" } },
  },
}
