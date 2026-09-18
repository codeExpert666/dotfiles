-- 调整 blink.cmp 的命令行补全：路径包含隐藏项、复用文件浏览器图标，回车先接受选中的候选。
local function complete_hidden(source, ctx, callback)
  local completion_type = vim.fn.getcmdcompltype()
  local is_plain_path = completion_type == "file" or completion_type == "dir"
  local is_search_path = completion_type == "file_in_path" or completion_type == "dir_in_path"
  -- 只处理文件路径候选；命令名、选项和搜索词继续使用各自的补全类型与图标。
  if ctx.mode ~= "cmdline" or not (is_plain_path or is_search_path) then
    return source:get_completions(ctx, callback)
  end

  local function finish(result)
    local icons = require("mini.icons")
    for _, item in ipairs(result.items) do
      -- Blink 的原 cmdline 源将路径统一标为 Property；普通项和隐藏项都在这里校正。
      local is_dir = (item.filterText or item.label):sub(-1) == "/"
      item.kind = is_dir and vim.lsp.protocol.CompletionItemKind.Folder or vim.lsp.protocol.CompletionItemKind.File
      -- 原候选的替换文字包含完整路径，但追加的隐藏项只替换最后一段，因此另存原始路径。
      -- 还原 fnameescape 的转义后按完整路径取图标，让 .git/config 等特殊路径也能识别。
      local path = item.data and item.data.cmdline_path or item.textEdit.newText:gsub("\\(.)", "%1")
      -- Snacks 文件浏览器同样使用 mini.icons；同时复用图标和高亮，不硬编码颜色。
      item.kind_icon, item.kind_hl = icons.get(is_dir and "directory" or "file", (path:gsub("/$", "")))
    end
    callback(result)
  end

  -- :find/:cd 等带 path/cdpath 搜索语义的候选只统一图标，不追加另一组搜索结果。
  if not is_plain_path then
    return source:get_completions(ctx, finish)
  end
  -- 下面的替换范围针对命令行末尾；在中间编辑时交给原实现处理后续文字。
  if ctx.cursor[2] ~= #ctx.line then
    return source:get_completions(ctx, finish)
  end
  -- 提取最后一个路径参数，将反斜杠转义的空格视为文件名的一部分。
  local argument = vim.fn.matchstr(ctx.line, [=[\%(\\.\|[^[:space:]]\)*$]=])
  local basename = argument:match("[^/]*$")
  -- 显式输入前导点时原补全已包含隐藏项；通配符、% 和 # 保留原生展开语义。
  if basename:sub(1, 1) == "." or argument:find("[*?%[%]%%#]") then
    return source:get_completions(ctx, finish)
  end

  -- 例如 edit nvim/co：使用 edit nvim/. 获取隐藏项，再由 Blink 匹配 co。
  -- 第三个参数 true 使查询遵守 wildignore，与原命令行补全的忽略规则一致。
  local prefix = ctx.line:sub(1, #ctx.line - #basename)
  local hidden = vim.fn.getcompletion(prefix .. ".", "cmdline", true)
  -- 先取得原候选再追加隐藏项，并将原实现的取消函数返回给 Blink。
  return source:get_completions(ctx, function(result)
    for _, path in ipairs(hidden) do
      -- 候选只展示最后一段名称，目录保留末尾斜杠；跳过当前目录和父目录。
      local name = path:match("([^/]+/?)$")
      if name and name ~= "./" and name ~= "../" then
        table.insert(result.items, {
          label = name,
          filterText = name,
          data = { cmdline_path = path },
          textEdit = {
            -- 接受候选时只替换最后一段路径，并转义文件名中的空格等特殊字符。
            newText = vim.fn.fnameescape(name),
            range = {
              -- Blink 使用从 0 开始的字节偏移；#prefix 正好是待替换名称的起点。
              start = { line = 0, character = #prefix },
              ["end"] = { line = 0, character = ctx.cursor[2] },
            },
          },
        })
      end
    end
    finish(result)
  end)
end

return {
  {
    "saghen/blink.cmp",
    -- 在首次命令行补全前加载图标库，沿用 LazyVim 为文件浏览器配置的图标规则。
    dependencies = { "nvim-mini/mini.icons" },
    opts = {
      -- cmdline 管理 : 命令和 /、? 搜索时的补全。
      cmdline = {
        keymap = {
          -- <CR> 表示回车；按顺序尝试 accept，再 fallback。
          -- accept 接受当前选中的补全项，成功后本次回车只完成补全。
          -- 没有可接受的选中项时，fallback 使用其他映射或原生回车行为，通常会执行命令或搜索。
          ["<CR>"] = { "accept", "fallback" },
        },
      },
      -- providers 配置补全源；与管理命令行界面和按键的 cmdline 选项平级。
      sources = {
        providers = {
          cmdline = {
            -- 使用 Blink 的 override 接口包装原补全源，复用原候选和异步取消机制。
            override = { get_completions = complete_hidden },
          },
        },
      },
    },
  },
}
