# 脚本使用与维护

三个入口分别负责配置部署、状态诊断和新机器准备，可从任意工作目录调用。
以下相对路径命令均在仓库根目录执行；完整参数以各入口的 `--help` 为准。

| 入口                         | 用途                                                 | 默认行为                             |
| ---------------------------- | ---------------------------------------------------- | ------------------------------------ |
| [deploy.sh](deploy.sh)       | 用 GNU Stow 部署链接，创建个人 Git 入口和 Codex 角色副本 | 模拟部署；显式 `--apply` 才写入      |
| [doctor.sh](doctor.sh)       | 检查环境、部署和应用配置；可选受控运行               | 离线检查，累积独立故障；不安装或修复 |
| [bootstrap.sh](bootstrap.sh) | 准备软件、插件和桌面资源，调用 deploy，再调用 doctor | 离线预览；必须指定 profile           |

```sh
# 已有软件，只需部署或检查配置
bash scripts/deploy.sh --dry-run
bash scripts/deploy.sh --apply
bash scripts/doctor.sh
bash scripts/doctor.sh --only zsh --only nvim --runtime

# 新机器：先预览，再执行所选准备流程
bash scripts/bootstrap.sh --profile server
bash scripts/bootstrap.sh --apply --profile server
bash scripts/bootstrap.sh --apply --profile desktop

# doctor 报告写入 stderr
bash scripts/doctor.sh --verbose 2>doctor.log
```

`server` 准备命令行环境与 terminfo 工具；`desktop` 追加 Ghostty、IosevkaTerm Nerd Font、
Sarasa Term SC，以及 Linux 剪贴板工具。远程机器上的字体通常属于终端客户端；已有 Ghostty
SSH 集成负责向远端提供终端定义。

三个入口的帮助均写入 stdout，执行报告、错误与提示均写入 stderr。

## 支持范围与目标目录

| 入口      | 系统与解释器                                                                 | 其他前提                                                                        |
| --------- | ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| deploy    | Linux/macOS，Bash 3.2+                                                       | GNU Stow 支持 `--no-folding`，Git 支持 `--fixed-value`；新建 Git 入口和 Codex 副本需要硬链接 |
| doctor    | Linux/macOS，Bash 3.2+                                                       | 原生应用可按可用性检查；`--runtime` 需要 Python 3.7+ 标准库及已准备的插件       |
| bootstrap | Ubuntu 24.04/26.04（x86_64、arm64）；macOS 15/26（Apple Silicon），Bash 3.2+ | 普通用户运行；安装阶段按需在前台 sudo 认证，随后准备 Python 3.9+ 等依赖         |

HOME 必须是已存在的绝对目录。四个 XDG 变量须未设置、为空，或指向默认的
`~/.config`、`~/.local/share`、`~/.local/state`、`~/.cache`；已存在的目录可按实际目录身份识别别名。
HOME 的逻辑表示会传给子入口，物理目标另行计算。不要导出 `ZDOTDIR`；仓库根 `.zshenv`
负责设置未导出的 ZDOTDIR。bootstrap 还要求 `NVIM_APPNAME` 未设置或为 `nvim`。

bootstrap 在安装前拒绝继承的 Git 仓库上下文：`GIT_DIR`、`GIT_WORK_TREE`、`GIT_COMMON_DIR`、
`GIT_INDEX_FILE`、`GIT_OBJECT_DIRECTORY`、`GIT_ALTERNATE_OBJECT_DIRECTORIES`、`GIT_SHALLOW_FILE`、
`GIT_GRAFT_FILE`、`GIT_NAMESPACE`、`GIT_IMPLICIT_WORK_TREE`、`GIT_PREFIX`，设置为空也不接受。
这些变量可能使 `git -C` 检查或操作其他仓库；取消对应变量后，从普通 Shell 重跑。
Git 用户配置、鉴权及 `GIT_CONFIG_GLOBAL` 等配置选择仍予以保留。

## deploy 的部署产物与失败路径

deploy 拒绝调用目录和 HOME 中的 `.stowrc`，以及 `~/.stow-global-ignore`。
具体目录和旧配置入口在 [layout.bash](layout.bash) 集中声明。

