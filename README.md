# dotfiles

个人跨平台终端环境配置：Zsh、Neovim、Ghostty、Git 以及相关命令行工具。
配置通过 GNU Stow 链接到 HOME 下的应用默认路径，主要采用 XDG 布局，
同一套仓库同时服务 Ubuntu 服务器与 macOS 桌面。

- **单一来源**：受管配置在仓库维护，多数入口通过符号链接即时生效；Codex 角色使用普通副本，更新后需重新部署。
- **先预览后写入**：环境准备脚本 `bootstrap.sh` 和配置部署脚本 `deploy.sh` 默认只做预览；
  显式传入 `--apply` 后，才会安装软件或部署配置。
- **可诊断**：诊断脚本 `doctor.sh` 分模块检查依赖、部署与配置，可选受控运行会话。
- **可回归**：四套独立测试覆盖脚本行为与应用实际加载结果。

## 受管配置

下列顶层配置目录通常是 Stow 包，包内入口对应 HOME 下的目标路径；`codex` 由部署脚本单独复制。

| 包                             | 部署入口（相对 HOME）                | 主要内容                                                  |
| ------------------------------ | ------------------------------------ | --------------------------------------------------------- |
| [zsh](zsh)                     | `.zshenv`、`.config/zsh/`            | 补全、语法高亮、历史搜索与目录跳转                        |
| [nvim](nvim)                   | `.config/nvim/`                      | 基于 LazyVim，支持 Bash、Zsh 与 Markdown 编辑             |
| [git](git)                     | `.config/git/config.shared`          | 共享别名、快进拉取、delta 分页与 zdiff3 冲突样式          |
| [ghostty](ghostty)             | `.config/ghostty/config.ghostty`     | 字体、主题、透明度、剪贴板与 SSH 集成                     |
| [ghostty-macos](ghostty-macos) | `.config/ghostty/platform.ghostty`   | 仅 macOS：背景模糊、光标着色器、快捷终端、Option 作为 Alt |
| [lazygit](lazygit)             | `.config/lazygit/config.yml`         | 用 delta 渲染差异，按 `e` 调用 Neovim                     |
| [starship](starship)           | `.config/starship.toml`              | Tokyo Night 分段提示符，SSH 下显示 user@host              |
| [atuin](atuin)                 | `.config/atuin/config.toml`          | 本地历史搜索，不自动同步，过滤疑似凭据                    |
| [shuck](shuck)                 | `.config/shuck/shuck.toml`           | Zsh 格式化规则，命令行与语言服务器共用                    |
| [vim](vim)                     | `.vimrc`                             | 轻量的后备编辑器配置，状态写入 XDG state                  |
| [skills](skills)               | `.agents/skills/`、`.claude/skills/` | 自定义 Skill；源文件共用一份，按客户端提供入口            |
| [codex](codex)                 | `.codex/agents/`                     | Codex 执行代理 sol_worker，以普通文件副本部署             |

Zsh 中用 `Ctrl-R` 打开 Atuin 历史搜索，`Ctrl-T` 打开 fzf 文件选择，
`z`/`zi` 通过 zoxide 跳转目录。
原生 Zsh 历史保存到 `~/.local/state/zsh/history`；macOS Terminal 的额外会话保存与恢复已禁用。

## 部署说明

### 平台与依赖

三个脚本均需 Bash 3.2+，适用范围如下：

| 入口      | 支持范围                                                                  |
| --------- | ------------------------------------------------------------------------- |
| bootstrap | Ubuntu 24.04/26.04（x86_64、arm64）、macOS 15/26（Apple Silicon）         |
| deploy    | Linux/macOS；GNU Stow（支持 `--no-folding`）、Git（支持 `--fixed-value`） |
| doctor    | Linux/macOS；`--runtime` 另需 Python 3.7+ 与已准备的插件                  |

bootstrap 在 `--apply` 时准备所需的软件和插件；请以普通用户运行，并按提示
提供 `sudo` 认证。各项软件的最低版本见
[requirements.tsv](scripts/bootstrap/requirements.tsv)。

### 部署约定

