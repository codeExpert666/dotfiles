-- mini.icons 的精确文件名规则之外，补充有限的 basename 模式。
-- 包括 devicons 兼容接口在内，所有消费者仍使用同一个 MiniIcons.get。
local M = {}
local installed = setmetatable({}, { __mode = "k" })

function M.setup(icons)
  if installed[icons] then
    return
  end
  local get = icons.get
  icons.get = function(category, name)
    if category == "file" and type(name) == "string" then
      local basename = vim.fs.basename(name)
      -- 明确的用户文件名配置优先于模式；不读文件或修改 filetype。
      if not icons.config.file[basename] then
        for _, rule in ipairs(require("config.icons").file_patterns) do
          if basename:match(rule[1]) then
            return get("file", rule[2])
          end
        end
      end
    end
    return get(category, name)
  end
  installed[icons] = true
end

return M
