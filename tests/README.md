# 测试

本目录验证脚本行为和应用配置，运行时使用临时 HOME 与仓库副本。根目录的 `.sh` 是公开
入口；Python、Lua 和 Bash 辅助程序放在对应套件目录中，不作为日常运行入口。

| 入口                                   | 验证范围                                               | 运行时机             |
| -------------------------------------- | ------------------------------------------------------ | -------------------- |
| [all.sh](all.sh)                       | 按顺序运行下列四套，任一失败立即停止                   | 完整回归、跨套件调整 |
| [deploy.sh](deploy.sh)                 | 链接布局、平台包选择、冲突、幂等性、错误报告和清理     | 调整部署行为         |
| [doctor.sh](doctor.sh)                 | 诊断的状态和输出、独立故障、只读保证、受控运行及清理   | 调整诊断行为         |
| [bootstrap.sh](bootstrap.sh)           | 安装来源和清单、阶段顺序、失败重试、资源发布、插件准备 | 调整环境准备行为     |
| [config-loading.sh](config-loading.sh) | 应用实际发现配置，并加载出预期选项或行为               | 调整应用配置         |

`all.sh` 是全量运行的便利命令，不持有夹具或断言；中断时把信号转交当前套件并等待清理。
四套测试可以独立运行。
共享 Skill 在 deploy 中验证源文件与 Claude 别名的一致性、资源可读性、重复部署及冲突
保留，以及包内 `src` 和元数据不被部署、已有 `~/src` 在部署和卸载时保持不变。在 doctor
中验证断链、正文缺失或变成文件链接、包内入口指向错误 Skill，以及源目录或排除规则缺失
的诊断。测试只需复制完整的 `skills` 包，保留目录软链接和包内 `.agents`；这些检查
不需要 AI 客户端或 API Key，实际客户端发现仍需本机验收。
Codex 专用工作流还验证 `astra-sol` 没有 Claude 别名、`sol_worker.toml` 的普通副本部署、
原文件冲突保留、个人配置和其他代理共存，以及 doctor 对声明的源目录和必需入口缺失、
代理内容差异及符号链接的诊断，并覆盖复制失败清理和重试。`codex` 源目录须与 `skills`
一起复制进测试夹具；Stow 卸载技能不会删除独立的角色副本。
`config-loading` 保留独立的应用断言：例如 Git 的个人覆盖、Zsh 的加载顺序、Shuck 的格式化
结果、Lazygit 的 delta 渲染结果及编辑器参数。它调用部署脚本来准备输入，不重复验证完整
链接布局。`doctor` 则检查诊断程序能否正确识别健康或故障状态；诊断通过不代替上述应用断言。
doctor 用真实 PTY 和已安装的 delta 验证后台能力探针；Lazygit 夹具模拟编辑器交接丢失首个
退出键，以及 TUI 忽略渲染器错误，原生 Lazygit 会话另行验证。进程清理用例覆盖组长退出后
的后代回收、退出进程组暂时报 `EPERM` 后消失，以及真实权限错误不得被忽略。
Atuin 日志路径报告用命令替身验证临时 HOME 到真实 HOME 的转换，覆盖带引号的输出、路径
中的空格和特殊字符，以及自定义绝对路径保持原样；同时检查真实 HOME 和仓库未被修改。

## 运行

在仓库根目录执行：

```sh
bash tests/all.sh
bash tests/config-loading.sh
bash tests/doctor.sh DoctorTests.test_multiple_independent_link_faults
bash tests/bootstrap.sh Publication.test_requirements_and_platform_manifests_agree
```

入口也可以从任意工作目录按绝对路径调用。用 `/path/to/bash tests/all.sh` 指定 Bash，
各套件会把所选解释器传给被测脚本；两个 Python 套件接受标准 `unittest` 用例选择参数。
Bash 入口和支持文件保持 Bash 3.2 语法，Python 需要 3.9+。

完整回归需要 Git、GNU Stow、Zsh、Vim、Python 3，以及 `ps`、`stat` 等常见 Unix 命令。
未安装的可选应用（Neovim、Shuck、Lazygit/delta、Starship、Atuin、Ghostty）会明确标为
`SKIP`。已安装但无法完成验证的应用会报错。Ghostty 检查仅验证配置解析，图形效果仍需
在对应桌面环境查看。
macOS 的 Ghostty 检查同时查找 PATH 和系统、用户的 `Applications/Ghostty.app`，避免
应用已安装却因缺少 PATH 入口而跳过；校验使用 `+validate-config --config-file=...`。

