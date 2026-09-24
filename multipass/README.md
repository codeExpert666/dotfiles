# Multipass Ubuntu 开发机

本扩展在 Apple Silicon Mac 上创建独立的 Ubuntu arm64 虚拟机、配置 SSH，并从指定远程
提交运行客户机内的 `bootstrap.sh --profile server`。在已有机器上部署终端环境，直接使用
[核心脚本](../scripts/README.md)即可。

客户机用户为 `ubuntu`，HOME 固定为 `/home/ubuntu`，项目目录固定为 `/home/ubuntu/workspace`，
不挂载宿主目录，也不支持自定义这两个路径。`multipass/` 保存 VM 声明和 cloud-init 模板，
不作为 Stow 包部署。

## 宿主机要求与首次创建

以普通用户在 macOS 15+ Apple Silicon 上运行，准备 Bash 3.2+、Python 3.9+、Git、curl、
OpenSSH 和可访问官方软件源及公共 HTTPS 仓库的网络。入口还使用 macOS 的 `pkgutil`、
`sudo`、`/usr/bin/nc`。HOME 与 `~/.ssh` 须为已存在的真实目录，HOME 为绝对路径；
四个 XDG 变量须未设置、为空或直接指向 HOME 下的默认目录。

Multipass 缺失或低于[要求版本](host-releases.json)时，应用模式会安装或升级官方 pkg，
可能需要前台 sudo 认证。预览不安装软件、不创建实例。

### 准备 SSH 身份

在宿主机准备专用、带密码短语的密钥，并解锁加入 ssh-agent；已有专用密钥可直接复用。
脚本接收普通文件形式的绝对公钥路径，要求 agent 中有对应身份；私钥留在宿主机。

```sh
ssh-keygen -t ed25519 -a 100 -f ~/.ssh/id_ed25519_multipass
ssh-add ~/.ssh/id_ed25519_multipass
```

### 选择提交并创建

以下命令在宿主机仓库根目录执行。将 `<40位SHA>` 替换为远程可获取的完整小写提交 SHA；
创建后固定该提交，不自动跟随 `main`。

```sh
git ls-remote https://github.com/codeExpert666/dotfiles.git refs/heads/main
bash scripts/multipass.sh create --dry-run --ref '<40位SHA>' \
  --ssh-public-key "$HOME/.ssh/id_ed25519_multipass.pub"
bash scripts/multipass.sh create --apply --ref '<40位SHA>' \
  --ssh-public-key "$HOME/.ssh/id_ed25519_multipass.pub"
```

[默认值](defaults.json)为 `ubuntu-dev`、Ubuntu 24.04 LTS、4 CPU、8 GiB 内存和 40 GiB 磁盘。
选择另一版镜像或名称时，在预览和应用命令中同时增加 `--image 26.04 --name ubuntu-dev-2604`；
资源可用 `--cpus`、`--memory`、`--disk` 调整，内存和磁盘接受整数 `M`/`G`。
应用前检查宿主磁盘余量，不接管同名非受管实例；完整选项见 `create --help`。

