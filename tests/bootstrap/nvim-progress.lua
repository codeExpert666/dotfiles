-- 离线执行生产 nvim.lua；插件接口和慢任务由此夹具控制，不访问用户目录或网络。
local entrypoint, scenario, marker = assert(arg[1]), assert(arg[2]), assert(arg[3])
local uv = vim.uv
local real_timer = uv.new_timer
local counters = { started = 0, stopped = 0, closed = 0, task_closed = 0 }
local results = marker .. ".timers"

-- 缩短测试中的 30 秒心跳，仍调用生产脚本的定时器及清理逻辑。
if vim.env.DOTFILES_TEST_REAL_TIMER ~= "1" then
  uv.new_timer = function()
    local timer = assert(real_timer())
    return {
      start = function(_, timeout, repeat_ms, callback)
        counters.started = counters.started + 1
        return timer:start(math.min(timeout, 30), math.min(repeat_ms, 30), callback)
      end,
      stop = function()
        counters.stopped = counters.stopped + 1
        return timer:stop()
      end,
      close = function()
        counters.closed = counters.closed + 1
        return timer:close()
      end,
    }
  end
end

local original_notify, original_system, original_cmd = vim.notify, vim.system, vim.cmd
vim.cmd = function(command)
  if command == "qa!" or command == "cquit 1" then
    counters.notify_restored = vim.notify == original_notify
    counters.system_restored = vim.system == original_system
    -- 清理后留出多个心跳周期，确认不会再输出 WAIT。
    vim.wait(100, function()
      return false
    end, 10)
    local file = assert(io.open(results, "w"))
    file:write(vim.json.encode(counters))
    file:close()
  end
  return original_cmd(command)
end

vim.fn.executable = function()
  return 1
end

local lazy = {
  setup = function()
    if scenario == "setup_failure" then
      error("fixture plugin setup failed")
    end
  end,
  restore = function() end,
  load = function(opts)
    local plugin = opts.plugins[1]
    local errors = {
      mason_notify_error = "mason.nvim",
      treesitter_notify_error = "nvim-treesitter",
      completion_notify_error = "blink.cmp",
    }
    -- lazy.nvim 可将配置异常转为通知而不向调用者抛出；缓存资源仍能继续准备。
    if plugin == errors[scenario] then
      vim.notify("fixture config failed: " .. plugin, vim.log.levels.ERROR)
    elseif scenario == "treesitter_notify_warning" and plugin == "nvim-treesitter" then
      vim.notify("fixture config warning: " .. plugin, vim.log.levels.WARN)
    end
  end,
}
package.preload.lazy = function()
  return lazy
end
package.preload["lazy.core.loader"] = function()
  return { startup = function() end }
end
package.preload["lazy.core.config"] = function()
  return { plugins = {} }
end
package.preload["lazy.manage.lock"] = function()
  return { update = function() end }
end

LazyVim = {
  opts = function(name)
    if name == "mason.nvim" then
      local packages = (scenario == "mason_wait" or scenario == "mason_failure" or scenario == "mason_not_installed")
          and { "taplo" }
        or {}
      return { ensure_installed = packages }
    elseif name == "nvim-lspconfig" then
      return { servers = {} }
    elseif name == "nvim-treesitter" then
      local parallel = scenario == "curl_parallel" or scenario:match("^curl_real_")
      return { ensure_installed = parallel and { "bash", "go" } or { "bash" } }
    end
    error("unexpected plugin options: " .. name)
  end,
}

local function released()
  return uv.fs_stat(marker) ~= nil
end

package.preload["mason-registry"] = function()
  local installed = false
  return {
    refresh = function(callback)
      if scenario == "registry_wait" then
        local timer = assert(real_timer())
        timer:start(10, 10, function()
          if released() then
            timer:stop()
            timer:close()
            callback(true, {})
          end
        end)
      elseif scenario == "registry_failure" then
        callback(false, "registry transport refused")
      else
        callback(true, {})
      end
    end,
    get_package = function(name)
      assert(name == "taplo")
      return {
        is_installed = function()
          return installed
        end,
        install = function(_, _, callback)
          local timer = assert(real_timer())
          timer:start(10, 10, function()
            if released() then
              timer:stop()
              timer:close()
              if scenario == "mason_failure" then
                callback(false, "archive checksum mismatch")
              else
                installed = scenario ~= "mason_not_installed"
                callback(true)
              end
            end
          end)
        end,
      }
    end,
  }
end
package.preload["mason-lspconfig.mappings"] = function()
  return {
    get_mason_map = function()
      return { lspconfig_to_package = {} }
    end,
  }
end

local logger = {}
function logger:info(message, ...)
  return message:format(...)
end
function logger:error(message, ...)
  return message:format(...)
end
package.preload["nvim-treesitter.log"] = function()
  return {
    new = function()
      return setmetatable({}, { __index = logger })
    end,
  }
end

