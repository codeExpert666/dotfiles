return {
  {
    "akinsho/bufferline.nvim",
    opts = function(_, opts)
      local click = require("config.bufferline-click")
      click.setup()
      opts.options = opts.options or {}
      opts.options.left_mouse_command = click.open
    end,
  },
}
