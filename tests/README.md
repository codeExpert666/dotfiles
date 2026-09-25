# 测试与验证

本目录验证脚本行为和应用配置。测试体系由 **5 套核心离线回归套件**（覆盖部署、诊断、安装编排、应用配置与 Multipass 编排替身）、**1 项已准备缓存集成模式** 以及 **1 套真实 Multipass 虚拟机在线验收套件** 组成。

所有离线套件均使用临时 HOME 与仓库副本，绝不向真实环境写入配置；生产行为约定见[脚本说明](../scripts/README.md)。除特别说明外，命令均在仓库根目录执行，也可从其他目录按绝对路径调用入口。

## 运行测试

### 运行前提与依赖

- **解释器**：公开入口与支持文件兼容 Bash 3.2+；Python 用例需要 Python 3.9+。
- **必要工具**：全量回归依赖 Git、GNU Stow（支持 `--no-folding`）、Zsh、Vim 以及常见 Unix 命令。
- **可选应用**：Neovim、Shuck、Lazygit/delta、Starship、Atuin、Ghostty 等应用未安装时明确标记为 `SKIP`；已安装但验证未通过则报告失败。
- **解释器传递**：调用入口所使用的 Bash 解释器会统一传递给各子套件及被测脚本。

### 运行命令

```sh
# 五套离线全量回归（指定系统或特定版本的 Bash 解释器）
bash tests/all.sh
# 或显式指定绝对路径：/opt/homebrew/bin/bash tests/all.sh

# 按改动单独运行对应套件
bash tests/deploy.sh
bash tests/doctor.sh
bash tests/bootstrap.sh
bash tests/config-loading.sh
bash tests/multipass.sh

# doctor、bootstrap、multipass 采用 Python unittest，接受用例选择与测试参数
bash tests/doctor.sh DoctorTests.test_multiple_independent_link_faults
bash tests/bootstrap.sh Publication.test_requirements_and_platform_manifests_agree
bash tests/multipass.sh Inputs.test_missing_ssh_key
```

[all.sh](all.sh) 按 `deploy → doctor → bootstrap → config-loading → multipass` 固定顺序执行，任一套失败即刻停止。聚合器自身不持有夹具或断言，捕获中断信号（HUP/INT/TERM）时会转交信号给当前运行套件并等待其完成安全清理。

## 选择套件

`tests/` 顶层的 `.sh` 为公开调用入口，同名子目录保存测试用例实现；[support/](support/) 提供跨套件复用的临时仓库副本、文件快照、进程组管控与终端 PTY 设施。

| 套件                                   | 驱动机制        | 适用改动范围                                    | 用例与关键辅助程序                                                           |
| -------------------------------------- | --------------- | ----------------------------------------------- | ---------------------------------------------------------------------------- |
| [deploy.sh](deploy.sh)                 | Bash Harness    | 部署布局、冲突保护、Stow 规则、Skill 与角色副本 | [deploy/](deploy/)、[harness.bash](support/harness.bash)                     |
| [doctor.sh](doctor.sh)                 | Python unittest | 健康状态诊断、只读边界、配置检查与受控运行      | [cases.py](doctor/cases.py)、[lazygit-fixture.py](doctor/lazygit-fixture.py) |
| [bootstrap.sh](bootstrap.sh)           | Python unittest | 安装编排、平台清单、资源下载发布与失败恢复      | [cases.py](bootstrap/cases.py)、[mock.py](bootstrap/mock.py)                 |
| [config-loading.sh](config-loading.sh) | Bash Harness    | 应用配置发现、加载顺序与终端交互行为            | [config-loading/](config-loading/)、[harness.bash](support/harness.bash)     |
| [multipass.sh](multipass.sh)           | Python unittest | 宿主机/客户机编排、身份安全、SSH 配置与退役清理 | [cases.py](multipass/cases.py)                                               |

以下按实际执行顺序说明各套件的验证职责与边界：

### deploy

使用真实 Git 和 GNU Stow 验证预览（`--dry-run`）、实际应用（`--apply`）、平台配置包选择、文件级链接、个人配置共存与部署幂等性。同时验证 Skill 源文件目录与多客户端入口暴露、Codex 角色普通副本复制及其他外部代理的共存。注入写入故障时，验证故障精准命中、原配置完好保留、中间半成品得到清理，并在故障排除后支持幂等重试。

