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

local original_notify, original_cmd = vim.notify, vim.cmd
vim.cmd = function(command)
  if command == "qa!" or command == "cquit 1" then
    counters.notify_restored = vim.notify == original_notify
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
      return { ensure_installed = { "bash" } }
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

local function task(phase)
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
      return { "bash" }
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

dofile(entrypoint)
