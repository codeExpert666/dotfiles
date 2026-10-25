-- PR #2907（0e163b4）的目录合并修复；与该提交配套使用。
local M = {}
local configured = false

-- 合并后的可见父节点跳过了中间目录，补回它们的样式标记。
function M.transform(item)
  if item.display_name then
    local Tree = require("snacks.explorer.tree")
    local parent = item.parent and item.parent.file
    local node = Tree:node(item.file)
    while node and node.path ~= parent do
      item.hidden = item.hidden or node.hidden
      item.ignored = item.ignored or node.ignored
      node = node.parent
    end
  end
  return item
end

function M.setup()
  if configured then
    return
  end
  local Tree = require("snacks.explorer.tree")
  local Actions = require("snacks.explorer.actions")
  local Watch = require("snacks.explorer.watch")
  local Git = require("snacks.explorer.git")
  assert(Tree.compact_chain, "Snacks compact folders requires PR #2907 at commit 0e163b4")

  -- 只合并真实目录；隐藏的中间层保持打开，让定位和监听仍可遍历真实路径。
  function Tree:compact_chain(node, filter)
    local segments = { node.name }
    local current = node
    while current.type == "directory" do
      if not current.expanded then
        self:expand(current)
      end
      local single
      for _, child in pairs(current.children) do
        if filter(child) then
          if single then
            single = nil
            break
          end
          single = child
        end
      end
      if not single or single.type ~= "directory" then
        break
      end
      current.open = true
      current = single
      segments[#segments + 1] = current.name
    end
    return current, segments
  end

  -- 标签只属于本次生成的列表，不应泄漏到新根目录或未启用合并的列表。
  local get = Tree.get
  function Tree:get(cwd, cb, opts)
    get(self, cwd, function(node)
      local compact = node._compact
      node._compact = nil
      cb(compact and setmetatable({ _compact = compact }, { __index = node }) or node)
    end, opts)
    if opts and opts.compact then
      vim.schedule(Watch.watch)
    end
  end

  -- 合并模式会检查收起目录的内容，内容失效时也需要重建显示。
  function Tree:is_dirty(cwd, opts)
    opts = opts or {}
    if Git.is_dirty(cwd) then
      return true
    end
    local dirty = false
    self:get(cwd, function(node)
      if node.dir and (node.open or opts.compact_folders) and not node.expanded then
        dirty = true
      end
    end, {
      hidden = opts.hidden,
      ignored = opts.ignored,
      exclude = opts.exclude,
      include = opts.include,
      expand = false,
    })
    return dirty
  end

  local close = Actions.actions.explorer_close
  function Actions.actions.explorer_close(picker, item)
    if picker.opts.compact_folders and item and item.dir and not item.open and item.parent then
      local parent = item.parent.file
      Tree:close(parent)
      Actions.update(picker, { target = parent, refresh = true })
    else
      close(picker, item)
    end
  end

  -- 除打开的目录，还需监听为合并而扫描过的可见收起目录。
  function Watch.watch()
    local used, cwds = {}, {}
    for _, picker in ipairs(Snacks.picker.get({ source = "explorer", tab = false })) do
      if picker.opts.watch then
        local cwd = picker:cwd()
        cwds[cwd] = cwds[cwd] or picker.opts.compact_folders or false
      end
    end
    for cwd, compact in pairs(cwds) do
      local root = Snacks.git.get_root(cwd)
      if root then
        local git_dir = root .. "/.git"
        -- worktree / submodule 的 .git 是指向实际 Git 目录的文件，路径也可能是相对的。
        if vim.fn.isdirectory(git_dir) == 0 then
          local ok, lines = pcall(vim.fn.readfile, git_dir, "", 1)
          local target = ok and lines[1] and lines[1]:match("^gitdir: (.+)$")
          git_dir = target
              and vim.fs.normalize(target:sub(1, 1) == "/" and target or root .. "/" .. target, { expand_env = false })
            or nil
        end
        if git_dir then
          used[git_dir] = true
          Watch.start(git_dir, function(file)
            if vim.fs.basename(file) == "index" then
              Git.refresh(root)
              Watch.refresh()
            end
          end)
        end
      end
      Tree:walk(Tree:find(cwd), function(node)
        if node.dir and (node.open or compact and node.expanded) then
          used[node.path] = true
          Watch.start(node.path)
        end
      end)
    end
    for path in pairs(Watch._watches) do
      if not used[path] then
        Watch.stop(path)
      end
    end
  end
  configured = true
end

return M