### doctor

验证 PASS/WARN/FAIL/SKIP 的诊断判定逻辑、遇到独立故障时继续报告其他检查项的能力，以及绝不修改被测系统真实状态的只读边界。覆盖部署缺失、工具版本失配及 Java/Maven runtime 路径分歧。借助真实 PTY 终端与应用替身夹具检查 TUI 交互、编辑器交接与子进程清理；路径与权限故障必须报告失败，不得误判为健康。

### bootstrap

通过包管理器替身、本地预置归档和受控时钟，验证 profile 配置（server/desktop）、安装阶段顺序、sudo 权限请求、部署前置预检、日志与文件锁、资源复用与失败重试逻辑。针对 Go/JDK/Maven 验证版本选择、归档校验和、缓存损坏恢复及安装失败时保留旧版本；针对字体验证族名匹配与延迟发现；针对 Zsh 验证终端隔离及被中断时子进程树的安全回收。

### config-loading

先由 deploy 准备好已部署的配置夹具，再启动原生应用验证其真实发现并加载配置：包括 Git 别名与合并配置覆盖、Zsh 配置文件加载顺序、JDK/Maven 路径选择、代码格式化、TUI 界面渲染、编辑器传参及已安装工具的个性化设置。常规 Neovim 检查不发起联网插件安装；Ghostty 验证配置语法解析，不代表着色器图形渲染通过。本套件专注于应用实际加载行为，不重复验证部署链接结构，也不以 doctor 的静态判定替代应用自身断言。

### multipass

通过公开入口和命令/VM 替身验证宿主编排的完整状态机与安全约定，完全离线运行且不连接真实 Multipass：

- **输入与身份**：参数解析、镜像与资源分配、公钥与 agent 身份核验、提交 SHA 固定、SSH 信任关系；遇到同名冲突、脏仓库或锁被占用时安全保留现场。
- **失败与续跑**：元数据记录先于 launch 创建，持久化保存失败历史与实例 UUID；首次必要重启按 stop → `Stopped` → start → `Running` 核对状态，覆盖命令非零但目标已达成、中断续跑、旧收据边界、身份与 boot ID、cloud-init、helper 清理和共享超时预算；禁止借 provision 跳过首次启动。异常状态下 helper 清理和在线诊断不得隐式启动实例，未完成的清理保留待办标记。
- **退役与归属**：严禁凭旧回执同名重建；验证 destroy 预览、定向删除实例、宿主状态迁移至 `retired/` 以及共享 SSH 配置的安全保留。

## 验证范围与证据边界

离线回归套件通过替身与受控隔离提供了高频、确定的行为断言，但无法完全替代真实网络与原生系统行为。下表定义不同验证方式的证据边界：

| 验证方式           | 能提供的证据                                                                          | 不覆盖的范围                                                  |
| ------------------ | ------------------------------------------------------------------------------------- | ------------------------------------------------------------- |
| **常规离线回归**   | 真实 Git/Stow 行为、可用原生应用与 PTY 会话；安装与 VM 替身的流程编排、错误传播与回滚 | 真实系统软件包安装、在线插件下载、真实虚拟机网络与驱动        |
| **模拟平台分支**   | 跨平台分支选择逻辑、CLI 参数与系统清单解析规则                                        | 目标平台原生包管理器实际执行、首次系统级安装和桌面端 GUI 行为 |
| **已准备缓存集成** | 隔离环境中真实 Neovim/Zsh 插件 API 调用、资源加载与运行流程                           | 插件首次在线下载、网络编译与更新拉取                          |
| **真实 VM 验收**   | 指定提交在实际 Mac 宿主与真实 Ubuntu 虚拟机上的全流程运行证据                         | 其他 Git 提交、未经测试的系统版本或未来上游变更               |

> [!NOTE]
> `SKIP` 结果表示当前环境缺少提供该证据的前提条件，测试框架会记录跳过原因。在维护和评估中，不得将替身模拟或静态解析通过等同于原生实机验收。

