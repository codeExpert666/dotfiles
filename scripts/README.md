# 脚本使用与维护

本目录有四个公开脚本入口：deploy、doctor、bootstrap 直接作用于当前机器；
multipass 在 Apple Silicon Mac 宿主机上编排可选的 Ubuntu 开发机。入口可从任意
工作目录调用。以下相对路径命令均在仓库根目录执行；当前机器的首次使用流程见
[根目录 README](../README.md#快速开始)，完整参数以各入口或子命令的 `--help` 为准。

## 入口与执行关系

| 入口                         | 运行位置与职责                                              | 默认行为                                            |
| ---------------------------- | ----------------------------------------------------------- | --------------------------------------------------- |
| [deploy.sh](deploy.sh)       | 当前机器；部署配置链接、个人 Git 入口和 Codex 角色副本      | 只预览；`--apply` 才写入                            |
| [doctor.sh](doctor.sh)       | 当前机器；检查依赖、部署及应用配置，可选受控运行            | 离线检查；不安装或修复                              |
| [bootstrap.sh](bootstrap.sh) | 当前机器；准备软件和插件，调用 deploy，最后调用 doctor 验收 | 离线预览；必须指定 profile                          |
| [multipass.sh](multipass.sh) | Mac 宿主机；创建、配置、检查和接入 Ubuntu 开发机            | `create`/`provision` 预览；`check` 查询、`ssh` 连接 |

在已有机器上，依赖和插件备妥时使用 deploy 部署，再用 doctor 检查；需要准备完整环境
时使用 bootstrap，它依次准备软件、调用 deploy、准备插件和桌面资源，再调用 doctor。
需要独立开发机时，宿主机运行 multipass，在 Ubuntu 客户机内调用
`bootstrap.sh --profile server`，进而复用 deploy 和 doctor。

```sh
bash scripts/deploy.sh --dry-run
bash scripts/deploy.sh --apply
bash scripts/doctor.sh
bash scripts/doctor.sh --only zsh --only nvim --runtime

bash scripts/bootstrap.sh --dry-run --profile server
bash scripts/bootstrap.sh --apply --profile server
# 桌面机器将 server 改为 desktop

# Ubuntu 开发机的完整创建步骤见 Multipass 专文
bash scripts/multipass.sh create --help
```

Multipass 的提交选择、SSH 身份准备及创建命令见
[开发机说明](../multipass/README.md#宿主机要求与首次创建)。

## 代码导航

| 位置                                                           | 职责                                                     |
| -------------------------------------------------------------- | -------------------------------------------------------- |
| `scripts/*.sh`                                                 | 四个公开命令入口；核心入口持有运行状态、阶段和 trap      |
| [layout.bash](layout.bash)                                     | deploy、doctor、bootstrap 共用的包、目标目录和旧入口声明 |
| `bootstrap/`、`doctor/`                                        | 各自私有的安装器、检查器及受控运行辅助程序               |
| [multipass.sh](multipass.sh)、`multipass/`                     | 宿主机入口和 Python 编排实现；客户机运行核心脚本         |
| [multipass/](../multipass/README.md)                           | VM 默认值、宿主安装声明及 cloud-init 模板；不是 Stow 包  |

Bash 辅助文件负责核心流程的调度，Python 标准库处理复杂文件操作、PTY 和
Multipass 编排，Lua/Zsh 调用对应运行时。被 source 的文件只声明函数或数据。
平台安装、部署和诊断各有自己的判断规则；具体清单及更新职责见
[维护与验证](#维护与验证)。

## 平台与运行约定

| 入口      | 支持范围                                                                     | 附加前提                                                                       |
| --------- | ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| deploy    | Linux/macOS，Bash 3.2+                                                       | GNU Stow 支持 `--no-folding`，Git 支持 `--fixed-value`；创建普通文件需要硬链接 |
| doctor    | Linux/macOS，Bash 3.2+                                                       | 原生检查按应用可用性执行；`--runtime` 需要 Python 3.7+ 和已准备的插件          |
| bootstrap | Ubuntu 24.04/26.04（x86_64、arm64）；macOS 15/26（Apple Silicon），Bash 3.2+ | 普通用户运行；安装时按需 sudo 认证，随后准备 Python 3.9+ 等依赖                |
| multipass | macOS 15+（Apple Silicon），Bash 3.2+                                        | 宿主机 Python 3.9+、Git、curl、OpenSSH、网络；创建前需准备专用 SSH 身份        |

四个入口均要求 HOME 是已存在的绝对目录，四个 XDG 变量须未设置、为空，或指向
该 HOME 下默认的 `.config`、`.local/share`、`.local/state`、`.cache`。
三个当前机器入口可按实际目录身份识别已有目录的别名；multipass 的宿主 HOME 和
`~/.ssh` 必须是真实目录，XDG 值须直接指向默认路径，完整前提见
[宿主机要求](../multipass/README.md#宿主机要求与首次创建)。
当前机器入口不得导出 `ZDOTDIR`，仓库根 `.zshenv` 负责设置未导出的 ZDOTDIR；
bootstrap/deploy 将 HOME 的逻辑表示传给子入口，物理目标另行计算。

三个当前机器入口的 `--help` 写入 stdout，报告、错误和提示写入 stderr，例如可用
`bash scripts/doctor.sh --verbose 2>doctor.log` 保存完整诊断。HUP/INT/TERM 分别使用
退出码 `129`/`130`/`143`。multipass 的帮助写入 stdout；`create`/`provision` 的
预检、计划、`STAGE` / `STEP` 进度和错误写入 stderr，`--apply` 的阶段输出也进入宿主日志。
`check` 向 stdout 输出 JSON，`ssh` 进入
交互会话。其余退出状态在各入口下说明。

## deploy 部署配置

deploy 默认只预览目标与冲突；`--apply` 才部署。包及旧入口在
[layout.bash](layout.bash) 声明。部署产物如下：

- 多数受管入口由 GNU Stow 建立文件级链接，公共父目录保持真实目录，不折叠整个目录。
  `skills` 采用两层目录链接：客户端的 `~/.agents/skills/<名称>` 或
  `~/.claude/skills/<名称>` 指向包内入口，包内入口再指向 `skills/src/<名称>`。
  [.stow-local-ignore](../skills/.stow-local-ignore) 排除包内 `src`；doctor 按
  `layout_skill_clients` 检查源目录、必需客户端入口及排除规则，不从现存入口反推范围。
  `astra-sol` 仅声明 `.agents` 入口；“仅用于 Codex”是使用约定，扫描该目录的其他客户端
  仍可能发现它。
- `~/.config/git/config` 是个人普通文件。不存在时可原子创建并包含
  `include.path = config.shared`；已有文件必须直接包含该 include，脚本不会编辑它。
  个人覆盖应写在共享 include 后面。
- macOS 的 Ghostty 平台入口由 `ghostty-macos` 包部署；Linux 上该入口必须缺席。
- Codex 角色以普通文件副本部署，因为[上游拒绝符号链接角色文件](https://github.com/openai/codex/pull/39299)。
  [sol_worker.toml](../codex/agents/sol_worker.toml) 复制到
  `~/.codex/agents/sol_worker.toml`：相同的普通文件保持不变；内容不同、目标为链接或目录
  时保留原件并报错。`.codex` 及 `.codex/agents` 保持真实目录，以便个人配置、其他代理、
  认证和运行状态共存。doctor 核对源文件、目标类型及内容；仓库改动不会自动更新副本。

调用目录和 HOME 中的 `.stowrc`，以及 `~/.stow-global-ignore` 必须缺席。旧
`~/.gitconfig`、Ghostty、Neovim、Lazygit、Shuck 竞争入口须先人工迁移；脚本会报告冲突
并保留内容。其他包的自定义 Stow 忽略规则会提示人工核对。执行中途失败不会整体回滚：
检查报告并处理冲突后，重新预览，再运行 `--apply`。

退出码：`0` 为预览或部署成功，`1` 为执行失败，`2` 为参数错误。

## doctor 检查与证据

doctor 默认离线读取真实环境、受管链接和状态目录，累积独立故障；权限检查不试写，
也不读取历史内容。`--only` 可重复指定模块，`--verbose` 提供有界的原生错误输出。
报告中的状态含义如下：

| 状态 | 含义                                               |
| ---- | -------------------------------------------------- |
| PASS | 有该项检查的验证证据，范围以报告文字为准           |
| WARN | 可选组件缺失、能力未准备、偏离锁文件或存在个人覆盖 |
| FAIL | 部署、必要依赖、解析、受控运行或清理发生故障       |
| SKIP | 不适用、未初始化、缺少前置条件或不能按当前方式验证 |

`0` 表示没有 FAIL，仍可能有 WARN/SKIP；`1` 表示健康检查失败；`2` 表示参数错误或
检查器无法完成。整体计数在必要清理之后输出，单项故障不阻止其他独立检查。
普通查询有 8 秒时限，每个受控运行模块有 30 秒时限。

### 默认检查

- 依赖检查离线查询实际选中的 Java/Javac/Maven：Java 必须是版本一致的完整 JDK 21+；
  非空 `JAVA_HOME` 必须与 PATH 和 `java.home` 一致；Maven 必须是稳定 3.9.x 且使用同一
  runtime。查询在临时 HOME 中进行，跳过 Maven RC。
- Git 保留环境覆盖并检查配置来源；Zsh 解析配置及工具生成的初始化脚本。Neovim
  不普通启动，只编译 Lua、解析 JSON，并检查本地插件及工具。无法在本地取得锁定
  LazyVim 的最低版本说明时跳过，不联网获取。
- Starship 使用实际选中的配置；Atuin 和 Shuck 使用所选配置的副本及临时状态。
  Ghostty 等原生应用按可用性检查。delta 的后台能力探针固定深色主题，避免颜色查询
  操作控制终端。

### 受控运行与边界

`--runtime` 在临时 HOME 中运行配置和插件副本，验证**新**会话，不自动安装或更新。
非默认布局、本机可执行覆盖、未知配置或缺少前提时，相应检查会 SKIP。这是应用级
受控运行，不是执行任意代码的操作系统沙箱；Zsh/Neovim 提前退出会报告失败。

| 模块    | 受控运行核对的内容及主要限制                                                                                                                                                 |
| ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Zsh     | Antidote 和插件缓存、非交互/登录/PTY 会话、补全、按键与钩子；有 `local.zsh` 或 `local.zprofile` 时跳过运行，仍检查默认配置语法                                               |
| Neovim  | 锁定插件及配置、已有 LSP、格式化、Zsh 诊断和 parser；关闭自动安装与更新。额外启动代码或配置目录链接会使运行检查跳过，Stow 文件级链接可用；Taplo 的远程 schema 不纳入离线检查 |
| Lazygit | 在临时 Git 仓库中运行真实 TUI，确认 delta 渲染并记录编辑器参数；编辑器本身由 Neovim 模块检查，自定义 YAML 或多配置层不作为仓库基线                                           |
| Vim     | 复制受管配置，检查配置发现、行号和状态目录                                                                                                                                   |

显式路径解析通过，不等于应用发现了同一配置；新会话通过，也不代表已经运行的父 Shell
或编辑器状态正确。Atuin 环境覆盖、项目 Shuck 配置、GUI 渲染、剪贴板及 SSH 客户端字体
有各自的检查限制，应结合真实终端复核。

## bootstrap 准备完整环境

bootstrap 必须选择 profile。`server` 准备命令行环境与 terminfo 工具；`desktop`
在此基础上增加 Ghostty、IosevkaTerm Nerd Font、Sarasa Term SC，以及 Linux 剪贴板工具。
profile 控制软件和字体范围，配置包按平台选择；远程服务器的字体通常应装在终端客户端，
Ghostty SSH 集成负责向远端提供终端定义。multipass 在 Ubuntu 客户机内预览并执行
`bootstrap.sh --profile server`，继承此处的安装、部署和诊断约定。

### 预检、预览与执行阶段

除共同环境约定外，`NVIM_APPNAME` 必须未设置或为 `nvim`。安装前还会拒绝继承的
Git 仓库上下文变量，即使其值为空：`GIT_DIR`、`GIT_WORK_TREE`、`GIT_COMMON_DIR`、
`GIT_INDEX_FILE`、`GIT_OBJECT_DIRECTORY`、`GIT_ALTERNATE_OBJECT_DIRECTORIES`、
`GIT_SHALLOW_FILE`、`GIT_GRAFT_FILE`、`GIT_NAMESPACE`、`GIT_IMPLICIT_WORK_TREE`、
`GIT_PREFIX`。这些变量可能使 `git -C` 操作其他仓库；取消后从普通 Shell 重跑。
Git 用户配置、鉴权及 `GIT_CONFIG_GLOBAL` 等配置选择仍保留。

默认预览只读取安装清单、查询现有命令，不调用包管理器或下载资源；远端最新版本仅在
`--apply` 需要安装时解析。预览使用真实 PATH 选中的二进制，隔离 HOME/XDG、日志、
临时文件和工作目录，退出时清理私有临时目录。Git/Stow 尚未满足要求时，部署预检
延后到安装完成。

| 阶段          | `--apply` 执行内容                                              |
| ------------- | --------------------------------------------------------------- |
| 1. 软件与能力 | 平台软件，按需准备 Go/JDK/Maven，并探测 npm、C、Java/Maven 能力 |
| 2. 部署       | 先运行 deploy 预检，再部署配置                                  |
| 3. 插件与资源 | 准备 Zsh、Neovim 插件和工具；desktop 另准备桌面软件及字体       |
| 4. 验收       | 复查必需工具，运行适用的 doctor 及受控运行检查，清理后汇总结果  |

macOS 先检查 Command Line Tools 和 Homebrew；首次安装可能需要完成 Apple 的安装窗口
后重跑。每次 apt 更新或安装、以及启动 Homebrew 安装器前，先检查免密 sudo 权限；
需要密码时在前台用 `sudo -v` 认证。长任务后认证过期可能再次提示；拒绝认证会停止
当前阶段，保留已完成内容，不启动后台 sudo 保活任务。

### 软件来源与复用规则

| 范围      | 选择和发布规则                                                                                                                                                                                                                                                                                                        |
| --------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| macOS 包  | Brewfile 准备 Homebrew 管理的包，即使 PATH 上已有其他来源的兼容命令仍会补齐；Bundle 使用 `--no-upgrade`，仅对不满足最低要求的工具定向升级。首次安装版本及必要依赖仍由包管理器决定                                                                                                                                     |
| Ubuntu 包 | 复用兼容命令；需要时先装 apt 包，再用声明的固定上游资源或后备资源。24.04 的 Ghostty 使用固定的 mkasberg/ghostty-ubuntu 社区 deb，26.04 使用发行版包；macOS 使用 Homebrew cask                                                                                                                                         |
| Go        | 用 `GOTOOLCHAIN=local go version` 离线检查 PATH 中的 Go，达到[最低要求](bootstrap/requirements.tsv)即复用；否则从 [Go 官方版本列表](https://go.dev/dl/?mode=json)选择最新稳定版并校验 SHA256，发布到 `~/.local/share/dotfiles-bootstrap/go/`，将 `go`/`gofmt` 入口放在 `~/.local/bin/`                                |
| JDK       | 优先检查非空 `JAVA_HOME`，否则从 PATH 的 `java` 查询 `java.home`；同一 JDK 的 `java`/`javac` 都可执行、版本一致且不低于 21 才复用。否则从 [Adoptium API](https://api.adoptium.net/v3/assets/latest/25/hotspot)取得最新 Temurin JDK 25 GA，核对元数据及 SHA256，再发布 `~/.local/share/dotfiles-bootstrap/jdk/current` |
| Maven     | 仅复用使用所选 JDK、版本稳定且满足 3.9 要求的 3.9.x；否则从 [Maven Central 元数据](https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/maven-metadata.xml)选择最新稳定 3.9.x，校验 SHA512 sidecar，发布 `~/.local/bin/mvn`                                                                             |

Go、JDK、Maven 达标后重跑保持离线，不追逐新版本；它们不列入 Brewfile 或 apt 清单。
已有非受管命令入口冲突时保留并报错。Go 1.21 起可按默认
[`GOTOOLCHAIN=auto`](https://go.dev/doc/toolchain) 为 Mason 模块选用更新工具链；
禁用自动切换的个人配置需自行满足模块要求，bootstrap 不改写它。

JDK 完整性在发布前核对，损坏的新归档或旧缓存不会替换 `current`。受管 JDK 发布后，
根 `.zshenv` 让所有新 Zsh 使用其 `JAVA_HOME` 和完整 `bin`，登录 Shell 在
`brew shellenv` 后恢复 PATH 优先级；启动文件不在每次启动时运行版本查询。
需要其他 JDK 时，可在 `~/.config/zsh/local.zprofile` 中显式重设 `JAVA_HOME`
并同步调整 PATH。Maven 的隔离探针不读取或写入个人 `~/.m2`。

固定版本的其他上游归档在 [releases.json](bootstrap/releases.json) 声明；资源先校验，
再在私有暂存目录准备并发布，有效缓存会复用。单独运行 Brewfile 只准备软件，不完成
部署、插件准备和整体验收。

### 插件与桌面资源

Antidote 读取仓库的 Zsh 插件清单，下载缺失插件并更新必要缓存，不主动更新已有插件；
清单尚未锁定提交，新机器使用下载时的上游版本。Neovim 用配置副本和
`lazy-lock.json` 准备、恢复插件，不写回锁文件；Mason 按实际配置准备工具，
包版本仍由 registry 解析。Treesitter parser 跟随锁定插件，补全资源也必须成功。

字体按内部族名选择文件并保留上游许可，Sarasa Term SC 归档需要 7zz。
macOS bootstrap 与使用 Ghostty 的 doctor 以 `+list-fonts --family=...` 精确查询目标族名；
无参数列表仅显示被识别为等宽的字体，不能据此判断缺失。已可查询的字体重跑直接复用。
首次发布或复用有匹配族名记录的受管目录时，最多等待 30 秒供字体服务识别；
查询错误或超时会报告失败、保留文件，后续可重跑验收。

### 日志、失败与重试

| 内容                         | 路径                                                                                    |
| ---------------------------- | --------------------------------------------------------------------------------------- |
| 持久日志、并发锁及 PID       | `~/.local/state/dotfiles-bootstrap/run.*`、`~/.local/state/dotfiles-bootstrap/lock/pid` |
| 已校验下载缓存               | `~/.cache/dotfiles-bootstrap/`                                                          |
| 上游工具、完成标记及命令入口 | `~/.local/share/dotfiles-bootstrap/<工具>/<版本>-<平台>/`、`~/.local/bin/`              |
| Antidote、Neovim 插件和工具  | `~/.local/share/antidote`、`~/.cache/antidote/`、`~/.local/share/nvim/`                 |
| Linux/macOS 字体             | `~/.local/share/fonts/dotfiles-bootstrap/`、`~/Library/Fonts/dotfiles-bootstrap/`       |

长任务输出同时进入终端和日志。命令失败、日志转发失败或必要清理失败都会阻止总体成功；
已有主体错误或中断码优先保留，清理错误另行报告并指出保留路径。中断会回收本次任务的
进程组并释放锁；强制关机或 SIGKILL 后，须先确认 PID 所指进程已结束，再处理遗留锁。
包管理器自身的中断恢复依其错误提示进行。

失败保留已完成的安装与部署，不整体回滚。个人文件、其他安装位置的命令入口或脏插件
checkout 冲突时，先核对并保留个人内容，之后重跑同一命令；达标步骤会复用。
Git 身份、登录 Shell 和 Atuin 历史导入属于[个人收尾操作](../README.md#个人配置)。

退出码：`0` 为预览或所选准备及适用检查成功，`1` 为执行失败，`2` 为参数错误。
bootstrap 成功仍可能包含 doctor 的 WARN/SKIP，应阅读具体报告范围。

## multipass 创建和管理 Ubuntu 开发机

multipass 在 Apple Silicon Mac 宿主机上编排 Ubuntu arm64 客户机；客户机从指定的
远程提交部署本仓库，再以普通 `ubuntu` 用户运行前述 bootstrap 流程。它使用客户机内的
核心脚本，不共享所有宿主环境、状态和输出约定。四个子命令承担不同职责：

| 子命令      | 职责                                                                       |
| ----------- | -------------------------------------------------------------------------- |
| `create`    | 创建实例、建立 SSH 连接并从固定提交配置客户机；默认只预览                  |
| `provision` | 重新配置已有实例，可用 `--ref` 更新目标提交；默认只预览                    |
| `check`     | 查询受管实例状态，不启动或修复；`--runtime` 还在客户机运行 doctor 受控诊断 |
| `ssh`       | 进入已运行实例的普通 SSH 会话                                              |

`create`/`provision` 在 `--apply` 时可能安装或升级宿主 Multipass，并写入宿主状态；
客户机的项目目录位于自己的 `~/workspace`，不挂载宿主目录。专用 SSH 私钥由操作者
保留在宿主机，脚本仅把公钥写入客户机。完整的
[创建步骤](../multipass/README.md#宿主机要求与首次创建)、
[日常命令](../multipass/README.md#日常使用)、
[宿主与客户机状态路径](../multipass/README.md#配置目录与状态)、
[失败恢复边界](../multipass/README.md#失败处理与恢复边界)
由 Multipass 专文维护。

## 维护与验证

### 声明与安装来源

安装要求、部署目标和健康等级有不同职责，按下表维护对应声明，避免让同一份清单
承担三种判断规则：

| 声明位置                                                                                                                                                                                     | 维护内容                                                             |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| [layout.bash](layout.bash)                                                                                                                                                                   | Stow 包、默认布局、旧入口及 Skill 客户端范围                         |
| [requirements.tsv](bootstrap/requirements.tsv)                                                                                                                                               | 命令最低要求、base/desktop 和平台；最低版本 `0` 表示先检查可执行文件 |
| [releases.json](bootstrap/releases.json)                                                                                                                                                     | 固定下载资源的版本、平台资产、URL、SHA256 和安装入口                 |
| [macOS Brewfile](bootstrap/macos/Brewfile)、[desktop 增量](bootstrap/macos/Brewfile.desktop)                                                                                                 | 静态 tap/formula/cask 声明                                           |
| [Ubuntu 包清单](bootstrap/ubuntu/packages.bash)、[desktop 增量](bootstrap/ubuntu/packages.desktop.bash)                                                                                      | apt 包、命令映射、上游资源及发行版差异                               |
| [resources.py](bootstrap/resources.py)                                                                                                                                                       | 按需解析 Go/JDK/Maven 版本、校验和发布资源                           |
| [Multipass 默认值](../multipass/defaults.json)、[宿主安装声明](../multipass/host-releases.json)、[cloud-init 模板](../multipass/cloud-init.yaml.tmpl)                                        | VM 资源、宿主安装版本及客户机初始化；不参与 Stow 部署                |

维护平台清单时，注意以下现有约定：

- Shuck formula 使用 `trusted: true`，仅信任该 formula，不把整个 tap 视为已授权；
  `tree-sitter` 命令来自 `tree-sitter-cli` 包，不是只提供库的 `tree-sitter` 包。
- Ubuntu 的 `apt_commands` 用 `包名|命令列表` 表示映射；空命令列表按 dpkg 状态判断。
  `release_commands` 可声明一份资源提供多个命令；`apt_fallbacks` 仅在 apt 结果仍不兼容
  时使用。发行版后缀数组区分来源，加载时重置清单以免 profile 串扰。
- Homebrew 初始安装器的提交与校验值留在 macOS 安装器中，因为该阶段尚不能依赖 Python。
  Maven 用官方 SHA512，其他当前固定归档用 SHA256。

### 进程与探针

Antidote 的插件准备在无控制终端的后台任务中运行，防止子进程操作终端时因
SIGTTOU 停住；输出仍实时转发，超时或中断会清理其子进程。普通版本查询有
8 秒时限；bootstrap 的 Java 运行时属性和 Maven 版本查询分别为 12、15 秒，
Java 编译、运行及离线 Maven validate 分别为 30、15、60 秒。Maven 探针禁用
用户 RC，并使用临时 settings、toolchains 与本地仓库。

doctor 的受控进程清理会回收组长退出后遗留的子进程；对已退出进程组短暂的
`EPERM` 最多等待 0.2 秒确认消失，仍存在或持续拒绝访问则报错。

### 回归与静态检查

全量回归入口为 [tests/all.sh](../tests/all.sh)，包括 Multipass 离线套件；
可单独运行 `bash tests/multipass.sh`。五套离线测试的职责、单用例运行、缓存集成
及测试代码静态检查见[测试说明](../tests/README.md)。真实双版本 VM 验收须显式
运行 `bash tests/multipass-live.sh`，前提、命令和清理边界见
[Multipass 维护与验收](../multipass/README.md#维护与验收)。
生产脚本的静态检查在仓库根目录执行：

```sh
bash tests/all.sh
# 指定 Bash 验证全部套件
/path/to/bash tests/all.sh

files=(scripts/*.sh scripts/*.bash scripts/bootstrap/*.bash scripts/bootstrap/*/*.bash)
for file in "${files[@]}"; do bash -n "$file" || exit; done
shellcheck -x "${files[@]}"
shfmt -d -ci -sr "${files[@]}"
for file in scripts/bootstrap/zsh.zsh scripts/doctor/zsh.zsh; do zsh -n "$file" || exit; done
stylua --check --indent-type Spaces --indent-width 2 scripts/*/*.lua
python3 -B - <<'PY'
import ast
from pathlib import Path
for path in Path('scripts').rglob('*.py'):
    ast.parse(path.read_text(), filename=str(path))
PY
```

更新资源时核对 URL、校验值、最低要求及平台清单；更新插件锁时同步验收 Neovim
内部 API、实际 Zsh/Neovim 会话及不变的锁文件。模拟 uname 只证明分支选择；
目标系统的原生 Bash、包管理器、首次安装和桌面行为仍须在对应平台验收；
Multipass 的命令与 VM 替身也不能代替真实宿主和客户机验收。
