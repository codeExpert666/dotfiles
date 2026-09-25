-- 缓存集成夹具：在真实安装入口才发布预编译解析器，保留首次启动时目录不存在的条件。
-- 下载和编译由缓存代替，其余准备流程及插件 API 仍执行生产代码。
local cached_site, entrypoint = assert(arg[1]), assert(arg[2])
local data = vim.fn.stdpath("data")
assert(not vim.uv.fs_stat(data .. "/site"), "first installation must start without the site directory")
vim.opt.rtp:prepend(data .. "/lazy/nvim-treesitter")
-- 离线缓存集成仍调用真实 Mason refresh API，但不刷新已过期的 registry。
vim.opt.rtp:prepend(data .. "/lazy/mason.nvim")
require("mason.settings").current.registry_cache.refresh = false
local ts = require("nvim-treesitter")
local install = ts.install
ts.install = function(...)
  -- bash 不属于 Neovim 内置解析器，确保加载检查不能借助系统 runtime 意外通过。
  assert(#vim.api.nvim_get_runtime_file("parser/bash.*", true) == 0, "bash parser already exists on runtimepath")
  local site = require("nvim-treesitter.config").get_install_dir("")
  local result = vim.system({ "cp", "-R", cached_site .. "/.", site }, { text = true }):wait(10000)
  assert(result.code == 0, result.stderr)
  io.stdout:write("FIXTURE: published parsers during first installation\n")
  io.stdout:flush()
  ts.install = install
  return install(...)
end
dofile(entrypoint)