常规回归不安装系统软件或下载插件，使用包管理器替身和本地归档。macOS 模拟同时替换
副本中的固定 Homebrew 路径，以免误调用宿主包管理器；模拟分支不代表原生 macOS 验证。
字体用例模拟 Ghostty 默认列表遗漏、按族名查询可用的情况，覆盖安装后验收、重跑复用、
精确族名与样式名区分、查询失败后的独立诊断，以及 Linux fontconfig 别名匹配。
macOS 首次发布后的延迟发现、发现超时保留文件及重跑恢复使用虚拟时钟验证；原生命令
错误直接失败，Linux 缓存刷新不会进入 macOS 的等待流程。
Homebrew 替身检查第三方 formula 的显式信任，并区分 Tree-sitter 库与 CLI；用例覆盖
信任失败后重跑、仅有库时补装 CLI，以及 CLI 版本不兼容时的定向升级。
Go 用例覆盖 `go version` 探测、缺少或版本过旧时在 Neovim 准备前安装官方最新版、达标
时不解析版本，以及失败停止和重跑复用。官方元数据与本地归档夹具覆盖稳定版排序、平台
选择、无效元数据、下载校验、入口发布及失败时保留旧安装；这些用例不访问真实下载服务。
JDK/Maven 用例同样使用本地元数据和小型归档：覆盖缺失、旧版、仅 JRE、Java/Javac 不一致、
无效 `JAVA_HOME`、完整 JDK 21/24 的离线复用、低于 21 时安装 JDK 25、稳定版筛选、
SHA256/SHA512 校验、损坏缓存不发布、失败重试及离线复用。
能力探针还检查实际 PATH 选择、Java 编译执行和隔离的离线 Maven validate。doctor 夹具覆盖
完整 JDK、PATH/JAVA_HOME/java.home 分歧、Maven 预发布版及 runtime 分歧；配置加载套件确认
新非交互和登录 Zsh 选择受管 JDK 全部命令与 `~/.local/bin/mvn`。这些替身证明编排和失败
传播，不代替真实官方归档、原生 JVM/Maven 或目标平台首次下载验收。
Zsh 准备用例在真实伪终端中调用原生 Zsh，以嵌套进程的 `read -d` 复现 Antidote 的终端
操作，并验证缓存发布、前台终端保留，以及后台任务超时和 Ctrl-C 后的子进程清理。
Neovim 的常规配置检查涵盖配置发现、语法和离线选项，不启动插件安装。

已有完整且匹配当前配置的插件缓存时，可显式启用额外的准备和运行集成：

```sh
DOTFILES_TEST_PREPARED_HOME=/path/to/prepared-home bash tests/all.sh
```

这些用例复制缓存并核对来源未变；Neovim 准备用例拒绝 Git/curl/wget 下载，并延迟到安装
步骤才放入缓存解析器，覆盖首次创建安装目录、重跑和加载失败的具体诊断。下载和编译由
预编译缓存代替，其余流程执行真实插件 API；缓存集成不能代替首次在线安装验收。

## 内部组织与失败诊断

- `deploy/support.bash` 与 `deploy/mock-command.sh`：部署专用夹具、命令替身和断言。
  `deploy/git-write-failure.bash` 仅在 Git 写入失败用例中通过 `BASH_ENV` 加载，先写入部分
  配置，再在目标写入子 Shell 中用 `ulimit -f 0` 触发真实失败；不限制初始化时 Bash 3.2
  的 here-string 临时文件写入。用例核对注入命中、失败阶段、清理及解除故障后的重试。
- `doctor/cases.py` 与 `doctor/lazygit-fixture.py`：诊断用例、Lazygit 交互夹具，
  以及诊断执行器和共享进程设施的回归。
- `bootstrap/cases.py` 与 `bootstrap/mock.py`：安装编排、资源和准备用例，以及命令替身。
- `config-loading/lazygit.py` 与 `config-loading/nvim.lua`：原生应用会话及应用内部断言。
- `support/harness.bash` 与 `support/harness.py`：跨套件的夹具、快照、进程清理和终端设施。

测试设施不导入生产脚本的运行器来管理自身进程。Bash 入口负责 trap 和夹具清理；Python
执行器拥有命令会话，等待时记录后代，并在正常结束、超时或可捕获信号后回收它们。
先停止测试创建的进程，再删除夹具。应用调用有时间上限，交互调用还限制终端输出大小。
这用于测试创建的进程，不负责追踪任意主动脱离进程树的守护程序。

失败时 Bash 会显示用例、阶段和命令日志；Python 用例缓冲正常输出，在失败时显示相关
命令输出。终端失败会把经过转义的输出尾部写入诊断，临时目录删除后仍可查看。

## 静态检查

在仓库根目录执行；工具缺失应记录为未验证，不能算通过：

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
