# 测试与验证

`tests/` 验证脚本行为及应用配置。离线套件使用临时 HOME 和仓库副本，不在真实 HOME
部署配置；以下相对路径命令均在仓库根目录执行。生产脚本的行为约定见
[脚本说明](../scripts/README.md)。

## 入口与选择

[all.sh](all.sh) 按表中顺序运行五套独立离线测试；任一套失败即停止。聚合器不持有
夹具或断言，中断时把信号交给当前套件并等待清理。每套也可单独运行。

| 入口                                   | 验证重点                               | 适合运行的改动                    |
| -------------------------------------- | -------------------------------------- | --------------------------------- |
| [deploy.sh](deploy.sh)                 | 目标布局、冲突保留、幂等性和失败清理   | 部署规则、Stow 包及角色副本       |
| [doctor.sh](doctor.sh)                 | 诊断状态、独立故障、只读边界和受控运行 | 检查器、状态解释及运行探针        |
| [bootstrap.sh](bootstrap.sh)           | 平台清单、安装编排、资源发布与重试     | 环境准备、软件或插件来源          |
| [config-loading.sh](config-loading.sh) | 原生应用发现并加载受管配置后的行为     | 应用配置与启动顺序                |
| [multipass.sh](multipass.sh)           | 宿主和客户机编排、身份保护与清理归属   | Multipass 扩展                    |
| [multipass-live.sh](multipass-live.sh) | 真实 macOS 宿主与两版 Ubuntu 客户机    | 显式真实 VM 验收；不属于 `all.sh` |

### 运行方式

```sh
# 五套离线测试
bash tests/all.sh

# 按需单独运行，顺序与 all.sh 相同
bash tests/deploy.sh
bash tests/doctor.sh
bash tests/bootstrap.sh
bash tests/config-loading.sh
bash tests/multipass.sh

# Python unittest 套件可选择单个用例
bash tests/doctor.sh DoctorTests.test_multiple_independent_link_faults
bash tests/bootstrap.sh Publication.test_requirements_and_platform_manifests_agree
```

入口可从其他工作目录按绝对路径调用。用 `/path/to/bash tests/all.sh` 可指定聚合器
和套件入口的 Bash；调用核心 Bash 脚本的套件也会传递该解释器。
doctor、bootstrap 和 multipass 三套 Python 测试接受标准 `unittest` 用例选择参数。
Bash 入口和支持文件兼容 Bash 3.2，Python 需要 3.9+。

完整离线回归需要 Git、GNU Stow、Zsh、Vim、Python 3，以及 `ps`、`stat` 等常见
Unix 命令。未安装的可选应用（Neovim、Shuck、Lazygit/delta、Starship、Atuin、
Ghostty）会明确标为 `SKIP`；已安装却无法完成验证则报告失败。

### 结果能证明什么

