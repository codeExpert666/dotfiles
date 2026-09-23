# Multipass Ubuntu 开发机

## 定位与工作流程

Multipass 开发机是本仓库的可选扩展。在受支持的已有 macOS 或 Ubuntu 机器上部署终端环境，
直接使用 [bootstrap、deploy 和 doctor](../../scripts/README.md) 即可；需要独立的
Ubuntu 开发机时，才在 Apple Silicon Mac 宿主机上运行 `scripts/multipass.sh`。
`environments/` 存放虚拟机声明和 cloud-init 模板，不是 Stow 包，不部署到 HOME。

扩展在宿主机创建 Ubuntu arm64 客户机并建立 SSH 连接，在客户机拉取指定的远程提交，
以普通 `ubuntu` 用户先预览、再执行 `bootstrap.sh --profile server`，复用核心的依赖准备、
配置部署与诊断。客户机保留 Multipass 的管理用户和通道；日常通过独立 SSH 密钥登录，
项目目录位于客户机自己的 `~/workspace`，不挂载宿主目录。

## 宿主机要求与首次创建

宿主机须为 macOS 15+ Apple Silicon，以普通用户运行，提供 Bash 3.2+、Python 3.9+、
Git、curl、OpenSSH 和可访问官方软件源与公共 HTTPS 仓库的网络。入口还使用 macOS
的 `pkgutil`、`sudo` 和 `/usr/bin/nc`。Multipass 缺失或版本低于
[宿主安装声明](host-releases.json)时，应用模式会安装或升级；官方 pkg 安装可能要求
前台 `sudo` 认证。宿主 HOME 须为已存在的真实绝对目录，四个 XDG 变量须未设置、为空，
或指向该 HOME 下的默认目录；`~/.ssh` 须为已存在的真实目录。

创建前，由操作者在宿主机准备一把专用、带密码短语的 SSH 密钥，并把私钥解锁加入
ssh-agent。脚本只接收现有普通文件形式的绝对公钥路径，且公钥须能在 agent 中找到
对应身份；私钥不会复制到客户机，只有公钥写入 cloud-init。以下命令在**宿主机**执行，
如已有专用密钥，直接使用现有公钥路径：

```sh
ssh-keygen -t ed25519 -a 100 -f ~/.ssh/id_ed25519_multipass
ssh-add ~/.ssh/id_ed25519_multipass
```

以下命令在**宿主机仓库根目录**执行。先从远程 `main` 取得完整提交 SHA；将
`<40位SHA>` 替换为远程可获取的 40 位小写 SHA。创建会固定该提交，之后不会自动跟随
`main`。首次创建默认使用 [defaults.json](defaults.json) 声明的 Ubuntu 24.04 LTS、
实例名 `ubuntu-dev`、4 CPU、8 GiB 内存和 40 GiB 客户机磁盘。

```sh
git ls-remote https://github.com/codeExpert666/dotfiles.git refs/heads/main
bash scripts/multipass.sh create --dry-run --ref '<40位SHA>' \
  --ssh-public-key "$HOME/.ssh/id_ed25519_multipass.pub"
bash scripts/multipass.sh create --apply --ref '<40位SHA>' \
  --ssh-public-key "$HOME/.ssh/id_ed25519_multipass.pub"
```

选择另一版镜像或实例名时，在创建命令增加 `--image 26.04 --name ubuntu-dev-2604`。
`--cpus`、`--memory`、`--disk` 可覆盖资源；内存和磁盘接受整数 `M`/`G` 单位。
创建前会检查宿主磁盘余量。预览不会安装软件或创建实例，应用模式不会覆盖已有的
同名非受管实例。完整参数见 `bash scripts/multipass.sh create --help`。

## 日常使用

以下命令在**宿主机仓库根目录**执行，示例实例名须按实际名称替换：

```sh
bash scripts/multipass.sh check --name ubuntu-dev
bash scripts/multipass.sh check --name ubuntu-dev --runtime
bash scripts/multipass.sh ssh --name ubuntu-dev
ssh ubuntu-dev

# 重新配置现有实例；更新提交时替换新 SHA
bash scripts/multipass.sh provision --dry-run --name ubuntu-dev
bash scripts/multipass.sh provision --apply --name ubuntu-dev
bash scripts/multipass.sh provision --apply --name ubuntu-dev --ref '<新40位SHA>'
```

`check` 向 stdout 输出 JSON 状态；`--runtime` 还在客户机运行受控诊断。
创建或重新配置的计划、进度和错误写入 stderr，阶段输出也写入宿主日志。
`ssh` 进入普通 SSH 会话；可用 `ssh -L 8080:localhost:8080 ubuntu-dev` 转发本地端口。
实例停止、启动和删除使用原生 `multipass stop/start/delete <name>`。

`create` 重跑须保留原创建声明；更新 Git 提交用 `provision --ref`。重新配置前会检查
客户机仓库 origin、已跟踪和未跟踪改动；发现用户修改时保留内容并停止。`check`
只查询状态，不启动或修复实例。

## 配置、目录与状态

除表中标注的客户机行外，以下 `~` 均指**宿主机用户 HOME**。仓库外的宿主配置文件可在
`~/.config/dotfiles-multipass/config.json` 提供参数，命令行参数优先：

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

