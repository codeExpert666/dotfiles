# 在 LazyVim 中使用原生 Codex CLI

日常 AI 编程使用 Sidekick 内嵌的独立 Codex CLI：输入、审批、模型、推理等级、
上下文统计和历史都沿用 CLI。需要 Neovim 聊天缓冲区、Inline diff 或 ACP 审阅时，
使用[备用 CodeCompanion](codecompanion.md)。两套入口共享本机 Codex 认证和个人配置，
各自持有会话；不用同一会话同时运行两个客户端。

配置启用 [LazyVim 官方 Sidekick extra](https://www.lazyvim.org/extras/ai/sidekick)，
并在 [sidekick.lua](../nvim/.config/nvim/lua/plugins/sidekick.lua) 中覆盖 CLI 和按键。
NES、Copilot 状态和普通模式 Tab 接管已关闭，不需要 Copilot 或额外 HTTP 模型。
插件提交记录在 [lazy-lock.json](../nvim/.config/nvim/lazy-lock.json)，现有插件未整体升级。

## 安装与检查

Sidekick 上游要求 Neovim 0.11.2+；同时保留 CodeCompanion 的本仓库环境使用
Neovim 0.12+、Node.js 24+。CLI 持久终端使用 tmux；无需先进入 tmux，也无需配置
个人 tmux 文件。macOS 与 Ubuntu 分别安装：

```sh
brew install tmux
# Ubuntu：sudo apt install tmux
```

核心 bootstrap 会准备 Neovim、Node/npm、插件和 codex-acp，独立 Codex CLI 与
tmux 是额外依赖。已有可用 CLI 时直接复用；新机器按
[OpenAI 安装说明](https://developers.openai.com/codex/cli)安装并登录。
不修改个人模型、权限、MCP 或认证配置，不在 dotfiles 中保存凭据与历史。

```sh
command -v codex
codex --version
codex login status
tmux -V
```

本机现有入口是 `~/.local/bin/codex`，转调 Homebrew CLI；Sidekick 执行 PATH 中的
`codex`，会继续使用这个包装器及现有 `~/.codex`。codex-acp 在 Mason 的独立目录
使用自带 Codex，版本可以不同；不要把 `CODEX_PATH` 改到 ACP 包内来配置 Sidekick。

Sidekick 显式使用 `codex --no-daemon`，由当前 CLI 承载 embedded app-server，
运行中的任务由 tmux 保活。这里的模式选择只作用于 Sidekick，不修改全局 CLI 包装器、
共享后台服务或个人配置。`--no-daemon` 不可与 `--remote` 或 `codex agents` 组合；
需要这些功能时在独立终端按其原生命令使用，见
[OpenAI CLI 参数说明](https://learn.chatgpt.com/docs/cli/reference)。

部署与启动：

```sh
bash scripts/deploy.sh --dry-run
bash scripts/deploy.sh --apply
cd /path/to/project
nvim .
```

重启 Neovim，lazy.nvim 按锁文件准备 Sidekick；本轮本机已完成依赖安装与部署。
在编辑器内运行 `:echo exepath('codex')`、`:echo exepath('tmux')`，确认 GUI 启动的
Neovim 也继承了正确 PATH。运行 `:checkhealth sidekick` 时，CLI/tmux 项应通过；
当前上游仍会报告未启用 Copilot LSP 的 ERROR，以及其他 CLI 未安装的 WARN，
这些不属于本配置启用的能力。

## 项目与 worktree

每个项目或 worktree 推荐一个 Neovim 实例，从对应目录执行 `nvim .`。
**Codex 工作目录以 `:pwd` 为准**，与 LazyVim 的 LSP root、文件树当前目录可能不同。
只从其他目录打开一个文件不会自动切换 Codex 的工作目录。开始前确认：

```vim
:pwd
:tcd /path/to/project-or-worktree
```

Sidekick 按工具名和工作目录标识 tmux 会话；不同 Git worktree 使用不同目录，
不会合并到 Git common directory。主快捷键只复用当前目录会话，切换 `:tcd` 后会为
新目录创建或重连会话，旧目录会话继续存在。改变编辑器目录不会迁移运行中的 Codex。
同一目录的多个 Neovim 可能连接同一个 tmux 会话，避免同时输入。

保存相关文件后，再把文件路径或选区发给 CLI。`Space a f` 添加 `@路径`，由 Codex
读取磁盘；`Space a v` 添加选区位置和当时的文本快照。文件引用不代表发送整个未保存
缓冲区。对话不会自动注入所有打开的文件、诊断或规则；项目 `AGENTS.md`、skills
和 MCP 继续由 Codex 自己加载。

## 快捷键

`Space` 为 LazyVim 的 leader；除特别注明外，以下按键在普通模式使用。

| 按键 | 作用 |
| --- | --- |
| `Space a a` | 打开或隐藏当前目录的 Codex CLI |
| `Ctrl-.` | 编辑器与 Codex 终端间切换焦点；支持普通、插入、可视及终端模式 |
| `Space a h` | 隐藏终端，保留进程 |
| `Space a d` | 断开当前 Neovim 的终端连接；tmux 中的 Codex 保持运行 |
| `Space a s` | 选择 Codex 运行会话，列表显示目录；可显式连接其他目录 |
| `Space a f` | 把当前文件引用添加到 CLI 输入框 |
| `Space a t` | 添加光标位置；可视模式添加选区位置和文本 |
| 可视模式 `Space a v` | 添加选区位置和文本 |
| `Space a p` | 选择提示或上下文模板，普通/可视模式可用 |
| `Space a c c` | 显示或隐藏备用 CodeCompanion 聊天 |
| `Space a c i` | 备用 Inline，支持可视选区 |
| `Space a c r` | 备用 CodeCompanion 审阅 |
| `Space g g` | 用现有 Lazygit 检查与提交工作区改动 |

添加文件、选区和模板**只填入 CLI 输入框，不自动提交**；补充要求后用 Codex 原生
`Enter` 提交。`Ctrl-.` 保持终端可见；从终端隐藏窗口可先按 `Ctrl-q` 进入 Neovim
终端普通模式，再按 `q`。也可以 `Ctrl-.` 回到代码后按 `Space a a`。
`Space a …` 没有映射到终端输入模式，避免吞掉聊天正文。

`Ctrl-q` 是 Sidekick 的终端模式出口；标准 `Ctrl-\ Ctrl-n` 也可退出终端输入模式。
历史、光标移动和撤销等 Codex 原生控制键继续交给 CLI：本配置移除了 Sidekick 对
`Ctrl-p/b/f/h/j/k/l/z` 的接管。`Ctrl-.` 若被外层终端拦截，使用上述终端普通模式出口，
再以 `Ctrl-w p` 切窗、`Space a a` 重开；不要求修改 Ghostty 的全局快捷键。
Sidekick 自建 tmux 会话的前缀键也被关闭，避免默认 `Ctrl-b` 截走原生输入；
不修改用户的 tmux 配置或其他 tmux 会话。

## 日常任务

1. 从项目目录打开 Neovim，保存相关文件，确认 `:pwd`。
2. `Space a a` 打开 CLI，沿用现有登录；首次访问新目录按 Codex 提示确认信任。
3. 直接输入项目任务；需要定位时回到代码，用 `Space a f` 或选区 `Space a v` 添加上下文。
4. 补充约束和验收要求，`Enter` 提交；审批和工具执行在 CLI 内处理。
5. `Ctrl-.` 回到代码，检查更改与测试结果；Sidekick 的文件监听与 Neovim autoread
   会检测磁盘变更。缓冲区有未保存修改时先处理冲突，不强制覆盖。
6. 用 Gitsigns、`Space g g` 或 CLI 的 `/review` 检查 diff，再按需提交。

例如选中函数并添加后输入：“处理空输入，保持公开接口不变，完成后运行相关测试”。
Sidekick 的 `changes`、`review`、`diagnostics` 等模板同样可以从 `Space a p` 选择，
填入后可以编辑。CodeCompanion 的审阅基线属于它自己的任务；CLI 完成的改动优先
使用 Git diff 或原生 `/review`，不用备用聊天的旧审阅快照作为唯一依据。

模型与推理用 `/model`；权限用 `/permissions`；会话信息和用量用 `/status`；
CLI 底部字段用 `/statusline`。具体选项以当前 CLI 为准。本配置不另造 CLI 状态栏，
备用聊天原有的居中、高亮状态栏继续保留。
同一 CLI 中改做新任务时用 `/new` 新建对话；回到旧任务用 `/resume`。

## 隐藏、断开、退出与恢复

| 操作 | 进程与任务 | 再次打开 |
| --- | --- | --- |
| 隐藏终端 | 当前 Codex 和 Neovim 终端都保留，任务继续 | `Space a a`，保留当前输入与屏幕 |
| `Space a d` 或退出 Neovim | Neovim 终端连接断开；tmux 中的 Codex 可继续运行 | 同目录重启 Neovim，`Space a a` 重连 |
| CLI 内 `/quit` 或 `/exit` | 退出 Codex TUI，不再以该客户端继续任务 | 重新打开 CLI，再 `/resume` 选择历史 |
| 系统重启、tmux 服务退出 | 活跃进程不能继续 | 用持久历史恢复，而非重连旧进程 |

需要真正停止本轮客户端时，在 CLI 内执行 `/quit`，不要仅隐藏窗口或退出 Neovim。
退出后 `Space a a` 启动新的 CLI，可在其中使用 `/resume`。独立终端也可运行：

```sh
cd /path/to/project-or-worktree
codex resume                  # 选择历史
codex resume --last           # 继续当前目录最近一条，先确认没有选错任务
codex resume <session-id>     # 恢复指定会话
```

`codex resume --all` 可以扩大历史查找范围，但要确认会话目录；恢复历史不会自动保证
文件已切回原来的 worktree。CLI 与 ACP 的历史互通范围以实际列举结果为准。
恢复前关闭仍持有同一会话的旧客户端；隐藏或 tmux 断开不会释放 Codex 会话写入权。
这些原生命令见 [OpenAI 官方说明](https://developers.openai.com/codex/cli/slash-commands)。

## 排查与验证

- 出现 `Running without the shared background server ... requires embedded mode`：
  检查 CLI 包装器或启动参数是否追加了 `-c`、`--enable`、`--disable` 或 `--search`。
  本机 `~/.local/bin/codex` 会在没有桌面桥接身份时追加
  `-c mcp_servers.node_repl.enabled=false`，避免启动依赖桌面身份的浏览器 MCP。
  Codex 0.164.0 因这项按次覆盖选择 embedded 模式；警告不表示共享后台服务停止。
  Sidekick 已显式传入 `--no-daemon`，沿用同一模式并消除自动回退提示；旧终端需先
  `/quit`，再打开 CLI。只隐藏或断开重连会复用原进程，不会更新启动参数。
  这项调整不把任务迁移到共享后台服务，保活仍来自 tmux；直接绕过包装器可能重新
  触发桌面 MCP 缺少身份的问题。
- 无法启动：先在项目终端直接运行 `codex`，再检查编辑器内的 `exepath`、tmux 版本
  和 `:messages`。Sidekick 用独立 CLI，`codex-acp --version` 不能替代这个检查。
- 目录或上下文不对：检查 `:pwd`；文件树/LSP root 不代表 CLI cwd。选区只发送一次快照，
  修改后重新添加；文件引用前保存文件。
- 重开后仍是旧任务：tmux 会话仍在运行，这是重连；结束它后才使用历史恢复。
  `tmux list-sessions` 可以查看遗留会话，优先在对应 Codex 中 `/quit` 退出。
- 退出 Neovim 后占用资源：使用同目录 `Space a a` 重连并 `/quit`。不要用全局
  `tmux kill-server` 清理，以免影响其他终端任务。
- 历史恢复被拒绝：退出占用同一会话的 CLI/ACP 客户端，再恢复。
- 按键不生效：`:verbose nmap <leader>aa`、`:verbose tmap <C-.>` 查看最终映射；
  重启 Neovim，避免旧插件映射留在当前进程。

离线配置与缓存集成：

```sh
bash tests/config-loading.sh
DOTFILES_TEST_PREPARED_HOME="$HOME" TMPDIR=/tmp bash tests/config-loading.sh
```

缓存检查使用真实 Sidekick 终端和 `cat`，验证目录隔离、文件/选区输入、焦点和隐藏复用，
不读取凭据、不启动模型或连接宿主 tmux；CodeCompanion 状态栏另有缓存回归。
这些检查与真实 Codex PTY、Ghostty GUI 验收的边界见[测试文档](../tests/README.md)。
升级 Sidekick 时同时复核目录选择逻辑使用的 `sidekick.cli.state` API、窗口控制键和
LazyVim extra 默认映射，再定向更新锁文件。

上游实现：[Sidekick](https://github.com/folke/sidekick.nvim)、
[Codex 工具配置](https://github.com/folke/sidekick.nvim/blob/main/sk/cli/codex.lua)、
[tmux 后端](https://github.com/folke/sidekick.nvim/blob/main/lua/sidekick/cli/session/tmux.lua)。

## 本轮验收（2026-10-25）

本机 macOS、Neovim 0.12.6、Sidekick `3d80a47`、独立 Codex CLI 0.164.0、
tmux 3.8；备用 CodeCompanion v20.0.1、codex-acp 2.2.2（自带 Codex 0.160.1）。
已通过部署预览并应用新插件链接，保留现有私有配置与认证，安装了缺失的 tmux。

- **原生 PTY**：在临时 Git 项目及其真实 worktree 中启动独立 CLI，确认两个目录
  分别持有会话；文件引用和行选区实际进入 CLI 输入框，补充说明前不自动提交。
  一轮真实请求正确返回文件首行、选区两个标识与工作目录；首次直接从选区启动 CLI
  也能保留选区位置与文本。首次访问信任目录后再添加上下文。
- **焦点与生命周期**：实际 Neovim 映射验证焦点切换、隐藏重开时终端 job 不变，
  断开与 Neovim 重启后仍连接原 Codex PID；原生 `/quit` 退出后重开产生新 PID，
  `/resume` 恢复历史并通过无需重新读文件的续问。自建 tmux 会话两个前缀均为 `None`。
- **备用入口**：用 `Space a c c` 实际连接 ACP，状态栏显示模型、推理、权限及
  未上报上下文，居中与高亮仍存在；其余入口最终映射已检查，完整状态栏缓存回归通过。
  本轮未重做模型驱动的 Inline 和审阅操作，原功能及适配器配置未修改。
- **回归**：五套 `bash tests/all.sh` 全部通过，包含 deployment 246、doctor 70、
  bootstrap 149、config-loading 17 PASS/4 SKIP、multipass 140 个检查；doctor 和
  bootstrap 各有 2 项可选缓存 SKIP。另跑最终配置的缓存 config-loading 为
  **21 PASS、0 SKIP**，覆盖冷启动选区、目录隔离和缺少依赖时的安全失败。
- **静态与部署诊断**：Lua StyLua、Bash 语法/ShellCheck/shfmt、JSON 解析与
  `git diff --check` 通过；根 README、两份使用文档、tests README 的 Markdownlint
  为 0 问题。scripts README 全文有 8 项既有 MD033/MD060 问题，与修改前一致。
  deployment/nvim doctor 为 131 PASS、0 WARN、0 FAIL、1 SKIP（未请求 runtime）；
  单独 runtime doctor 因现有私有 `java-practice.lua` 跳过，本轮未修改它。

Ghostty 图形界面访问被工具安全策略拒绝，未做截图、物理快捷键或 GUI 输入验收；
Ubuntu 原生运行未验证。模拟 Linux 分支、原生 PTY 和 GUI 验收按上述边界分别报告。
含消息的临时验收会话已归档，测试客户端与本轮创建的 tmux 会话均已关闭。

共享后台提示专项检查：本机 CLI、受管 daemon 与运行中 app-server 均为 0.164.0，
daemon 状态为 running。核对该版本启动判断，并在真实 CLI PTY 中打开 F2 警告面板：
原包装器复现该提示，显式 `--no-daemon` 显示 `No warnings`；对照前后个人
`config.toml` 内容未变。该参数用于明确 Sidekick 的运行模式，不用于启用共享后台托管。