常规离线套件不安装系统软件或下载插件。deploy 使用宿主的 Git/Stow，
doctor 和 config-loading 在可用时运行真实应用及 PTY 会话；bootstrap 以包管理器
替身和本地归档验证编排，multipass 以宿主命令和 VM 替身验证保护边界。
模拟 macOS 或 Ubuntu 分支只能证明对应决策和失败传播，不能代替目标平台的原生
包管理器、首次在线安装、图形效果或真实 VM 验收。可选缓存集成和真实 VM 入口见
[额外验收](#额外验收)。

## 代码导航

| 位置                                                                                                                                           | 职责                                     |
| ---------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------- |
| [all.sh](all.sh)、五套套件入口、[multipass-live.sh](multipass-live.sh)                                                                         | 离线聚合、独立套件和显式真实 VM 验收入口 |
| [deploy/support.bash](deploy/support.bash)、[mock-command.sh](deploy/mock-command.sh)、[git-write-failure.bash](deploy/git-write-failure.bash) | 部署夹具、命令替身与目标写入故障注入     |
| [doctor/cases.py](doctor/cases.py)、[lazygit-fixture.py](doctor/lazygit-fixture.py)                                                            | 诊断用例和交互夹具                       |
| [bootstrap/cases.py](bootstrap/cases.py)、[mock.py](bootstrap/mock.py)                                                                         | 安装编排、资源发布与命令替身             |
| [config-loading/lazygit.py](config-loading/lazygit.py)、[nvim.lua](config-loading/nvim.lua)                                                    | 原生应用会话和应用内部断言               |
| [multipass/cases.py](multipass/cases.py)、[live.py](multipass/live.py)                                                                         | 离线宿主/VM 用例和显式真实验收执行器     |
| [support/harness.bash](support/harness.bash)、[harness.py](support/harness.py)                                                                 | 跨套件仓库副本、文件快照、进程与终端设施 |

根目录 `.sh` 是公开测试入口，套件目录中的 Python、Lua 和 Bash 文件是内部辅助程序。
测试设施不依赖生产脚本的运行器来管理自身进程；入口负责 trap，Python 执行器负责
命令会话、后代记录和限时回收。失败诊断见[文末](#失败定位与静态检查)。

## 五套离线回归

以下顺序与 [all.sh](all.sh) 的实际执行顺序一致。各套件只对自己的职责作断言；
跨套件复用夹具不等于重复验证同一行为。

### deploy

- 用临时 HOME 和真实 Git/Stow 检查默认预览、应用、平台包选择、文件级链接、
  路径含空格、个人配置共存、重复执行和无关目录保持不变；冲突必须保留原件。
- Skill 用例核对源目录、Claude 别名和资源的一致性，确认包内 `src`/元数据不被部署，
  原有 `~/src` 在部署和卸载时保持不变。Codex 用例核对 `astra-sol` 无 Claude 别名、
  `sol_worker.toml` 的普通副本、其他代理及个人配置共存；卸载 Stow 技能不会删除
  独立副本。
- 在目标子进程中注入真实 Git 写入故障，断言故障命中、部分写入清理、错误报告及
  排除故障后的重试；角色复制失败同样检查未发布半成品。模拟 Darwin 分支不算
  原生 macOS 验证。

### doctor

- 核对 PASS/WARN/FAIL/SKIP、独立故障继续报告、只读保证和受控运行。Skill 断链、
  源目录、入口和排除规则缺失，以及 Codex 角色内容差异或符号链接，都须得到对应
  诊断。Java/Javac/Maven 的版本、`JAVA_HOME` 与实际 runtime 分歧也在这里验证。
- 用真实 PTY 和已安装的 delta 检查后台能力探针；Lazygit 夹具覆盖编辑器交接丢失
  退出键及 TUI 忽略渲染器错误。进程用例区分组长退出后的后代、短暂 `EPERM`
  与持续权限错误，不把未清理的子进程当作成功。
- Atuin 用例使用命令替身核对日志路径从临时 HOME 映射到真实 HOME：引号、空格、
  特殊字符和自定义绝对路径保持正确，同时真实 HOME 与仓库不被修改。

### bootstrap

- 用包管理器替身和本地归档核对 profile、平台清单、阶段顺序、sudo 认证、
  部署前预检、插件准备、日志/锁，以及失败保留和重跑。macOS 替身覆盖固定
  Homebrew 路径，避免误调用宿主包管理器；Homebrew formula 信任和 Tree-sitter
  CLI 与库的区别均有断言。
- Go 用例覆盖实际版本探测、缺失或过旧时在 Neovim 前准备、达标后离线复用，
  以及稳定版排序、平台资产、无效元数据、SHA256 校验、入口发布和失败保留旧安装。
  JDK/Maven 用例覆盖仅 JRE、Java/Javac 不一致、无效 `JAVA_HOME`、完整 JDK 21/24
  复用、低于 21 时安装 JDK 25、稳定 Maven 3.9.x、SHA256/SHA512 校验、损坏缓存、
  runtime 一致性和重试。
- 字体用例区分精确族名与样式名、Ghostty 默认列表遗漏、Linux fontconfig 别名；
  虚拟时钟验证 macOS 发布后延迟发现、超时保留及重跑恢复。Zsh 准备在真实伪终端
  复现 Antidote 的终端操作，核对缓存、前台终端和超时/中断后的子进程清理。

### config-loading

- 使用 deploy 准备输入，再由原生应用检查 Git 个人覆盖、Zsh 加载顺序和受管
  JDK/Maven 命令选择、Shuck 格式化、Lazygit 的 delta 渲染与编辑器参数、
  Neovim/Vim 配置，以及 Atuin、Starship 等可用应用的实际设置。Neovim 常规检查
  配置发现、Lua 语法和离线选项，不启动插件安装。
- Ghostty 在 macOS 上同时从 PATH 和系统、用户的 `Applications/Ghostty.app` 查找，
  以 `+validate-config --config-file=...` 验证配置解析；着色器等图形效果仍需
  桌面环境验收。本套验证应用是否发现并使用配置，不重复验证完整链接布局；
  doctor 能识别健康状态也不能代替这里的应用断言。

### multipass

- 通过公开的 `multipass.sh` 入口检查顶层及各子命令帮助、选项说明和错误用法提示。
- 无网络、无真实 Multipass 实例；替身核对 VM 参数、宿主安装来源和复用、
  公钥与 agent 身份、SSH 信任、固定提交、脏仓库及 bootstrap 锁保护。
  服务就绪用虚拟时钟覆盖 daemon 延迟、旧 CLI 回退、超时和不可重试的配置错误。
- 重启超时后续跑须保留创建身份和失败历史，核验新 boot ID，禁止重复重启或通过
  `provision` 跳过未完成的首次启动。异常管理状态须准确报告，helper 清理不得隐式
  启动实例；清理失败保留待办，恢复后再清除。这些场景使用替身，不是真实 VM 重启验收。
- 创建记录须先于 launch；失败保留可重试状态，已删除的受管实例不得凭旧回执
  自动重建。`destroy` 预览、已手动清除实例后的状态收尾、客户机身份核对、指定实例
  删除和共享 SSH 配置保留均在离线替身中验证。名称冲突、并发竞争和锁占用不得改写
  既有实例或 SSH 文件。
  验收清理只在本次创建记录、宿主状态和客户机标记相符时进行；
  替身证明这些决策，真实宿主和客户机另行验收。

## 额外验收

### 已准备插件缓存的集成

已有完整且与当前配置匹配的插件缓存时，可显式启用额外的准备及运行检查；
这是现有套件的可选模式，不是第六套离线测试：

```sh
DOTFILES_TEST_PREPARED_HOME=/path/to/prepared-home bash tests/all.sh
```

用例复制缓存并确认来源未变。Neovim 准备拒绝 Git/curl/wget 下载，在安装步骤才
提供缓存解析器，核对首次创建安装目录、重跑及加载失败诊断；除下载和编译由缓存
代替，其余流程执行真实插件 API。该模式不能证明首次在线安装。

### 真实 Multipass 虚拟机

[multipass-live.sh](multipass-live.sh) 须单独显式运行，不由 `all.sh` 启动。
它要求受支持的 Apple Silicon Mac、已解锁加入 ssh-agent 的专用身份、远程可获取的
完整提交 SHA 和实际网络；依次验收 Ubuntu 24.04、26.04 的创建、诊断、重配及 SSH。
先运行 `bash tests/multipass-live.sh --help`；具体命令、实例归属核对和定向清理边界
见[Multipass 开发机说明](../multipass/README.md#维护与验收)。
验收结论只适用于运行时的提交、宿主和客户机环境。

## 失败定位与静态检查

Bash 用例失败时显示用例、阶段和命令日志；Python 用例缓冲正常输出，失败时展示
相关命令输出。终端失败还会显示经过转义的输出尾部，临时目录删除后仍可查看。
测试先停止自己创建的进程，再删除夹具；应用调用有时限，交互调用限制输出量。
这些设施不追踪主动脱离进程树的任意守护程序。

测试代码的静态检查在仓库根目录执行；生产脚本检查见
[脚本说明](../scripts/README.md#维护与验证)。工具缺失须记录为未验证，不能算通过：

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