`ssh_public_key` 路径和 Git 身份示例须替换为自己的值。已有实例的镜像、资源及仓库来源
仍由创建声明决定。未显式设置 `git_identity` 时，不读取宿主 Git 身份；设置时姓名和邮箱
须同时提供。扩展在客户机 bootstrap 成功后将 `ubuntu` 的登录 Shell 设为 Zsh，并按显式
配置将 Git 身份写入客户机个人 `~/.config/git/config`，不修改受管仓库文件。
直接在已有机器运行核心 bootstrap 不会自动设置登录 Shell 或 Git 身份，详见
[个人配置](../../README.md#个人配置)。

| 所属机器              | 路径                                                                                                                   | 内容                                               |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| 宿主机仓库            | [defaults.json](defaults.json)、[host-releases.json](host-releases.json)、[cloud-init.yaml.tmpl](cloud-init.yaml.tmpl) | 默认资源、Multipass 安装声明和客户机初始化模板     |
| 宿主机 HOME           | `~/.config/dotfiles-multipass/config.json`                                                                             | 可选个人参数配置                                   |
| 宿主机 HOME           | `~/.local/state/dotfiles-multipass/instances/<name>/`                                                                  | 创建声明、`receipt.json`、阶段日志、user-data 和锁 |
| 宿主机 HOME           | `~/.cache/dotfiles-multipass/`                                                                                         | 已校验的官方 pkg 缓存                              |
| 宿主机 HOME           | `~/.ssh/config`、`~/.ssh/dotfiles-multipass/`                                                                          | 受管 Include、实例 Host 片段和专用 known_hosts     |
| 宿主机 HOME           | `~/.local/share/dotfiles-multipass/ssh-proxy.py`                                                                       | 哈希管理的实例地址解析助手副本                     |
| 客户机 `/home/ubuntu` | `~/.dotfiles`、`~/workspace`                                                                                           | 固定提交的仓库和独立项目目录                       |
| 客户机 `/home/ubuntu` | `~/.config/git/config`、`~/.local/state/dotfiles-bootstrap/`                                                           | 个人 Git 入口和核心 bootstrap 日志、锁             |

宿主 SSH 配置只在 `~/.ssh/config` 顶部加入一次受管 Include；首次编辑前备份已有内容。
实例 Host 片段固定用户 `ubuntu`、严格主机密钥校验和专用 known_hosts，
ProxyCommand 在连接时查询当前 IPv4。状态目录默认 0700、记录默认 0600；
日志与回执记录目标及实际提交、镜像哈希、Multipass 版本、阶段结果和 doctor 计数，
不记录私钥或密码短语。

客户机首次系统更新只在创建阶段执行；cloud-init 要求重启时，宿主最多自动重启一次。
bootstrap 由普通 `ubuntu` 用户运行；它先探测免密 sudo 命令权限，确需密码时才在前台
刷新凭据。核心安装细节、日志和失败重试规则见[脚本说明](../../scripts/README.md)。

## 失败处理与恢复边界

失败时先查看宿主实例状态目录的 `receipt.json` 中失败阶段和对应
`logs/<阶段>.log`，排除原因后重跑相同命令。创建失败会保留实例，不自动删除；
launch 超时后先核对实际实例和 cloud-init 状态。若实例已停止，先运行
`multipass start <name>`，再检查或重试。

在修改 SSH、更新客户机仓库前，入口检查客户机 bootstrap 锁；仓库操作和 bootstrap
本身也会复查。客户机里仍运行的 bootstrap、PID 缺失或无法核实的遗留锁须先人工核实，
不要直接启动第二份安装。宿主锁冲突也须等待持有者结束或查明遗留状态。
主机公钥不符时先排查实例是否被替换，不要关闭 `StrictHostKeyChecking` 或清空
known_hosts。Multipass 安装或升级后的版本及服务探测会在 120 秒窗口内重试命令失败；
版本不达标或驱动等配置错误会立即停止。

**已创建的实例被删除后，同名自动重建暂不支持。** 宿主回执和 SSH 信任仍保留，
同名 `create` 会在 launch 前报告冲突及状态路径。使用新的 `--name` 创建下一台开发机，
不要通过复用旧回执或清空 known_hosts 绕过实例身份检查。

## 维护与验收

[Multipass 离线测试](../../tests/README.md#multipass)使用命令与 VM 替身，
不要求真实 Multipass。需要验证真实客户机时，先准备前述专用公钥和远程可获取的提交，
再在**宿主机仓库根目录**显式运行双版本验收；它不会由 `tests/all.sh` 启动：

```sh
bash tests/multipass-live.sh --help
bash tests/multipass-live.sh --ref '<40位SHA>' \
  --ssh-public-key "$HOME/.ssh/id_ed25519_multipass.pub"
```

验收依次创建 Ubuntu 24.04 和 26.04 实例，测试创建、受控诊断、重复配置及原生重启后的
SSH，并在每版结束时定向清理。报告默认保存在宿主机
`~/.local/state/dotfiles-multipass/reports/<时间戳>/`；也可用 `--report-dir <绝对路径>`
指定尚不存在的报告目录。验收只删除自己登记且创建标识匹配的实例与受管 SSH 文件，
报告保留；个人密钥和宿主 Multipass 保留，由操作者管理。缺少本次创建记录、宿主状态
或客户机标记不符、实例锁占用时，不删除实例。预先存在的同名实例或状态目录会被拒绝。
验收结果仅适用于运行时的提交、宿主和客户机环境；后续提交需重新验收确认。

高级自动化调用可在全新实例名上使用
`create --creation-record <绝对路径>`：记录文件须尚不存在，父目录须已存在；
创建命令在 launch 前独占写入名称和 UUID，预览不会写入。验收用它在后续阶段失败时
核对本次实例归属，再决定是否清理。
