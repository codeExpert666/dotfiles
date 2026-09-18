-- 无界面准备使用仓库配置的副本，并将插件写入用户实际的 Neovim 数据目录。
-- 锁文件保持不变；安装、恢复或构建失败均终止准备。
local function report(message)
  io.stdout:write(message .. "\n")
  io.stdout:flush()
end

local function command(args)
  local result = vim.system(args, { text = true }):wait(60000)
  assert(result.code == 0, table.concat(args, " ") .. ": " .. (result.stderr or ""))
  return vim.trim(result.stdout)
end

-- 以下阶段适配 lazy-lock.json 锁定版本的插件接口；这些临时覆盖只用于准备阶段，
-- 更新锁定版本时须重新验证接口和覆盖行为。
local function prepare_plugins(config_dir, lock_path, lock, notifications)
  vim.opt.rtp:prepend(vim.fn.stdpath("data") .. "/lazy/lazy.nvim")
  local lazy = require("lazy")
  local setup = lazy.setup
  vim.notify = function(message, level)
    if level == vim.log.levels.ERROR then
      notifications[#notifications + 1] = tostring(message)
    end
    report(tostring(message))
  end

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
  dofile(config_dir .. "/init.lua")
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
    lazy.restore({ plugins = restore, wait = true, show = false })
  end
  for name, pinned in pairs(lock) do
    local plugin = cfg.plugins[name]
    for _, task in ipairs(plugin._.tasks or {}) do
      assert(not task:has_errors(), name .. ": " .. task:output(vim.log.levels.ERROR))
    end
    assert(command({ "git", "-C", plugin.dir, "rev-parse", "HEAD" }) == pinned.commit, "lock mismatch: " .. name)
  end
  report("READY: all plugin checkouts match lazy-lock.json")

  -- LazyVim 的初始化会建立选项辅助函数；必须等插件恢复完成后再运行。
  startup()
end

local function prepare_mason()
  local lazy = require("lazy")
  lazy.load({ plugins = { "mason.nvim", "mason-lspconfig.nvim", "nvim-lspconfig" } })
  local registry = require("mason-registry")
  local registry_done = false
  registry.refresh(function()
    registry_done = true
  end)
  assert(
    vim.wait(300000, function()
      return registry_done
    end, 50),
    "Mason registry refresh timed out"
  )
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
      report("INSTALL: Mason " .. name)
      local done, failure = false, nil
      package:install({}, function(success, err)
        done = true
        if not success then
          failure = tostring(err)
        end
      end)
      assert(
        vim.wait(600000, function()
          return done
        end, 50),
        "Mason installation timed out: " .. name
      )
      assert(not failure, "Mason installation failed: " .. name .. ": " .. tostring(failure))
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

local function prepare_treesitter()
  local lazy = require("lazy")
  lazy.load({ plugins = { "nvim-treesitter" } })
  local ts = require("nvim-treesitter")
  local parsers = LazyVim.opts("nvim-treesitter").ensure_installed
  assert(type(parsers) == "table" and #parsers > 0, "no configured Treesitter parser set")
  -- 补装缺失的解析器，并将已有解析器更新到锁定插件声明的修订；
  -- nvim-treesitter 会跳过已经匹配的修订。
  assert(ts.install(parsers):wait(600000), "Treesitter installation failed")
  assert(ts.update(parsers):wait(600000), "Treesitter update to pinned parser revisions failed")
  local installed = ts.get_installed()
  for _, name in ipairs(parsers) do
    assert(vim.tbl_contains(installed, name), "Treesitter parser missing: " .. name)
    assert(vim.treesitter.language.add(name), "Treesitter parser cannot be loaded: " .. name)
  end
  report("READY: configured Treesitter parsers installed and loadable")
end

local function prepare_completion()
  local lazy = require("lazy")
  lazy.load({ plugins = { "blink.cmp" } })
  local done, failure, implementation = false, nil, nil
  require("blink.cmp.fuzzy.download").ensure_downloaded(function(err, selected)
    done, failure, implementation = true, err, selected
  end)
  assert(
    vim.wait(300000, function()
      return done
    end, 50),
    "completion resource preparation timed out"
  )
  assert(not failure, "completion resources: " .. tostring(failure))
  assert(
    implementation == "rust" or implementation == "lua",
    "invalid completion implementation: " .. tostring(implementation)
  )
  local requested = require("blink.cmp.config").fuzzy.implementation
  assert(implementation == "rust" or requested == "lua", "completion binary download failed and fell back to Lua")
  require("blink.cmp.fuzzy").set_implementation(implementation)
  report("READY: completion resources (" .. implementation .. ")")
end

local function main()
  -- 启动时使用 -u NONE 跳过隐式配置加载；本脚本负责显式初始化。
  vim.go.loadplugins = true
  local config_dir = vim.fn.stdpath("config")
  local lock_path = config_dir .. "/lazy-lock.json"
  local lock_text = table.concat(vim.fn.readfile(lock_path), "\n")
  local lock = vim.json.decode(lock_text)
  local notifications = {}
  prepare_plugins(config_dir, lock_path, lock, notifications)
  prepare_mason()
  prepare_treesitter()
  prepare_completion()
  assert(table.concat(vim.fn.readfile(lock_path), "\n") == lock_text, "bootstrap changed the lockfile")
  assert(#notifications == 0, table.concat(notifications, "\n"))
end

local ok, err = xpcall(main, debug.traceback)
if not ok then
  io.stderr:write("FAIL: Neovim preparation: " .. tostring(err) .. "\n")
  vim.cmd("cquit 1")
end
vim.cmd("qa!")
