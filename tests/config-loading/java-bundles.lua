-- 只使用合成文件，验证测试运行依赖不会误作扩展，以及不同版本不会被误删。
local spec = dofile(vim.fn.stdpath("config") .. "/lua/plugins/java-dap.lua")
local root = vim.fn.tempname()
local original_mason = vim.env.MASON
vim.env.MASON = root
local test_dir = root .. "/share/java-test"
local core_dir = root .. "/packages/jdtls/plugins"
vim.fn.mkdir(test_dir, "p")
vim.fn.mkdir(core_dir, "p")
local function file(path)
  vim.fn.writefile({}, path)
  return path
end
local plugin = file(test_dir .. "/com.microsoft.java.test.plugin-1.jar")
local alias = test_dir .. "/com.microsoft.java.test.plugin.jar"
assert(vim.uv.fs_symlink(plugin, alias))
local runner = file(test_dir .. "/com.microsoft.java.test.runner-jar-with-dependencies.jar")
local agent = file(test_dir .. "/jacocoagent.jar")
local shared = file(test_dir .. "/org.objectweb.asm_9.10.1.jar")
local different = file(test_dir .. "/org.objectweb.asm_9.11.0.jar")
local tree = file(test_dir .. "/org.objectweb.asm.tree_9.10.1.jar")
local debug = file(root .. "/debug.jar")
file(core_dir .. "/org.objectweb.asm_9.10.1.jar")

local opts = { jdtls = { settings = { java = { marker = true } } } }
spec[1].opts(nil, opts)
local input = { debug, plugin, alias, runner, agent, shared, different, tree }
local config = opts.jdtls({ init_options = { bundles = input, marker = true } })
assert(vim.deep_equal(config.init_options.bundles, { debug, plugin, different, tree }))
assert(config.settings.java.marker and config.init_options.marker)
assert(#input == 8, "shared input list was modified")
assert(vim.fn.filereadable(runner) == 1 and vim.fn.filereadable(agent) == 1, "runtime JARs were deleted")

vim.fn.delete(core_dir .. "/org.objectweb.asm_9.10.1.jar")
config = opts.jdtls({ init_options = { bundles = { shared } } })
assert(vim.deep_equal(config.init_options.bundles, { shared }), "dependency missing from JDTLS must be retained")

local called = 0
opts = {
  jdtls = function(current)
    called = called + 1
    current.init_options.bundles[#current.init_options.bundles + 1] = alias
    return vim.tbl_extend("force", current, { marker = true })
  end,
}
spec[1].opts(nil, opts)
config = opts.jdtls({ init_options = { bundles = { plugin } } })
assert(called == 1 and config.marker and #config.init_options.bundles == 1)

opts = {
  jdtls = function(current)
    current.marker = true
  end,
}
spec[1].opts(nil, opts)
assert(opts.jdtls({}).marker, "in-place callback was not preserved")
vim.env.MASON = nil
opts = {}
spec[1].opts(nil, opts)
assert(vim.deep_equal(opts.jdtls({ init_options = { bundles = { debug } } }).init_options.bundles, { debug }))

local reload = "_java.reloadBundles.command"
local global_reload = vim.lsp.commands[reload]
local unrelated = function()
  return "unrelated"
end
config = opts.jdtls({ commands = { unrelated = unrelated } })
assert(vim.json.encode(config.commands[reload]()) == "[]", "reload response must be a JSON array")
assert(config.commands.unrelated == unrelated and config.commands.unknown == nil)
assert(vim.lsp.commands[reload] == global_reload, "compatibility command leaked to other clients")
local custom_reload = function()
  return { "/custom/late-extension.jar" }
end
config = opts.jdtls({ commands = { [reload] = custom_reload } })
assert(config.commands[reload] == custom_reload, "existing bundle synchronization was replaced")

vim.env.MASON = original_mason
vim.fn.delete(root, "rf")
print("PASS: Java bundle filtering, reload protocol, runtime preservation and option composition")
