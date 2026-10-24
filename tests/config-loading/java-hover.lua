-- 使用缓存中的 Noice、Nui 与 Markdown parser；不启动 LSP 或下载依赖。
local data = vim.fn.stdpath("data")
vim.opt.rtp:append(data .. "/lazy/noice.nvim")
vim.opt.rtp:append(data .. "/lazy/nui.nvim")
vim.cmd("syntax on")
vim.o.columns, vim.o.lines = 160, 60

local config = require("noice.config")
config.options = config.defaults()
local hover = require("noice.lsp.hover")
local received
hover.on_hover = function(err, result, ctx, extra)
  received = { err = err, result = result, ctx = ctx, extra = extra }
  return "forwarded"
end
vim.lsp.get_client_by_id = function(id)
  return ({ [1] = { name = "jdtls" }, [2] = { name = "lua_ls" } })[id]
end
local spec = dofile(vim.fn.stdpath("config") .. "/lua/plugins/java-hover.lua")
local opts = { presets = { command_palette = true } }
assert(spec[1].opts(nil, opts) == nil and opts.presets.command_palette, "Noice options were replaced")

local function process(contents, client_id)
  local unchanged = vim.deepcopy(contents)
  local result = { contents = contents, range = { start = { line = 0, character = 1 } } }
  local ctx = { client_id = client_id or 1, bufnr = 1 }
  assert(hover.on_hover(nil, result, ctx, "extra") == "forwarded")
  assert(received.ctx == ctx and received.extra == "extra", "callback context was not forwarded")
  assert(vim.deep_equal(received.result.range, result.range), "hover range changed")
  assert(vim.deep_equal(contents, unchanged), "raw LSP contents were mutated")
  return received.result.contents
end

local url = "jdt://contents/java.net.http/HttpClient.java?=" .. string.rep("path%5C", 60) .. "HttpClient.class#1"
local paragraph = (
  "An `HttpClient` can be used to send [requests](%s) and retrieve their [responses](%s). "
  .. "An `HttpClient` is created through a [builder](%s)."
):format(url, url, url)
local plain = "An `HttpClient` can be used to send requests and retrieve their responses. "
  .. "An `HttpClient` is created through a builder."
local original = { { language = "java", value = "java.net.http.HttpClient" }, "An HTTP Client.\n\n" .. paragraph }
local snapshot = vim.deepcopy(original)
local cleaned = process(original)
assert(vim.deep_equal(original, snapshot), "raw LSP response was mutated")
assert(vim.deep_equal(cleaned[1], original[1]), "Java signature changed")
assert(cleaned[2] == "An HTTP Client.\n\n" .. plain, "Javadoc labels or paragraphs changed")

local text = table.concat({
  "中文 [中文 `List<T>`](jdt://foo(a)) and [web](https://example.com/a)",
  "",
  "- Keep list structure",
  "",
  "`[literal](jdt://inline)`",
  "",
  "```java",
  'String s = "[literal](jdt://code)";',
  "```",
  "",
  "[multiple\nlines](jdt://multiline)",
}, "\n")
local expected = table.concat({
  "中文 中文 `List<T>` and [web](https://example.com/a)",
  "",
  "- Keep list structure",
  "",
  "`[literal](jdt://inline)`",
  "",
  "```java",
  'String s = "[literal](jdt://code)";',
  "```",
  "",
  "multiple\nlines",
}, "\n")
assert(process(text) == expected, "code, external links, lists or multiline labels changed")
assert(process({ kind = "markdown", value = text }).value == expected, "MarkupContent was not cleaned")

-- 来自 JDK Thread 文档的公开段落，保留 JDTLS 的包装列表和 Flexmark 表格标题。
local parallelism = "The number of platform threads available for scheduling virtual threads. "
  .. "It defaults to the number of available processors."
