-- Material Design Icons 优先（Nerd Fonts 的 nf-md-*）；缺少专属标志时保留上游图标。
-- 仅替换造型，沿用 Catppuccin 和各插件的高亮。
local diagnostics = { Error = "󰀨 ", Warn = "󰀦 ", Hint = "󰌵 ", Info = "󰋼 " }
local git = { added = "󰝒 ", modified = "󱇧 ", removed = "󱪡 " }

-- 补全与符号列表共用；AI 产品标志保留 LazyVim 的默认图标。
local kinds = {
  Array = "󰅨 ", -- code_array
  Boolean = "󰔡 ", -- toggle_switch
  Class = "󰠱 ", -- shape
  Color = "󰏘 ", -- palette
  Constant = "󰇼 ", -- equal
  Constructor = "󱌣 ", -- hammer_wrench
  Enum = "󰉻 ", -- format_list_numbered
  EnumMember = "󰉻 ", -- format_list_numbered
  Event = "󱐋 ", -- lightning_bolt
  Field = "󰠵 ", -- table_column
  File = "󰧮 ", -- file_document_outline
  Folder = "󰉋 ", -- folder
  Function = "󰊕 ", -- function
  Interface = "󰌘 ", -- lan_connect
  Key = "󰌋 ", -- key_variant
  Keyword = "󰌆 ", -- key
  Method = "󰡱 ", -- function_variant
  Module = "󰕳 ", -- view_module
  Namespace = "󰅩 ", -- code_braces
  Null = "󰟢 ", -- null
  Number = "󰎠 ", -- numeric
  Object = "󰆧 ", -- cube_outline
  Operator = "󰃬 ", -- calculator
  Package = "󰏖 ", -- package_variant
  Property = "󰘮 ", -- tune
  Reference = "󰌹 ", -- link_variant
  Snippet = "󰆐 ", -- content_cut
  String = "󰅳 ", -- code_string
  Struct = "󰕴 ", -- view_quilt
  Text = "󰦨 ", -- text
  TypeParameter = "󰬛 ", -- alpha_t_box
  Unit = "󰑭 ", -- ruler
  Value = "󰻾 ", -- identifier
  Variable = "󰫧 ", -- variable
  Control = "󱃖 ", -- code_braces_box
  Collapsed = "󰅂 ", -- chevron_right
}

local dashboard = {
  f = "󰍉 ",
  n = "󰝒 ",
  g = "󱎸 ",
  r = "󰋚 ",
  c = "󰒓 ",
  s = "󰦛 ",
  x = "󰐱 ",
  l = "󰒲 ",
  q = "󰍃 ",
  p = "󰉓 ",
}

return {
  {
    "LazyVim/LazyVim",
    opts = {
      icons = {
        diagnostics = diagnostics,
        git = git,
        kinds = kinds,
        dap = {
          Stopped = { "󰜴 ", "DiagnosticWarn", "DapStoppedLine" },
          Breakpoint = "󰝥 ",
          BreakpointCondition = "󰋗 ",
          BreakpointRejected = { "󰅙 ", "DiagnosticError" },
          LogPoint = "󰧭 ",
        },
      },
    },
  },
  {
    "nvim-mini/mini.icons",
    opts = function(_, opts)
      -- mini.icons 默认已优先选 Material；补齐有对应图标的 React、Shell 等项。
      -- YAML、TOML 等没有对应 Material 标志，继续沿用其专属 Logo。
      opts.filetype = vim.tbl_deep_extend("force", opts.filetype or {}, {
        javascriptreact = { glyph = "󰜈" },
        typescriptreact = { glyph = "󰜈" },
        lhaskell = { glyph = "󰲒" },
        solidity = { glyph = "󰡪" },
        proto = { glyph = "󰊭" },
        sh = { glyph = "󱆃" },
        bash = { glyph = "󱆃" },
        zsh = { glyph = "󰆍" },
      })
      opts.file = vim.tbl_deep_extend("force", opts.file or {}, {
        ["devcontainer.json"] = { glyph = "󰡨" },
      })
      opts.directory = vim.tbl_deep_extend("force", opts.directory or {}, {
        [".git"] = { glyph = "󱞞" },
        [".github"] = { glyph = "󰊤" },
      })
      -- mini.icons 的 LSP 默认采用 Codicons；与 Blink、Snacks 同步替换。
      opts.lsp = opts.lsp or {}
      for kind, icon in pairs(kinds) do
        local key = kind:lower()
        opts.lsp[key] = vim.tbl_extend("force", opts.lsp[key] or {}, { glyph = vim.trim(icon) })
      end
    end,
  },
  {
    "folke/snacks.nvim",
    opts = function(_, opts)
      opts.picker = vim.tbl_deep_extend("force", opts.picker or {}, {
        prompt = "󰍉 ",
        icons = {
          diagnostics = diagnostics,
          kinds = kinds,
          git = {
            commit = "󰜘 ",
            staged = "󰄬 ",
            added = git.added,
            modified = git.modified,
            deleted = git.removed,
            renamed = "󰪹 ",
            ignored = "󰈉 ",
            unmerged = "󰘭 ",
            untracked = "󰡯 ",
          },
          undo = { saved = "󰆓 " },
          lsp = { unavailable = "󰅖", enabled = "󰔡 ", disabled = "󰔢 " },
        },
      })
      opts.notifier = vim.tbl_deep_extend("force", opts.notifier or {}, {
        icons = {
          error = diagnostics.Error,
          warn = diagnostics.Warn,
          info = diagnostics.Info,
          debug = "󰃤 ",
          trace = "󰴠 ",
        },
      })
      for _, item in ipairs(opts.dashboard.preset.keys) do
        item.icon = dashboard[item.key] or item.icon
      end
    end,
  },
  {
    "folke/noice.nvim",
    opts = {
      cmdline = {
        format = {
          cmdline = { icon = "󰅂" },
          search_down = { icon = "󰍉 󰄼" },
          search_up = { icon = "󰍉 󰄿" },
          filter = { icon = "󰆍" },
          lua = { icon = "󰢱" },
          help = { icon = "󰋗" },
          calculator = { icon = "󰃬" },
        },
      },
    },
  },
  {
    "folke/which-key.nvim",
    opts = {
      icons = {
        rules = {
          { pattern = "find", icon = "󰍉", color = "green" },
          { pattern = "search", icon = "󰍉", color = "green" },
          { pattern = "terminal", icon = "󰆍", color = "red" },
          { pattern = "window", icon = "󰖯", color = "blue" },
          { pattern = "format", icon = "󰉢", color = "cyan" },
          { pattern = "code", icon = "󰅴", color = "orange" },
          { pattern = "toggle", icon = "󰔡", color = "yellow" },
          { pattern = "session", icon = "󰆓", color = "azure" },
        },
      },
    },
  },
  {
    "nvim-lualine/lualine.nvim",
    opts = function(_, opts)
      opts.sections.lualine_b = { { "branch", icon = "󰘬" } }
    end,
  },
  {
    "akinsho/bufferline.nvim",
    opts = {
      options = {
        buffer_close_icon = "󰅖",
        close_icon = "󰅖",
        modified_icon = "󰧟",
        left_trunc_marker = "󰅁",
        right_trunc_marker = "󰅂",
      },
    },
  },
}
