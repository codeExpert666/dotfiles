# Multipass Ubuntu 开发机

这个入口在 Apple Silicon Mac 上创建 Ubuntu arm64 命令行开发机，默认 24.04 LTS、
`ubuntu-dev`、4 CPU、8 GiB 内存和 40 GiB 客户机磁盘。也可选择 26.04 LTS。
客户机保留 Multipass 的 `ubuntu` 管理用户和通道；日常登录使用独立 SSH 公钥，
项目文件保存在客户机的 `/home/ubuntu/workspace`，不挂载宿主目录。

## 首次创建

需要 macOS 15+、Python 3.9+、Git、curl、OpenSSH，以及可访问官方软件源的网络。
入口只在缺失或过旧时安装/升级 Multipass；安装官方 pkg 可能要求前台 `sudo` 认证。
用一把专用、带密码短语的 SSH 密钥，先把私钥解锁加入宿主 ssh-agent；脚本只接收公钥：

```sh
ssh-keygen -t ed25519 -a 100 -f ~/.ssh/id_ed25519_multipass
ssh-add ~/.ssh/id_ed25519_multipass
```

从远程 main 查询完整提交 SHA，然后预览、创建。示例中的 `<40位SHA>` 必须替换为
实际远程可获取的完整小写 commit SHA；运行后不会自动跟随 main。

```sh
git ls-remote https://github.com/codeExpert666/dotfiles.git refs/heads/main
bash scripts/multipass.sh create --dry-run --ref <40位SHA> \
  --ssh-public-key "$HOME/.ssh/id_ed25519_multipass.pub"
bash scripts/multipass.sh create --apply --ref <40位SHA> \
  --ssh-public-key "$HOME/.ssh/id_ed25519_multipass.pub"
```

选另一个发行版或实例名时，创建命令可增加 `--image 26.04 --name ubuntu-dev-2604`。
`--cpus`、`--memory`、`--disk` 可覆盖资源；内存和磁盘接受 `M`/`G` 整数单位。
创建前预检磁盘余量。入口不会替现有同名、非受管实例写入配置。

## 重跑、检查和登录

```sh
bash scripts/multipass.sh provision --dry-run --name ubuntu-dev
bash scripts/multipass.sh provision --apply --name ubuntu-dev
bash scripts/multipass.sh provision --apply --name ubuntu-dev --ref <新40位SHA>
bash scripts/multipass.sh check --name ubuntu-dev
bash scripts/multipass.sh check --name ubuntu-dev --runtime
bash scripts/multipass.sh ssh --name ubuntu-dev
ssh ubuntu-dev
```

`create` 重跑仅接受原声明；更新 Git 提交使用 `provision --ref`。它会检查仓库 origin、
所有已跟踪和未跟踪改动，再决定是否切换提交。发现用户修改时保留原内容并停止。
失败的阶段可以在排除原因后重跑；`check` 只查询状态，不启动或修复实例。
停止、启动和删除仍使用原生 `multipass stop/start/delete`。

SSH 配置在 `~/.ssh/config` 顶部只加入一次受管 Include，现有内容保持原样并在首次
编辑前备份。实例片段将专用公钥路径交给 ssh-agent，固定 `ubuntu` 和严格主机密钥检查；
ProxyCommand 每次连接都查询实例当前 IPv4。SSH 密钥不复制到客户机，只有公钥进入
cloud-init。要转发本地端口，可运行 `ssh -L 8080:localhost:8080 ubuntu-dev`。

## 本机配置与状态

默认值在 [defaults.json](defaults.json)，最低 Multipass 版本及官方 pkg 摘要在
[host-releases.json](host-releases.json)。仓库之外可建
`~/.config/dotfiles-multipass/config.json`：

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

命令行覆盖本机配置；已有实例的镜像、资源和仓库来源保持创建记录的值。
不指定 `git_identity` 时，不读取宿主 Git 身份。两项身份须同时设置，写入客户机
`~/.config/git/config` 的个人部分，不写入受管仓库文件。

| 路径 | 内容 |
| --- | --- |
| `~/.local/state/dotfiles-multipass/instances/<name>/` | 声明、阶段回执、日志、user-data 和锁 |
| `~/.cache/dotfiles-multipass/` | 已校验的官方 pkg 缓存 |
| `~/.local/share/dotfiles-multipass/ssh-proxy.py` | 哈希管理的地址解析助手副本 |
| `~/.ssh/dotfiles-multipass/` | 实例 Host 片段和专用 known_hosts |

状态目录默认 0700，记录默认 0600。日志与回执记录目标/实际提交、镜像哈希、
Multipass 版本和阶段结果，不记录私钥或密码短语。首次系统更新只在创建阶段执行；
cloud-init 要求重启时由宿主最多自动重启一次。Multipass 镜像默认允许 `ubuntu`
免密执行管理命令；bootstrap 先验证这一权限，需密码时才在前台刷新 sudo 凭据。
`bootstrap` 以普通 `ubuntu` 用户运行，
先预览再应用；成功后才将其登录 Shell 改为 Zsh。

## 失败和验收

阅读 `receipt.json` 中的失败阶段和对应 `logs/<阶段>.log`，修复原因后用相同命令
重跑。创建失败会保留实例，不自动删除；launch 超时后先核对实际实例和 cloud-init
状态。客户机中仍在运行的 bootstrap 或遗留锁需要核实，不能直接开启第二份安装。
实例停止时先执行 `multipass start <name>`。主机公钥不符时先排查实例是否被替换，
不要关闭 `StrictHostKeyChecking` 或直接清空 known_hosts。

离线回归是 `bash tests/multipass.sh`，完整离线回归是 `bash tests/all.sh`。
在准备好公钥与远程提交后，显式运行 [真实验收入口](../../tests/multipass-live.sh)
依次测试 24.04 和 26.04。验收入口只定向清理自己登记且创建标识匹配的实例，
报告保留在指定目录；个人密钥及宿主 Multipass 均保留，由操作者自行管理。

设计依据和行为边界见 [实施方案](../../docs/plan/multipass-dev-machine.md)。
