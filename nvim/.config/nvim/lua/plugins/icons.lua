-- 字形、文件规则和品牌例外统一维护于 config.icons；这里只连接插件接口。
local icons = require("config.icons")
local g = icons.glyphs
local function spaced(values)
  return vim.tbl_map(function(icon)
    return icon .. " "
  end, values)
end
local diagnostics, kinds = spaced(icons.diagnostics), spaced(icons.kinds)

local function status_icon(value)
  -- 只转换开头的已知装饰；路径和状态文字内部的相同字符保持原样。
  for from, to in pairs(icons.statusline_replacements) do
    if value:sub(1, #from) == from then
      return to .. value:sub(#from + 1)
    end
  end
  return value
end

return {
  {
    "LazyVim/LazyVim",
    opts = {
      icons = {
        diagnostics = diagnostics,
        git = spaced(icons.git),
        kinds = kinds,
        ft = { octo = g.github, gh = g.github, ["markdown.gh"] = g.github },
        dap = {
          Stopped = { g.stopped .. " ", "DiagnosticWarn", "DapStoppedLine" },
          Breakpoint = g.breakpoint .. " ",
          BreakpointCondition = g.breakpoint_condition .. " ",
          BreakpointRejected = { g.breakpoint_rejected .. " ", "DiagnosticError" },
          LogPoint = g.logpoint .. " ",
        },
      },
    },
    init = function()
      vim.api.nvim_create_autocmd("User", {
        pattern = "VeryLazy",
        once = true,
        callback = function()
          vim.opt.fillchars:append({ foldopen = g.chevron_down, foldclose = g.chevron_right })
        end,
      })
    end,
  },
  {
    "nvim-mini/mini.icons",
    opts = function(_, opts)
      local filetypes = {
        javascriptreact = g.react,
        typescriptreact = g.react,
        lhaskell = g.haskell,
        solidity = g.ethereum,
        proto = g.google,
        sh = g.bash,
        bash = g.bash,
        zsh = g.console,
        env = g.config,
        dotenv = g.config,
        lazy = g.lazy,
        trouble = g.debug,
        ["neotest-summary"] = g.test,
        ["neotest-output"] = g.test,
        codecompanion = g.robot,
      }
      opts.filetype = opts.filetype or {}
      for name, glyph in pairs(filetypes) do
        opts.filetype[name] = vim.tbl_extend("force", opts.filetype[name] or {}, { glyph = glyph })
      end
      for _, name in ipairs({ "env", "dotenv" }) do
        opts.filetype[name].hl = "MiniIconsYellow"
      end
      opts.file = vim.tbl_deep_extend("force", opts.file or {}, icons.files)
      opts.directory = vim.tbl_deep_extend("force", opts.directory or {}, {
        [".git"] = { glyph = g.git },
        [".github"] = { glyph = g.github },
      })
      opts.lsp = opts.lsp or {}
      for kind, glyph in pairs(icons.kinds) do
        local name = kind:lower()
        opts.lsp[name] = vim.tbl_extend("force", opts.lsp[name] or {}, { glyph = glyph })
      end
    end,
    config = function(_, opts)
      local mini = require("mini.icons")
      mini.setup(opts)
      require("config.icon-files").setup(mini)
      -- LazyVim 的 devicons preload 入口保留，两种加载顺序共用相同 getter。
    end,
  },
  {
    "folke/snacks.nvim",
    opts = function(_, opts)
      opts.picker = vim.tbl_deep_extend("force", opts.picker or {}, {
        prompt = g.search .. " ",
        icons = {
          diagnostics = diagnostics,
          kinds = kinds,
          files = spaced({ dir = g.folder, dir_open = g.folder_open, file = g.file }),
          git = spaced({
            commit = g.commit,
            staged = g.success,
            added = g.added,
            modified = g.modified,
            deleted = g.removed,
            renamed = g.renamed,
            ignored = g.ignored,
            unmerged = g.unmerged,
            untracked = g.untracked,
          }),
          undo = { saved = g.save .. " " },
          lsp = spaced({ unavailable = g.close, enabled = g.enabled, disabled = g.disabled, attached = g.connection }),
          ui = spaced({ live = g.play, selected = g.circle_filled, unselected = g.circle_outline }),
        },
      })
      opts.notifier = vim.tbl_deep_extend("force", opts.notifier or {}, {
        icons = spaced({ error = g.error, warn = g.warn, info = g.info, debug = g.debug, trace = g.trace }),
      })
      opts.input = vim.tbl_extend("force", opts.input or {}, { icon = g.edit .. " " })
      opts.profiler = vim.tbl_deep_extend("force", opts.profiler or {}, {
        icons = spaced({
          time = g.clock,
          pct = g.percent,
          count = g.restart,
          require = g.lua,
          modname = icons.kinds.Module,
          plugin = g.package,
          autocmd = g.event,
          file = g.file,
          fn = icons.kinds.Function,
          status = g.flame,
        }),
      })
      opts.toggle = vim.tbl_deep_extend(
        "force",
        opts.toggle or {},
        { icon = spaced({ enabled = g.enabled, disabled = g.disabled }) }
      )
      local dashboard = opts.dashboard or {}
      for _, item in ipairs(vim.tbl_get(dashboard, "preset", "keys") or {}) do
        item.icon = icons.dashboard[item.key] and icons.dashboard[item.key] .. " " or item.icon
      end
      dashboard.sections = dashboard.sections
        or {
          { section = "header" },
          { section = "keys", gap = 1, padding = 1 },
          { section = "startup" },
        }
      for _, section in ipairs(dashboard.sections) do
        if type(section) == "table" and section.section == "startup" then
          section.icon = g.event .. " "
        end
      end
      opts.dashboard = dashboard
    end,
  },
  {
    "folke/noice.nvim",
    opts = {
      cmdline = {
        format = {
          cmdline = { icon = g.chevron_right },
          search_down = { icon = g.search .. " " .. g.chevron_down },
          search_up = { icon = g.search .. " " .. g.chevron_up },
          filter = { icon = g.console },
          lua = { icon = g.lua },
          help = { icon = g.question },
          calculator = { icon = icons.kinds.Operator },
          input = { icon = g.edit },
        },
      },
      popupmenu = { kind_icons = icons.kinds },
      format = { level = { icons = spaced({ error = g.error, warn = g.warn, info = g.info }) } },
      lsp = {
        progress = {
          format_done = {
            { g.success .. " ", hl_group = "NoiceLspProgressSpinner" },
            { "{data.progress.title} ", hl_group = "NoiceLspProgressTitle" },
            { "{data.progress.client} ", hl_group = "NoiceLspProgressClient" },
          },
        },
      },
    },
  },
  {
    "folke/which-key.nvim",
    opts = {
      icons = {
        breadcrumb = g.chevron_right,
        separator = g.arrow_right,
        keys = spaced({ Up = g.arrow_up, Down = g.arrow_down, Left = g.arrow_left, Right = g.arrow_right }),
        rules = {
          { plugin = "persistence.nvim", icon = g.save, color = "azure" },
          { plugin = "sidekick.nvim", icon = g.robot, color = "green" },
          { plugin = "codecompanion.nvim", icon = g.robot, color = "green" },
          { pattern = "profiler", icon = g.event, color = "orange" },
          { pattern = "%f[%a]ai", icon = g.robot, color = "green" },
          { pattern = "find", icon = g.search, color = "green" },
          { pattern = "search", icon = g.search, color = "green" },
          { pattern = "terminal", icon = g.console, color = "red" },
          { pattern = "window", icon = g.window, color = "blue" },
          { pattern = "format", icon = g.format, color = "cyan" },
          { pattern = "code", icon = g.code, color = "orange" },
          { pattern = "toggle", icon = g.enabled, color = "yellow" },
          { pattern = "session", icon = g.save, color = "azure" },
        },
      },
    },
  },
  {
    "nvim-lualine/lualine.nvim",
    opts = function(_, opts)
      -- fmt 是公开接口；保留上游组件的条件、颜色、位置及已有格式化。
      for _, section in pairs(opts.sections) do
        for index, component in ipairs(section) do
          if component == "branch" then
            component = { "branch" }
            section[index] = component
          end
          if type(component) == "function" then
            component = { component }
            section[index] = component
          end
          if type(component) == "table" then
            if component[1] == "branch" then
              component.icon = g.branch
            end
            if type(component[1]) == "function" then
              local format = component.fmt
              component.fmt = function(value, context)
                return status_icon(format and format(value, context) or value)
              end
            end
          end
        end
      end
    end,
  },
  {
    "akinsho/bufferline.nvim",
    opts = function(_, opts)
      opts.options = vim.tbl_extend("force", opts.options or {}, {
        buffer_close_icon = g.close,
        close_icon = g.close,
        modified_icon = g.circle,
        left_trunc_marker = g.chevron_left,
        right_trunc_marker = g.chevron_right,
      })
      -- 插件公开的内置分组接口；保留已有分组和默认固定标签行为。
      require("bufferline.groups").builtin.pinned:with({ icon = g.pin })
    end,
  },
  {
    "lewis6991/gitsigns.nvim",
    opts = {
      signs = { delete = { text = g.chevron_right }, topdelete = { text = g.chevron_right } },
      signs_staged = { delete = { text = g.chevron_right }, topdelete = { text = g.chevron_right } },
    },
  },
  {
    "folke/trouble.nvim",
    opts = {
      icons = {
        kinds = kinds,
        folder_closed = g.folder,
        folder_open = g.folder_open,
        indent = { fold_open = g.chevron_down .. " ", fold_closed = g.chevron_right .. " " },
      },
    },
  },
  {
    "folke/todo-comments.nvim",
    opts = {
      keywords = {
        FIX = { icon = g.debug .. " " },
        TODO = { icon = g.success .. " " },
        HACK = { icon = g.flame .. " " },
        WARN = { icon = g.warn .. " " },
        PERF = { icon = g.clock .. " " },
        NOTE = { icon = g.info .. " " },
        TEST = { icon = g.test .. " " },
      },
    },
  },
  {
    "MagicDuck/grug-far.nvim",
    opts = {
      icons = {
        fileIconsProvider = "mini.icons",
        actionEntryBullet = g.chevron_right .. " ",
        searchInput = g.search .. " ",
        replaceInput = g.replace .. " ",
        filesFilterInput = g.filter .. " ",
        flagsInput = g.config .. " ",
        pathsInput = g.folder .. " ",
        resultsStatusReady = g.play .. " ",
        resultsStatusError = g.error .. " ",
        resultsStatusSuccess = g.success .. " ",
        resultsActionMessage = g.info .. " ",
        historyTitle = g.history .. " ",
        helpTitle = g.question .. " ",
        lineNumbersEllipsis = g.dots .. " ",
        newline = g.return_key .. " ",
      },
    },
  },
  {
    "rcarriga/nvim-dap-ui",
    optional = true,
    opts = {
      icons = { expanded = g.chevron_down, collapsed = g.chevron_right, current_frame = g.chevron_right },
      controls = {
        icons = {
          pause = g.pause,
          play = g.play,
          step_into = g.step_into,
          step_over = g.step_over,
          step_out = g.step_out,
          step_back = g.step_back,
          run_last = g.restart,
          terminate = g.stop,
          disconnect = g.disconnect,
        },
      },
    },
  },
  {
    "nvim-neotest/neotest",
    optional = true,
    opts = {
      icons = {
        passed = g.success,
        running = g.play,
        failed = g.error,
        skipped = g.skip,
        unknown = g.question,
        watching = g.eye,
        test = g.test,
        notify = g.test,
        dir = g.folder,
        file = g.file,
        namespace = icons.kinds.Namespace,
      },
    },
  },
  {
    "MeanderingProgrammer/render-markdown.nvim",
    optional = true,
    opts = function(_, opts)
      local defaults = require("render-markdown").default
      opts.callout = opts.callout or {}
      for name, glyph in pairs(icons.callouts) do
        local original = defaults.callout[name]
        if original then
          local label = original.rendered:match("%s.*$") or ""
          opts.callout[name] = vim.tbl_extend("force", opts.callout[name] or {}, { rendered = glyph .. label })
        end
      end
      opts.link = vim.tbl_deep_extend("force", opts.link or {}, {
        image = g.image .. " ",
        email = g.email .. " ",
        hyperlink = g.link .. " ",
        custom = {
          apple = { icon = g.apple .. " " },
          microsoft = { icon = g.microsoft .. " " },
          neovim = { icon = g.neovim .. " " },
          hackernews = { icon = g.web .. " " },
          steam = { icon = g.steam .. " " },
          twitter = { icon = g.twitter .. " " },
          x = { icon = g.web .. " " },
        },
      })
    end,
  },
  {
    "folke/sidekick.nvim",
    optional = true,
    opts = {
      ui = {
        icons = spaced({
          nes = g.robot,
          attached = g.enabled,
          started = g.disabled,
          installed = g.package,
          missing = g.close,
          external_attached = g.connection,
          external_started = g.connection_off,
          terminal_attached = g.console,
          terminal_started = g.console,
        }),
      },
    },
  },
  {
    "olimorris/codecompanion.nvim",
    optional = true,
    opts = {
      display = {
        icons = { warning = g.warn .. " " },
        chat = {
          icons = spaced({
            sync_all = g.sync,
            sync_diff = g.diff,
            chat_fold = g.chevron_right,
            tool_pending = g.pending,
            tool_in_progress = g.play,
            tool_failure = g.error,
            tool_success = g.success,
          }),
        },
      },
      interactions = { code_review = { display = { comments = { icon = g.comment .. " " } } } },
    },
  },
  {
    "mason-org/mason.nvim",
    optional = true,
    opts = {
      ui = {
        icons = { package_installed = g.success, package_pending = g.pending, package_uninstalled = g.circle_outline },
      },
    },
  },
}