## 额外验收

### 已准备插件缓存的集成

在本地拥有完整且匹配当前配置的插件缓存时，可为现有套件启用准离线集成模式：

```sh
DOTFILES_TEST_PREPARED_HOME=/path/to/prepared-home bash tests/all.sh
```

测试用例会将缓存复制到临时夹具中并校验来源完整性。Neovim 准备步骤会主动屏蔽 Git/curl/wget 外部下载，通过缓存解析器验证首次安装目录创建、重跑幂等性与加载失败恢复；Zsh 与 Neovim 其余调用均执行真实插件 API。该模式不是独立的第六套测试，而是对离线套件的增强集成模式。

### 真实 Multipass 虚拟机验收

[multipass-live.sh](multipass-live.sh) 由 [live.py](multipass/live.py) 驱动，独立于 `all.sh`，用于在真实硬件上做双版本闭环验收。它要求 Apple Silicon Mac、已加入 ssh-agent 的专用密钥、远程可拉取的完整提交 SHA 以及外网连接：

```sh
# 查看参数与说明
bash tests/multipass-live.sh --help

# 执行双镜像（Ubuntu 24.04、26.04）实实验收
bash tests/multipass-live.sh --ref '<40位提交SHA>' \
  --ssh-public-key "$HOME/.ssh/id_ed25519_multipass.pub"
```

该入口依次验收 Ubuntu 24.04 与 26.04：首次正常 stop 后受控中断创建，再以同一声明续跑，独立核验管理状态、身份、boot ID、cloud-init 与重启标志，并完成 bootstrap/doctor、重新配置及日常 stop/start 的 SSH 检查。每个版本结束后仅清理本轮登记且能再次核实归属的实例；状态不稳时只读采集并保留实例。详细运行参数、测试报告位置及清理机制见 [Multipass 维护与验收](../multipass/README.md#维护与验收)。
首次启动无需重启时，仍独立验证客户机状态、身份和重启标志并继续其余检查，将中断恢复场景明确记为 `skipped`。`summary.json` 的 `reboot_coverage` 区分已通过、已跳过和未到达的场景，并保存本轮宿主编排器的 `runtime_sha256`；检查双镜像覆盖时须同时核对这些字段。
外部下载临时失败时，可用 `--only-image 24.04` 或 `--only-image 26.04` 与新的报告目录只重试失败镜像；这份单镜像报告不能单独代表双镜像全部通过。

## 失败定位与维护

### 失败排查与进程清理

- **日志与输出**：Bash 套件用例失败时会输出失败阶段与对应的命令执行日志；Python unittest 用例默认缓冲（buffer）标准输出与错误，仅在断言失败时展开相关上下文；终端交互故障会保留转义后的 PTY 输出末尾。排查时应先依据断言确认所属职责，区分究竟是代码产品故障、测试夹具问题还是缺失运行前提。
- **清理保证**：测试夹具遵循“先停止测试产生的进程组，再删除临时目录”的顺序。入口负责安装全局 trap 捕获退出信号，Python 执行器独立管理测试会话、后代进程及执行超时，不依赖被测的生产运行器来验证自身的资源清理。

### 测试代码静态检查

修改测试套件本身后，需在本地执行静态检查；生产脚本的检查规范参见[脚本维护与验证](../scripts/README.md#维护与验证)。若本地缺失检查工具，应在提交说明中如实记录未验证项：

```sh
# 1. Shell 脚本语法与规范检查
files=(tests/*.sh tests/deploy/*.sh tests/deploy/*.bash tests/support/*.bash)
for file in "${files[@]}"; do bash -n "$file" || exit; done
shellcheck -x "${files[@]}"
shfmt -d -ci -sr "${files[@]}"

# 2. Python 测试用例与夹具 AST 语法树解析
python3 -B - <<'PY'
import ast
from pathlib import Path
for path in Path('tests').rglob('*.py'):
    ast.parse(path.read_text(), filename=str(path))
PY

# 3. Lua 测试夹具代码格式校验
stylua --check --indent-type Spaces --indent-width 2 tests/config-loading/nvim.lua tests/bootstrap/*.lua
```
