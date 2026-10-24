-- 临时整理 JDTLS hover；每项可独立移除，验收条件见根 README 的 Java 文档说明。
-- 长链接：移除 jdt:// 目标会失去浮窗内跳转；源码 gd 不受影响。
-- https://github.com/neovim/neovim/issues/14409
-- https://github.com/neovim/neovim/pull/42370
-- 表格关联：下列 PR 已合并，但只优化空白，不提供 Noice 表格排版。
-- https://github.com/eclipse-jdtls/eclipse.jdt.ls/pull/3786
-- 标签遵循 Javadoc 规范；systemProperty 和元信息排版尚无已确认的直接修复 PR。
-- https://docs.oracle.com/en/java/javase/25/docs/specs/javadoc/doc-comment-spec.html#systemproperty
local query

local function apply_edits(text, edits)
  table.sort(edits, function(a, b)
    return a.first > b.first
  end)
  for _, edit in ipairs(edits) do
    text = text:sub(1, edit.first) .. edit.text .. text:sub(edit.last + 1)
  end
  return text
end

local function edit_node(edits, node, replacement)
  local _, _, first = node:start()
  local _, _, last = node:end_()
  edits[#edits + 1] = { first = first, last = last, text = replacement }
end

local function walk(node, visit)
  if visit(node) then
    return
  end
  for child in node:iter_children() do
    walk(child, visit)
  end
end

local function inline_trees(text, visit)
  local parser = vim.treesitter.get_string_parser(text, "markdown")
  parser:parse(true)
  parser:for_each_tree(function(tree, language)
    -- 只处理文档本身的行内树；```markdown 示例中的递归注入仍是代码。
    if language:lang() == "markdown_inline" and language:parent() == parser then
      visit(tree:root())
    end
  end)
end

local function strip_jdt_links(text)
  if not text:find("jdt://", 1, true) then
    return text
  end
  query = query
    or vim.treesitter.query.parse(
      "markdown_inline",
      "(inline_link (link_text) @label (link_destination) @destination) @link"
    )
  local edits = {}
  inline_trees(text, function(root)
    for _, match in query:iter_matches(root, text, 0, -1) do
      local label, destination, link = match[1][1], match[2][1], match[3][1]
      if vim.startswith(vim.treesitter.get_node_text(destination, text), "jdt://") then
        edit_node(edits, link, vim.treesitter.get_node_text(label, text))
      end
    end
  end)
  return apply_edits(text, edits)
end

local function format_system_properties(text)
  if not text:find("{@systemProperty", 1, true) then
    return text
  end
  local edits = {}
  inline_trees(text, function(root)
    local protected = {}
    walk(root, function(node)
      if
        vim.tbl_contains({ "code_span", "backslash_escape", "link_destination", "link_title", "html_tag" }, node:type())
      then
        local _, _, first = node:start()
        local _, _, last = node:end_()
        protected[#protected + 1] = { first, last }
        return true
      end
    end)
    local _, _, offset = root:start()
    local value = vim.treesitter.get_node_text(root, text)
    for first, name, last in value:gmatch("(){@systemProperty%s+([^%s{}`]+)%s*}()") do
      first, last = offset + first - 1, offset + last - 1
      local literal = false
      for _, range in ipairs(protected) do
        literal = literal or (first < range[2] and last > range[1])
      end
      if not literal then
        edits[#edits + 1] = { first = first, last = last, text = "`" .. name .. "`" }
      end
    end
  end)
  return apply_edits(text, edits)
end

