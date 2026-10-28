return {
  {
    "catppuccin/nvim",
    name = "catppuccin",
    opts = {
      -- Neovim 接收终端的背景变化；不手动设置 background，以保留自动检测。
      flavour = "auto",
      background = { light = "latte", dark = "macchiato" },
    },
  },
  {
    "LazyVim/LazyVim",
    opts = {
      -- 使用插件入口，避免与 Neovim 0.12 内置的 catppuccin 重名。
      colorscheme = "catppuccin-nvim",
    },
  },
}