创建依次完成系统更新与首次启动验收、必要的重启、身份与 SSH 配置、仓库检出和核心
bootstrap。首次更新较慢时，可按[进度与记录](#进度与记录)定位当前步骤。
bootstrap 由普通 `ubuntu` 用户运行，按需认证 sudo；成功后将登录 Shell 设为 Zsh。

## 日常使用

以下示例均在宿主机执行，将 `ubuntu-dev` 换成实际实例名；脚本命令在仓库根目录执行。

### 连接与检查

```sh
ssh ubuntu-dev
bash scripts/multipass.sh check --name ubuntu-dev
bash scripts/multipass.sh check --name ubuntu-dev --runtime
```

`bash scripts/multipass.sh ssh --name ubuntu-dev` 也会进入普通 SSH 会话；端口转发可用
`ssh -L 8080:localhost:8080 ubuntu-dev`。`check` 只查询，不启动或修复实例；
`--runtime` 在客户机运行[受控诊断](../scripts/README.md#doctor-检查与证据)。

### 重新配置或更新提交

创建已完成的实例使用 `provision`。省略 `--ref` 会复用保存的目标提交；更新时将新 SHA
同时传给预览和应用命令：

```sh
bash scripts/multipass.sh provision --dry-run --name ubuntu-dev --ref '<新40位SHA>'
bash scripts/multipass.sh provision --apply --name ubuntu-dev --ref '<新40位SHA>'
```

重新配置前会检查仓库 origin、已跟踪与未跟踪改动，以及 bootstrap 锁；冲突时保留现场。
创建中断后的续跑规则见[失败恢复](#失败处理与恢复边界)。

### 停止与退役

管理通道正常、来宾任务空闲时，使用原生 `multipass stop <name>` 和 `multipass start <name>`。
`Starting`、`Restarting` 等异常状态先按下文排查。

永久结束受管实例使用 destroy。应用会删除客户机数据，先保存需要的内容并核对预览：

```sh
bash scripts/multipass.sh destroy --dry-run --name ubuntu-dev
bash scripts/multipass.sh destroy --apply --name ubuntu-dev
```

它核对身份后仅删除指定实例、清理对应 SSH 文件，将声明、回执和日志移至 `retired/`。
停止的实例会先启动以核对身份；已标记 `Deleted` 的实例须先 `multipass recover <name>`。
若已手动 purge，destroy 只收尾宿主状态；清理失败可在排除原因后重跑。
它不安装或升级 Multipass，不运行无实例名的 purge，也不删除用户密钥；受管文件有改动时
保留冲突文件和状态。

## 配置、目录与状态

参数按命令行、`~/.config/dotfiles-multipass/config.json`、[默认值](defaults.json)的顺序解析。
已有实例的镜像、资源和仓库来源以创建声明为准，更新提交使用 `provision --ref`。

```json
{
  "name": "ubuntu-dev",
  "ssh_public_key": "/Users/your-name/.ssh/id_ed25519_multipass.pub",
  "git_identity": {
    "name": "Your Name",
    "email": "you@example.com"
  }
}
```

公钥路径和身份示例须替换。`git_identity` 可省略；提供时姓名与邮箱须同时填写，
bootstrap 成功后写入客户机个人 Git 入口，不改受管仓库文件。不提供时不读取宿主 Git 身份。
直接运行核心 bootstrap 不设置登录 Shell 或 Git 身份，见[个人配置](../README.md#个人配置)。

| 所属位置 | 路径 | 内容 |
| --- | --- | --- |
| 宿主 HOME | `~/.config/dotfiles-multipass/config.json` | 个人参数 |
| 宿主 HOME | `~/.local/state/dotfiles-multipass/instances/<name>/` | `declaration.json`、`receipt.json`、user-data、日志与锁 |
| 宿主 HOME | `~/.local/state/dotfiles-multipass/retired/<name>-<uuid>/` | 退役后保留的声明、回执、日志与记录 |
| 宿主 HOME | `~/.cache/dotfiles-multipass/` | 校验过的官方 pkg |
| 宿主 HOME | `~/.ssh/config`、`~/.ssh/dotfiles-multipass/` | 受管 Include、Host 片段与专用 known_hosts |
| 宿主 HOME | `~/.local/share/dotfiles-multipass/ssh-proxy.py` | 哈希管理的地址解析助手 |
| 客户机 HOME | `~/.dotfiles`、`~/workspace` | 固定提交的仓库和项目目录 |
| 客户机 HOME | `~/.config/git/config`、`~/.local/state/dotfiles-bootstrap/` | 个人 Git 入口及核心 bootstrap 日志、锁 |

宿主 SSH config 顶部只加入一次受管 Include，首次修改前备份原文；Host 片段固定 `ubuntu`、
严格主机密钥检查和专用 known_hosts，连接时查询当前 IPv4。状态目录默认 0700，记录默认
0600，不记录私钥或密码短语。主机公钥变化时须核实身份，不能清空信任记录或关闭严格检查。

## 进度与记录

计划、进度和错误写入 stderr；`check` 的状态摘要写入 stdout JSON，`ssh` 进入交互会话。
`STAGE` 表示主阶段，`STEP` 表示具体动作；`RUN`、`OK`、`SKIP`、`FAIL` 表示开始、完成、
复用和失败。`STEP WAIT` 每 30 秒显示等待时间与命令上限，不代表 apt 下载或 cloud-init
内部进度。`CHILD BEGIN/END` 之间直接转发客户机 bootstrap 输出。

预检只显示在终端；进入应用阶段后，实例目录中的记录负责保留执行证据：

| 记录 | 用途 |
| --- | --- |
| `receipt.json` 的 `stages`、`current_step` | 各阶段最近结果和执行位置 |
| `attempts`、`failed_at` | 每次尝试的完整阶段结果与失败位置 |
| `logs/<尝试 ID>/<阶段>.log` | 对应尝试的命令输出；旧版平铺日志仍保留原位 |
| 回执中的提交、身份、镜像、版本和 doctor 计数 | 核对目标与实际运行环境 |

客户机 bootstrap 的安装规则和日志解释见[核心脚本说明](../scripts/README.md#bootstrap-准备完整环境)。

## 失败处理与恢复边界

### 先定位失败和实例状态

失败保留实例及已完成修改。先看回执和对应日志，再用 `multipass list`、`multipass info <name>`
核对管理状态；宿主 daemon 日志位于 `/Library/Logs/Multipass/multipassd.log`。
launch 超时还须核对客户机实际启动与 cloud-init 结果。

| 状态 | 下一步 |
| --- | --- |
| `Running` | 核对身份、cloud-init 和任务状态后再续跑 |
| `Stopped` / `Suspended` | `multipass start <name>`，等管理连接恢复后再续跑 |
| `Starting` / `Restarting` / `Unknown` | 排查管理连接，不当作停止状态；`exec`、`shell` 可能隐式请求 start，不能作为纯查询 |
| `Deleted` | 先 `multipass recover <name>` 恢复，再检查 |
| 已缺失但仍有创建记录 | 保留旧声明与 SSH 信任；用 destroy 预览并退役旧身份后再创建，或换新名称，不自动同名重建 |

### 管理恢复后选择续跑命令

创建未完成时，保留声明、原提交和公钥，以原参数重跑 `create`；它复用实例、重新验收未完成
阶段并继续配置，不重新指定仅供新实例使用的 `--creation-record`。首次启动阶段尚未成功时，
`provision` 会拒绝执行；该阶段通过后，重新配置或更新提交使用 `provision`。

首次系统更新只在创建阶段执行。自动重启最多请求一次，限制跨 create 重试保留：
`reboot_from` 记录请求前的 boot ID，只有身份一致、boot ID 已变化且无待重启标志时才写入
`reboot_to`。`cloud-init/restart` 超时不代表来宾没有重启；续跑核验原请求，不发起第二次重启。

管理不可用时，helper 清理只查询状态，不调用可能隐式启动实例的 exec；失败路径与原因
保存为 `pending_helper_cleanup`，恢复后续跑并成功清理才移除。清理失败不覆盖原故障，也
不能算完整成功；原失败 attempt 和日志始终保留。

### SSH 可达但管理状态异常

正常 stop/start 适合健康的管理状态，Multipass 1.16.4 的普通关机路径会拒绝 `Restarting`。
确需人工恢复时按以下顺序处理：

1. 保存声明、回执和日志，通过可信 SSH 核对创建 UUID、machine-id、cloud instance-id，
   确认 apt/dpkg 与 bootstrap 空闲，并确认来宾工作可中断。
2. 正常关闭来宾，确认 QEMU 退出且 Multipass 显示 `Stopped` 后再 start。
3. 重验实例身份和管理连接；仍不一致时保留现场，进一步排查 daemon，避免循环重试。

脚本不强制关机、不自动重启宿主 daemon，也不停止其他实例；只有显式 `destroy --apply`
才永久删除核对过身份的受管实例。

### 其他失败的处理

- **锁或内容冲突**：宿主锁、来宾 bootstrap 锁及仓库改动须先核实。PID 缺失或无法确认的
  遗留锁不能直接忽略，SSH 文件被修改时也保留现场，不并行启动第二份安装。
- **就绪失败**：宿主安装后的版本与服务探测可重试 120 秒，版本不达标或驱动配置错误立即停止；
  launch 后暂时不可达的 SSH 连接也最多等待 120 秒，cloud-init 自身失败不会按连接故障重试。

## 维护与验收

默认资源在 [defaults.json](defaults.json)，宿主安装来源在 [host-releases.json](host-releases.json)，
初始化内容在 [cloud-init.yaml.tmpl](cloud-init.yaml.tmpl)。编排代码位于
[scripts/multipass/](../scripts/multipass/)，[离线测试](../tests/README.md#multipass)验证命令和 VM 替身。

真实双版本验收须另行显式运行，不由 `tests/all.sh` 启动。准备前述 SSH 身份和远程提交后，
在宿主机仓库根目录执行：

```sh
bash tests/multipass-live.sh --help
bash tests/multipass-live.sh --ref '<40位SHA>' \
  --ssh-public-key "$HOME/.ssh/id_ed25519_multipass.pub"
```

它依次验收 Ubuntu 24.04、26.04 的创建、受控诊断、重复配置及原生重启后的 SSH，每版结束
时定向清理。报告默认保留在 `~/.local/state/dotfiles-multipass/reports/<时间戳>/`，也可用
`--report-dir <绝对路径>` 指定尚不存在的目录；结论仅适用于运行时的提交和环境。

清理必须同时匹配本次创建记录、宿主状态和客户机标记；锁占用或身份不符时保留实例。
预先存在的同名实例或状态目录会被拒绝，个人密钥与宿主 Multipass 不删除。
高级自动化可在全新名称上使用 `create --creation-record <绝对路径>`：文件须不存在、父目录
须已存在，应用时在 launch 前独占记录名称与 UUID，供失败后的归属核对；预览不写入。
