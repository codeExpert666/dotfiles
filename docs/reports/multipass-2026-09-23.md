# Multipass 开发机实施与真实验收（2026-09-23）

## 实施范围

在 `codex/multipass-dev-machine` 分支实现了 [方案](../plan/multipass-dev-machine.md)：
宿主按需准备 Multipass，创建 Ubuntu 24.04/26.04 arm64 实例，固定远程 Git 提交，
通过 cloud-init 和已有 server bootstrap 配置开发环境，发布独立 SSH 入口，并提供
预览、重跑、检查与显式双版本验收。用户仓库保持在客户机磁盘的
`/home/ubuntu/.dotfiles`，项目目录为 `/home/ubuntu/workspace`。

验收开始时远程 `main` 为 `bd28c79ab0da6b1818243dab18f9c256bb57f674`。
该版本在真实 Ubuntu 首次安装中暴露了包名、Node 归档入口等问题；修复提交推送到
本分支后，最终两版验收均固定在
`fa9b55af8bfc20488eb2178be071edc0c326033e`，而非声称原始 `main` 已通过。

## 宿主与客户机事实

宿主为 macOS 15.8（24H23）Apple Silicon arm64，24 GiB 内存，验收前约 410 GiB
可用磁盘。已安装的 Multipass 官方 pkg 客户端和 daemon 均为 `1.16.4+mac`，
驱动为 QEMU；这次真实验收复用了该安装，没有触发安装或升级。

| 项目 | Ubuntu 24.04 LTS | Ubuntu 26.04 LTS |
| --- | --- | --- |
| 实例 | `dotfiles-test-2404-20260923131251` | `dotfiles-test-2604-20260923131251` |
| 架构、内核 | aarch64、`6.8.0-142-generic` | aarch64、`7.0.0-34-generic` |
| 镜像 SHA-256 | `7b682958a67ff5de068e36de6af8b75fa645d296af5a70d6500527f6a33781db` | `8dc812bc6356d0abf825d8029f25f1b71f02cb103e1d0cc5c17fbb2572322972` |
| 资源 | 4 CPU、8 GiB、40 GiB | 4 CPU、8 GiB、40 GiB |
| 首次创建至重启验收 | 483.2 秒 | 419.4 秒 |
| Git / Zsh | 2.43.0 / 5.9 | 2.53.0 / 5.9 |
| Node / npm / Go | 24.21.0 / 11.19.0 / 1.27.1 | 24.21.0 / 11.19.0 / 1.27.1 |
| Java / Maven / Neovim | Temurin 25.0.4.1 / 3.9.16 / 0.12.5 | Temurin 25.0.4.1 / 3.9.16 / 0.12.5 |

两版 `cloud-init schema --system` 均通过，cloud-init 最终状态为 `done`、错误列表为空；
首次系统更新均触发自动重启，前后 boot ID 不同，重启后无待处理重启标志。
客户机 `ubuntu` 用户的 HOME 为 `/home/ubuntu`，登录 Shell 为 `/usr/bin/zsh`，
时区为 `Asia/Shanghai`。bootstrap 实际完成 Java 编译、运行及离线 Maven 验证，
Neovim 锁定插件、Mason 工具和 parser 准备成功。
两版客户机还直接通过了 `LANG=C.UTF-8` 与 UTF-8 charmap 检查。

每版的首次 bootstrap、独立 `check --runtime`、重复 `provision` 三次 doctor 均为
`PASS=207 WARN=0 FAIL=0 SKIP=7`，首次和重复 provision 的回执也保存了这四项计数。
7 个 SKIP 分别是 Taplo 离线 schema catalog、
Atuin 环境覆盖、Shuck 项目配置，以及四个尚未初始化但父目录可写的状态目录；
没有 WARN。重复 provision 保留了 workspace 哨兵文件，SSH 主配置的受管 Include
仍只有一个。原生 stop/start 后普通 SSH 再次连接成功，`JAVA_HOME` 指向可执行的
完整 JDK，`java` 与 `$JAVA_HOME/bin/java` 相同，Maven 可从 `~/.local/bin` 找到。

## 回归与边界

`bash tests/all.sh` 在 macOS 原生 `/bin/bash` 3.2.57 下通过：deploy 246、doctor
69、bootstrap 102、config-loading 14、Multipass 26 项。doctor 中 2 项、bootstrap
中 1 项需要未提供的预制缓存，按条件跳过；具体模拟平台分支由套件显式标注。
`bash -n`、`shellcheck -x`、`shfmt -d -ci -sr`、`zsh -n`、Shuck、Python AST 和
`git diff --check` 也通过。

真实验收证明了当前 macOS 安装来源的复用路径、两版客户机首次安装、重启、普通 SSH、
固定提交、重复配置和清理。缺失/旧版 Multipass 的 Homebrew 与官方 pkg 升级、摘要失败、
SSH 冲突等分支由离线命令替身测试，未在宿主上故意破坏现有安装。
GUI、剪贴板和本机自定义覆盖不属于 server profile 的真实验收范围；Taplo 的在线
schema catalog 未在离线 doctor 中启动。固定 Git 提交不固定随时间变化的 apt
仓库、Go/JDK/Maven 最新稳定版或 Mason registry 解析结果，实际版本已记录在回执。

## 修复与清理

真实验收促成了针对性修复：Ubuntu 不存在独立 `lesspipe` apt 包；Node 归档有顶层
`bin/npm` 和嵌套文件同名；zoxide 0.9 用别名而 0.10 用函数暴露 `z/zi`；
独立 doctor 和版本回执需读取受管 Zsh 的 JDK 环境；系统更新重启后需重新传入
`/tmp` 助手。Ubuntu 26.04 的 sudo-rs 对 `sudo -v` 仍要求交互认证，即使
`sudo -n true` 已免密可用，因此 bootstrap 先测试免密命令，再在必要时调用
`sudo -v`。cloud-init 不再额外写入 sudoers 规则。

最终两个验收实例均由归属标记核对后定向 `delete --purge`。最终 `multipass list`
为空；实例状态目录和受管 Host/known_hosts 条目为空，原有 GitHub SSH 配置保留。
本次临时专用 Ed25519 身份已从 ssh-agent 移除，私钥、公钥、passphrase 与 askpass
临时文件已删除，报告副本中的 cloud-init user-data 公钥文件也已清除；用户原有
GitHub 身份仍在 agent 中。两版运行日志、cloud-init 状态、
bootstrap、回执和清理证明保留在本机：

`/Users/xinnz/.local/state/dotfiles-multipass/reports/2026-09-23/final-complete/`

该目录的 `summary.json` 为 `failures: []`，各版本 `cleanup-ok.txt` 记录定向清理成功。
此前失败尝试的证据保留在同日其他报告目录，不作为最终通过的结果。