package.preload["nvim-treesitter.parsers"] = function()
  local base = vim.env.DOTFILES_TEST_CURL_BASE or "http://127.0.0.1:1"
  return {
    bash = { install_info = { url = base .. "/repo/bash", revision = "test-revision" } },
    go = { install_info = { url = base .. "/repo/go", revision = "test-revision" } },
  }
end

local function curl_task(phase)
  if phase == "update" then
    return {
      pwait = function()
        return true, true
      end,
    }
  end
  vim.fn.mkdir(vim.fn.stdpath("cache"), "p")
  local names = scenario == "curl_parallel" and { "bash", "go" } or { "bash" }
  local handles, completed, failures = {}, {}, {}
  local non_target_done, unrelated_done = scenario ~= "curl_no_stats", scenario ~= "curl_no_stats"
  for _, name in ipairs(names) do
    local thread = coroutine.create(function()
      local log = require("nvim-treesitter.log").new("install/" .. name)
      log:info("Downloading tree-sitter-" .. name .. "...")
      if scenario == "curl_no_stats" then
        local other = vim.system({ "sh", "-c", "printf unchanged" }, nil, function(result)
          counters.non_target_original = result.code == 0 and result.stdout == "unchanged"
          non_target_done = true
        end)
        assert(other.close == nil, "non-download SystemObj was replaced")
        local unrelated = vim.system(
          {
            "curl",
            "--silent",
            "--fail",
            "--show-error",
            "--retry",
            "7",
            "-L",
            "http://example.invalid/unrelated",
            "--output",
            vim.fs.joinpath(vim.fn.stdpath("cache"), "unrelated.tar.gz"),
          },
          nil,
          function(result)
            counters.unrelated_curl_original = result.code == 0 and result.stdout == ""
            unrelated_done = true
          end
        )
        assert(unrelated.close == nil, "unrelated curl SystemObj was replaced")
      end
      local info = require("nvim-treesitter.parsers")[name].install_info
      local target = info.url .. "/archive/" .. info.revision .. ".tar.gz"
      local output = vim.fs.joinpath(vim.fn.stdpath("cache"), "tree-sitter-" .. name .. ".tar.gz")
      -- 与锁定插件 install.lua 的参数和回调方式一致；响应来自本地 HTTP 服务。
      local args = { "curl", "--silent", "--fail", "--show-error", "--retry", "7", "-L", target, "--output", output }
      local before = vim.deepcopy(args)
      local handle = vim.system(args, nil, function(result)
        counters.curl_callbacks = (counters.curl_callbacks or 0) + 1
        counters.curl_results = counters.curl_results or {}
        counters.curl_results[name] = result
        if result.code ~= 0 then
          failures[#failures + 1] = name
          log:error("Error during download: %s", result.stderr)
        else
          log:info("Compiling parser")
          log:info("Installing parser")
          log:info("Language installed")
        end
        completed[name] = true
      end)
      assert(vim.deep_equal(args, before), "download wrapper changed the caller's arguments")
      handles[name] = handle
      local file = assert(io.open(marker .. "." .. name .. ".pid", "w"))
      file:write(handle.pid)
      file:close()
    end)
    assert(coroutine.resume(thread))
  end
  return {
    pwait = function(_, timeout)
      local limit = scenario == "curl_cancel" and 90 or timeout
      local finished = vim.wait(limit, function()
        if not non_target_done or not unrelated_done then
          return false
        end
        for _, name in ipairs(names) do
          if not completed[name] then
            return false
          end
        end
        return true
      end, 10)
      if not finished then
        return false, "timeout"
      end
      return true, #failures == 0
    end,
    close = function()
      counters.task_closed = counters.task_closed + 1
      local closed, needed = 0, 0
      for _, name in ipairs(names) do
        if not completed[name] then
          needed = needed + 1
          handles[name]:close(function()
            closed = closed + 1
          end)
        end
      end
      assert(
        vim.wait(2000, function()
          return closed == needed
        end, 10),
        "curl cancellation did not reap child"
      )
    end,
    traceback = function(_, reason)
      return reason
    end,
  }
end

local function task(phase)
  if scenario:match("^curl_") then
    return curl_task(phase)
  end
  local slow = (scenario == "install_wait" or scenario == "install_failure" or scenario == "install_timeout")
      and phase == "install"
    or scenario == "update_wait" and phase == "update"
  if not slow then
    return {
      pwait = function()
        return true, true
      end,
    }
  end
  local log = require("nvim-treesitter.log").new("install/bash")
  log:info("Downloading tree-sitter-bash...")
  return {
    pwait = function(_, timeout)
      assert(timeout > 0 and timeout <= 600000, "step deadline was reset")
      if scenario == "install_timeout" then
        vim.wait(65, function()
          return false
        end, 10)
        return false, "timeout"
      end
      local compiled = false
      local done = vim.wait(timeout, function()
        if released() and not compiled then
          compiled = true
          log:info("Compiling parser")
          return false
        end
        return compiled
      end, 10)
      if not done then
        return false, "timeout"
      end
      if scenario == "install_failure" then
        log:error('Error during "tree-sitter build": clang: bad grammar')
        return true, false
      end
      log:info("Installing parser")
      log:info("Language installed")
      return true, true
    end,
    close = function()
      counters.task_closed = counters.task_closed + 1
    end,
    traceback = function(_, reason)
      return reason
    end,
  }
end

package.preload["nvim-treesitter"] = function()
  return {
    install = function()
      return task("install")
    end,
    update = function()
      return task("update")
    end,
    get_installed = function()
      return scenario == "curl_parallel" and { "bash", "go" } or { "bash" }
    end,
  }
end
package.preload["nvim-treesitter.config"] = function()
  return {
    get_install_dir = function()
      return vim.fn.stdpath("data") .. "/site"
    end,
  }
end

vim.treesitter.language.add = function()
  if scenario == "verify_failure" then
    return false, 'No parser for language "bash"'
  end
  return true
end

package.preload["blink.cmp.fuzzy.download"] = function()
  return {
    ensure_downloaded = function(callback)
      if scenario == "completion_wait" then
        local timer = assert(real_timer())
        timer:start(10, 10, function()
          if released() then
            timer:stop()
            timer:close()
            callback(nil, "rust")
          end
        end)
      elseif scenario == "completion_async_notify_error" then
        vim.schedule(function()
          vim.notify("fixture completion callback failed", vim.log.levels.ERROR)
          callback(nil, "rust")
        end)
      else
        callback(nil, "rust")
      end
    end,
  }
end
package.preload["blink.cmp.config"] = function()
  return { fuzzy = { implementation = "rust" } }
end
package.preload["blink.cmp.fuzzy"] = function()
  return {
    set_implementation = function()
      if scenario == "lock_failure" then
        local lock = vim.fn.stdpath("config") .. "/lazy-lock.json"
        local file = assert(io.open(lock, "w"))
        file:write('{"changed":true}\n')
        file:close()
      end
    end,
  }
end

if scenario:match("^curl_real_") then
  -- 真实安装器的 join 不会级联关闭下载任务；不能用上方手动遍历 handles 的夹具替代。
  for _, name in ipairs({ "nvim-treesitter", "nvim-treesitter.config", "nvim-treesitter.log" }) do
    package.preload[name], package.loaded[name] = nil, nil
  end
  vim.opt.rtp:prepend(assert(vim.env.DOTFILES_TEST_TREESITTER))
  local system = vim.system
  vim.system = function(cmd, opts, callback)
    if cmd[1] ~= "curl" or cmd[2] == "--version" then
      return system(cmd, opts, callback)
    end
    local process = system(cmd, opts, function(result)
      counters.real_curl_exits = (counters.real_curl_exits or 0) + 1
      callback(result)
    end)
    local name = assert(cmd[10]:match("/tree%-sitter%-(.+)%.tar%.gz$"))
    local file = assert(io.open(marker .. "." .. name .. ".pid", "w"))
    file:write(process.pid)
    file:close()
    if scenario == "curl_real_cleanup_failure" then
      local kill = process.kill
      process.kill = function(self, ...)
        kill(self, ...)
        error("fixture kill diagnostic")
      end
    end
    return process
  end
  original_system = vim.system

  local ts = require("nvim-treesitter")
  local install = ts.install
  ts.install = function(...)
    local task = install(...)
    local pwait = task.pwait
    task.pwait = function(self, timeout)
      local seen = vim.fs.dirname(marker) .. "/http-seen/"
      assert(
        vim.wait(2000, function()
          return uv.fs_stat(seen .. "bash") ~= nil and uv.fs_stat(seen .. "go") ~= nil
        end, 10),
        "real parser requests did not reach the local server"
      )
      if scenario == "curl_real_error" or scenario == "curl_real_cleanup_failure" then
        error("fixture waiter failed with active downloads")
      end
      -- 只缩短夹具中的等待；真实任务的 pwait/close 和生产下载策略保持原样。
      return pwait(self, math.min(timeout, 90))
    end
    return task
  end
end

-- 在生产包装器与真实进程之间记录实际参数；只在 TLS 故障夹具中缩短等待。
if scenario:match("^curl_") then
  local system = vim.system
  counters.system_commands = {}
  vim.system = function(cmd, opts, callback)
    table.insert(counters.system_commands, vim.deepcopy(cmd))
    if scenario:match("^curl_tls_") and cmd[1] == "curl" and cmd[2] ~= "--version" then
      cmd = vim.deepcopy(cmd)
      for index, value in ipairs(cmd) do
        if value == "--connect-timeout" then
          cmd[index + 1] = "0.3"
        end
      end
      if scenario == "curl_tls_failure" then
        cmd[6] = "1" -- 生产值另行断言为 7；失败测试只等一次原生退避。
      end
      counters.executed_curl = vim.deepcopy(cmd)
    end
    return system(cmd, opts, callback)
  end
  original_system = vim.system
end

dofile(entrypoint)
