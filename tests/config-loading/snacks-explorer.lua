-- 使用已准备的 PR #2907 插件缓存验证真实文件树与文件操作，不启动插件安装。
-- 运行方式见 tests/README.md；所有文件变更只发生在本次创建的临时目录中。
local plugin = assert(arg[1], "pass the prepared snacks.nvim directory")
local repo = vim.fn.fnamemodify(debug.getinfo(1, "S").source:sub(2), ":p:h:h:h")
vim.opt.rtp:prepend(plugin)
local Snacks = require("snacks")
local compact = dofile(arg[2] or repo .. "/nvim/.config/nvim/lua/config/snacks-compact.lua")
compact.setup()
Snacks.setup({
  picker = { enabled = true, sources = { explorer = { transform = compact.transform } } },
  input = { enabled = true },
  explorer = { enabled = true },
})
local temporary = vim.fn.tempname()
vim.fn.mkdir(temporary, "p")
local base = assert(vim.uv.fs_realpath(temporary))
local original_cwd = vim.fn.getcwd()
local results, failures = {}, {}
local current
local function check(value, message)
  assert(value, message)
end
local function wait_for(fn, message)
  assert(vim.wait(5000, fn, 10), message)
end
local function settle(p, refresh)
  if refresh then
    p:find()
  end
  wait_for(function()
    return not p.finder:running() and not p.matcher.task:running()
  end, "picker did not finish")
  local flushed = false
  vim.schedule(function()
    flushed = true
  end)
  wait_for(function()
    return flushed
  end, "picker callbacks did not finish")
end
local function fixture(name, files)
  local root = assert(vim.uv.fs_realpath(base)) .. "/cases/" .. name
  vim.fn.mkdir(root, "p")
  for path, contents in pairs(files) do
    vim.fn.mkdir(vim.fs.dirname(root .. "/" .. path), "p")
    vim.fn.writefile({ contents }, root .. "/" .. path)
  end
  return root
end
local function open(root, opts)
  if current and not current.closed then
    current:close()
  end
  current = Snacks.picker.explorer(vim.tbl_deep_extend("force", {
    cwd = root,
    compact_folders = true,
    hidden = true,
    ignored = true,
    follow_file = false,
    watch = false,
    git_status = false,
    diagnostics = false,
  }, opts or {}))
  settle(current)
  return current
end
local function item(p, path)
  for _, it in ipairs(p:items()) do
    if it.file == path then
      return it
    end
  end
end
local function select(p, path)
  for i, it in ipairs(p:items()) do
    if it.file == path then
      p.list:view(i)
      return it
    end
  end
  error("missing item: " .. path)
