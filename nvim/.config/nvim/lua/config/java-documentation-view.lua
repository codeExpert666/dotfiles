-- Blink 的局部显示修复，不修改文档文字或全局 Treesitter query。
-- render-markdown 禁用全局围栏规则：https://github.com/MeanderingProgrammer/render-markdown.nvim/issues/577
-- Blink 未转发 conceal_lines：https://github.com/saghen/blink.cmp/issues/2637
-- 两项在所用版本修复、打开 Markdown 后 Comparator 验收通过时移除，详见 README。
local ns = vim.api.nvim_create_namespace("dotfiles.java-documentation-fences")

local function hide_fences(buf)
  local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
  local root = vim.treesitter.get_parser(buf, "markdown"):parse()[1]:root()
  local rows = {}
  local function visit(node)
    if node:type() == "fenced_code_block" then
      local delimiters, typed = {}, false
      for child in node:iter_children() do
        if child:type() == "fenced_code_block_delimiter" then
          delimiters[#delimiters + 1] = child
        elseif child:type() == "info_string" then
          typed = true
        end
      end
      -- 不处理未闭合围栏，也不隐藏同一行上的列表或引用标记。
      if #delimiters ~= 2 then
        return
      end
      local first, col = delimiters[1]:start()
      local last, end_col = delimiters[2]:start()
      if lines[first + 1]:sub(1, col):find("%S") or lines[last + 1]:sub(1, end_col):find("%S") then
        return
      end
      rows[first], rows[last] = true, true
      -- Javadoc 的无语言代码块常在公式两侧带空行；仅收起边缘空白，保留内部空行。
      if not typed then
        for row = first + 1, last - 1 do
          if lines[row + 1]:find("%S") then
            break
          end
          rows[row] = true
        end
        for row = last - 1, first + 1, -1 do
          if lines[row + 1]:find("%S") then
            break
          end
          rows[row] = true
        end
      end
      return -- 外层代码块中的 Markdown 示例不能再按文档解析。
    end
    for child in node:iter_children() do
      visit(child)
    end
  end
  visit(root)
  for row in pairs(rows) do
    vim.api.nvim_buf_set_extmark(buf, ns, row, 0, {
      end_row = row,
      end_col = #lines[row + 1],
      conceal_lines = "",
    })
  end
end

return {
  apply = function(buf, enabled)
    vim.api.nvim_buf_clear_namespace(buf, ns, 0, -1)
    if enabled then
      local ok = pcall(hide_fences, buf)
      if not ok then
        vim.api.nvim_buf_clear_namespace(buf, ns, 0, -1)
      end
    end
  end,
}
