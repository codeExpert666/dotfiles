-- Java 点号补全优先显示成员，保留显式输入的后缀模板和其他场景的原排序。
return {
  {
    "saghen/blink.cmp",
    opts = function(_, opts)
      opts.fuzzy = opts.fuzzy or {}
      local original = opts.fuzzy.sorts or require("blink.cmp.config").fuzzy.sorts
      opts.fuzzy.sorts = function()
        return require("config.java-completion").sorts(original)
      end
    end,
  },
}
