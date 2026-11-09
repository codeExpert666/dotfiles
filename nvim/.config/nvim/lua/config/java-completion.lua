local M = {}

local excluded = {
  line_comment = true,
  block_comment = true,
  string_literal = true,
  character_literal = true,
  decimal_floating_point_literal = true,
  hex_floating_point_literal = true,
  import_declaration = true,
  package_declaration = true,
}

-- 只识别插入模式下点号后的 Java 名称；在语法树中排除注释、字符串和包名。
-- 缺少 parser 时沿用原排序，不猜测注释／字符串边界。
function M.in_member_access()
  if vim.bo.filetype ~= "java" or vim.api.nvim_get_mode().mode:sub(1, 1) ~= "i" then
    return false
  end
  local cursor = vim.api.nvim_win_get_cursor(0)
  local before = vim.api.nvim_get_current_line():sub(1, cursor[2])
  local dot = before:match("()%.[%w_$\128-\255]*$")
  if not dot then
    return false
  end
  local ok, parser = pcall(vim.treesitter.get_parser, 0, "java")
  if not ok or not parser then
    return false
  end
  local parsed, trees = pcall(parser.parse, parser)
  if not parsed or not trees or not trees[1] then
    return false
  end
  local row, col = cursor[1] - 1, dot - 1
  local node = trees[1]:root():named_descendant_for_range(row, col, row, col)
  if not node then
    return false
  end
  while node do
    if excluded[node:type()] then
      return false
    end
    node = node:parent()
  end
  return true
end

local members = { [2] = true, [5] = true, [10] = true, [20] = true, [21] = true }
local function rank(item)
  if item.client_name == "jdtls" and members[item.kind] then
    return 0 -- Method / Field / Property / EnumMember / Constant
  end
  return item.kind == 15 and 2 or 1 -- Snippet 排在其他候选之后。
end

local function member_first(a, b)
  local ar, br = rank(a), rank(b)
  if ar ~= br then
    return ar < br
  end
end

function M.sorts(original)
  local base = type(original) == "function" and original() or original
  if not M.in_member_access() then
    return base
  end
  -- JDTLS postfix 的 filterText 含整个表达式，分数不能直接与成员名比较。
  -- 完整匹配仍优先，确保显式输入 .cast / .syserr 可以直接接受模板。
  local sorts = { "exact", member_first }
  vim.list_extend(sorts, base)
  if not vim.tbl_contains(sorts, "label") then
    sorts[#sorts + 1] = "label"
  end
  return sorts
end

return M