- **链接方式**：Stow 将包内受管入口链接到 HOME，公共父目录保持真实目录，便于与本机文件共存。
  `skills` 使用两层目录链接共用一份正文，Codex 角色则按普通文件副本部署；
  [新增与维护配置](#新增与维护配置)说明这两种特殊产物的维护方式。
  链接依赖仓库中的源文件，请保留仓库目录；切换分支前确认目标分支也包含已经部署的包。
- **默认路径**：目标固定为 HOME 与默认的 `~/.config`、`~/.local/share`、
  `~/.local/state`、`~/.cache`；对应的四个 XDG 变量须未设置、为空或指向这些默认值。
- **Zsh 入口**：不要导出 `ZDOTDIR`；HOME 根目录的 `~/.zshenv` 会负责设置它。
- **配置冲突**：`~/.gitconfig`、Ghostty `config`、Neovim `init.vim` 等旧入口
  需要手动迁移；脚本会报告冲突，不自动迁移或覆盖冲突文件。
- **Git 入口**：已有的 `~/.config/git/config` 必须是普通文件，并包含直接的
  `include.path = config.shared`。文件不存在时才由脚本创建；已有文件不会自动补写。
- **Stow 环境**：`.config`、`.config/nvim` 等指定目录若已存在，必须是真实目录。
  调用目录及 HOME 中的 `.stowrc`、HOME 中的 `.stow-global-ignore` 均须不存在。
- **失败处理**：`--apply` 中途失败会保留已完成的修改，不自动回滚。
  解决问题后，先重新预览，再执行 `--apply`。

完整预检条件和迁移路径见 `bash scripts/deploy.sh --help`。

## 快速开始

仓库可以放在任意目录，以下示例使用 `~/src/dotfiles`。

```sh
git clone https://github.com/codeExpert666/dotfiles.git ~/src/dotfiles
cd ~/src/dotfiles
```

后续命令均在仓库根目录执行。如果还需要安装软件或准备插件，请按[安装依赖并部署](#安装依赖并部署)操作；
如果依赖和插件都已准备好，请按[仅部署配置](#仅部署配置)操作。

### 安装依赖并部署

需要安装软件、准备插件时，使用 bootstrap。先选择 profile：

- `server`：准备命令行环境与 terminfo 工具，适合远程服务器。
- `desktop`：额外安装 Ghostty、IosevkaTerm Nerd Font 和 Sarasa Term SC。
  字体应安装在本地终端客户端。

profile 控制软件和字体的准备范围，配置包按平台选择；`server` 也会部署
Ghostty 配置。以下以 `server` 为例，桌面环境将两条命令中的值改为 `desktop`。

```sh
# 离线预览
bash scripts/bootstrap.sh --dry-run --profile server

# 确认预览后，安装软件、部署配置并准备插件
bash scripts/bootstrap.sh --apply --profile server
```

### 仅部署配置

依赖和插件已准备好时，使用 deploy 建立配置链接：

```sh
bash scripts/deploy.sh --dry-run   # 预览链接与冲突
bash scripts/deploy.sh --apply     # 建立符号链接
```

### 部署后检查

部署后重新打开终端。bootstrap 完成时已自动运行 doctor 及适用的运行检查；
仅部署配置后，或日常排查时，可以手动检查：

```sh
bash scripts/doctor.sh                                # 离线检查
bash scripts/doctor.sh --only zsh --only nvim --runtime  # 受控运行会话
```

报告中的 `FAIL` 表示检查失败，`WARN` 表示需要核实，`SKIP` 表示该项未验证。
退出码为 0 时仍可能有 `WARN` 或 `SKIP`，需要查看具体原因。

### 个人配置

身份、凭据和机器特定设置都留在仓库之外，由下面几步手动完成。

**Git 身份**：对部署脚本新建的 `~/.config/git/config`，可用以下命令添加身份：

```sh
git config --file ~/.config/git/config user.name "Your Name"
git config --file ~/.config/git/config user.email "you@example.com"
```

修改已有文件时，要覆盖共享默认值的个人设置应放在 `config.shared` include 之后；
`git config` 不会自动调整已有配置项与 include 的先后顺序。

**登录 Shell（可选）**：bootstrap 不执行 `chsh`。需要将 Zsh 设为默认登录
Shell 时，手动执行：

```sh
chsh -s "$(command -v zsh)"   # 重新登录后生效
```

**本机 Zsh 设置（可选）**：交互设置写入 `~/.config/zsh/local.zsh`，登录环境写入
`~/.config/zsh/local.zprofile`。两者都不纳入仓库；doctor 会在文件存在时检查其语法。

**Atuin 历史（可选）**：bootstrap 不导入历史、不登录账户，按需选择以下操作：

```sh
atuin import auto           # 导入既有 Shell 历史
atuin login && atuin sync   # 登录账户并同步
```

`auto_sync` 默认关闭；登录后，后续同步仍需手动运行 `atuin sync`。

## 新增与维护配置

内容放仓库还是放本机，添加方式完全不同：仓库维护可复用和可审查的设置，凭据、身份、
认证状态和实验性设置留在仓库之外，见[个人配置](#个人配置)。

**普通配置包**（zsh、nvim、git、lazygit、starship、atuin、shuck、vim 等）方式相同：
在包内按 HOME 相对路径放入文件，在仓库根目录预览并部署，不需要改脚本。

```sh
$EDITOR zsh/.config/zsh/common.zsh   # 编辑已有配置
$EDITOR starship/.config/starship.toml
bash scripts/deploy.sh --dry-run     # 确认链接与冲突
bash scripts/deploy.sh --apply
```

新增 Stow 包时，先用同样的目录结构建包，再把包名加入 [scripts/layout.bash](scripts/layout.bash)
`load_dotfiles_layout` 里的 `layout_packages`，三个入口按该列表部署和检查；包内需要排除
部署的内容才使用自定义忽略规则，参考 [skills/.stow-local-ignore](skills/.stow-local-ignore)。
新包涉及的公共父目录若为新增，同样要有对应的 `layout_directories` 声明。
测试夹具维护独立的包清单，还需同步更新 [tests/support/harness.bash](tests/support/harness.bash)
中的 `packages` 和 [tests/doctor/cases.py](tests/doctor/cases.py) 中的 `PACKAGES`，
确保回归测试复制并验证新包。

**特殊情况**各有单独流程，三处都遵循同一原则：声明在前，预览验证在后。

- **Ghostty**：共用入口在 `ghostty` 包，macOS 专有内容放在 `ghostty-macos` 包，不要混写。
- **skills**：源文件统一维护在 `skills/src/<名称>/`。新增技能时，创建带有 `name`、
  `description` 的普通文件 `SKILL.md`，并在 `layout_skill_clients` 中声明所需客户端入口。

  **共享技能**声明 `.agents .claude`，为两个客户端建立指向同一源目录的链接。
  在仓库根目录执行，链接目标相对于链接所在目录：

  ```sh
  ln -s '../../src/<名称>' 'skills/.agents/skills/<名称>'
  ln -s '../../src/<名称>' 'skills/.claude/skills/<名称>'
  ```

  **专用技能**只声明并建立所需入口。例如 `astra-sol` 仅用于 Codex，声明 `.agents`，
  不建立 `.claude` 入口：

  ```sh
  ln -s '../../src/astra-sol' 'skills/.agents/skills/astra-sol'
  ```

- **Codex 角色**：源文件位于 `codex/agents/sol_worker.toml`，部署为
  `~/.codex/agents/sol_worker.toml` 的普通副本，修改仓库不会自动更新本机文件。
  首次接管或更新已有角色时，先核对差异、备份并移走旧文件，再预览和部署。
  部署后新开 Codex 会话验证角色加载与一次小任务委派；doctor 只检查部署文件。
  部署机制见[脚本说明](scripts/README.md#deploy-的部署产物与失败路径)。

改完先预览，再执行 `--apply`，随后用 `bash scripts/doctor.sh --only deployment` 检查部署状态；
改动较大时运行 `bash tests/all.sh` 回归。目标位置已有旧入口或个人文件时，deploy 会报告冲突并
保留原文件，不自动迁移或覆盖，先手工把有用设置并入仓库再重新预览。`--apply` 失败时已完成
的修改保留，解决后重新预览再应用。

本地试验和机器特定设置放仓库外：Zsh 用 `~/.config/zsh/local.zsh` 与 `local.zprofile`，
其他应用仅在支持独立本机覆盖文件时使用该机制。默认配置路径可能已链接到仓库，修改前
先确认链接目标，避免将本机设置写入受管源文件。

## 更多文档

- [脚本使用与维护](scripts/README.md)：三个入口的完整行为、安装来源、日志、
  失败与重试、doctor 的证据范围。
- [测试](tests/README.md)：四套测试的职责、单用例运行与静态检查。
- [仓库约定](AGENTS.md)：目录组织、代码风格、提交信息与验证要求。

## 许可证

除另有声明的内容外，本仓库采用 [MIT License](LICENSE)。

以下内容保留各自的许可证及来源说明：

- 基于 LazyVim starter 的 Neovim 配置：
  [Apache License 2.0](nvim/.config/nvim/LICENSE)。
- Ghostty 光标着色器：
  [MIT License](ghostty-macos/.config/ghostty/shaders/LICENSE)，
  来源见[着色器说明](ghostty-macos/.config/ghostty/shaders/README.md)。
- Starship 配置基于[官方 Tokyo Night 预设](https://starship.rs/presets/tokyo-night)修改，
  保留 ISC 许可；完整声明见[配置文件](starship/.config/starship.toml)。