多数受管入口是 Stow 创建的文件级链接，公共父目录必须保持真实目录，Stow 不折叠整个目录。
`skills` 使用两层目录链接：`~/.agents/skills/<名称>` 与 `~/.claude/skills/<名称>` 由 Stow
指向包内入口，包内入口再指向 `skills/src/<名称>`；包内 `src` 由
[.stow-local-ignore](../skills/.stow-local-ignore) 排除部署，doctor 单独检查源目录、
按 `layout_skill_clients` 声明的必需入口，以及该 `^/src$` 排除规则。不能只根据实际存在的
入口推断客户端范围，否则遗漏入口会逃过检查；其他包的自定义忽略规则仍提示人工核对。
`astra-sol` 只声明 `.agents` 入口；“仅用于 Codex”是使用范围约定，`.agents/skills`
不是客户端隔离机制，其他扫描该目录的客户端仍可能发现它。

`~/.config/git/config` 是个人普通文件。首次部署可原子创建其 `include.path = config.shared`；
已有文件必须直接包含该 include，脚本不自动编辑。个人设置放在共享 include 后面。
macOS 的 Ghostty 平台入口由 `ghostty-macos` 包部署，Linux 上该入口必须缺席。

Codex 角色以仓库内容的普通副本部署：上游[拒绝符号链接形式的角色文件](https://github.com/openai/codex/pull/39299)，
deploy 因而把 [codex/agents/sol_worker.toml](../codex/agents/sol_worker.toml) 复制为
`~/.codex/agents/sol_worker.toml`。复制复用普通文件的暂存与发布流程，内容写完后再发布；
已有普通文件内容相同时保持不变，内容不同或为链接时在部署前失败并保留原文件，
`.codex` 与 `.codex/agents` 保持真实目录，以便个人 `config.toml`、其他代理、认证和运行状态共存。
doctor 检查源文件可读、目标为普通文件且内容一致；修改仓库不会自动更新副本。

旧 `~/.gitconfig`、Ghostty、Neovim、Lazygit、Shuck 竞争入口须先人工迁移；脚本会报告冲突
并保留内容。失败时已完成的其他修改保留，不做全量回滚。

## bootstrap 的阶段与安装来源

执行顺序：环境和版本预检 → 平台软件 → Go/JDK/Maven 准备 → npm、C、Java/Maven 能力探针 → deploy 预检及部署 → Zsh 插件
→ Neovim 插件和工具 → 桌面资源 → 必需工具复查及 doctor → 清理并报告总体结果。

预览只读取安装清单和查询现有命令，不调用包管理器或下载资源。普通版本查询有 8 秒时限，
Java 运行时属性和 Maven 版本查询分别有 12 秒与 15 秒时限，
使用真实 PATH 选中的二进制，但 HOME/XDG、日志、临时文件及工作目录均隔离。
Git/Stow 不满足要求时，部署预检延后到安装完成。预览退出时也清理私有临时目录。

每次 apt 更新/安装及启动 Homebrew 安装器前，都在前台用 `sudo -v` 刷新认证。
缓存仍有效时无需再输入密码；长任务之后认证已过期时可能再次提示。认证被拒绝就停止当前阶段，
保留已完成的安装与部署，并按普通失败路径清理；不启动后台 sudo 保活任务。

| 内容                      | 声明位置                                                                                                         | 维护职责                                                                              |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| Stow 包、默认布局、旧入口 | [layout.bash](layout.bash)                                                                                       | deploy、doctor、bootstrap 共用的布局事实                                              |
| 命令最低要求              | [requirements.tsv](bootstrap/requirements.tsv)                                                                   | Tab 分隔的命令、最低版本、base/desktop、适用平台；最低版本为 `0` 表示先检查可执行文件 |
| 固定下载资源              | [releases.json](bootstrap/releases.json)                                                                         | 版本、平台资产、URL、SHA256 和安装入口                                                |
| macOS 软件                | [Brewfile](bootstrap/macos/Brewfile)、[Brewfile.desktop](bootstrap/macos/Brewfile.desktop)                       | 基础软件及 desktop 增量，静态 tap/formula/cask 声明                                   |
| Ubuntu 软件               | [packages.bash](bootstrap/ubuntu/packages.bash)、[packages.desktop.bash](bootstrap/ubuntu/packages.desktop.bash) | apt 包、命令映射、上游资源和发行版差异                                                |
| Go 工具链                 | [requirements.tsv](bootstrap/requirements.tsv)、[resources.py](bootstrap/resources.py) | 共用最低要求；缺失或不兼容时从 Go 官方解析最新稳定版与 SHA256 |
| Java 工具链               | [requirements.tsv](bootstrap/requirements.tsv)、[resources.py](bootstrap/resources.py) | JDTLS 运行下限为 Java 21；bootstrap 要求完整 JDK，不满足时解析最新 Temurin 25 GA 与 SHA256 |
| Maven                     | [requirements.tsv](bootstrap/requirements.tsv)、[resources.py](bootstrap/resources.py) | 共用 Maven 3.9 最低要求；缺失、不稳定或 runtime 不一致时从 Maven Central 解析最新 3.9.x 与 SHA512 |

macOS 先检查 Command Line Tools 和 Homebrew；首次需要完成 Apple 安装窗口后重新运行。
Brewfile 表示 Homebrew 管理的安装，即使 PATH 上已有其他来源的兼容命令，仍会补齐对应包。
Bundle 使用 `--no-upgrade`，随后只对不满足最低要求的工具定向升级；它不固定首次安装版本，
包管理器仍可能更新所需依赖。单独运行 Brewfile 只准备软件，不执行部署、插件准备和完整验收。

Shuck 的 Brewfile 条目用 `trusted: true` 显式信任 `ewhauser/tap/shuck-cli`，授权范围仅为
该 formula；`tap "ewhauser/tap"` 本身不授予整个 tap 的信任，见
[Homebrew 的信任声明](https://docs.brew.sh/Brew-Bundle-and-Brewfile#trusted)。
`tree-sitter` 命令由 `tree-sitter-cli` 包提供；Homebrew 的 `tree-sitter` 包仅提供库，
安装清单和最低版本检查的升级映射均使用 CLI 包。

Neovim 启用了 Go 支持，Mason 安装 Delve、gopls、goimports 等工具时需要先有 `go`。
两个 profile、macOS 和 Ubuntu 共用 Go 准备逻辑：以 `GOTOOLCHAIN=local go version`
离线检查实际 PATH 中的 Go，达到 `requirements.tsv` 的最低要求（当前为 1.21.0）即复用。
只有缺失或版本不达标时，`--apply` 才查询 [Go 官方版本列表](https://go.dev/dl/?mode=json)，
选择最新稳定版（排除 beta/RC），下载对应系统与架构的官方归档并校验列表提供的 SHA256。
Go 不再列入 Brewfile 或 apt 清单；安装目录为 `~/.local/share/dotfiles-bootstrap/go/`，
`go`、`gofmt` 入口放在 `~/.local/bin/`，优先于系统旧版。已有非受管入口冲突时保留并报错。
预览不联网查询最新版本；达标后的重跑也不查询或追逐新版本。解析、下载或校验失败会停止
软件阶段，保留已有安装；版本号写入本次日志，校验值写入安装回执。
Go 1.21 起支持[自动选择和下载工具链](https://go.dev/doc/toolchain)，Mason 安装的模块要求
更新版本时可由默认的 `GOTOOLCHAIN=auto` 获取；若个人配置禁用了自动切换，须自行准备
这些模块要求的 Go 版本。bootstrap 不改写个人 Go 配置。

Java 选择先尊重非空 `JAVA_HOME`，否则从 PATH 的 `java` 查询实际 `java.home`；只有同一 JDK
中的 `java`、`javac` 都可执行、报告完全相同且不低于 21 的版本时才复用。这个下限来自
[当前核对的 JDTLS v1.60.0 Java 21 运行要求](https://github.com/eclipse-jdtls/eclipse.jdt.ls/tree/v1.60.0#requirements)。无效或过旧的
`JAVA_HOME`、仅有 JRE、版本不一致以及 macOS 的启动器都不会被误当作完整 JDK。需要准备时，
`--apply` 查询 [Adoptium API](https://api.adoptium.net/v3/assets/latest/25/hotspot) 的 Eclipse
Temurin JDK 25 正式版，验证平台、架构、JDK/HotSpot 元数据和官方 SHA256，再发布稳定入口
`~/.local/share/dotfiles-bootstrap/jdk/current`。发布前同时验证 `bin/java` 和 `bin/javac`；
损坏的新归档或旧缓存不会替换原有 `current`。完整 JDK 的其他 `bin` 工具通过
`$JAVA_HOME/bin` 一并可用。

Maven 只复用版本号严格匹配稳定 `3.9.x`、达到 3.9 最低要求且 runtime 与所选 JDK 相同的
命令；RC、Maven 4 或使用其他 Java 的 Maven 会触发准备。`--apply` 从
[Maven Central 元数据](https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/maven-metadata.xml)
筛选数值最大的稳定 3.9.x，并用同一官方仓库的 SHA512 sidecar 校验归档，入口发布为
`~/.local/bin/mvn`。Java 和 Maven 达标后的重跑保持离线，不追逐补丁版本；预览也不解析
远端最新版。Java 查询、Maven 查询、编译、运行及离线 Maven validate 分别设有有限时限；
Maven 探针禁用用户 RC，显式使用临时 user/global settings、user/global toolchains 与本地仓库，
不读取或写入个人 `~/.m2`。Java 编译、运行和 Maven validate 的时限分别为 30、15 和 60 秒。

受管 JDK 发布后，根 `.zshenv` 为所有新 Zsh 设置 `JAVA_HOME`，并让 `$JAVA_HOME/bin` 位于
`~/.local/bin` 和其他 Java 之前；登录 Shell 在 `brew shellenv` 重排 PATH 后恢复同一顺序，
因此受管 Maven 与完整 JDK 都保持可发现。启动文件只检查受管入口是否完整，不在每次启动时
运行版本命令。仓库默认会覆盖继承的旧 `JAVA_HOME`；需要使用其他 JDK 时，可在
`~/.config/zsh/local.zprofile` 中显式重设 `JAVA_HOME` 并同步调整 PATH，该文件在仓库默认之后加载。

Ubuntu 复用兼容命令；需要时先安装 apt 包，再使用已声明的固定上游资源或后备资源。
包名不等于命令名时，`apt_commands` 用 `包名|命令列表` 声明；空命令列表按 dpkg 状态判断。
`release_commands` 描述一项资源提供的多个命令，例如 Node/npm；`apt_fallbacks` 只在 apt
安装后仍不兼容时使用。带发行版后缀的数组描述来源差异，加载器每次重置清单，避免 profile 串扰。
Ubuntu 24.04 的 Ghostty 使用固定的 mkasberg/ghostty-ubuntu 社区 deb，由 apt 处理依赖；
26.04 使用发行版包，macOS 使用 Homebrew cask。

上游归档先按资源声明校验后在私有暂存目录准备并发布；Maven 使用官方 SHA512，其余当前
资源使用 SHA256，已有有效缓存会复用。除按需解析的 Go、JDK 25 和 Maven 3.9 最新稳定版外，
上游归档版本由 `releases.json` 固定。Homebrew 初始
安装脚本的提交和校验值保留在 macOS 安装器中，因为该阶段尚不能依赖 Python。
字体按内部族名选择文件，并保留上游许可文件；Sarasa Term SC 的 7z 归档需要 7zz。
macOS bootstrap 与使用 Ghostty 的 doctor 检查通过 `+list-fonts --family=...` 查询目标
字体，并精确匹配输出的族名。无参数列表只显示被识别为等宽的字体，不能据此判断字体缺失；
已有字体能通过族名查询时，重跑会直接复用，无需重新下载或删除安装目录。
macOS 首次发布字体后，以及复用带匹配族名记录的受管目录时，提供 30 秒发现窗口，
等待字体服务识别新文件。只有查询成功但暂未发现目标族名时才重试；查询错误或等待超时
仍会失败并保留已安装文件，后续重跑可继续验收。

Antidote 读取仓库的 Zsh 插件清单，下载缺失插件并更新必要缓存，不主动更新已有插件。
插件准备在无控制终端的后台任务中执行，避免 Antidote 的 Zsh 子进程操作终端时触发
SIGTTOU，停在 `RUN: Zsh plugin preparation`。任务仍保留原进程组，超时和中断会一并
清理其子进程；输出继续实时写入终端与日志。
Zsh 插件清单尚未锁定提交，新机器使用下载时的上游版本。Neovim 使用配置副本和
`lazy-lock.json` 准备、恢复插件，禁止写回锁文件；Mason 根据实际配置准备工具，包版本仍由
registry 解析。Treesitter parser 修订跟随锁定插件；补全资源也必须准备成功。

## 日志、失败和重试

| 内容                       | 路径                                                                              |
| -------------------------- | --------------------------------------------------------------------------------- |
| bootstrap 持久日志         | `~/.local/state/dotfiles-bootstrap/run.*`                                         |
| 并发执行锁及 PID           | `~/.local/state/dotfiles-bootstrap/lock/pid`                                      |
| 校验后的下载缓存           | `~/.cache/dotfiles-bootstrap/`                                                    |
| 上游工具与完成标记         | `~/.local/share/dotfiles-bootstrap/<工具>/<版本>-<平台>/`                         |
| 工具命令入口               | `~/.local/bin/`                                                                   |
| Antidote、Zsh 插件缓存     | `~/.local/share/antidote`、`~/.cache/antidote/`                                   |
| Neovim 插件、工具和 parser | `~/.local/share/nvim/`                                                            |
| Linux/macOS 字体           | `~/.local/share/fonts/dotfiles-bootstrap/`、`~/Library/Fonts/dotfiles-bootstrap/` |

长任务输出持续进入终端和持久日志。命令失败、日志转发失败和必要清理失败都会阻止总体成功；
已有主体错误或中断码优先保留，清理错误另外报告并指出保留路径。
bootstrap 中断会回收本次任务进程组并释放锁；强制关机或 SIGKILL 后，须确认记录的进程
已结束，再处理遗留锁。包管理器自身的中断恢复按它的错误提示进行。

失败保留已经完成的安装与部署，不做全量回滚。已有个人文件、指向其他安装位置的入口或脏
插件 checkout 发生冲突时，先检查并保留个人内容，再重跑同一命令。重复执行会复用已完成
且满足要求的步骤。Git 身份、登录 Shell 和 Atuin 历史导入属于个人收尾操作，见
[个人配置](../README.md#个人配置)。

deploy/bootstrap 的退出码为 `0` 成功或预览成功、`1` 执行失败、`2` 参数错误；
HUP/INT/TERM 使用 `129/130/143`。bootstrap 成功仍可能包含 doctor 的 WARN/SKIP，应阅读具体范围。

## doctor 的证据范围

| 状态 | 含义                                                 |
| ---- | ---------------------------------------------------- |
| PASS | 有该项检查的验证证据，范围以报告文字为准             |
| WARN | 可选组件缺失、能力尚未准备、偏离锁文件或存在个人覆盖 |
| FAIL | 部署、必要依赖、解析、受控运行或清理发生故障         |
| SKIP | 不适用、尚未初始化、缺少前置条件或不能按当前方式验证 |

doctor 返回 `0` 表示没有 FAIL，`1` 表示健康检查失败，`2` 表示参数错误或检查器不能完成；
中断使用相同信号码。普通查询有 8 秒时限，每个受控运行模块有 30 秒时限。
整体计数在必要清理之后输出，单项故障不阻止其他独立检查。

默认检查读取真实环境、受管链接和状态目录；权限检查不试写，也不读取历史内容。
依赖检查会离线查询实际选择的 Java/Javac/Maven：Java 必须是完整且版本一致的 JDK 21+，
非空 `JAVA_HOME` 必须与 PATH 和 `java.home` 一致，Maven 必须是稳定 3.9.x 且使用同一 runtime。
这些查询在临时 HOME 中运行、跳过 Maven RC，并受普通 8 秒探针时限约束。
Git 保留环境覆盖并检查配置来源；Zsh 解析配置及工具生成的初始化脚本；Neovim 禁止普通启动，
只编译 Lua、解析 JSON、检查本地插件及工具。锁定 LazyVim 的最低版本说明无法在本地取得时会跳过，
不联网获取。Starship 使用实际选中配置，Atuin/Shuck 使用所选配置的副本和临时状态。
Atuin 任意环境覆盖、项目 Shuck 配置、GUI 渲染、剪贴板及 SSH 客户端字体有各自的检查限制。

`--runtime` 在临时 HOME 内执行配置和插件副本，验证新会话；非默认布局、本机可执行覆盖
或未知配置会使相应检查跳过。这是应用级受控运行，不是执行任意代码的操作系统沙箱。
Zsh/Neovim 会话须实际完成检查；提前退出会报告失败。

- Zsh：使用已准备的 Antidote 和插件缓存，检查非交互、登录及 PTY 交互会话、补全、按键与钩子；
  存在 `local.zsh`/`local.zprofile` 时跳过运行，默认语法检查仍保留。
- Neovim：复制锁定插件及配置，关闭 lazy/Mason/Treesitter 自动安装和更新，检查已有 LSP、
  格式化、Zsh 诊断和 parser。Taplo 的远程 schema 初始化不属于离线检查，因此其 LSP 附着会跳过。
  临时配置只包含已核对的受管 Lua/Vimscript 和两份 JSON；额外启动代码或配置目录链接会使
  运行检查跳过，Stow 的文件级链接仍可使用。
- Lazygit：临时 Git 仓库里运行真实 TUI，关闭 fetch/更新，使用真实 delta 并记录传给编辑器的参数；
  编辑器自身由 Neovim 模块检查，自定义 YAML 或多配置层不当作仓库基线。
- Vim：复制受管配置，检查配置发现、行号与状态目录。

显式路径解析通过不等于应用发现了同一配置；受控会话通过也不代表已经运行的父 Shell 或
编辑器状态正确。沙箱权限限制、GUI 效果和剪贴板需结合真实终端复核。

## 代码导航与验证

`.sh` 是三个公开命令入口；[layout.bash](layout.bash) 是唯一跨入口布局库。
`bootstrap/` 和 `doctor/` 内的文件是各自私有实现：Bash 负责调度，Python 标准库负责复杂
文件操作和 PTY，Lua/Zsh 调用对应运行时的能力。source 文件只声明函数或数据，入口持有
运行状态和 trap。平台安装器保留各自政策，不把安装要求、健康等级与部署规则合成通用清单。

全量回归入口为 [tests/all.sh](../tests/all.sh)。四套独立测试的职责、单用例运行、依赖、
缓存集成和测试代码静态检查见 [测试说明](../tests/README.md)。

```sh
bash tests/all.sh
# 验证特定 Bash：
/path/to/bash tests/all.sh
```

生产脚本的静态检查在仓库根目录执行：

```sh
files=(scripts/*.sh scripts/*.bash scripts/bootstrap/*.bash scripts/bootstrap/*/*.bash)
for file in "${files[@]}"; do bash -n "$file" || exit; done
shellcheck -x "${files[@]}"
shfmt -d -ci -sr "${files[@]}"
for file in scripts/bootstrap/zsh.zsh scripts/doctor/zsh.zsh; do zsh -n "$file" || exit; done
stylua --check --indent-type Spaces --indent-width 2 scripts/*/*.lua
```

更新资源时核对 URL/SHA256、最低要求和平台清单；更新插件锁时同步验收 Neovim 内部 API
适配、实际 Zsh/Neovim 会话及不变的锁文件。模拟 uname 只证明分支选择，目标系统的原生
Bash、包管理器、首次安装及桌面行为仍须在对应平台验收。
