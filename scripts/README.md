# 脚本使用与维护

首次部署按[根目录快速开始](../README.md#快速开始)操作；本页说明各入口的行为约定和维护位置。
入口可从任意工作目录调用，以下相对路径命令均在仓库根目录执行。完整参数以 `--help` 为准。

## 入口与执行关系

| 入口 | 职责 | 默认行为 |
| --- | --- | --- |
| [deploy.sh](deploy.sh) | 在当前机器部署配置链接、个人 Git 入口和 Codex 角色副本 | 预览；`--apply` 才写入 |
| [doctor.sh](doctor.sh) | 检查当前机器的依赖、部署及应用配置 | 离线检查；不安装或修复 |
| [bootstrap.sh](bootstrap.sh) | 准备完整环境，调用 deploy 部署、doctor 验收 | 离线预览；须指定 profile |
| [multipass.sh](multipass.sh) | 在 Mac 宿主机管理独立 Ubuntu 开发机 | 创建、重配和退役默认预览；另提供检查与 SSH |

已有依赖和插件时使用 deploy，需要安装和准备插件时使用 bootstrap。
Multipass 在 Ubuntu 客户机内运行 `bootstrap.sh --profile server`，复用相同的部署和诊断流程。

## 平台与运行约定

| 入口 | 支持范围 | 附加前提 |
| --- | --- | --- |
| deploy | Linux/macOS，Bash 3.2+ | GNU Stow 支持 `--no-folding`，Git 支持 `--fixed-value`；创建普通文件需要硬链接 |
| doctor | Linux/macOS，Bash 3.2+ | 原生检查依赖相应应用；`--runtime` 需要 Python 3.7+ 和已准备的插件 |
| bootstrap | Ubuntu 24.04/26.04（x86_64、arm64）；macOS 15/26（Apple Silicon），Bash 3.2+ | 普通用户运行，按需 sudo 认证；安装过程中准备 Python 3.9+ 等依赖 |
| multipass | macOS 15+（Apple Silicon），Bash 3.2+ | Python 3.9+、Git、curl、OpenSSH、网络和专用 SSH 身份；详见[宿主机要求](../multipass/README.md#宿主机要求与首次创建) |

HOME 须为已存在的绝对目录。四个 XDG 变量须未设置、为空，或指向 HOME 下默认的
`.config`、`.local/share`、`.local/state`、`.cache`。三个当前机器入口按实际目录身份
识别路径别名；Multipass 的宿主 HOME、`~/.ssh` 须为真实目录，XDG 值须直接指向默认路径。
当前机器入口不得导出 `ZDOTDIR`，由受管 `~/.zshenv` 设置；父子入口保留 HOME 的逻辑表示。

三个当前机器入口的帮助写入 stdout，报告、错误和提示写入 stderr，可用
`bash scripts/doctor.sh --verbose 2>doctor.log` 保存诊断。HUP/INT/TERM 的退出码分别为
129/130/143。deploy 和 bootstrap 的退出码为 0（成功）、1（执行失败）、2（参数错误）；
doctor 的状态含义见下文。Multipass 的输出和日志约定见[扩展专文](../multipass/README.md#进度与记录)。

## deploy 部署配置

```sh
bash scripts/deploy.sh --dry-run
bash scripts/deploy.sh --apply
```

deploy 按 [layout.bash](layout.bash) 选择平台配置，保持公共父目录为真实目录。

| 产物 | 部署与共存规则 |
| --- | --- |
| 普通配置 | Stow 建立文件级链接，不折叠整个目录 |
| Skills | 客户端入口链接到包内入口，再链接到 `skills/src/<名称>`；`src` 由 [.stow-local-ignore](../skills/.stow-local-ignore) 排除 |
| 个人 Git 入口 | `~/.config/git/config` 不存在时创建普通文件，包含直接的 `include.path = config.shared`；已有文件须包含该项，不自动改写 |
| Ghostty 平台入口 | macOS 部署 `ghostty-macos`，Linux 上该入口必须缺席 |
| Codex 角色 | 复制到 `~/.codex/agents/`；相同普通文件复用，内容不同或目标为链接、目录时保留原件并报错 |

Skill 的必需客户端由 `layout_skill_clients` 声明，doctor 不从现存链接反推范围。
`astra-sol` 仅声明 `.agents`；这是客户端使用约定，其他扫描该目录的客户端仍可能发现它。
Codex 的 `.codex` 和 `.codex/agents` 保持真实目录，允许个人配置、认证状态及其他代理共存；
修改仓库不会自动更新已部署的角色副本。

预检要求调用目录和 HOME 中无 `.stowrc`，HOME 中无 `.stow-global-ignore`；其他包的自定义
忽略规则会提示人工核对。旧 Git、Ghostty、Neovim、Lazygit、Shuck 竞争入口须先人工迁移。
冲突保留原件，执行失败保留已完成修改；处理后重新预览再应用。新增包和更新角色的操作见
[配置维护](../README.md#新增与维护配置)，个人 Git 覆盖见[个人配置](../README.md#个人配置)。

## doctor 检查与证据

```sh
bash scripts/doctor.sh
bash scripts/doctor.sh --only zsh --only nvim --runtime
```

`--only` 可重复选择模块，`--verbose` 显示有界的原生错误输出。独立故障会累积报告，
不会在首个故障后停止；最终计数包含必要清理的结果。

| 状态 | 含义 |
| --- | --- |
| PASS | 有对应验证证据，范围以报告文字为准 |
| WARN | 可选组件缺失、能力未准备、偏离锁文件或存在个人覆盖 |
| FAIL | 部署、必要依赖、解析、受控运行或清理发生故障 |
| SKIP | 不适用、未初始化、缺少前提或当前方式无法验证 |

退出码 0 表示没有 FAIL，仍可能有 WARN/SKIP；1 表示健康检查失败；2 表示参数错误或检查器
无法完成。普通查询限时 8 秒，单个受控运行模块限时 30 秒。

### 默认检查

默认检查读取真实环境、受管链接和状态目录，权限检查不试写，也不读取历史内容。

- **依赖与环境**：检查实际选中的命令。Java/Javac 须来自版本一致的完整 JDK 21+，
  `JAVA_HOME`、PATH 和 `java.home` 须一致；Maven 须为稳定 3.9.x 并使用同一 JDK。
- **配置与缓存**：Git 检查生效配置及来源，Zsh 检查配置和工具生成的初始化脚本；
  Neovim 编译 Lua、解析 JSON 并检查本地插件与工具，不常规启动 Neovim 或联网补齐插件。
- **原生应用**：按可用性调用应用。Starship 使用实际选中的配置；Java/Maven、Atuin、
  Shuck 的查询使用临时环境或配置副本，Maven 跳过用户 RC。

### 受控运行与边界

`--runtime` 在临时 HOME 中使用配置和插件副本验证新会话，不自动安装或更新。
非默认布局、本机可执行覆盖、未知配置或缺少前提时，相应项目 SKIP。
这是应用级受控运行，不是操作系统沙箱；会话提前退出或进程清理失败均不能算成功。

| 模块 | 运行检查 | 主要限制 |
| --- | --- | --- |
| Zsh | Antidote 缓存、非交互/登录/PTY 会话、补全、按键与钩子 | 有 `local.zsh` 或 `local.zprofile` 时跳过运行，仍检查默认配置语法 |
| Neovim | 锁定插件、已有 LSP、格式化、Zsh 诊断和 parser | 关闭安装与更新；额外启动代码或配置目录链接会跳过运行，Stow 文件级链接可用；不获取 Taplo 远程 schema |
| Lazygit | 临时仓库中的真实 TUI、delta 渲染和编辑器参数 | 自定义 YAML、多配置层不作为仓库基线；编辑器本身由 Neovim 检查 |
| Vim | 配置发现、行号和状态目录 | 使用受管配置副本 |

新会话通过不代表已经运行的 Shell 或编辑器已加载新配置。Atuin 任意环境覆盖、Shuck 项目
配置、GUI 渲染、剪贴板及 SSH 客户端字体不由这些检查完整覆盖，须结合实际使用验收。

## bootstrap 准备完整环境

```sh
bash scripts/bootstrap.sh --dry-run --profile server
bash scripts/bootstrap.sh --apply --profile server
```

`server` 准备命令行环境与 terminfo 工具；`desktop` 额外准备 Ghostty、两套字体和 Linux
剪贴板工具。profile 控制软件与字体，配置包按平台选择。远程字体装在终端客户端，
Ghostty SSH 集成向远端提供终端定义。

### 预检与执行流程

除共同路径约定外，`NVIM_APPNAME` 须未设置或为 `nvim`。预检拒绝 `GIT_DIR`、
`GIT_WORK_TREE` 等改变 Git 仓库上下文的变量，即使值为空；完整清单见
[common.bash](bootstrap/common.bash)。Git 用户配置、鉴权和配置选择变量仍保留。

预览离线读取声明并探测真实 PATH 中的命令，不调用包管理器或下载资源；探针隔离
HOME/XDG、日志、临时文件和工作目录。Git/Stow 未达标时，部署预检延后到依赖安装完成。

| 阶段 | `--apply` 执行内容 |
| --- | --- |
| 软件与能力 | 准备平台软件及所需 Go/JDK/Maven，探测 npm、C、Java/Maven 能力 |
| 部署 | 运行 deploy 预检，再部署配置 |
| 插件与资源 | 准备 Zsh、Neovim 插件和工具；desktop 另准备桌面软件及字体 |
| 验收 | 复查工具，运行适用的 doctor 与受控运行检查，清理后汇总 |

macOS 首次安装可能需要完成 Command Line Tools 安装窗口后重跑。apt 操作和 Homebrew
初始安装器启动前先检查免密 sudo 权限，必要时在前台认证；不运行后台 sudo 保活。
长任务后认证可能过期，拒绝认证会停止当前阶段并保留已完成内容。

### 软件来源与复用

| 范围 | 复用条件与安装来源 |
| --- | --- |
| macOS 包 | Brewfile 补齐 Homebrew 管理的包，即使 PATH 中已有其他来源的命令；Bundle 使用 `--no-upgrade`，仅定向升级未达标工具 |
| Ubuntu 包 | 复用兼容命令，缺失时先用 apt，再按声明使用固定资源或后备资源；24.04 的 Ghostty 来自固定社区 deb，26.04 来自发行版包 |
| Go | 用 `GOTOOLCHAIN=local go version` 检查最低要求；否则从 [Go 官方列表](https://go.dev/dl/?mode=json)选最新稳定版，校验 SHA256，发布 `~/.local/bin/go`、`gofmt` |
| JDK | 优先 `JAVA_HOME`，否则查询 PATH 中的 `java.home`；完整且版本一致的 JDK 21+ 可复用，否则从 [Adoptium](https://api.adoptium.net/v3/assets/latest/25/hotspot)安装经 SHA256 校验的最新 Temurin JDK 25 GA |
| Maven | 复用使用所选 JDK 的稳定 3.9.x；否则从 [Maven Central](https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/maven-metadata.xml)选最新稳定 3.9.x，校验 SHA512，发布 `~/.local/bin/mvn` |

最低要求见 [requirements.tsv](bootstrap/requirements.tsv)，其他固定归档见
[releases.json](bootstrap/releases.json)。首次安装版本和必要依赖由各来源决定；
Go/JDK/Maven 达标后离线复用，不追逐新版本，也不列入 Brewfile 或 apt 清单。
归档先校验、暂存，再发布；损坏资源不会替换有效安装，非受管入口冲突会保留并报错。

受管 JDK 发布到 `~/.local/share/dotfiles-bootstrap/jdk/current`，Zsh 自动设置其
`JAVA_HOME` 和 PATH，登录时在 `brew shellenv` 后恢复优先级。需要其他 JDK 时，在
`local.zprofile` 同时调整两者。Maven 的隔离探针不读写个人 `~/.m2`。
Go 模块可按默认 `GOTOOLCHAIN=auto` 选择更新工具链；若个人配置禁用自动切换，须自行满足
Mason 模块的要求，bootstrap 不改写该设置。单独运行 Brewfile 只准备软件，不完成部署和验收。

### 插件与桌面资源

| 资源 | 准备规则 |
| --- | --- |
| Zsh | Antidote 下载缺失插件并更新必要缓存，不主动更新已有插件；清单未锁定提交，新机器使用下载时的上游版本 |
| Neovim | 用配置副本与 `lazy-lock.json` 恢复插件，不写回锁文件；Mason 按配置和 registry 解析工具，Treesitter 跟随锁定插件修订，补全资源也须准备成功 |
| 字体 | 按内部族名选择并保留许可，Sarasa Term SC 解包需要 7zz；macOS 用 Ghostty `+list-fonts --family=...` 精确验收 |

字体重跑会复用已可查询的族名。macOS 发布或复用受管字体后最多等待 30 秒供系统识别；
查询失败或超时会保留文件供重试，不能仅凭 Ghostty 默认等宽字体列表判断字体缺失。

### 日志、失败与重试

| 内容 | 默认路径 |
| --- | --- |
| 日志与并发锁 | `~/.local/state/dotfiles-bootstrap/` 下的 `run.*`、`lock/pid` |
| 校验过的下载缓存 | `~/.cache/dotfiles-bootstrap/` |
| 工具与入口 | `~/.local/share/dotfiles-bootstrap/<工具>/<版本>-<平台>/`、`~/.local/bin/` |
| 插件与工具 | `~/.local/share/antidote/`、`~/.cache/antidote/`、`~/.local/share/nvim/` |
| 字体 | Linux：`~/.local/share/fonts/dotfiles-bootstrap/`；macOS：`~/Library/Fonts/dotfiles-bootstrap/` |

长任务输出进入终端和日志。命令、日志转发或必要清理失败都会阻止总体成功；主体错误或
中断码优先保留，清理错误另行报告。中断会回收本次任务的进程组并释放锁；强制终止后，
先确认 `lock/pid` 对应进程已结束，再处理遗留锁。包管理器中断按其提示恢复。

失败不整体回滚。处理个人文件、命令入口或脏插件 checkout 等冲突后，重跑同一命令，
达标步骤会复用；成功时仍应阅读 doctor 的 WARN/SKIP。
Git 身份、登录 Shell 和 Atuin 历史属于[个人收尾操作](../README.md#个人配置)。

## multipass 创建和管理 Ubuntu 开发机

宿主入口负责实例生命周期、SSH 与状态记录；客户机从固定远程提交运行核心 server
bootstrap。项目目录为客户机的 `~/workspace`，不挂载宿主目录，私钥留在宿主机。

`create`、`provision` 和 `destroy` 默认预览，前两者应用时可能安装或升级宿主 Multipass；
`check` 查询状态，`ssh` 登录运行中的实例。宿主和客户机有独立的状态及输出约定，具体见：
[首次创建](../multipass/README.md#宿主机要求与首次创建)、
[日常使用](../multipass/README.md#日常使用)、
[配置与目录](../multipass/README.md#配置目录与状态)、
[失败恢复](../multipass/README.md#失败处理与恢复边界)。

## 维护与验证

### 修改入口与声明

公开 `.sh` 入口负责运行状态、阶段和 trap；被 source 的 Bash 文件只声明函数或数据。
Python 标准库负责文件操作、PTY 和 Multipass 编排，Lua/Zsh 负责对应运行时。
按职责维护下列位置，避免将安装要求、部署目标和诊断等级混为一份清单：

| 位置 | 维护内容 |
| --- | --- |
| [layout.bash](layout.bash) | Stow 包、默认目录、旧入口、Skill 客户端及角色副本 |
| [requirements.tsv](bootstrap/requirements.tsv)、[releases.json](bootstrap/releases.json) | 最低要求与固定归档；最低版本 `0` 表示先检查可执行文件 |
| [macOS](bootstrap/macos/)、[Ubuntu](bootstrap/ubuntu/) | 平台安装器、包清单及 desktop 增量 |
| [resources.py](bootstrap/resources.py) | 按需解析 Go/JDK/Maven、资源校验和发布 |
| [common.bash](bootstrap/common.bash)、[without_tty.py](bootstrap/without_tty.py) | 准备流程、探针、进程与终端隔离 |
| [doctor/](doctor/) | 配置检查、受控会话、超时与后代进程清理 |
| [multipass/](multipass/) | 宿主编排、客户机助手、SSH 发布与地址解析 |
| [VM 声明](../multipass/defaults.json)、[宿主安装声明](../multipass/host-releases.json)、[cloud-init](../multipass/cloud-init.yaml.tmpl) | 虚拟机资源和初始化，不参与 Stow 部署 |

平台清单遵循以下约定：Shuck 只信任指定 formula；Tree-sitter 命令使用 `tree-sitter-cli` 包。
Ubuntu 的 `apt_commands` 按 `包名|命令列表` 映射，空列表检查 dpkg 状态；`release_commands`
可提供多个命令，`apt_fallbacks` 仅在 apt 结果仍不兼容时使用，发行版后缀数组加载时须重置。
Homebrew 初始安装器的固定提交与校验值留在 macOS 安装器中，因为此时尚不能依赖 Python。

### 静态检查与验收

生产脚本的静态检查如下；回归套件选择、测试代码静态检查和额外验收见
[测试说明](../tests/README.md)。工具缺失应记录为未验证。

```sh
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

更新资源要核对来源、校验值、最低要求和平台清单；更新插件锁要验收真实插件 API、
Zsh/Neovim 会话及锁文件不被准备流程改写。离线替身只证明编排和失败处理，首次安装、
平台包管理器、桌面效果及真实 VM 须在相应环境验收。
