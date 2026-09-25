-- 无界面准备使用仓库配置的副本，并将插件写入用户实际的 Neovim 数据目录。
-- 锁文件保持不变；安装、恢复或构建失败均终止准备。
local function report(message)
  io.stdout:write(message .. "\n")
  io.stdout:flush()
end

local uv = vim.uv

local function elapsed(started)
  return string.format("%.1fs", (uv.hrtime() - started) / 1e9)
end

-- vim.wait() 会处理 libuv 事件；心跳使用独立定时器，等待始终使用同一个截止时间。
local function step(label, timeout_ms, work, detail)
  local started = uv.hrtime()
  local limit = timeout_ms and (timeout_ms / 1000 .. "s") or "none (caller controls the total command)"
  report("RUN: " .. label .. "; timeout=" .. limit)
  local timer = assert(uv.new_timer())
  local active = true
  timer:start(30000, 30000, function()
    if active then
      local suffix = detail and detail() or ""
      report("WAIT: " .. label .. "; elapsed=" .. elapsed(started) .. "; timeout=" .. limit .. suffix)
    end
  end)
  local function remaining()
    if not timeout_ms then
      return nil
    end
    local left = math.floor((started + timeout_ms * 1e6 - uv.hrtime()) / 1e6)
    assert(left > 0, label .. " timed out after " .. timeout_ms / 1000 .. "s")
    return left
  end
  local ok, result = pcall(work, remaining)
  active = false
  timer:stop()
  timer:close()
  if not ok then
    report("FAIL: " .. label .. "; elapsed=" .. elapsed(started) .. "; reason=" .. tostring(result))
    error(result, 0)
  end
  report("READY: " .. label .. "; elapsed=" .. elapsed(started))
  return result
end

local function command(args)
  local result = vim.system(args, { text = true }):wait(60000)
  assert(result.code == 0, table.concat(args, " ") .. ": " .. (result.stderr or ""))
  return vim.trim(result.stdout)
end

