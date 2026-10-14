-- Java 命中断点时优先返回源码窗口，避免跳入 DAP UI 的固定面板。
return {
  {
    "mfussenegger/nvim-dap",
    optional = true,
    opts = function()
      require("dap").defaults.java.switchbuf = "useopen,usetab,newtab"
    end,
  },
}
