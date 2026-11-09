-- MyBatis XML：保留手写 SQL 排版，离线补全关键字；不连接数据库。
return {
  {
    "neovim/nvim-lspconfig",
    opts = function(_, opts)
      local server = opts.servers.lemminx or {}
      server.settings = server.settings or {}
      server.settings.xml = server.settings.xml or {}
      local xml = server.settings.xml
      xml.format = xml.format or {}
      -- 部分 format 设置会令 LemMinX 将缺省 enabled 读为 false，需显式保留格式化。
      xml.format.enabled = true
      -- 标签、注释之间保留最多两行空白；部分设置对象不会自动补齐服务端默认值。
      xml.format.preservedNewlines = 2
      -- preserveSpace 是替换列表：保留 LemMinX 默认项，再追加 SQL 容器。
      local preserve = xml.format.preserveSpace
        or {
          "xsl:text",
          "xsl:comment",
          "xsl:processing-instruction",
          "literallayout",
          "programlisting",
          "screen",
          "synopsis",
          "pre",
          "xd:pre",
        }
      for _, tag in ipairs({ "select", "insert", "update", "delete", "sql", "selectKey" }) do
        if not vim.tbl_contains(preserve, tag) then
          table.insert(preserve, tag)
        end
      end
      xml.format.preserveSpace = preserve
      opts.servers.lemminx = server
    end,
  },
  {
    "saghen/blink.cmp",
    dependencies = { "Kaiser-Yang/blink-cmp-dictionary" },
    opts = {
      sources = {
        -- LazyVim 合并 default 列表，保留原有 LSP、路径、片段及 buffer 来源。
        default = { "mybatis_sql" },
        providers = {
          mybatis_sql = {
            name = "SQL",
            module = "blink-cmp-dictionary",
            enabled = function()
              return require("config.mybatis").in_sql()
            end,
            min_keyword_length = 2,
            opts = {
              dictionary_files = { vim.fn.stdpath("config") .. "/dictionaries/sql.txt" },
              force_fallback = true, -- 纯 Lua，本地词表，不运行外部搜索或访问数据库。
              get_kind_name = function()
                return "Keyword"
              end,
              get_documentation = function()
                return "SQL keyword (offline)"
              end,
              -- SQL 的下划线属于同一个单词，例如 CURRENT_TIMESTAMP。
              get_prefix = function(ctx)
                return ctx.line:sub(1, ctx.cursor[2]):match("[%w_]+$") or ""
              end,
            },
          },
        },
      },
    },
  },
}