-- 以下阶段适配 lazy-lock.json 锁定版本的插件接口；这些临时覆盖只用于准备阶段，
-- 更新锁定版本时须重新验证接口和覆盖行为。
local function prepare_plugins(config_dir, lock_path, lock)
  vim.opt.rtp:prepend(vim.fn.stdpath("data") .. "/lazy/lazy.nvim")
  local lazy = require("lazy")
  local setup = lazy.setup
  -- 推迟插件启动，避免在恢复锁定提交前执行旧版本。
  local loader = require("lazy.core.loader")
  local startup = loader.startup
  loader.startup = function() end
  -- lazy.nvim 的安装和恢复操作即使未改变内容也会写锁文件；
  -- 本次准备的各轮安装共用同一份固定输入，因此禁用锁文件写回。
  ---@diagnostic disable-next-line: duplicate-set-field
  require("lazy.manage.lock").update = function() end
  lazy.setup = function(opts)
    opts.lockfile = lock_path
    opts.checker = { enabled = false }
    opts.change_detection = { enabled = false }
    opts.local_spec = false
    opts.rocks = { enabled = false }
    opts.pkg = { enabled = false }
    opts.install = { missing = true, colorscheme = { "default" } }
    local spec = opts.spec
    assert(type(spec) == "table", "bootstrap requires a plugin spec list")
    -- 临时接管这些插件的配置，并关闭对应的自动构建和 LSP 初始化；
    -- 后续阶段显式准备工具、解析器及补全资源，统一等待并检查结果。
    vim.list_extend(spec, {
      {
        "mason-org/mason.nvim",
        build = false,
        config = function(_, settings)
          require("mason").setup(settings)
        end,
      },
      { "mason-org/mason-lspconfig.nvim", config = function() end },
      { "neovim/nvim-lspconfig", config = function() end },
      {
        "nvim-treesitter/nvim-treesitter",
        build = false,
        config = function(_, settings)
          require("nvim-treesitter").setup(settings)
        end,
      },
      {
        "saghen/blink.cmp",
        config = function(_, settings)
          require("blink.cmp.config").merge_with(settings)
        end,
      },
    })
    return setup(opts)
  end
  step("Neovim / plugins / setup", nil, function()
    dofile(config_dir .. "/init.lua")
  end)
  lazy.setup = setup
  loader.startup = startup

  local cfg = require("lazy.core.config")
  local restore = {}
  for name, pinned in pairs(lock) do
    local plugin = assert(cfg.plugins[name], "lockfile entry is absent from the resolved plugin spec: " .. name)
    assert(plugin._.installed, "plugin was not installed: " .. name)
    if command({ "git", "-C", plugin.dir, "rev-parse", "HEAD" }) ~= pinned.commit then
      restore[#restore + 1] = name
    end
  end
  if #restore > 0 then
    step("Neovim / plugins / restore", nil, function()
      lazy.restore({ plugins = restore, wait = true, show = false })
    end)
  end
  step("Neovim / plugins / verify", nil, function()
    for name, pinned in pairs(lock) do
      local plugin = cfg.plugins[name]
      for _, task in ipairs(plugin._.tasks or {}) do
        assert(not task:has_errors(), name .. ": " .. task:output(vim.log.levels.ERROR))
      end
      assert(command({ "git", "-C", plugin.dir, "rev-parse", "HEAD" }) == pinned.commit, "lock mismatch: " .. name)
    end
  end)
  report("READY: all plugin checkouts match lazy-lock.json")

  -- LazyVim 的初始化会建立选项辅助函数；必须等插件恢复完成后再运行。
  step("Neovim / plugins / startup", nil, startup)
end

local function prepare_mason()
  local lazy = require("lazy")
  step("Neovim / Mason / load", nil, function()
    lazy.load({ plugins = { "mason.nvim", "mason-lspconfig.nvim", "nvim-lspconfig" } })
  end)
  local registry = require("mason-registry")
  step("Neovim / Mason / registry refresh", 300000, function(remaining)
    local registry_done, registry_failure = false, nil
    registry.refresh(function(success, result)
      registry_done = true
      if success ~= true then
        registry_failure = type(result) == "string" and result or vim.inspect(result)
      end
    end)
    assert(
      vim.wait(remaining(), function()
        return registry_done
      end, 50),
      "Mason registry refresh timed out"
    )
    assert(not registry_failure, "Mason registry refresh failed: " .. tostring(registry_failure))
  end)
  -- ShellCheck 和 Shuck 使用 PATH 中的命令；其余工具从当前 Mason 与 LSP 配置解析，
  -- 让工具安装列表随实际配置变化。
  local packages = {}
  for _, name in ipairs(LazyVim.opts("mason.nvim").ensure_installed or {}) do
    packages[name] = true
  end
  local mapping = require("mason-lspconfig.mappings").get_mason_map().lspconfig_to_package
  for name, settings in pairs(LazyVim.opts("nvim-lspconfig").servers or {}) do
    if name ~= "*" and settings ~= false and settings.enabled ~= false and settings.mason ~= false then
      packages[assert(mapping[name], "no Mason mapping for LSP server: " .. name)] = true
    end
  end
  local names = vim.tbl_keys(packages)
  table.sort(names)
  for _, name in ipairs(names) do
    local package = registry.get_package(name)
    if not package:is_installed() then
      step("Neovim / Mason / install / " .. name, 600000, function(remaining)
        local done, failure = false, nil
        package:install({}, function(success, err)
          done = true
          if not success then
            failure = tostring(err)
          end
        end)
        assert(
          vim.wait(remaining(), function()
            return done
          end, 50),
          "Mason installation timed out: " .. name
        )
        assert(not failure, "Mason installation failed: " .. name .. ": " .. tostring(failure))
        assert(package:is_installed(), "Mason package is not installed: " .. name)
      end)
    end
    assert(package:is_installed(), "Mason package is not installed: " .. name)
    report("READY: Mason " .. name)
  end
  for _, name in ipairs({
    "bash-language-server",
    "lua-language-server",
    "taplo",
    "stylua",
    "shfmt",
    "shellcheck",
    "shuck",
  }) do
    assert(vim.fn.executable(name) == 1, "configured editor tool is not executable: " .. name)
  end
end

-- curl 的计时是最终传输的累计检查点；重试和退避另由单调时钟计入 wall_elapsed。
-- 不记录 URL 的用户信息、查询参数或片段，也不转发 curl 的原始 stderr 到日志。
local function safe_url(url)
  local scheme, rest = tostring(url or ""):match("^([Hh][Tt][Tt][Pp][Ss]?://)(.+)$")
  if not scheme then
    return "unavailable"
  end
  local authority, suffix = rest:match("^([^/?#]+)(.*)$")
  if not authority then
    return "unavailable"
  end
  authority = authority:match("@(.+)$") or authority
  local path = suffix:match("^[^?#]*") or ""
  local parts, redact_next = vim.split(path, "/", { plain = true }), false
  for index, part in ipairs(parts) do
    local lower = part:lower()
    if redact_next or lower:match("^gh[pousr]_[%w_]+$") then
      parts[index] = "<redacted>"
    end
    redact_next = lower == "token"
      or lower == "secret"
      or lower == "password"
      or lower == "apikey"
      or lower == "api_key"
  end
  path = table.concat(parts, "/")
  return scheme .. authority .. path .. (suffix:find("?", 1, true) and "?<redacted>" or "")
end

local function safe_error(message)
  local line = vim.trim(tostring(message):gsub("%c", " "))
  line = line:gsub("[Hh][Tt][Tt][Pp][Ss]?://[^%s<>\"']+", safe_url)
  line = line:gsub("([Bb]earer%s+)[^%s]+", "%1<redacted>")
  return line:gsub("([%w_-]+)=([^%s&]+)", function(key, value)
    if key:lower():find("token") or key:lower():find("secret") or key:lower():find("key") then
      return key .. "=<redacted>"
    end
    return key .. "=" .. value
  end)
end

local curl_fields = {
  "http",
  "bytes",
  "final_url",
  "redirects",
  "dns_at",
  "connect_at",
  "tls_at",
  "first_byte_at",
  "curl_total",
  "redirect_total",
}

local function curl_write_out(has_retries)
  local variables = {
    "http_code",
    "size_download",
    "url_effective",
    "num_redirects",
    "time_namelookup",
    "time_connect",
    "time_appconnect",
    "time_starttransfer",
    "time_total",
    "time_redirect",
  }
  if has_retries then
    variables[#variables + 1] = "num_retries"
  end
  local format = "DOTFILES_CURL"
  for _, variable in ipairs(variables) do
    format = format .. "\\t%{" .. variable .. "}"
  end
  return format .. "\\n"
end

local function curl_summary(stdout, has_retries)
  local values = vim.split((stdout or ""):match("DOTFILES_CURL\t([^\r\n]*)") or "", "\t", { plain = true })
  local summary = {}
  for index, field in ipairs(curl_fields) do
    local value = values[index]
    if field == "final_url" then
      value = value and safe_url(value)
    elseif field == "http" and tonumber(value) == 0 then
      value = "unavailable"
    elseif not value or not tonumber(value) then
      value = "unavailable"
    end
    summary[#summary + 1] = field .. "=" .. (value or "unavailable")
  end
  local retries = has_retries and tonumber(values[#curl_fields + 1]) or nil
  summary[#summary + 1] = "retries=" .. (retries and tostring(retries) or "unavailable")
  return table.concat(summary, "; ")
end

-- 锁定的 nvim-treesitter 在 install.lua 中为每个实际任务创建 install/<lang>
-- logger；异步调度器随后调用 vim.system(curl)。事件、目标 URL 和输出文件共同确定请求归属。
-- 只转发这些 logger 的真实事件；旧 parser.so 不能代表 update 已完成。
local function treesitter_events()
  local log = require("nvim-treesitter.log")
  local original_new, original_system = log.new, vim.system
  local state = { phase = nil, started = nil, active = {}, errors = {}, latest = {}, pending = {}, next_request = 0 }
  local requests, closing = {}, false
  local version_ok, curl_version = pcall(function()
    return original_system({ "curl", "--version" }, { text = true }):wait(2000)
  end)
  local major, minor = (version_ok and curl_version and curl_version.stdout or ""):match("^curl (%d+)%.(%d+)")
  local has_retries = major and (tonumber(major) > 8 or tonumber(major) == 8 and tonumber(minor) >= 9)

  local function request_line(request, event, details)
    if not state.phase then
      return
    end
    local prefix = "DOWNLOAD: Neovim / Treesitter / " .. request.phase .. " / " .. request.lang
    report(
      prefix
        .. "; request="
        .. request.id
        .. "; event="
        .. event
        .. "; at="
        .. os.date("!%Y-%m-%dT%H:%M:%SZ")
        .. "; wall_elapsed="
        .. elapsed(request.started)
        .. "; "
        .. details
    )
    state.latest[request.lang] = "request=" .. request.id .. " " .. event
  end

  local function matches_download(cmd, opts, callback)
    if state.phase == nil or opts ~= nil or type(callback) ~= "function" or type(cmd) ~= "table" or #cmd ~= 10 then
      return nil
    end
    if
      cmd[1] ~= "curl"
      or cmd[2] ~= "--silent"
      or cmd[3] ~= "--fail"
      or cmd[4] ~= "--show-error"
      or cmd[5] ~= "--retry"
      or cmd[6] ~= "7"
      or cmd[7] ~= "-L"
      or cmd[9] ~= "--output"
    then
      return nil
    end
    local parsers = require("nvim-treesitter.parsers")
    for lang in pairs(state.pending) do
      local info = parsers[lang]
      info = info and info.install_info
      if info and info.url and not info.path then
        local target = info.url:gsub(".git$", "")
          .. "/archive/"
          .. (info.revision or info.branch or "main")
          .. ".tar.gz"
        local output = vim.fs.joinpath(vim.fn.stdpath("cache"), "tree-sitter-" .. lang .. ".tar.gz")
        if cmd[8] == target and cmd[10] == output then
          return lang
        end
      end
    end
  end

  vim.system = function(cmd, opts, callback)
    local lang = matches_download(cmd, opts, callback)
    if not lang then
      return original_system(cmd, opts, callback)
    end
    assert(not closing, "Treesitter download requested during cleanup")
    state.pending[lang] = nil

    state.next_request = state.next_request + 1
    local request =
      { id = tostring(state.next_request), phase = state.phase, lang = lang, started = uv.hrtime(), retries_seen = 0 }
    request_line(request, "start", "url=" .. safe_url(cmd[8]))
    local args = vim.deepcopy(cmd)
    args[2] = "--no-progress-meter"
    args[#args + 1] = "--write-out"
    args[#args + 1] = curl_write_out(has_retries)

    local stderr_parts, pending = {}, ""
    local finished, cancelled, close_callbacks = false, false, {}
    local process
    local function emit(line)
      line = safe_error(line)
      if line == "" then
        return
      end
      local lower = line:lower()
      local event = lower:match("^warning:") and (lower:find("will retry", 1, true) or lower:find("retrying", 1, true))
      event = event and "retry" or "stderr"
      if event == "retry" then
        request.retries_seen = request.retries_seen + 1
      end
      request_line(request, event, "detail=" .. line)
      state.latest[lang] = "request=" .. request.id .. " " .. event .. " (" .. line .. ")"
    end
    local function flush_lines()
      while true do
        local newline = pending:find("\n", 1, true)
        if not newline then
          break
        end
        emit(pending:sub(1, newline - 1))
        pending = pending:sub(newline + 1)
      end
    end
    local function on_stderr(err, data)
      if err then
        emit("stderr read: " .. tostring(err))
      end
      if data then
        stderr_parts[#stderr_parts + 1] = data
        pending = pending .. data
        flush_lines()
      end
    end
    local function on_exit(result)
      finished = true
      requests[request.id] = nil
      if pending ~= "" then
        emit(pending)
        pending = ""
      end
      if cancelled then
        request_line(request, "cancel", "exit=" .. tostring(result.code))
        for _, done in ipairs(close_callbacks) do
          done()
        end
        return
      end
      local stderr = table.concat(stderr_parts)
      result.stderr = safe_error(stderr)
      local summary = curl_summary(result.stdout, has_retries)
      result.stdout = "" -- 原插件将响应写入 --output，未要求 stdout。
      request_line(
        request,
        "finish",
        "exit="
          .. tostring(result.code)
          .. "; retry_notices="
          .. request.retries_seen
          .. "; original_url="
          .. safe_url(cmd[8])
          .. "; "
          .. summary
      )
      callback(result)
    end
    local ok, spawned = pcall(original_system, args, { stderr = on_stderr }, on_exit)
    if not ok then
      request_line(request, "spawn_fail", "reason=" .. safe_error(spawned))
      error(spawned, 0)
    end
    process = spawned
    -- 单个异步任务可关闭此句柄，但插件的并发 join 不会级联关闭所有任务；
    -- 同时登记下载进程，由本次准备的退出路径统一取消并等待回收。
    local handle = setmetatable({
      close = function(_, done)
        if finished then
          if done then
            done()
          end
        else
          if done then
            close_callbacks[#close_callbacks + 1] = done
          end
          if not cancelled then
            cancelled = true
            process:kill(9)
          end
        end
      end,
    }, {
      __index = function(_, key)
        local value = process[key]
        if type(value) == "function" then
          return function(_, ...)
            return value(process, ...)
          end
        end
        return value
      end,
    })
    requests[request.id] = handle
    return handle
  end
  log.new = function(context)
    local logger = original_new(context)
    local lang = context and context:match("^install/(.+)$")
    if not lang then
      return logger
    end
    local original_info, original_error = logger.info, logger.error
    logger.info = function(self, message, ...)
      local formatted = message:format(...)
      local result = original_info(self, message, ...)
      if state.phase then
        if formatted == "Downloading tree-sitter-" .. lang .. "..." then
          state.pending[lang] = true
        else
          state.pending[lang] = nil
          state.latest[lang] = nil
        end
        if formatted == "Language installed" then
          state.active[lang] = nil
          state.latest[lang] = nil
        else
          state.active[lang] = formatted
        end
        report(
          "PARSER: Neovim / Treesitter / "
            .. state.phase
            .. " / "
            .. lang
            .. "; at="
            .. os.date("!%Y-%m-%dT%H:%M:%SZ")
            .. "; elapsed="
            .. elapsed(state.started)
            .. "; action="
            .. formatted
        )
      end
      return result
    end
    logger.error = function(self, message, ...)
      local formatted = message:format(...)
      local result = original_error(self, message, ...)
      if state.phase then
        state.pending[lang] = nil
        state.errors[#state.errors + 1] = lang .. ": " .. formatted
        state.active[lang] = nil
        state.latest[lang] = nil
        report(
          "PARSER FAIL: Neovim / Treesitter / "
            .. state.phase
            .. " / "
            .. lang
            .. "; at="
            .. os.date("!%Y-%m-%dT%H:%M:%SZ")
            .. "; elapsed="
            .. elapsed(state.started)
            .. "; reason="
            .. formatted
        )
      end
      return result
    end
    return logger
  end
  return state,
    function()
      closing = true
      local errors = {}
      for _, handle in ipairs(vim.tbl_values(requests)) do
        local ok, err = pcall(handle.close, handle)
        if not ok then
          errors[#errors + 1] = safe_error(err)
        end
      end
      if next(requests) then
        local ok, drained = pcall(vim.wait, 2000, function()
          return next(requests) == nil
        end, 10)
        if not ok then
          errors[#errors + 1] = safe_error(drained)
        elseif not drained then
          errors[#errors + 1] = "curl processes did not exit within the 2s cleanup limit"
        end
      end
      -- 清理失败也恢复覆盖；主调用方保留原始故障，并补充清理诊断。
      state.phase = nil
      log.new = original_new
      vim.system = original_system
      assert(#errors == 0, table.concat(errors, "; "))
    end
end

local function wait_treesitter(task, remaining, state)
  local ok, result = task:pwait(remaining())
  if not ok then
    if result == "timeout" then
      local closed, close_error = pcall(function()
        task:close()
      end)
      if not closed then
        report("FAIL: Neovim / Treesitter / " .. state.phase .. " cleanup: " .. tostring(close_error))
      end
      local active = vim.tbl_keys(state.active)
      table.sort(active)
      local parser = #active > 0 and table.concat(active, ", ") or "unknown (no parser event yet)"
      error("Treesitter " .. state.phase .. " timed out; active parser=" .. parser, 0)
    end
    error(task:traceback(result), 0)
  end
  local reason = #state.errors > 0 and table.concat(state.errors, "\n")
    or "plugin returned false without a parser error"
  assert(result, "Treesitter " .. state.phase .. " failed: " .. reason)
end

local function prepare_treesitter()
  local lazy = require("lazy")
  step("Neovim / Treesitter / load", nil, function()
    lazy.load({ plugins = { "nvim-treesitter" } })
  end)
  local ts = require("nvim-treesitter")
  local parsers = LazyVim.opts("nvim-treesitter").ensure_installed
  assert(type(parsers) == "table" and #parsers > 0, "no configured Treesitter parser set")
  -- 补装缺失的解析器，并将已有解析器更新到锁定插件声明的修订；
  -- nvim-treesitter 会跳过已经匹配的修订。
  local state, cleanup_events = treesitter_events()
  local function active_parsers()
    local entries = {}
    for name, action in pairs(state.active) do
      entries[#entries + 1] = name .. " (" .. action .. ")"
    end
    table.sort(entries)
    local downloads = {}
    for name, latest in pairs(state.latest) do
      downloads[#downloads + 1] = name .. " (" .. latest .. ")"
    end
    table.sort(downloads)
    local suffix = #downloads > 0 and "; downloads=" .. table.concat(downloads, ", ") or ""
    return (#entries > 0 and "; active=" .. table.concat(entries, ", ") or "; waiting for nvim-treesitter task")
      .. suffix
  end
  local ok, err = pcall(function()
    for _, phase in ipairs({ "install", "update" }) do
      state.phase, state.started, state.active, state.errors, state.latest, state.pending =
        phase, uv.hrtime(), {}, {}, {}, {}
      step("Neovim / Treesitter / " .. phase, 600000, function(remaining)
        wait_treesitter(ts[phase](parsers), remaining, state)
      end, active_parsers)
    end
  end)
  local cleaned, cleanup_error = pcall(cleanup_events)
  if not cleaned then
    report("FAIL: Neovim / Treesitter / download cleanup; reason=" .. tostring(cleanup_error))
  end
  if not ok then
    error(err, 0)
  end
  assert(cleaned, cleanup_error)
  -- 首次安装前目录尚不存在，lazy.nvim 加载插件时可能将其从 runtimepath 移除；
  -- 安装完成后补回实际目录，让本次会话能发现新安装的解析器及查询文件。
  step("Neovim / Treesitter / verify", nil, function()
    vim.opt.rtp:prepend(require("nvim-treesitter.config").get_install_dir(""))
    local installed = ts.get_installed()
    for _, name in ipairs(parsers) do
      assert(vim.tbl_contains(installed, name), "Treesitter parser missing: " .. name)
      report("PARSER: Neovim / Treesitter / verify / " .. name .. "; action=load")
      local loaded, load_error = vim.treesitter.language.add(name)
      assert(loaded, "Treesitter parser cannot be loaded: " .. name .. ": " .. tostring(load_error))
    end
  end)
  report("READY: configured Treesitter parsers installed and loadable")
end

local function prepare_completion()
  local lazy = require("lazy")
  step("Neovim / completion / load", nil, function()
    lazy.load({ plugins = { "blink.cmp" } })
  end)
  local implementation = step("Neovim / completion / resources", 300000, function(remaining)
    local done, failure, selected = false, nil, nil
    require("blink.cmp.fuzzy.download").ensure_downloaded(function(err, value)
      done, failure, selected = true, err, value
    end)
    assert(
      vim.wait(remaining(), function()
        return done
      end, 50),
      "completion resource preparation timed out"
    )
    assert(not failure, "completion resources: " .. tostring(failure))
    assert(selected == "rust" or selected == "lua", "invalid completion implementation: " .. tostring(selected))
    local requested = require("blink.cmp.config").fuzzy.implementation
    assert(selected == "rust" or requested == "lua", "completion binary download failed and fell back to Lua")
    require("blink.cmp.fuzzy").set_implementation(selected)
    return selected
  end)
  report("READY: completion resources (" .. implementation .. ")")
end

local function main(notifications)
  -- 启动时使用 -u NONE 跳过隐式配置加载；本脚本负责显式初始化。
  vim.go.loadplugins = true
  local config_dir = vim.fn.stdpath("config")
  local lock_path = config_dir .. "/lazy-lock.json"
  local lock_text = table.concat(vim.fn.readfile(lock_path), "\n")
  local lock = vim.json.decode(lock_text)
  prepare_plugins(config_dir, lock_path, lock)
  prepare_mason()
  prepare_treesitter()
  prepare_completion()
  assert(table.concat(vim.fn.readfile(lock_path), "\n") == lock_text, "bootstrap changed the lockfile")
  assert(#notifications == 0, table.concat(notifications, "\n"))
end

-- lazy.nvim 可仅通过 ERROR 通知报告配置异常；收集必须覆盖后续惰性加载与异步准备。
-- 整体成功或异常退出都在同一处恢复通知接口。
local original_notify, notifications = vim.notify, {}
vim.notify = function(message, level)
  if level == vim.log.levels.ERROR then
    notifications[#notifications + 1] = tostring(message)
  end
  report(tostring(message))
end
local ok, err = xpcall(function()
  main(notifications)
end, debug.traceback)
vim.notify = original_notify
if not ok then
  io.stderr:write("FAIL: Neovim preparation: " .. tostring(err) .. "\n")
  vim.cmd("cquit 1")
end
vim.cmd("qa!")
