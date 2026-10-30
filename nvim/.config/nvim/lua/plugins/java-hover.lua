-- Java hover 与补全文档共用 config.java-documentation；上游及移除条件见 README。
return {
  {
    "folke/noice.nvim",
    -- 在 opts 阶段安装回调，保留 LazyVim 的 Noice 初始化及其他配置。
    opts = function()
      local hover = require("noice.lsp.hover")
      local original = hover.on_hover
      hover.on_hover = function(err, result, ctx, ...)
        local client = ctx and ctx.client_id and vim.lsp.get_client_by_id(ctx.client_id)
        if not err and client and client.name == "jdtls" and result and result.contents then
          -- 解析 Markdown 可避开代码块、行内代码及普通网页链接。
          -- 缺少 parser 时保留原始文档，避免 hover 完全失效。
          local copy = vim.deepcopy(result)
          local max_width = vim.tbl_get(require("noice.config").options, "views", "hover", "size", "max_width")
          local width = math.min(tonumber(max_width) or 120, math.max(1, vim.o.columns - 4))
          copy.contents = require("config.java-documentation").clean(result.contents, width)
          result = copy
        end
        return original(err, result, ctx, ...)
      end
    end,
  },
  {
    "saghen/blink.cmp",
    opts = function(_, opts)
      opts.completion = opts.completion or {}
      opts.completion.documentation = opts.completion.documentation or {}
      local docs = opts.completion.documentation
      local original = docs.draw or function(ctx)
        return ctx.default_implementation()
      end
      docs.draw = function(ctx)
        local item = ctx.item
        local client = item.client_id and vim.lsp.get_client_by_id(item.client_id)
        local documentation = item.documentation
        -- Blink 的字符串文档表示 plaintext；只整理 JDTLS 的 Markdown。
        if
          client
          and client.name == "jdtls"
          and type(documentation) == "table"
          and documentation.kind == "markdown"
        then
          local cleaned = require("config.java-documentation").clean(documentation, ctx.window.config.max_width)
          -- 保留已有 draw 扩展和默认渲染器；不修改 textEdit、附加导入或候选缓存。
          local context = vim.tbl_extend("force", ctx, {
            item = vim.tbl_extend("force", item, { documentation = cleaned }),
            default_implementation = function(overrides)
              return ctx.default_implementation(vim.tbl_extend("force", { documentation = cleaned }, overrides or {}))
            end,
          })
          return original(context)
        end
        return original(ctx)
      end
    end,
  },
}
