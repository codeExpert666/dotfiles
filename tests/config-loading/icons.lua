-- 完整 LazyVim 的离线图标探针；由 icons.py 提供隔离 HOME、插件缓存和 tmux TUI。
vim.defer_fn(function()
	local results = {}
	local ok, err = xpcall(function()
		local function check(name, fn)
			fn()
			results[#results + 1] = name
		end
		local function equal(actual, expected, label)
			assert(
				vim.deep_equal(actual, expected),
				label .. ": " .. vim.inspect(actual) .. " ~= " .. vim.inspect(expected)
			)
		end
		local cfg = require("config.icons")
		local g = cfg.glyphs
		-- 首次通过兼容接口加载 mini.icons，覆盖最容易遗漏的懒加载顺序。
		local dev = require("nvim-web-devicons")
		local mini = require("mini.icons")
		local files = {
			{ "compose.yml", g.docker, "Blue" },
			{ "compose.yaml", g.docker, "Blue" },
			{ "compose.prod.yml", g.docker, "Blue" },
			{ "compose.db.prod.yaml", g.docker, "Blue" },
			{ "docker-compose.yaml", g.docker, "Blue" },
			{ "docker-compose.override.yml", g.docker, "Blue" },
			{ "Dockerfile", g.docker, "Blue" },
			{ "Dockerfile.dev", g.docker, "Blue" },
			{ "Containerfile", g.docker, "Blue" },
			{ "Containerfile.dev", g.docker, "Blue" },
			{ ".dockerignore", g.docker, "Blue" },
			{ "devcontainer.json", g.docker, "Blue" },
			{ ".devcontainer.json", g.docker, "Blue" },
			{ "pom.xml", g.maven, "Red" },
			{ "mvnw", g.console, "Red" },
			{ "mvnw.cmd", g.console, "Red" },
			{ "maven-wrapper.properties", g.maven, "Red" },
			{ "application.yml", g.spring, "Green" },
			{ "application-prod.yaml", g.spring, "Green" },
			{ "application-local.properties", g.spring, "Green" },
			{ "bootstrap.yml", g.spring, "Green" },
			{ "bootstrap-test.properties", g.spring, "Green" },
			{ "AGENTS.md", g.robot, "Purple" },
			{ "AGENTS.override.md", g.robot, "Purple" },
			{ "CLAUDE.md", g.robot, "Purple" },
			{ ".env", g.config, "Yellow" },
			{ ".env.local", g.config, "Yellow" },
			{ "README", g.book, "Yellow" },
			{ "README.md", g.book, "Yellow" },
			{ "README.txt", g.book, "Yellow" },
		}
		check("30 file rules / basename, path, compatibility shim", function()
			for _, row in ipairs(files) do
				local expected = { row[2], "MiniIcons" .. row[3], false }
				for _, name in ipairs({ row[1], "/synthetic project/sub/" .. row[1] }) do
					equal({ mini.get("file", name) }, expected, name)
					equal(
						{ dev.get_icon(name, nil, { default = true }) },
						{ expected[1], expected[2] },
						"devicons: " .. name
					)
					equal({ Snacks.util.icon(name, "file") }, { expected[1], expected[2] }, "Snacks: " .. name)
				end
			end
		end)
		check("negative rules, unknown fallback and filetypes", function()
			for _, name in ipairs({ "ordinary.yml", "my-application.yml", "compose-prod.yml", "Compose.yml" }) do
				equal({ mini.get("file", name) }, { mini.get("file", "normal.yml") }, name)
			end
			equal({ mini.get("file", "normal.xml") }, { mini.get("filetype", "xml") }, "ordinary XML")
			equal({ mini.get("file", "normal.md") }, { mini.get("filetype", "markdown") }, "ordinary Markdown")
			for _, name in ipairs({ "compose.yaml.bak", "application-prod.yml.bak", "application-prod.txt" }) do
				local icon = mini.get("file", name)
				assert(icon ~= g.docker and icon ~= g.spring, "suffix false positive: " .. name)
			end
			local fallback_icon, fallback_hl = mini.get("default", "file")
			equal({ mini.get("file", "not-supported-icon") }, { fallback_icon, fallback_hl, true }, "unknown fallback")
			for name, ft in pairs({
				["compose.yaml"] = "yaml",
				["application-local.yml"] = "yaml",
				["pom.xml"] = "xml",
				["AGENTS.md"] = "markdown",
			}) do
				equal(vim.filetype.match({ filename = name }), ft, "filetype: " .. name)
			end
		end)
		check("repeat setup and explicit filename precedence", function()
			local get = mini.get
			local resolver = require("config.icon-files")
			resolver.setup(mini)
			equal(mini.get, get, "wrapper stacked")
			local opts = vim.deepcopy(mini.config)
			opts.file["compose.custom.yaml"] = { glyph = g.book, hl = "MiniIconsYellow" }
			mini.setup(opts)
			resolver.setup(mini)
			equal(mini.get, get, "mini.setup replaced resolver")
			equal(mini.get("file", "compose.custom.yaml"), g.book, "exact override")
			equal(mini.get("file", "compose.other.yaml"), g.docker, "pattern after cache reset")
			opts.file["compose.custom.yaml"] = nil
			mini.setup(opts)
		end)
		check("bufferline, lualine, Trouble and command-line paths share icons", function()
			require("lazy").load({ plugins = { "bufferline.nvim", "lualine.nvim", "trouble.nvim", "blink.cmp" } })
			local buffer_icons = require("bufferline.utils").get_icon
			local completion
			for _, spec in ipairs(dofile(vim.fn.stdpath("config") .. "/lua/plugins/completion.lua")) do
				if spec[1] == "saghen/blink.cmp" then
					completion = spec.opts.sources.providers.cmdline.override.get_completions
				end
			end
			local old_type = vim.fn.getcmdcompltype
			vim.fn.getcmdcompltype = function()
				return "file_in_path"
			end
			local status =
				require("lualine.components.filetype")({ icon_only = true, colored = false, icons_enabled = true })
			for _, row in ipairs(files) do
				local name, glyph, hl = row[1], row[2], "MiniIcons" .. row[3]
				equal(
					{ buffer_icons({ path = name, filetype = vim.filetype.match({ filename = name }) or "" }) },
					{ glyph, hl },
					"bufferline: " .. name
				)
				equal({ require("trouble.format").get_icon(name, "") }, { glyph, hl }, "Trouble: " .. name)
				local b = vim.api.nvim_create_buf(true, false)
				vim.api.nvim_buf_set_name(b, "/synthetic project/" .. name)
				vim.api.nvim_set_current_buf(b)
				status.status = status:update_status()
				status:apply_icon()
				equal(vim.trim(status.status), glyph, "lualine: " .. name)
				completion({
					get_completions = function(_, _, cb)
						cb({ items = { { label = name, textEdit = { newText = name } } } })
					end,
				}, { mode = "cmdline" }, function(value)
					equal(value.items[1].kind_icon, glyph, "Blink: " .. name)
					equal(value.items[1].kind_hl, hl, "Blink highlight: " .. name)
				end)
			end
			vim.fn.getcmdcompltype = old_type
		end)
		check("all configured UI families and shared semantics", function()
			for name, glyph in pairs(g) do
				equal(vim.trim(glyph), glyph, "raw glyph spacing: " .. name)
				assert(vim.fn.strchars(glyph) == 1, "not one glyph: " .. name)
				local cp = vim.fn.char2nr(glyph)
				assert(
					(cp >= 0xf0001 and cp <= 0xf1af0) or name == "maven" or name == "spring",
					"unexpected icon family: " .. name
				)
			end
			local function options(name)
				return LazyVim.opts(name)
			end
			equal(options("trouble.nvim").icons.kinds, LazyVim.config.icons.kinds, "Trouble kinds")
			equal(options("noice.nvim").popupmenu.kind_icons, cfg.kinds, "Noice kinds")
			equal(options("neotest").icons.passed, g.success, "test success")
			equal(options("nvim-dap-ui").controls.icons.play, g.play, "DAP play")
			equal(options("todo-comments.nvim").keywords.WARN.icon, g.warn .. " ", "TODO warning")
			equal(options("sidekick.nvim").ui.icons.attached, g.enabled .. " ", "Sidekick attached")
			equal(options("mason.nvim").ui.icons.package_installed, g.success, "Mason installed")
			local md = options("render-markdown.nvim")
			equal(md.heading.icons, {}, "Markdown headings re-enabled")
			equal(md.checkbox.enabled, false, "Markdown checkbox re-enabled")
			assert(md.callout.warning.rendered:find(g.warn, 1, true) == 1, "Markdown warning mismatch")
			if require("lazy.core.config").plugins["codecompanion.nvim"] then
				local cc = options("codecompanion.nvim")
				equal(cc.display.chat.icons.tool_failure, g.error .. " ", "CodeCompanion failure")
				equal(cc.interactions.code_review.display.comments.icon, g.comment .. " ", "CodeCompanion comments")
			end
			local wk = options("which-key.nvim")
			equal(wk.icons.keys.Right, g.arrow_right .. " ", "which-key direction")
			equal(require("lazy.core.config").options.ui.icons.plugin, g.package .. " ", "Lazy package")
			equal(vim.opt.fillchars:get().foldclose, g.chevron_right, "Neovim fold")
			local line = options("lualine.nvim")
			assert(line.sections.lualine_b[1].icon == g.branch)
			local clock = line.sections.lualine_z[1]
			equal(clock.fmt(" 12:00"), g.clock .. " 12:00", "clock adapter")
			equal(clock.fmt("/file/.txt"), "/file/.txt", "formatter changed body")
			local callback = require("bufferline.config").options.left_mouse_command
			equal(callback, require("config.bufferline-click").open, "tab click callback lost")
		end)
		check("dark/light palette and display widths", function()
			for _, background in ipairs({ "dark", "light" }) do
				vim.o.background = background
				vim.cmd.colorscheme("catppuccin-nvim")
				for _, row in ipairs(files) do
					local glyph, hl = mini.get("file", row[1])
					assert(vim.fn.strdisplaywidth(glyph) == 1, "unexpected cell width: " .. row[1])
					assert(vim.api.nvim_get_hl(0, { name = hl, link = false }).fg, "missing color: " .. hl)
				end
			end
		end)
		if vim.env.ICON_PREVIEW == "1" then
			-- 真正的文件树、标签栏与状态栏，所有内容均为临时合成夹具。
			local directory = vim.env.HOME .. "/icon-preview"
			vim.fn.mkdir(directory, "p")
			vim.cmd.enew()
			local preview = vim.api.nvim_get_current_buf()
			for _, buffer in ipairs(vim.api.nvim_list_bufs()) do
				if buffer ~= preview then
					pcall(vim.api.nvim_buf_delete, buffer, { force = true })
				end
			end
			for _, name in ipairs({
				"compose.yaml",
				"pom.xml",
				"application-local.yml",
				"AGENTS.md",
				"README.md",
				".env.local",
				"normal.yaml",
				"Dockerfile",
			}) do
				vim.fn.writefile({ "# Synthetic icon preview", "# " .. name }, directory .. "/" .. name)
				vim.cmd.badd(directory .. "/" .. name)
			end
			vim.cmd.cd(directory)
			vim.cmd.edit(directory .. "/application-local.yml")
			vim.cmd.vsplit()
			vim.cmd.enew()
			local gallery = vim.api.nvim_get_current_buf()
			vim.bo[gallery].buftype = "nofile"
			vim.bo[gallery].bufhidden = "wipe"
			vim.api.nvim_buf_set_name(gallery, "Icon Gallery")
			local lines = { "LazyVim icon preview", "", "d: dark / l: light", "", "FILE TYPES", "" }
			local highlights = {}
			for _, row in ipairs(files) do
				if #lines < 27 then
					highlights[#highlights + 1] = { #lines, row[2], "MiniIcons" .. row[3] }
					lines[#lines + 1] = row[2] .. "  " .. row[1]
				end
			end
			lines[#lines + 1] = ""
			lines[#lines + 1] = "SHARED UI STATES"
			for _, name in ipairs({ "error", "warn", "info", "hint", "success", "search", "debug", "play", "robot" }) do
				lines[#lines + 1] = g[name] .. "  " .. name
			end
			vim.api.nvim_buf_set_lines(gallery, 0, -1, false, lines)
			for _, item in ipairs(highlights) do
				vim.api.nvim_buf_set_extmark(
					gallery,
					vim.api.nvim_create_namespace("icon-preview"),
					item[1],
					0,
					{ end_col = #item[2], hl_group = item[3] }
				)
			end
			for key, background in pairs({ d = "dark", l = "light" }) do
				vim.keymap.set("n", key, function()
					vim.o.background = background
					vim.cmd.colorscheme("catppuccin-nvim")
				end, { buffer = gallery })
			end
			vim.o.background = "dark"
			vim.cmd.colorscheme("catppuccin-nvim")
			Snacks.explorer({ cwd = directory, focus = "list" })
		end
	end, debug.traceback)
	vim.fn.writefile(
		{ vim.json.encode({ result = ok and "PASS" or "FAIL", checks = results, error = err }) },
		vim.env.ICON_REPORT
	)
end, 700)