local max_pool_size = "The maximum number of platform threads available to the scheduler. It defaults to 256."
local thread = table.concat({
  "A thread of execution.",
  "",
  "* **Since:**",
  "  * 1.0",
  "* **Impl Note:**",
  "  * The virtual thread scheduler may be configured with the following system properties:",
  "",
  "    | System property | Description |",
  "    |" .. string.rep("-", 62) .. "|" .. string.rep("-", 125) .. "|",
  "    | **{@systemProperty jdk.virtualThreadScheduler.parallelism}** | " .. parallelism .. " |",
  "    | **{@systemProperty jdk.virtualThreadScheduler.maxPoolSize}** | " .. max_pool_size .. " |",
  "    [System properties]",
  "",
  "Source: *Java 25 (module: java.base)*",
}, "\n")
local normalized = process(thread)
assert(normalized:find("**Since:** 1.0", 1, true), "Since version was lost or left in a nested list")
assert(
  normalized:find("**Implementation Note:**\n\nThe virtual thread scheduler", 1, true),
  "Impl Note was not unwrapped"
)
assert(not normalized:find("{@systemProperty", 1, true), "systemProperty tags remain in prose")
assert(not normalized:find("|", 1, true), "wide table was not expanded")
assert(normalized:find("**System properties**", 1, true), "table caption was lost")
assert(normalized:find("- System property: **`jdk.virtualThreadScheduler.parallelism`**", 1, true))
assert(normalized:find("- System property: **`jdk.virtualThreadScheduler.maxPoolSize`**", 1, true))
assert(normalized:find("  Description: " .. parallelism, 1, true), "parallelism description changed")
assert(normalized:find("  Description: " .. max_pool_size, 1, true), "maxPoolSize description changed")
assert(normalized:find("Source: *Java 25 (module: java.base)*", 1, true), "source footer was lost")
assert(process(normalized) == normalized, "normalization is not idempotent")
assert(process({ kind = "markdown", value = thread }).value == normalized)

-- 真正的列表（含多项、嵌套项和有序项）及短表格不应被元信息规则吞掉。
local lists = table.concat({
  "* Normal item with **emphasis**",
  "  * Nested item",
  "    * **Since:**",
  "      * 1.0",
  "  * Another nested item",
  "* Next item",
  "",
  "1. First step",
  "2. Second step",
  "",
  "1. **Since:**",
  "   * 1.0",
  "2. **Impl Note:**",
  "   * This is an ordered list.",
  "",
  "* **Since:**",
  "  * this is prose, not a version",
  "* **Impl Note:**",
  "  * first independent item",
  "  * second independent item",
  "",
  "| Name | Value |",
  "| --- | --- |",
  "| small | 1 |",
}, "\n")
assert(process(lists) == lists, "normal lists or a narrow table changed")
local note_with_list = "* **Impl Note:**\n  * Introductory paragraph.\n\n    - first\n      1. nested\n    - second\n"
assert(
  process(note_with_list):find("\n- first\n  1. nested\n- second", 1, true),
  "a list inside Impl Note was flattened"
)

local literal = table.concat({
  "`{@systemProperty literal.inline}`",
  "\\{@systemProperty literal.escaped}",
  "",
  "```markdown",
  "[literal](jdt://markdown-code)",
  "",
  thread,
  "```",
  "",
  "    {@systemProperty literal.indented}",
  "    * **Since:**",
  "      * 1.0",
  "",
  '[web](https://example.com "{@systemProperty literal.title}")',
}, "\n")
assert(process(literal) == literal, "code examples, escaped tags or a link title changed")
assert(process("Use {@systemProperty java.home}.") == "Use `java.home`.", "standalone property tag was not converted")

-- 宽度跟随浮窗上限与终端宽度；多列表格保留列名、转义管道和代码单元格。
local table_text = "| Name | Value | Note |\n| --- | --- | --- |\n| alpha\\|beta | `1` | "
  .. string.rep("details ", 7)
  .. "|"
