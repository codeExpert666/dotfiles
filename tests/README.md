# 测试与验证

本目录验证脚本行为和应用配置。离线套件使用临时 HOME 与仓库副本，不向真实 HOME
部署配置；生产行为约定见[脚本说明](../scripts/README.md)。以下命令在仓库根目录执行，
也可从其他目录按绝对路径调用入口。

## 运行测试

```sh
# 五套离线回归；也可用 /path/to/bash 指定解释器
bash tests/all.sh

# 按改动选择单套
bash tests/deploy.sh
bash tests/doctor.sh
bash tests/bootstrap.sh
bash tests/config-loading.sh
bash tests/multipass.sh

# doctor、bootstrap、multipass 接受 unittest 用例选择参数
bash tests/doctor.sh DoctorTests.test_multiple_independent_link_faults
bash tests/bootstrap.sh Publication.test_requirements_and_platform_manifests_agree
```

[all.sh](all.sh) 按 deploy → doctor → bootstrap → config-loading → multipass 顺序运行，
任一套失败即停止；指定的 Bash 会传给套件及适用的被测入口。聚合器不持有夹具或断言，
中断时将信号交给当前套件并等待清理。

Bash 入口和支持文件兼容 Bash 3.2，Python 需要 3.9+。完整离线回归还需 Git、GNU Stow、
Zsh、Vim 以及常见 Unix 命令。Neovim、Shuck、Lazygit/delta、Starship、Atuin、Ghostty 等
可选应用未安装时明确 SKIP；已安装但验证失败则报告失败。

## 选择套件

| 套件 | 适用改动 | 用例与辅助程序 |
| --- | --- | --- |
| [deploy.sh](deploy.sh) | 部署布局、冲突保护、Stow 包、Skill 与角色副本 | [deploy/](deploy/)、[共享 Bash 夹具](support/harness.bash) |
| [doctor.sh](doctor.sh) | 健康状态、只读边界、配置检查和受控运行 | [cases.py](doctor/cases.py)、[Lazygit 夹具](doctor/lazygit-fixture.py) |
| [bootstrap.sh](bootstrap.sh) | 安装编排、平台清单、资源发布和失败恢复 | [cases.py](bootstrap/cases.py)、[命令替身](bootstrap/mock.py) |
| [config-loading.sh](config-loading.sh) | 应用配置、发现路径和启动顺序 | [原生应用辅助程序](config-loading/) |
| [multipass.sh](multipass.sh) | 宿主/客户机编排、身份、SSH 与清理 | [cases.py](multipass/cases.py) |

根目录 `.sh` 是公开入口，套件子目录保存实现；[support/](support/) 提供跨套件的仓库副本、
快照、进程和终端设施。以下按实际运行顺序说明验证职责。

### deploy

使用真实 Git/Stow 验证预览、应用、平台包选择、文件级链接、个人配置共存与幂等性。
Skill 的源目录、客户端入口和排除规则，以及 Codex 普通副本及其他代理共存均在此验证。
写入故障注入还须证明故障确实命中、原件保留、半成品得到处理，排除故障后可重试。

### doctor

验证 PASS/WARN/FAIL/SKIP 的判定、独立故障继续报告和不修改真实状态的边界，包括部署
缺失、工具版本及 Java/Maven runtime 分歧。真实 PTY 与应用夹具检查 TUI、编辑器交接和
进程清理；路径与权限故障不能被当作健康成功。

### bootstrap

通过包管理器替身、本地归档和受控时钟验证 profile、阶段顺序、sudo、部署预检、
日志与锁、资源复用和重试。Go/JDK/Maven 覆盖版本选择、完整性、校验和、缓存损坏及
失败保留旧安装；字体覆盖族名与延迟发现，Zsh 覆盖终端隔离和中断后的子进程回收。

### config-loading

