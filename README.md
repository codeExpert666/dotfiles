# dotfiles

个人跨平台终端环境配置，覆盖 Zsh、Neovim、Ghostty、Git 及相关命令行工具。
配置以仓库为来源，通过 GNU Stow 部署到 HOME 下的默认路径。

在已有机器上部署，从[快速开始](#快速开始)进入；需要独立 Ubuntu 开发机，使用
[Multipass 可选扩展](multipass/README.md)。安装与部署默认先预览，显式 `--apply` 才写入。

## 受管配置

顶层配置目录通常是 Stow 包，包内路径对应 HOME；`skills` 采用声明式客户端入口链接，`codex` 由部署脚本单独复制。

| 配置模块                       | 部署方式   | 平台支持 | 部署入口（相对 HOME）                | 内容与特性                                                |
| ------------------------------ | ---------- | -------- | ------------------------------------ | --------------------------------------------------------- |
| [zsh](zsh)                     | Stow 软链  | 全平台   | `.zshenv`、`.config/zsh/`            | 补全、语法高亮、历史搜索与目录跳转                        |
| [nvim](nvim)                   | Stow 软链  | 全平台   | `.config/nvim/`                      | LazyVim，Bash、Zsh 与 Markdown 编辑                       |
| [git](git)                     | Stow 软链  | 全平台   | `.config/git/config.shared`          | 共享别名、快进拉取、delta 与 zdiff3                       |
| [ghostty](ghostty)             | Stow 软链  | 全平台   | `.config/ghostty/config.ghostty`     | 字体、主题、透明度、剪贴板与 SSH 集成                     |
| [ghostty-macos](ghostty-macos) | Stow 软链  | 仅 macOS | `.config/ghostty/platform.ghostty`   | macOS 背景模糊、光标着色器、快捷终端与 Option 键          |
| [lazygit](lazygit)             | Stow 软链  | 全平台   | `.config/lazygit/config.yml`         | delta 差异渲染，按 `e` 调用 Neovim                        |
| [starship](starship)           | Stow 软链  | 全平台   | `.config/starship.toml`              | Tokyo Night 提示符，SSH 下显示 user@host                  |
| [atuin](atuin)                 | Stow 软链  | 全平台   | `.config/atuin/config.toml`          | 本地历史搜索与疑似凭据过滤                                |
| [shuck](shuck)                 | Stow 软链  | 全平台   | `.config/shuck/shuck.toml`           | 命令行与语言服务器共用的 Zsh 格式规则                     |
| [vim](vim)                     | Stow 软链  | 全平台   | `.vimrc`                             | 后备编辑器，状态使用 XDG state                            |
| [skills](skills)               | 声明式软链 | 全平台   | `.agents/skills/`、`.claude/skills/` | 按客户端声明入口，共用 `skills/src/` 技能源文件           |
| [codex](codex)                 | 独立副本   | 全平台   | `.codex/agents/`                     | Codex 执行代理 `sol_worker.toml` 的普通副本（不支持软链） |

Zsh 交互会话提供 `lg`（`lazygit`）和 `mp`（系统 `multipass`）别名。`mp` 调用原生命令，
仓库的开发机编排入口仍是 `bash scripts/multipass.sh`。

Ghostty SSH 会话中的 Neovim 使用 OSC52 与本地剪贴板交换文本。按 `"` 打开
寄存器菜单时，`*` / `+` 显示兼容性提示，菜单不会主动读取剪贴板；显式
`"+yy` / `"+p` 仍可复制和粘贴。

Neovim 的 Markdown 诊断与格式化共用[个人规则](nvim/.config/nvim/.markdownlint.json)，
禁用 `MD013/line-length` 行长检查，其余 Markdown 检查保留。

Markdown 图片与 Mermaid 代码块通过[Markdown 配置](nvim/.config/nvim/lua/plugins/markdown.lua)
启用 Snacks Image，在 Ghostty 中直接显示于编辑区；打开包含 `mermaid` 代码块的 `.md` 文件即可预览，
光标离开代码块时用图表替代源码，进入代码块时显示源码以便编辑，修改后图表自动刷新。
配置变更后重启 Neovim 生效；浏览器预览仍可用 `Space c p` 切换。

图表渲染需要 ImageMagick、Mermaid CLI 及其配套的无界面浏览器。这些是可选依赖，
不随核心 bootstrap 安装；macOS 可用以下命令准备（浏览器下载到当前用户的 Puppeteer 缓存）：

```sh
brew install imagemagick mermaid-cli
"$(brew --prefix mermaid-cli)/libexec/lib/node_modules/@mermaid-js/mermaid-cli/node_modules/.bin/puppeteer" \
  browsers install chrome-headless-shell
```

可运行 `:checkhealth snacks` 检查终端图形协议与渲染工具。其他文档功能所提示的 PDF、LaTeX
工具并非 Mermaid 的依赖。

Java DAP 调试通过[窗口跳转配置](nvim/.config/nvim/lua/plugins/java-dap.lua)优先复用已打开的
源码窗口，避免从输入终端命中断点时跳入固定的调试面板。设置在 `nvim-dap` 加载时自动应用。

Java 的 `K` 文档浮窗使用[临时修复](nvim/.config/nvim/lua/plugins/java-hover.lua)，在 Noice 渲染前整理
JDTLS 的 Markdown：移除 `jdt://` 链接目标以避免隐藏长链接造成大片空白；将 `{@systemProperty ...}`
显示为代码文本；将 `Since` 显示为单行、`Impl Note` 显示为标题与正文；过宽表格改成逐项列出列名和内容，
并保留表格标题。宽度参考 Noice hover 的 `max_width`（默认 120）与终端可用列数。

仅处理 Java hover，保留普通列表及其嵌套关系、短表格、代码块（含 Markdown 示例）、行内代码、转义文本、
普通网页链接和文档来源。元信息规则只识别顶层、单个子项的 JDTLS 包装结构，无法可靠解析的表格保留原文。
其他语言及源码中的 `gd` 不受影响，但浮窗内被移除目标的 Java 文档链接不再可跳转。
缺少 parser 或整理失败时回退为原始响应。部署后重启 Neovim 生效；已运行的会话需另行加载配置并重新请求 hover。

上游关联与移除条件（核对日期：2026-10-24）：

| 处理 | 上游关联 | 停用该处理后的验收条件 |
| --- | --- | --- |
| `strip_jdt_links` | [Neovim issue #14409](https://github.com/neovim/neovim/issues/14409)、[PR #42370](https://github.com/neovim/neovim/pull/42370)，PR 尚未合并，接替已关闭的 [#40897](https://github.com/neovim/neovim/pull/40897) | 修复进入本机 Neovim 后，`HttpClient` 在窄浮窗中保留链接也不再出现异常空白 |
| `format_system_properties` | [Javadoc systemProperty 规范](https://docs.oracle.com/en/java/javase/25/docs/specs/javadoc/doc-comment-spec.html#systemproperty)；尚无已确认的直接修复 PR | 本机 JDTLS 返回的 `Thread` 文档已将两项属性转换为代码文本，不再泄露原始标签 |
| `format_wide_tables` | [JDTLS PR #3786](https://github.com/eclipse-jdtls/eclipse.jdt.ls/pull/3786) 已合并，仅优化表格空白，本机版本仍有过宽表格；它不是完整排版修复 | 更新后的 JDTLS / Noice 在 80、120 列浮窗中能完整、清晰地显示 `Thread` 表格，列名、说明和标题均保留 |
| `format_metadata` | JDTLS 生成嵌套列表，当前 Noice 保留列表标记；尚无已确认的直接修复 PR | `Since: 1.0` 与 `Implementation Note` 排版清晰，正常列表和代码示例仍完整 |

各项独立验收后，可移除 `clean_markdown` 中相应调用、专属辅助函数及对应断言。
全部满足后删除 `java-hover.lua` 及其已部署软链，并移除对应的
[缓存集成回归](tests/README.md#已准备插件缓存的集成)和本说明；不要仅凭某个 PR 合并就删除整套处理。

Neovim 文件树使用 Snacks Explorer：`Space e` 打开项目根目录，
`Space E` 打开当前工作目录，`h` / `l` 收起或展开目录。连续单子目录合并为一行
（例如 `java/io/github/voiceinsight`），遇到文件或多个子目录时保留分支；隐藏项与被忽略项仍显示。
侧栏与 `Space Space` 文件搜索、`Space /` 内容搜索共用 Snacks Picker 输入框。
合并行上的新建、重命名、复制和移动操作针对路径末端目录；需要逐层选择中间目录时，
可将[文件树配置](nvim/.config/nvim/lua/plugins/snacks.lua)中的 `compact_folders` 改为 `false` 并重启。

目录合并暂用未合并的 [Snacks PR #2907](https://github.com/folke/snacks.nvim/pull/2907)，
插件来源与提交固定在 `snacks.lua` 和 `lazy-lock.json`；[修复模块](nvim/.config/nvim/lua/config/snacks-compact.lua)
处理目录导航、标签、隐藏样式、符号链接与文件监听问题。升级该版本时应运行[缓存集成回归](tests/README.md#已准备插件缓存的集成)，
并在上游正式支持后复核、移除临时来源和修复。

Neovim 的主要 AI 入口为 [Sidekick + 原生 Codex CLI](docs/sidekick.md)：
`Space a a` 打开/隐藏，`Ctrl-.` 切换焦点，`Space a f` 添加当前文件，选区 `Space a v`
添加位置与文本；补充要求后用 CLI 原生 `Enter` 提交。使用 LazyVim 官方 extra，关闭 NES，
无需 Copilot；额外安装独立 `codex` 与 `tmux`，沿用个人登录与配置。从项目或 worktree
目录启动 Neovim，以 `:pwd` 确定 CLI 目录；隐藏或退出 Neovim 后 tmux 中的任务仍可运行。

[CodeCompanion + codex-acp](docs/codecompanion.md) 保留为备用：`Space a c c` 聊天，
`Space a c i` 局部 Inline，`Space a c r` 审阅，`Space a c a` 添加审阅评论。
插件、Mason 适配器、居中高亮的模型/推理/上下文/权限状态栏及原有功能继续保留。
依赖、完整按键、项目工作流和恢复方式见两份使用文档。

## 快速开始

### 部署前须知与环境要求

完整环境准备原生支持 Ubuntu 24.04/26.04（x86_64、arm64）和 macOS 15 及 26（Apple Silicon），
需要 Bash 3.2+，以普通用户运行并按需提供 sudo 认证。仅部署或检查配置也可用于其他
Linux/macOS 环境，前提见[脚本平台说明](scripts/README.md#平台与运行约定)。
首次使用前请确认以下约束：

- **路径与环境变量**：`$HOME` 必须为已存在的真实绝对路径；四个 XDG 路径使用默认布局
  （未设置、为空或指向对应默认子目录）；**不得导出 `ZDOTDIR`**，统一由受管 `~/.zshenv` 设置。
- **目录与冲突规则**：受管配置的公共父目录（如 `.config/`、`.agents/` 等）必须保持为真实目录。
  旧入口（如 `~/.gitconfig`）、个人文件或 Stow 规则发生冲突时，脚本会安全保留原件并停止；
  请按预览提示迁移后重试，完整条件见 `bash scripts/deploy.sh --help`。
- **文件链接机制**：配置链接依赖本地仓库源文件，请保留仓库目录；切换 Git 分支前确认目标分支包含已部署的包。
  Codex 角色使用普通文件副本，源文件变更后需重新部署。
- **失败与幂等性**：部署无自动全局回滚机制，执行失败会保留已完成修改；排除报错原因后重新预览，再执行 `--apply`。

### 获取仓库

仓库可克隆到任意目录，以下使用 `~/src/dotfiles`；后续命令均在仓库根目录执行。

```sh
git clone https://github.com/codeExpert666/dotfiles.git ~/src/dotfiles
cd ~/src/dotfiles
```

### 部署方案与执行

根据当前机器状态与需求选择适合的部署方案：

#### 方案 A：完整环境准备与部署（推荐全新机器）

bootstrap 准备系统依赖与运行时工具链、部署配置，最后自动运行健康诊断。选择适合的 profile：

| Profile | 准备范围 | 适用场景 |
| --- | --- | --- |
| `server` | 命令行环境、terminfo 工具、`xterm-ghostty` 定义及基础配置 | 服务器或远程环境（字体安装在本地终端客户端） |
| `desktop` | 额外准备 Ghostty、IosevkaTerm Nerd Font 和 Sarasa Term SC 字体 | 本地桌面机器（macOS / Ubuntu Desktop） |

profile 控制软件和字体的安装范围，配置包链接则按操作系统平台选择。
两个 profile 均由核心 bootstrap 准备并验收 `xterm-ghostty` 定义；客户机不需要
Ghostty GUI 或字体。`server` 部署的 Ghostty 配置文件不负责安装定义，SSH 集成提供额外便利。
桌面机器将命令中的 profile 参数改为 `desktop`：

```sh
bash scripts/bootstrap.sh --dry-run --profile server
bash scripts/bootstrap.sh --apply --profile server
```

#### 方案 B：仅部署配置（已有软件与插件时）

软件和插件已具备时，使用 deploy 建立配置符号链接，**不安装任何系统软件与依赖**。它需要预装 GNU Stow（支持 `--no-folding`）和 Git（支持 `--fixed-value`）：

```sh
bash scripts/deploy.sh --dry-run
bash scripts/deploy.sh --apply
```

### 部署后检查

重新打开终端后检查环境。bootstrap 阶段已自动运行适用的检查；仅部署配置后或日常排查时可手动执行：

```sh
bash scripts/doctor.sh
bash scripts/doctor.sh --only zsh --only nvim --runtime
```

`FAIL` 表示失败，`WARN` 需要核实，`SKIP` 表示该项未验证；退出码 0 仍可能包含后两者。
检查范围见[doctor 说明](scripts/README.md#doctor-检查与证据)。

完成健康检查后，进入[个人配置](#个人配置)完成 Git 身份与环境收尾设置。

## 个人配置

身份、凭据、认证状态和机器特定设置留在仓库之外。默认应用配置可能已链接到仓库，
修改前先确认链接目标；应用支持独立本机覆盖文件时优先使用它。

### Git 身份

`~/.config/git/config` 是个人普通文件，包含共享配置的直接 `include.path = config.shared`。
部署只在文件不存在时创建它；已有文件须自行保留该 include。对新建文件可添加身份：

```sh
git config --file ~/.config/git/config user.name "Your Name"
git config --file ~/.config/git/config user.email "you@example.com"
```

个人覆盖应放在共享 include 之后；`git config` 不会自动调整已有配置项的先后顺序。

### Zsh 与历史

bootstrap 不修改登录 Shell。按需手动设置，重新登录后生效：

```sh
chsh -s "$(command -v zsh)"
```

本机交互设置放在 `~/.config/zsh/local.zsh`，登录环境放在 `~/.config/zsh/local.zprofile`；
这两个文件不纳入仓库，doctor 会检查其语法。

Linux 交互式 Zsh 在 `SSH_AUTH_SOCK` 未设置或为空、`XDG_RUNTIME_DIR` 非空且其中的
`openssh_agent` 是 socket 时，自动使用该系统 OpenSSH agent；已有代理地址优先保留。
这只设置连接地址，agent 服务与密钥解锁仍需单独准备。客户机使用自己的 GitHub 密钥，见
[Multipass GitHub 认证](multipass/README.md#客户机使用自己的-github-密钥)。

`Ctrl-R` 打开 Atuin 历史搜索，`Ctrl-T` 打开 fzf 路径查找，`z`/`zi` 通过 zoxide 跳转目录。
原生 Zsh 历史保存在 `~/.local/state/zsh/history`，macOS Terminal 的额外会话保存与恢复已禁用。
Atuin 默认关闭自动同步，bootstrap 不导入历史或登录账户；需要时手动执行：

```sh
atuin import auto
atuin login && atuin sync
```

登录后仍需手动运行 `atuin sync` 同步。

## 可选扩展：Multipass 开发机

[Multipass Ubuntu 开发机](multipass/README.md)在 Apple Silicon Mac 上创建独立虚拟机，
配置 SSH，并从指定远程提交运行客户机内的核心 server bootstrap。创建、日常使用、
状态和恢复均由扩展专文说明；它不参与 Stow 部署。

## 新增与维护配置

### 配置包

编辑现有包时，按 HOME 相对路径维护文件，再预览、部署和检查：

```sh
$EDITOR zsh/.config/zsh/common.zsh
bash scripts/deploy.sh --dry-run
bash scripts/deploy.sh --apply
bash scripts/doctor.sh --only deployment
```

新增包还需更新 [layout.bash](scripts/layout.bash) 的 `layout_packages`；新增公共父目录
写入 `layout_directories`。测试包清单同步更新到 [harness.bash](tests/support/harness.bash)
的 `packages` 和 [doctor/cases.py](tests/doctor/cases.py) 的 `PACKAGES`。
需要排除部署的包内文件可参考 [skills/.stow-local-ignore](skills/.stow-local-ignore)。
Ghostty 共用设置放在 `ghostty`，macOS 专有设置放在 `ghostty-macos`。

### Skills 与 Codex 角色

技能源文件位于 `skills/src/<名称>/SKILL.md`，须包含 `name` 和 `description`。
在 `layout_skill_clients` 声明客户端后，只建立所需入口。例如共享技能使用以下两个链接：

```sh
ln -s '../../src/<名称>' 'skills/.agents/skills/<名称>'
ln -s '../../src/<名称>' 'skills/.claude/skills/<名称>'
```

专用技能只声明对应客户端；`astra-sol` 仅声明 `.agents`，不建立 `.claude` 入口。

[post-dev-review](skills/src/post-dev-review/SKILL.md) 是共享技能，通过 `.agents` 和 `.claude`
发现同一份源码；用于开发后的代码冗余／兼容性审视、使用文档重编排与验证，也可只执行其中一阶段。
与其他共享技能一样，现有目录链接沿用同一份源码；新增入口通过 deploy 部署并由 doctor 核验。

Codex 角色源文件位于 [codex/agents/](codex/agents/)，部署为 `~/.codex/agents/` 下的普通文件。
首次接管或更新不同内容的角色时，先核对差异、备份并移走旧文件，再预览和部署。
新开 Codex 会话验证角色加载与一次小任务委派；doctor 只验证部署文件。
具体冲突规则见 [deploy 说明](scripts/README.md#deploy-部署配置)。

## 更多文档

- [脚本使用与维护](scripts/README.md)：四个入口的行为、软件来源、日志和诊断范围。
- [Multipass 开发机](multipass/README.md)：创建、SSH、重配、退役与恢复。
- [测试与验证](tests/README.md)：五套离线回归、缓存集成和真实 VM 验收。
- [仓库约定](AGENTS.md)：目录组织、代码风格、提交信息与验证要求。

## 许可证

除另有声明外，本仓库采用 [MIT License](LICENSE)。以下内容保留各自的许可与来源：

- LazyVim starter 衍生的 Neovim 配置：[Apache License 2.0](nvim/.config/nvim/LICENSE)。
- Ghostty 光标着色器：[MIT License](ghostty-macos/.config/ghostty/shaders/LICENSE)，
  来源和本地调整见[着色器说明](ghostty-macos/.config/ghostty/shaders/README.md)。
- Ghostty 终端定义：[MIT License](scripts/bootstrap/terminfo/LICENSE)，
  固定版本和生成方式见[来源说明](scripts/bootstrap/terminfo/README.md)。
- Starship 配置基于[官方 Tokyo Night 预设](https://starship.rs/presets/tokyo-night)，
  保留 ISC 许可；完整声明见[配置文件](starship/.config/starship.toml)。