end
local function test(name, fn)
  local ok, err = xpcall(fn, debug.traceback)
  results[#results + 1] = (ok and "PASS " or "FAIL ") .. name
  if not ok then
    failures[#failures + 1] = name .. "\n" .. err
  end
  if current and not current.closed then
    current:close()
  end
end
vim.o.columns, vim.o.lines = 160, 50

test("compact path and branch expansion", function()
  local root = fixture(
    "branches",
    { ["java/io/github/voiceinsight/model/a.txt"] = "a", ["java/io/github/voiceinsight/feedback/b.txt"] = "b" }
  )
  local p = open(root)
  local path = root .. "/java/io/github/voiceinsight"
  check(item(p, path).display_name == "java/io/github/voiceinsight", "chain not compacted")
  select(p, path)
  p:action("confirm")
  settle(p)
  check(item(p, path .. "/model") and item(p, path .. "/feedback"), "branches missing")
end)

test("repeated h navigates out of a closed compact row", function()
  local root = fixture("parent", { ["java/io/github/voiceinsight/a.txt"] = "a" })
  local p = open(root)
  local path = root .. "/java/io/github/voiceinsight"
  select(p, path)
  p:action("explorer_close")
  settle(p)
  check(p:current().file == root, "h remained on hidden intermediate parent: " .. vim.inspect(p:current()))
end)

test("reopening without compaction clears compact labels", function()
  local root = fixture("disable", { ["java/io/github/voiceinsight/a.txt"] = "a" })
  local p = open(root)
  local path = root .. "/java/io/github/voiceinsight"
  require("snacks.explorer.tree"):open(path)
  settle(p, true)
  p = open(root, { compact_folders = false })
  check(item(p, path) and item(p, path).display_name == nil, "compact metadata leaked into regular tree")
end)

test("narrowing root clears the previous compact label", function()
  local root = fixture("root", { ["java/io/github/voiceinsight/a.txt"] = "a" })
  local p = open(root)
  local path = root .. "/java/io/github/voiceinsight"
  p:set_cwd(path)
  settle(p, true)
  check(item(p, path).display_name == nil, "root inherited an old path outside its current scope")
end)

test("hidden ancestor keeps compact row hidden styling", function()
  local root = fixture("hidden", { [".hidden/a/b/file.txt"] = "a" })
  local p = open(root)
  check(item(p, root .. "/.hidden/a/b").hidden == true, "hidden ancestor flag disappeared")
end)

test("compact paths stop at symbolic links", function()
  local root = fixture("links", { ["actual/a.txt"] = "a" })
  vim.fn.mkdir(root .. "/java/io", "p")
  vim.uv.fs_unlink(root .. "/java/io/link")
  assert(vim.uv.fs_symlink(root .. "/actual", root .. "/java/io/link"))
  local p = open(root)
  check(item(p, root .. "/java/io"), "compaction followed a symbolic link instead of keeping its boundary")
end)

test("filesystem watchers cover compacted intermediate directories", function()
  local root = fixture("watch", { ["java/io/github/voiceinsight/a.txt"] = "a" })
  local p = open(root, { watch = true })
  local watches = require("snacks.explorer.watch")._watches
  wait_for(function()
    return watches[root .. "/java/io"]
  end, "intermediate directory is not watched")
  vim.fn.writefile({ "new branch" }, root .. "/java/io/new.txt")
  wait_for(function()
    return item(p, root .. "/java/io")
  end, "new branch did not automatically split compact chain")
end)

test("empty collapsed directory compacts again after external creation", function()
  local root = fixture("extend", {})
  vim.fn.mkdir(root .. "/java/io", "p")
  local p = open(root, { watch = true })
  local watches = require("snacks.explorer.watch")._watches
  wait_for(function()
    return watches[root .. "/java/io"]
  end, "empty endpoint is not watched")
  vim.fn.mkdir(root .. "/java/io/new", "p")
  wait_for(function()
    return item(p, root .. "/java/io/new")
  end, "new directory did not extend compact chain")
  check(item(p, root .. "/java/io/new").display_name == "java/io/new", "extended label incorrect")
end)

test("watch=false does not create directory watches", function()
  local root = fixture("no-watch", { ["java/io/a.txt"] = "a" })
  local p = open(root)
  require("snacks.explorer.watch").watch()
  check(require("snacks.explorer.watch")._watches[root .. "/java/io"] == nil, "disabled watcher was started")
  check(not p.closed, "picker closed unexpectedly")
end)

test("search confirms a result and restores the compact tree", function()
  local root = fixture("search", {
    ["java/io/github/voiceinsight/model/needle.txt"] = "needle",
    ["java/io/github/voiceinsight/feedback/other.txt"] = "other",
  })
  local p = open(root)
  local leaf = root .. "/java/io/github/voiceinsight/model/needle.txt"
  p.input:set("needle", "")
  settle(p, true)
  check(p.input.filter.meta.searching and item(p, leaf), "search failed to include matching file")
  select(p, leaf)
  p:action("confirm")
  settle(p)
  check(not p.input.filter.meta.searching and p:current().file == leaf, "confirm did not return to the original file")
  check(
    item(p, root .. "/java/io/github/voiceinsight").display_name == "java/io/github/voiceinsight",
    "compact path missing after search"
  )
  p:action("confirm")
  settle(p)
  check(vim.api.nvim_buf_get_name(0) == leaf, "original file path was not opened")
end)

test("tree follows files between package branches", function()
  local root = fixture(
    "follow",
    { ["java/io/github/voiceinsight/model/a.txt"] = "a", ["java/io/github/voiceinsight/feedback/b.txt"] = "b" }
  )
  vim.cmd.edit(root .. "/java/io/github/voiceinsight/model/a.txt")
  local p = open(root, { follow_file = true })
  local leaf = root .. "/java/io/github/voiceinsight/feedback/b.txt"
  vim.api.nvim_set_current_win(p.main)
  vim.cmd.edit(leaf)
  wait_for(function()
    return p:current() and p:current().file == leaf
  end, "follow_file did not locate the second branch")
end)

local function answer(value, fn)
  local input, ui = Snacks.input, vim.ui.input
  Snacks.input = function(_, cb)
    cb(value)
  end
  vim.ui.input = function(_, cb)
    cb(value)
  end
  local ok, err = pcall(fn)
  Snacks.input, vim.ui.input = input, ui
  assert(ok, err)
end
local function confirm(fn)
  local util = Snacks.picker.util
  local original = util.confirm
  util.confirm = function(_, cb)
    cb()
  end
  local ok, err = pcall(fn)
  util.confirm = original
  assert(ok, err)
end

test("new files and directories use the compact endpoint", function()
  local root = fixture("create", { ["java/io/github/voiceinsight/existing.txt"] = "keep" })
  local p = open(root)
  local endpoint = root .. "/java/io/github/voiceinsight"
  select(p, endpoint)
  answer("new.txt", function()
    p:action("explorer_add")
  end)
  settle(p)
  check(vim.fn.filereadable(endpoint .. "/new.txt") == 1, "file created at the wrong path")
  select(p, endpoint)
  answer("new-directory/", function()
    p:action("explorer_add")
  end)
  settle(p)
  check(vim.fn.isdirectory(endpoint .. "/new-directory") == 1, "directory created at the wrong path")
  check(vim.fn.readfile(endpoint .. "/existing.txt")[1] == "keep", "existing file changed")
end)

test("renaming a compact directory preserves ancestors and file contents", function()
  local root = fixture("rename-dir", { ["java/io/github/voiceinsight/a.txt"] = "keep", ["sentinel.txt"] = "sentinel" })
  vim.cmd.cd(root)
  local p = open(root)
  select(p, root .. "/java/io/github/voiceinsight")
  answer("java/io/github/renamed-package", function()
    p:action("explorer_rename")
  end)
  settle(p)
  check(vim.fn.isdirectory(root .. "/java/io/github/voiceinsight") == 0, "old directory remained")
  check(vim.fn.readfile(root .. "/java/io/github/renamed-package/a.txt")[1] == "keep", "renamed contents differ")
  check(vim.fn.readfile(root .. "/sentinel.txt")[1] == "sentinel", "neighbor changed")
end)

test("renaming a file keeps the compact package intact", function()
  local root = fixture("rename-file", { ["java/io/github/voiceinsight/a.txt"] = "keep" })
  vim.cmd.cd(root)
  local p = open(root)
  local endpoint = root .. "/java/io/github/voiceinsight"
  require("snacks.explorer.tree"):open(endpoint)
  settle(p, true)
  select(p, endpoint .. "/a.txt")
  answer("java/io/github/voiceinsight/renamed.txt", function()
    p:action("explorer_rename")
  end)
  settle(p)
  check(vim.fn.filereadable(endpoint .. "/a.txt") == 0, "old file remained")
  check(vim.fn.readfile(endpoint .. "/renamed.txt")[1] == "keep", "file contents changed")
end)

test("copying a compact directory copies only the endpoint", function()
  local root = fixture("copy", { ["java/io/github/voiceinsight/a.txt"] = "keep" })
  local p = open(root)
  select(p, root .. "/java/io/github/voiceinsight")
  answer("copied-package", function()
    p:action("explorer_copy")
  end)
  settle(p)
  check(
    vim.fn.readfile(root .. "/java/io/github/copied-package/a.txt")[1] == "keep",
    "copy used the display path instead of the real path"
  )
  check(vim.fn.readfile(root .. "/java/io/github/voiceinsight/a.txt")[1] == "keep", "source changed")
end)

test("moving a selected file uses the target compact directory", function()
  local root = fixture("move", { ["source/a.txt"] = "keep", ["java/io/github/voiceinsight/existing.txt"] = "existing" })
  local p = open(root)
  require("snacks.explorer.tree"):open(root .. "/source")
  settle(p, true)
  local source = select(p, root .. "/source/a.txt")
  p.list:select(source)
  local endpoint = root .. "/java/io/github/voiceinsight"
  select(p, endpoint)
  confirm(function()
    p:action("explorer_move")
  end)
  settle(p)
  check(vim.fn.filereadable(root .. "/source/a.txt") == 0, "source not moved")
  check(vim.fn.readfile(endpoint .. "/a.txt")[1] == "keep", "move destination incorrect")
  check(vim.fn.readfile(endpoint .. "/existing.txt")[1] == "existing", "target neighbor changed")
end)

test("deleting a compact row leaves its ancestors and neighbors", function()
  local root = fixture("delete", { ["java/io/github/voiceinsight/a.txt"] = "remove", ["sentinel.txt"] = "keep" })
  local p = open(root)
  select(p, root .. "/java/io/github/voiceinsight")
  local trash = Snacks.explorer.config.trash
  Snacks.explorer.config.trash = false
  confirm(function()
    p:action("explorer_del")
  end)
  Snacks.explorer.config.trash = trash
  settle(p)
  check(vim.fn.isdirectory(root .. "/java/io/github/voiceinsight") == 0, "endpoint not removed")
  check(vim.fn.isdirectory(root .. "/java/io/github") == 1, "ancestor was deleted")
  check(vim.fn.readfile(root .. "/sentinel.txt")[1] == "keep", "neighbor was deleted")
end)

test("diagnostic and ignored status remain on compact rows", function()
  local root = fixture("status", { [".gitignore"] = "java/", ["java/io/github/voiceinsight/a.txt"] = "keep" })
  vim.fn.system({ "git", "init", "-q", root })
  check(vim.v.shell_error == 0, "git fixture could not initialize")
  local leaf = root .. "/java/io/github/voiceinsight/a.txt"
  local buf = vim.fn.bufadd(leaf)
  local namespace = vim.api.nvim_create_namespace("compact-status-test")
  vim.diagnostic.set(
    namespace,
    buf,
    { { lnum = 0, col = 0, message = "probe", severity = vim.diagnostic.severity.ERROR } }
  )
  local p = open(root, { git_status = true, diagnostics = true })
  local path = root .. "/java/io/github/voiceinsight"
  wait_for(function()
    return item(p, path) and item(p, path).ignored
  end, "gitignored compact row lost its status")
  check(item(p, path).severity == vim.diagnostic.severity.ERROR, "recursive diagnostic lost")
  vim.diagnostic.reset(namespace)
end)

test("hidden toggle updates where the compact chain stops", function()
  local root = fixture("toggle-hidden", { ["java/.hidden"] = "hidden", ["java/io/github/a.txt"] = "a" })
  local p = open(root, { hidden = false })
  check(item(p, root .. "/java/io/github").display_name == "java/io/github", "hidden child prevented compaction")
  p:action("toggle_hidden")
  settle(p)
  check(item(p, root .. "/java") and item(p, root .. "/java/.hidden"), "hidden toggle did not split the compact row")
end)

test("explorer, files and grep share the picker input component", function()
  local root = fixture("input-style", { ["java/io/a.txt"] = "needle" })
  local p = open(root)
  local input_type = vim.bo[p.input.win.buf].filetype
  check(input_type == "snacks_picker_input", "explorer uses a different input component")
  p:close()
  current = Snacks.picker.files({ cwd = root })
  settle(current)
  check(vim.bo[current.input.win.buf].filetype == input_type, "file search input differs")
  current:close()
  current = Snacks.picker.grep({ cwd = root, search = "needle" })
  settle(current)
  check(vim.bo[current.input.win.buf].filetype == input_type, "content search input differs")
end)

vim.cmd.cd(original_cwd)
require("snacks.explorer.watch").abort()
vim.fn.delete(temporary, "rf")
for _, line in ipairs(results) do
  print(line)
end
for _, failure in ipairs(failures) do
  print(failure)
end
if #failures > 0 then
  vim.cmd("cquit 1")
else
  vim.cmd("qa!")
end