由 deploy 准备输入，再让原生应用证明自己发现并使用配置：Git 覆盖、Zsh 加载顺序、
JDK/Maven 选择、格式化、TUI 渲染、编辑器参数及可用应用的设置。常规 Neovim 检查不安装
插件；Ghostty 配置解析不等于着色器等图形效果通过。这里验证应用行为，不重复完整部署
布局，也不以 doctor 的健康判定替代应用内部断言。

### multipass

通过公开入口和命令/VM 替身验证三组约定，不连接真实 Multipass：

- **输入与身份**：帮助、参数、安装来源、VM 资源、公钥与 agent、固定提交、SSH 信任；
  名称冲突、脏仓库和锁占用时保留现场。
- **失败与续跑**：创建记录先于 launch，保留失败历史和实例身份；重启后核验 boot ID，
  不重复重启，不用 provision 跳过首次启动；异常状态不借 helper 清理隐式启动实例，
  清理失败记录待办且不能算完整成功。
- **退役与归属**：禁止凭旧回执同名重建；验证 destroy 预览、定向删除、宿主状态收尾和
  共享 SSH 保留。真实验收的清理决策也须同时核对本次创建记录、宿主状态与客户机标记。

## 验证范围

| 验证方式 | 能提供的证据 | 不覆盖的范围 |
| --- | --- | --- |
| 常规离线回归 | 真实 Git/Stow、可用原生应用与 PTY；安装和 VM 替身的编排、错误传播 | 系统软件安装、插件下载、真实 VM |
| 模拟平台分支 | 平台选择、参数与清单规则 | 对应平台的原生包管理器、首次安装和桌面行为 |
| 已准备缓存集成 | 隔离副本中的真实插件 API、准备和运行流程 | 首次在线下载 |
| 真实 VM 验收 | 指定提交在实际宿主与客户机上的运行结果 | 其他提交、平台或未来上游变化 |

SKIP 表示没有该项证据，应记录原因；不能把替身或解析通过写成原生运行验收。

## 额外验收

### 已准备插件缓存的集成

已有完整且匹配当前配置的插件缓存时，可启用现有套件的额外模式：

```sh
DOTFILES_TEST_PREPARED_HOME=/path/to/prepared-home bash tests/all.sh
```

用例复制缓存并确认来源未变。Neovim 准备拒绝 Git/curl/wget 下载，在安装步骤提供缓存
解析器，验证首次创建安装目录、重跑和加载失败；下载与编译由缓存替代，其余调用真实
插件 API。这不是第六套离线测试。

### 真实 Multipass 虚拟机

[multipass-live.sh](multipass-live.sh) 由 [live.py](multipass/live.py) 执行，不由 `all.sh` 启动。
它需要 Apple Silicon Mac、已加入 agent 的专用 SSH 身份、远程可获取的完整提交和网络，
依次验收 Ubuntu 24.04、26.04。运行命令、报告位置和归属清理规则见
[Multipass 维护与验收](../multipass/README.md#维护与验收)。

## 失败定位与维护

Bash 用例显示失败阶段和命令日志；Python 用例缓冲正常输出、失败时展开相关输出；
终端故障保留转义后的输出尾部。先确认断言对应的职责，再区分产品故障、夹具问题和缺失前提。

夹具先停止自己创建的进程，再删除临时文件。入口负责 trap，Python 执行器独立管理会话、
后代和超时，不借用生产运行器验证自身清理，也不追踪主动脱离进程树的任意守护程序。

测试代码的静态检查如下；生产脚本检查见[脚本维护](../scripts/README.md#维护与验证)。
缺失工具应记录为未验证：

```sh
files=(tests/*.sh tests/deploy/*.sh tests/deploy/*.bash tests/support/*.bash)
for file in "${files[@]}"; do bash -n "$file" || exit; done
shellcheck -x "${files[@]}"
shfmt -d -ci -sr "${files[@]}"
python3 -B - <<'PY'
import ast
from pathlib import Path
for path in Path('tests').rglob('*.py'):
    ast.parse(path.read_text(), filename=str(path))
PY
stylua --check --indent-type Spaces --indent-width 2 tests/config-loading/nvim.lua tests/bootstrap/*.lua
```