local function children(node, kind)
  local result = {}
  for child in node:iter_children() do
    if child:type() == kind then
      result[#result + 1] = child
    end
  end
  return result
end

local function block_edits(text, visit)
  local root = vim.treesitter.get_string_parser(text, "markdown"):parse()[1]:root()
  local edits = {}
  walk(root, function(node)
    local replacement = visit(node)
    if replacement then
      edit_node(edits, node, replacement)
      return true
    end
  end)
  return apply_edits(text, edits)
end

local function format_metadata(text)
  return block_edits(text, function(node)
    local _, col, first = node:start()
    if node:type() ~= "list_item" or col ~= 0 or #children(node, "list_marker_star") ~= 1 then
      return
    end
    local paragraphs, lists = children(node, "paragraph"), children(node, "list")
    if #paragraphs ~= 1 or #lists ~= 1 then
      return
    end
    local label = vim.trim(vim.treesitter.get_node_text(paragraphs[1], text))
    if label ~= "**Since:**" and label ~= "**Impl Note:**" then
      return
    end
    local items = children(lists[1], "list_item")
    if #items ~= 1 or #children(items[1], "list_marker_star") ~= 1 then
      return -- 多个子项或有序项是真正的列表，不将它们合并成段落。
    end
    local body = children(items[1], "paragraph")[1]
    if not body then
      return
    end
    local _, indent, start = body:start()
    local _, _, last = node:end_()
    local lines = vim.split(text:sub(start + 1, last), "\n", { plain = true })
    for i = 2, #lines do
      if lines[i]:match("%S") then
        if lines[i]:sub(1, indent) ~= string.rep(" ", indent) then
          return
        end
        lines[i] = lines[i]:sub(indent + 1)
      end
    end
    local value = table.concat(lines, "\n"):gsub("%s+$", "")
    if label == "**Since:**" and not value:match("^%d[%w._+%-]*$") then
      return
    end
    local heading = label == "**Since:**" and (label .. " " .. value) or ("**Implementation Note:**\n\n" .. value)
    local gap = first > 0 and text:sub(first - 1, first) ~= "\n\n" and "\n" or ""
    return gap .. heading .. "\n\n"
  end)
end

local function format_wide_tables(text, width)
  local source_lines = vim.split(text, "\n", { plain = true })
  return block_edits(text, function(node)
    if node:type() ~= "pipe_table" then
      return
    end
    local row, col = node:start()
    local prefix = source_lines[row + 1]:sub(1, col)
    if prefix:find("%S") then
      return -- 保留引用块或列表首行上的表格，避免改变其容器。
    end
    local wide = false
    for _, line in ipairs(vim.split(vim.treesitter.get_node_text(node, text), "\n", { plain = true })) do
      wide = wide or vim.fn.strdisplaywidth(prefix .. vim.trim(line)) > width
    end
    if not wide then
      return
    end
    local function cells(n)
      return vim.tbl_map(function(cell)
        return vim.trim(vim.treesitter.get_node_text(cell, text))
      end, children(n, "pipe_table_cell"))
    end
    local header = children(node, "pipe_table_header")[1]
    local rows = children(node, "pipe_table_row")
    if not header or #rows == 0 then
      return
    end
    local labels, output, caption = cells(header), {}, nil
    for i, data in ipairs(rows) do
      local values = cells(data)
      -- Flexmark 的 [caption] 会被 Markdown parser 吸收到最后一行。
      local title = vim.trim(vim.treesitter.get_node_text(data, text)):match("^%[([^%[%]]+)%]$")
      if title and i == #rows then
        caption = title
      elseif #values ~= #labels then
        return -- 不猜测不规则表格中缺失的单元格。
      else
        if #output > 0 then
          output[#output + 1] = ""
        end
        for j, value in ipairs(values) do
          output[#output + 1] = (j == 1 and "- " or "  ") .. labels[j] .. ": " .. value
        end
      end
    end
    if #output == 0 then
      return
    end
    if caption then
      table.insert(output, 1, "")
      table.insert(output, 1, "**" .. caption .. "**")
    end
    local _, end_col = node:end_()
    return table.concat(output, "\n" .. prefix) .. (end_col == 0 and "\n\n" or "")
  end)
end

local function clean_markdown(text, width)
  text = strip_jdt_links(text)
  text = format_system_properties(text)
  text = format_metadata(text)
  return format_wide_tables(text, width)
end

local function clean_contents(contents, width)
  if type(contents) == "string" then
    return clean_markdown(contents, width)
  end
  if type(contents) ~= "table" then
    return contents
  end
  if contents.kind == "markdown" and type(contents.value) == "string" then
    contents.value = clean_markdown(contents.value, width)
  elseif not contents.kind and not contents.language then
    for i, content in ipairs(contents) do
      contents[i] = clean_contents(content, width)
    end
  end
  return contents
end

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
          local ok, contents = pcall(clean_contents, copy.contents, width)
          if ok then
            copy.contents = contents
            result = copy
          end
        end
        return original(err, result, ctx, ...)
      end
    end,
  },
}
