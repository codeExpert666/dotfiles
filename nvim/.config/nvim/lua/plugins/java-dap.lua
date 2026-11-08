-- Java 扩展去重，并在命中断点时优先返回源码窗口。
return {
  {
    "mfussenegger/nvim-jdtls",
    optional = true,
    opts = function(_, opts)
      local original = opts.jdtls
      opts.jdtls = function(config)
        if type(original) == "function" then
          config = original(config) or config
        elseif type(original) == "table" then
          config = vim.tbl_deep_extend("force", config, original)
        end

        local mason = vim.env.MASON
        local test_dir = mason and (mason .. "/share/java-test")
        local seen, bundles = {}, {}
        for _, path in ipairs((config.init_options or {}).bundles or {}) do
          local name = vim.fs.basename(path)
          local test_bundle = test_dir and vim.fs.dirname(path) == test_dir
          -- runner/agent 由测试进程使用，不是 OSGi 扩展；同版本依赖复用 JDTLS 内置包。
          local excluded = test_bundle
            and (
              name == "com.microsoft.java.test.runner-jar-with-dependencies.jar"
              or name == "jacocoagent.jar"
              or vim.fn.filereadable(mason .. "/packages/jdtls/plugins/" .. name) == 1
            )
          -- Mason 可能同时导出带版本和不带版本的软链，只提交一次实际文件。
          local real = vim.uv.fs_realpath(path) or path
          if not excluded and not seen[real] then
            seen[real] = true
            bundles[#bundles + 1] = path
          end
        end
        config.init_options = config.init_options or {}
        config.init_options.bundles = bundles
        config.commands = config.commands or {}
        -- 扩展在初始化时已完整提交；本配置不在会话内异步发现新扩展，变更后重启 JDTLS。
        -- 此命令询问待补充的 bundles，协议要求返回数组（不能返回 nil 或空对象）。
        config.commands["_java.reloadBundles.command"] = config.commands["_java.reloadBundles.command"]
          or function()
            return {}
          end
        return config
      end
    end,
  },
  {
    "mfussenegger/nvim-dap",
    optional = true,
    opts = function()
      require("dap").defaults.java.switchbuf = "useopen,usetab,newtab"
    end,
  },
}
