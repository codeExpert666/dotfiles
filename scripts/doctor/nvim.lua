-- 离线探测：解析文件并检查配置目录发现结果，不执行用户的 Lua 配置。
local function emit(level, id, message, hint)
  local fields = { level, id, message, hint or "" }
  for i, value in ipairs(fields) do
    fields[i] = tostring(value):gsub("[\r\n\t]", " ")
  end
  io.stdout:write(table.concat(fields, "\t"), "\n")
  io.stdout:flush()
end

local function main()
  local expected = assert(arg[1], "missing expected config directory")
  local config = vim.fn.stdpath("config")
  local data = vim.fn.stdpath("data")
  emit("DATA", data, "", "")
  if vim.fn.resolve(config) == vim.fn.resolve(expected) then
    emit("PASS", "nvim.discovery", "native config lookup selects " .. config)
  else
    emit(
      "WARN",
      "nvim.discovery",
      "native config lookup selects " .. config,
      "Review NVIM_APPNAME and XDG_CONFIG_HOME; the selected directory is parsed below."
    )
  end
  if vim.fn.filereadable(config .. "/init.lua") ~= 1 then
    emit("FAIL", "nvim.entry", "selected configuration has no readable init.lua", "Deploy the managed configuration.")
    return
  end
  local files = vim.fn.globpath(config, "**/*.lua", false, true)
  table.sort(files)
  for _, path in ipairs(files) do
    local chunk, err = loadfile(path)
    if chunk then
      emit("PASS", "nvim.syntax", "parsed " .. path)
    else
      emit("FAIL", "nvim.syntax", err, "Correct the Lua syntax; no user Lua was executed.")
    end
  end
  local function json(name)
    local ok, value = pcall(function()
      return vim.json.decode(table.concat(vim.fn.readfile(config .. "/" .. name), "\n"))
    end)
    if not ok or type(value) ~= "table" then
      emit("FAIL", "nvim." .. name, "missing, unreadable or invalid JSON object", "Review " .. config .. "/" .. name)
      return nil
    end
    return value
  end
  local extras = json("lazyvim.json")
  if extras then
    if type(extras.extras) == "table" and vim.tbl_contains(extras.extras, "lazyvim.plugins.extras.lang.toml") then
      emit("PASS", "nvim.extras", "TOML extra is enabled")
    else
      emit("WARN", "nvim.extras", "managed TOML extra is not enabled", "Review intentional extra changes.")
    end
  end
  local lock = json("lazy-lock.json")
  if not lock then
    return
  end
  if not (lock.LazyVim and lock["lazy.nvim"]) then
    emit("FAIL", "nvim.lock", "lockfile omits LazyVim or lazy.nvim", "Restore a complete lockfile.")
  end
  local names = vim.tbl_keys(lock)
  table.sort(names)
  for _, name in ipairs(names) do
    local plugin = lock[name]
    if
      type(name) ~= "string"
      or name:find("[^%w_.-]")
      or type(plugin) ~= "table"
      or type(plugin.commit) ~= "string"
      or not plugin.commit:match("^[0-9a-f]+$")
      or #plugin.commit ~= 40
    then
      emit("FAIL", "nvim.lock", "invalid plugin name or commit in lockfile", "Review lazy-lock.json.")
    else
      emit("PLUGIN", name, plugin.commit, data .. "/lazy")
    end
  end
end

local ok, err = pcall(main)
if not ok then
  emit("FAIL", "nvim.probe", tostring(err), "Review the offline probe and installed Neovim version.")
  vim.cmd("cquit 1")
end
vim.cmd("qa!")
