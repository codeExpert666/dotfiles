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

| Profile   | 准备范围                                                       | 适用场景                                     |
| --------- | -------------------------------------------------------------- | -------------------------------------------- |
| `server`  | 命令行环境、terminfo 终端定义及基础配置                        | 服务器或远程环境（字体安装在本地终端客户端） |
| `desktop` | 额外准备 Ghostty、IosevkaTerm Nerd Font 和 Sarasa Term SC 字体 | 本地桌面机器（macOS / Ubuntu Desktop）       |

profile 控制软件和字体的安装范围，配置包链接则按操作系统平台选择；`server` 同样会部署 Ghostty 基础配置，以支持终端定义与 SSH 跨端集成。桌面机器将命令中的 profile 参数改为 `desktop`：

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
- Starship 配置基于[官方 Tokyo Night 预设](https://starship.rs/presets/tokyo-night)，
  保留 ISC 许可；完整声明见[配置文件](starship/.config/starship.toml)。