assert(process(table_text) == table_text, "a fitting table changed")
vim.o.columns = 70
local expanded = process(table_text)
assert(expanded:find("- Name: alpha\\|beta", 1, true), "escaped pipe was split into a cell")
assert(expanded:find("  Value: `1`", 1, true) and expanded:find("  Note: details", 1, true), "table columns were lost")
vim.o.columns = 160
config.options.views.hover = { size = { max_width = 70 } }
assert(process(table_text) == expanded, "configured hover width was ignored")
config.options.views.hover = nil
local malformed = "| A | B |\n| --- | --- |\n| " .. string.rep("long ", 30) .. "|\n"
assert(process(malformed) == malformed, "irregular table cells were guessed")
local nested_table = "- Parent\n\n  " .. table_text:gsub("\n", "\n  ") .. "\n\n- Sibling\n"
config.options.views.hover = { size = { max_width = 70 } }
local nested_result = process(nested_table)
assert(nested_result:find("- Parent\n\n  - Name:", 1, true), "table expansion escaped its list container")
assert(nested_result:find("\n    Note:", 1, true) and nested_result:find("\n- Sibling\n", 1, true))
config.options.views.hover = nil

for _, input in ipairs({
  { kind = "plaintext", value = text },
  { language = "java", value = text },
  { kind = "plaintext", value = thread },
  { language = "java", value = thread },
}) do
  assert(vim.deep_equal(process(input), input), "plaintext or typed code was parsed as Markdown")
end
for _, client_id in ipairs({ 2, 3 }) do
  assert(process(original, client_id) == original, "another or detached client was affected")
  assert(process(thread, client_id) == thread, "another client's metadata or tables changed")
end
hover.on_hover({ message = "request failed" }, original, { client_id = 1 })
assert(received.err.message == "request failed" and received.result == original, "error response changed")
hover.on_hover(nil, nil, { client_id = 1 })
assert(received.result == nil, "empty response changed")
local parser = vim.treesitter.get_string_parser
vim.treesitter.get_string_parser = function()
  error("missing parser")
end
assert(process(original) == original, "missing parser did not fall back to the original response")
assert(process(thread) == thread, "missing parser did not preserve the Thread response")
vim.treesitter.get_string_parser = parser

-- 真正调用 Noice 的 formatter 和 extmark 渲染，再测量 Neovim 窗口中的行高。
local function render(contents, check, width)
  width = width or 120
  local message = require("noice.message")("lsp", "hover")
  require("noice.lsp.format").format(message, contents, { ft = "java" })
  local buf = vim.api.nvim_create_buf(false, true)
  message:render(buf, config.ns)
  local win = vim.api.nvim_open_win(buf, false, { relative = "editor", row = 0, col = 0, width = width, height = 30 })
  vim.wo[win].wrap, vim.wo[win].linebreak = true, true
  vim.wo[win].conceallevel, vim.wo[win].concealcursor = 3, "nvic"
  vim.cmd("redraw!")
  assert(vim.api.nvim_win_get_width(win) == width)
  local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
  local result = check(win, lines)
  vim.api.nvim_win_close(win, true)
  vim.api.nvim_buf_delete(buf, { force = true })
  return result
end
local function paragraph_height(win, lines)
  assert(#lines == 5 and lines[1] == "java.net.http.HttpClient", "Noice document structure changed")
  return vim.api.nvim_win_text_height(win, { start_row = 4, end_row = 4 }).all
end
local before, after = render(original, paragraph_height), render(cleaned, paragraph_height)
assert(after == 1, "cleaned HttpClient paragraph still spans multiple screen rows")
local function check_thread(win, lines)
  local properties = 0
  for i, line in ipairs(lines) do
    if line:find("jdk.virtualThreadScheduler", 1, true) or line:find("**Since:**", 1, true) then
      assert(
        vim.api.nvim_win_text_height(win, { start_row = i - 1, end_row = i - 1 }).all == 1,
        "metadata/property unexpectedly wraps"
      )
      if line:find("jdk.virtualThreadScheduler", 1, true) then
        properties = properties + 1
      end
    end
    assert(not line:match("^%s*|"), "Noice still displays the wide table")
    assert(not line:match("^%s*%* %*%*Since"), "Noice still displays the metadata wrapper")
  end
  assert(properties == 2, "Noice lost a system property")
end
render(normalized, check_thread, 120)
render(normalized, check_thread, 80)
print(
  ("PASS Java hover: HttpClient %d -> %d rows; Thread metadata, tags, tables, lists and failure fallback checked"):format(
    before,
    after
  )
)
