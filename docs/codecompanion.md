# 在 LazyVim 中使用备用 CodeCompanion

CodeCompanion 提供 Neovim 聊天缓冲区、选区编辑与代码审阅，codex-acp 将这些操作接到
Codex App Server。聊天适合跨文件任务和测试，Inline 适合当前文件的局部修改。
日常主入口是 [Sidekick + 原生 Codex CLI](sidekick.md)；本文描述备用入口，
快捷键统一使用 `Space a c …`。两套客户端使用各自的会话，恢复同一会话前先退出旧客户端。

## 安装与登录

运行环境需要 Neovim 0.12+、Node.js 24+ 和 npm。核心 bootstrap 已准备这些依赖，
并通过 Mason 配置安装缺失的 codex-acp。CodeCompanion 固定为 v20.0.1，具体提交及
Plenary 依赖记录在 [lazy-lock.json](../nvim/.config/nvim/lazy-lock.json)。

新机器按[根 README](../README.md#快速开始)运行 bootstrap。已有完整环境时：

```sh
bash scripts/deploy.sh --dry-run
bash scripts/deploy.sh --apply
```

重启 Neovim 后，由 lazy.nvim 安装新增插件、Mason 安装缺失工具。需要显式准备当前
验收版本时，执行 `:MasonInstall codex-acp@2.2.2`。安装完成后先打开聊天加载插件，
再运行 `:checkhealth codecompanion`。离线检查可用 `bash scripts/doctor.sh --only nvim`。
健康检查中的 MarkItDown 缺失提醒仅涉及可选网页抓取，不影响 Codex 聊天或代码修改。

codex-acp 使用 Mason 的独立安装目录，不要求全局 npm 安装。新机器安装版本随 Mason
registry 解析，现有安装不会在每次启动或 bootstrap 时自动更新。检查已安装版本：

```sh
~/.local/share/nvim/mason/bin/codex-acp --version
```

配置使用 `chat-gpt` 认证。已有 Codex CLI 时，可先运行 `codex login status`；需要登录时
运行 `codex login`。聊天首次连接也可能启动浏览器登录。认证由本机 Codex 维护，
个人 `~/.codex/config.toml`、凭据和会话不进入 dotfiles。

适配器默认使用 npm 包附带的 Codex，未必与 PATH 中的独立 CLI 版本一致。
需要改用独立 CLI 时，可在个人环境中设置 `CODEX_PATH`，并重新验收聊天、Inline 和恢复；
通常保留适配器默认选择即可。

## 从项目开始

```sh
cd /path/to/project
nvim .
```

每个项目或 worktree 使用独立 Neovim 实例。ACP 根据 Neovim 的当前工作目录创建和
列举会话，先用 `:pwd` 确认；从其他目录打开文件时，先用 `:tcd /path/to/project`
切到项目再新建聊天。已经开始的会话不会因切换编辑器目录而迁移。

聊天窗口在右侧占约 40% 宽度，每个 tab 独立管理窗口。隐藏面板后仍可重新打开。
退出 Neovim 会关闭客户端进程；以后通过恢复会话继续，不应依赖退出后任务仍在执行。

## 聊天状态栏

聊天窗口顶部常驻显示当前会话的模型、推理等级、上下文已用/容量及占比、权限模式。
内容在聊天窗口内整体居中；字段名使用柔和的主题注释色，具体值使用主题强调色并加粗，
分隔符淡化。切换配色主题后自动更新这些高亮。
例如（容量和数值仅为示例，以会话实际报告为准）：

```text
模型 gpt-6-astra │ 推理 max │ 上下文 23.7k/272k 8.7% │ 权限 工作区
```

窗口较窄时省略字段名，顺序仍为“模型 | 推理 | 已用/容量 占比 | 权限”。
简化后的内容同样居中，数值仍保留强调色与加粗样式。
`k`、`m` 分别表示千和百万，显示数值最多保留一位小数，占比按未经缩写的原始数值计算。

| 状态栏字段 | 来源与查看方式 |
| --- | --- |
| 模型 | 当前 ACP 会话的 Model；用 `/acp_session_options` 或普通模式 `ga` 查看和修改 |
| 推理 | 当前会话的 Reasoning effort；用 `/acp_session_options` 修改 |
| 上下文 | ACP `usage_update` 的 `used/size`；用 `\status` 查看适配器的详细统计 |
| 权限 | 当前会话的 Mode；用 `/acp_session_options` 修改 |

权限显示为“只读”（`read-only`）、“工作区”（`workspace-write`）、
“自动审批”（`agent`）或“完全访问”（`agent-full-access`）。
状态按每个窗口对应的聊天和连接独立读取，非活动窗口也会随代理通知刷新。

初次连接尚无模型或用量数据时显示“待连接/待上报”，不把未知用量显示成 0%。
切换模型或恢复另一会话后等待新的用量通知，连续切回原模型也不会恢复旧统计；
通常完成下一次请求后即可看到统计。上下文压缩后已用数值可以下降。

这里的上下文统计包含代理实际使用的指令、历史和工具内容，不等于累计计费用量。
对话标题后的 token 数是上游显示的使用量；底部普通状态栏的百分比仍是光标所在位置。
输入 `\status` 后用 `Ctrl-s` 提交可查看模型、上下文容量、累计 token 和可用额度等详细信息。

实现位于[状态栏模块](../nvim/.config/nvim/lua/config/codecompanion-status.lua)。当前锁定的
CodeCompanion v20.0.1 尚未保留通知中的容量，因此模块只补充读取这条通知并转交上游处理。
升级后若上游已提供完整用量元数据，可移除通知包装并改用该元数据；升级时运行下述缓存集成检查。

## 快捷键与输入

以下 `Space` 为 LazyVim 的 leader。聊天、代码和审阅界面的同名按键各有作用域。

| 按键 | 作用 |
| --- | --- |
| `Space a c c` | 显示或隐藏备用聊天 |
| `Space a c n` | 新建 Codex 聊天 |
| 可视模式 `Space a c v` | 把选区加入最近的聊天，补充要求后提交 |
| `Space a c i` | 对当前文件发起 Inline 修改；可视模式限定选中行 |
| `Space a c p` | 打开动作与提示模板面板 |
| `Space a c r` | 打开代码审阅 |
| `Space a c a` | 为当前行或选区添加审阅评论 |
| `Space g g` | 打开已有 Lazygit，检查与提交全部变更 |

聊天插入模式下 `Enter` 换行、`Ctrl-s` 提交；普通模式下 `Enter` 也提交。
普通模式 `q` 停止当前请求，`[[` / `]]` 跳转消息，`?` 查看按键。
Inline 输入框的 `Enter` 直接提交，与聊天插入模式不同。

Blink 自动补全聊天上下文和命令，回答由现有 Markdown 渲染器显示。
后台自动生成聊天标题保持关闭，不需要另配一个 HTTP 模型。

## 提供上下文

输入 `#` 打开上下文补全。建议只分享本次任务需要的内容：

| 写法 | 分享内容 |
| --- | --- |
| `#{selection}` | 最近捕获的可视选区 |
| `#{buffer}` | 最近的源码缓冲区，后续轮次同步变化 |
| `#{diagnostics}` | 缓冲区诊断及对应代码 |
| `#{terminal}` | 最近终端的输出；再次使用时补充新增输出 |
| `#{quickfix}` | Quickfix 中的文件及相关代码 |
| `#{diff}` | 当前目录的已暂存和未暂存 Git diff |
| `/file`、`/buffer` | 用选择器添加文件或打开的缓冲区 |

例如：

```text
根据 #{diagnostics} 修复 #{buffer} 中的问题。
保持公开接口不变，完成后运行相关测试并说明结果。
```

缓冲区上下文默认同步差异；诊断、选区等上下文是快照，发生变化后重新添加。
让聊天代理修改文件前，保存相关缓冲区，使上下文与磁盘状态一致。

项目的 `AGENTS.md`、Codex skills、MCP 和个人配置由 Codex 加载。
本配置关闭 CodeCompanion 自动规则注入，避免重复附加相同规则。

## 局部修改与项目任务

选中函数后按 `Space a c i`，输入“处理空输入，保持其他行为不变”。Inline 使用独立的
Codex 会话，从只读模式开始；CodeCompanion 仅批准修改缓冲区的临时副本，
再把结果带回源码缓冲区供审阅。需要测试、运行命令或修改其他文件时，使用聊天。

选区按整行限制。等待 Inline 结果期间若缓冲区被修改，结果会丢弃，避免覆盖新内容。
diff 中 `ga` 接受当前块、`gr` 拒绝当前块，`}` / `{` 切换块；`g2` / `g3`
接受或拒绝剩余修改，`?` 查看完整按键。确认后正常保存文件。

聊天默认为 `workspace-write`：允许项目内修改，需要额外权限的操作通过 ACP 提出审批。
用 `/acp_session_options` 调整模式、模型和推理强度，可用值以适配器返回的选项为准。
单纯阅读代码时可以切到 `Read-only`。

## 审阅、反馈与提交

完成一轮任务后按 `Space a c r`。审阅窗口中：

| 按键 | 作用 |
| --- | --- |
| `ga` | 接受当前块；在文件行上接受整个文件 |
| `gr` | 回退当前块 |
| `gc` | 对代码留下修改意见 |
| `u` | 撤销上一次接受或回退 |
| `i` / `I` | 跳到真实文件编辑 |

聊天中输入“请处理 `#{code_review}`”将评论发给 Codex，下一轮审阅跟踪后续改动。
分享评论也会结束本轮，并认可未评论的其余修改，应先检查完整 diff。

审阅是修改后的接受与回退，执行前的权限审批另行处理。功能目前为上游 beta，
最终通过 Gitsigns 或 `Space g g` 检查工作区和暂存区，再按需提交。

审阅基线是首次提交任务时的 worktree 快照，插件使用独立索引，不改动 Git 暂存区。
同一 worktree 中你手动产生的后续修改也可能进入审阅；独立任务使用不同 worktree。

## 恢复会话与命令

新建聊天后，等待代理命令补全就绪，在第一次发送消息前使用 `/resume`，
选择 ACP 代理提供的历史会话。
ACP 的历史由 Codex 管理；CodeCompanion 的 `/save` 和 HTTP 聊天自动保存不适用于此入口，
无需额外安装历史插件。跨 CLI、桌面和 ACP 的恢复范围以实际列举与恢复结果为准。

同一个 Codex 会话同时只允许一个客户端持有写入权。恢复前先关闭另一客户端中的
对应会话；隐藏窗口不释放连接。若提示 `thread_active_writer`，关闭旧聊天或退出旧客户端
后再新建聊天恢复。v20 的恢复提示可能在加载失败时仍显示原会话标题，应确认历史消息确实出现。

CodeCompanion 自身命令用 `/`，代理暴露的命令用 `\`，例如 `\status`、`\compact`、
`\review`。插件会把后者转换为代理命令；首次连接后稍候即可补全，可用列表以当前代理为准。

## 排查与升级

- 无法启动代理：用 `:Mason` 检查 codex-acp，确认 Node/npm 可用，再运行
  `:checkhealth codecompanion`；安装后重新打开聊天。
- 认证失败：检查 `codex login status`，必要时重新登录；确认未用个人环境覆盖了 `CODEX_HOME`。
- 文件不对或恢复列表不对：检查 `:pwd`，切到对应项目后新建聊天。
- 上下文未更新：重新添加诊断或选区；缓冲区可用 `gba` / `gbd` 选择全文或差异同步。
- 插件错误：检查 `:messages` 和健康检查显示的日志位置；分享日志前检查其中的项目内容。

升级 CodeCompanion 时修改[插件配置](../nvim/.config/nvim/lua/plugins/codecompanion.lua)的 tag，
定向更新并提交锁文件；codex-acp 通过 Mason 显式更新。升级后复核聊天、中文多行输入、
选区范围、权限、取消、审阅回退和重启后的恢复。

上游参考：[CodeCompanion ACP](https://github.com/olimorris/codecompanion.nvim/blob/v20.0.1/doc/configuration/adapters-acp.md)、
[Inline](https://github.com/olimorris/codecompanion.nvim/blob/v20.0.1/doc/usage/inline.md)、
[审阅](https://github.com/olimorris/codecompanion.nvim/blob/v20.0.1/doc/usage/code-review.md)、
[codex-acp](https://github.com/agentclientprotocol/codex-acp/blob/v2.2.2/README.md)。

## 首次集成验证（2026-10-24）

本机 macOS、Neovim 0.12.6、CodeCompanion v20.0.1、codex-acp 2.2.2
（附带 Codex 0.160.1）已通过以下检查：

- 真实 ChatGPT 认证及 Codex 请求：临时 Git 项目中的代码修复、单元测试执行、中文回复。
- 原生 Neovim 无界面会话：实际可视选区按键、选区加入聊天、Inline diff 接受、
  审阅回退/撤销/接受、运行中取消、退出并重启后的 `/resume` 和上下文续接。
- 使用真实 Inline 模块检查临时副本写入许可、其他文件写入和命令拒绝，以及选区外修改过滤；
  这些拒绝分支使用构造的权限请求，未让模型执行越界写入。
- 五套 `bash tests/all.sh` 离线回归全部通过；可选缓存检查共 6 项 SKIP。
  单独启用已准备缓存的 config-loading 检查为 19 项 PASS、0 SKIP。
- 部署预览与应用、bootstrap 预览、deployment/nvim doctor（126 PASS、0 WARN、0 FAIL）、
  Bash 语法/ShellCheck/shfmt、Lua StyLua、新增使用文档的 Markdownlint 均通过。

健康检查仅缺少可选 MarkItDown。Ghostty 的图形界面访问被工具安全策略拒绝，
本轮未做截图与 GUI 输入验收；Ubuntu 的原生运行也未验证。测试客户端已关闭，
两条含消息的临时 Codex 会话已归档。

## 状态栏验证（2026-10-24）

新增的缓存集成包含 15 组状态栏行为检查，使用真实 CodeCompanion 窗口、通知处理与
Neovim winbar 求值，覆盖缺失/非法容量、切换模型与会话、多个窗口和 tab、窄窗口、关闭重开，
以及居中留白、字段名/值高亮区分和配色主题切换。
`DOTFILES_TEST_PREPARED_HOME="$HOME" TMPDIR=/tmp bash tests/config-loading.sh` 为 20 PASS、0 SKIP；
未提供缓存时为 17 PASS、3 SKIP。部署/nvim doctor 为 128 PASS、0 WARN、0 FAIL。

在独立终端 PTY 中实际连接 Codex，模型、推理和权限的真实配置变更能刷新顶部状态栏；
真实请求上报 `used=21976`、`size=475000`，显示为 `22k/475k 4.6%`，
与原始通知一致。64 列聊天窗口能够显示全部四个字段的简化形式。
居中与样式调整另在原生 Neovim PTY、当前 Tokyo Night Moon 主题下验证：
120 列完整状态栏左右留白为 26/27 列，64 列简化状态栏为 9/10 列，
字段名与值使用不同颜色，值为加粗；这次样式验收使用构造的 ACP 通知。
Lua、Bash 和新增文档的格式/语法检查通过；未执行 Ghostty GUI 截图或 Ubuntu 原生验收。
