-- 在临时 Neovim 会话的 VimEnter 后执行，验证已准备的编辑器能力。
-- 将诊断写入报告文件；全部检查执行完毕后写入完成标记，供 runtime.py 核对。
local function emit(level, id, message)
  local hint = ""
  if level == "FAIL" then
    hint =
      "Inspect this component with :messages, :LspInfo or :ConformInfo; --verbose includes temporary LSP diagnostics."
  elseif level == "WARN" then
    hint = "Prepare the missing or incompatible parser during bootstrap."
  end
  vim.fn.writefile(
    { table.concat({ level, "nvim.runtime." .. id, (message:gsub("[\t\r\n]", " ")), hint }, "\t") },
    vim.env.DOTFILES_DOCTOR_REPORT,
    "a"
  )
end
local function check(condition, id, message)
  emit(condition and "PASS" or "FAIL", id, message)
end

local function main()
  check(
    vim.g.autoformat == true and vim.o.relativenumber == false,
    "options",
    "managed autoformat and absolute line-number settings"
  )
  require("lazy").load({ plugins = { "nvim-lspconfig", "conform.nvim", "nvim-lint", "nvim-treesitter" } })
  local baseline = { 'case "$1" in', "foo)", "echo ok>out", ";;", "esac" }
  local expected = { 'case "$1" in', "\tfoo)", "\t\techo ok > out", "\t\t;;", "esac" }
  for _, test in ipairs({
    { ft = "sh", name = "bashls", cmd = "bash-language-server", suffix = "sh" },
    { ft = "zsh", name = "shuck", cmd = "shuck", suffix = "zsh" },
  }) do
    if vim.fn.executable(test.cmd) ~= 1 then
      emit("SKIP", test.name, test.cmd .. " is missing; attachment was not tested")
    else
      local path = vim.fn.getcwd() .. "/sample." .. test.suffix
      vim.fn.writefile(baseline, path)
      vim.cmd.edit(vim.fn.fnameescape(path))
      local buf = vim.api.nvim_get_current_buf()
      vim.bo[buf].filetype = test.ft
      local attached = vim.wait(3500, function()
        return #vim.lsp.get_clients({ bufnr = buf, name = test.name }) > 0
      end, 50)
      check(attached, test.name, test.name .. " attachment to " .. test.ft .. " (3.5-second deadline)")
      if test.ft == "sh" and vim.fn.executable("shfmt") == 1 then
        local format_error
        require("conform").format(
          { bufnr = buf, async = false, timeout_ms = 3000, formatters = { "shfmt" }, lsp_format = "never" },
          function(err)
            format_error = err
          end
        )
        check(
          not format_error and vim.deep_equal(vim.api.nvim_buf_get_lines(buf, 0, -1, false), expected),
          "shfmt",
          "Conform applies tab/case/redirect formatting using the managed arguments"
        )
      elseif test.ft == "zsh" and attached then
        vim.lsp.buf.format({ bufnr = buf, name = "shuck", async = false, timeout_ms = 3000 })
        check(
          vim.deep_equal(vim.api.nvim_buf_get_lines(buf, 0, -1, false), expected),
          "shuck-format",
          "Shuck LSP applies the copied global formatting settings"
        )
      end
      if test.ft == "zsh" and vim.fn.executable("zsh") == 1 then
        -- 外部 Zsh 检查器读取磁盘文件，故意在磁盘样例中写入错误语法以验证诊断。
        vim.fn.writefile({ "if then" }, path)
        require("lint").try_lint("zsh")
        local namespace = require("lint").get_namespace("zsh")
        check(
          vim.wait(2000, function()
            return #vim.diagnostic.get(buf, { namespace = namespace }) > 0
          end, 50),
          "zsh-lint",
          "nvim-lint detects invalid Zsh saved on disk"
        )
      end
      for _, client in ipairs(vim.lsp.get_clients({ bufnr = buf })) do
        client:stop(true)
      end
      vim.cmd("bwipeout!")
    end
  end
  emit(
    "SKIP",
    "taplo",
    "Taplo attachment requires a schema-catalog fetch in the supported build; offline probe does not start it"
  )
  for _, lang in ipairs({ "bash", "lua", "toml" }) do
    local ok = pcall(vim.treesitter.language.add, lang)
    emit(
      ok and "PASS" or "WARN",
      "parser." .. lang,
      ok and (lang .. " parser loads") or (lang .. " parser is missing or incompatible; no installation attempted")
    )
  end
  local errors = vim.g.dotfiles_doctor_errors or {}
  check(
    #errors == 0,
    "startup",
    #errors == 0 and "selected plugins loaded without error notifications"
      or ("plugin errors: " .. table.concat(errors, "; "))
  )
end

local ok, err = pcall(main)
if not ok then
  emit("FAIL", "probe", tostring(err))
  vim.cmd("cquit 1")
end
vim.fn.writefile({ "nvim" }, vim.env.DOTFILES_DOCTOR_COMPLETE)
vim.cmd("qa!")
