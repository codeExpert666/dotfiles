local M = {}
local statements = { select = true, insert = true, update = true, delete = true, sql = true, selectKey = true }
local markup = { STag = true, ETag = true, EmptyElemTag = true, Attribute = true, Comment = true, PI = true }

-- XML parser 已由通用 XML 支持安装。无法解析时不向其他 XML 区域泄漏 SQL 候选。
function M.in_sql()
  if vim.bo.filetype ~= "xml" or vim.api.nvim_get_mode().mode:sub(1, 1) == "c" then
    return false
  end
  local buf = vim.api.nvim_get_current_buf()
  local pos = vim.api.nvim_win_get_cursor(0)
  local ok, parser = pcall(vim.treesitter.get_parser, buf, "xml")
  if not ok then
    return false
  end
  local parsed, trees = pcall(parser.parse, parser)
  if not parsed or not trees[1] then
    return false
  end
  -- 补全光标位于已输入字符之后，检查前一个字节所属节点。
  local row, col = pos[1] - 1, math.max(0, pos[2] - 1)
  local node = trees[1]:root():named_descendant_for_range(row, col, row, col)
  if node and (node:type() == "CharData" or node:type() == "CData") then
    local sr, sc = node:start()
    local prefix = table.concat(vim.api.nvim_buf_get_text(buf, sr, sc, pos[1] - 1, pos[2], {}), "\n")
    if prefix:match("[#$]{[^}]*$") then
      return false -- MyBatis 参数名由原有补全负责，不混入 SQL 关键字。
    end
  end
  local statement = false
  while node do
    local kind = node:type()
    if markup[kind] then
      return false
    end
    if kind == "element" then
      local tag = node:named_child(0)
      local name = tag and tag:named_child(0)
      local text = name and vim.treesitter.get_node_text(name, buf)
      statement = statement or statements[text] or false
      if text == "mapper" then
        return statement
      end
    end
    node = node:parent()
  end
  return false
end

return M
