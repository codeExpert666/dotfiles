-- 复用隔离缓存时避免 Mason 的过期 registry 触发网络请求；其余插件接口仍真实执行。
local entrypoint = assert(arg[1])
vim.opt.rtp:prepend(vim.fn.stdpath("data") .. "/lazy/mason.nvim")
require("mason.settings").current.registry_cache.refresh = false
dofile(entrypoint)
