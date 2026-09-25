#!/usr/bin/env zsh
# 显式声明 Zsh，避免格式化器将 $+ 参数展开误当成加法并插入空格。

# 使用 Emacs 风格键位，供后续 ZLE 插件在同一套基础键位映射上绑定按键。
bindkey -e

# 原生 Zsh 历史写入 XDG state，作为 Atuin 之外的本地回退。
zsh_state_dir="$HOME/.local/state/zsh"
mkdir -p "$zsh_state_dir"
HISTFILE="$zsh_state_dir/history"

# 修改 less 历史文件路径，使其符合 XDG state 约定。
# 旧版 less 会默认把历史写到 ~/.lesshst
export LESSHISTFILE="${LESSHISTFILE:-$HOME/.local/state/lesshst}"

# 内存历史最多保留 50000 条，磁盘历史文件长期保留 20000 条。
HISTSIZE=50000
SAVEHIST=20000

# 多个会话退出时追加历史；需要裁剪时优先淘汰有副本的旧记录。
setopt append_history
setopt hist_expire_dups_first

# 原生历史跳过相邻重复和空格开头的命令，并压缩无意义的多余空白。
setopt hist_ignore_dups
setopt hist_ignore_space
setopt hist_reduce_blanks

# 临时路径变量不再需要，避免留在当前 Shell 的全局参数中。
unset zsh_state_dir

# 共享和平台文件随仓库发布；缺失时报告错误并停止本次初始化。
# shuck: source=common.zsh
source "$ZDOTDIR/common.zsh" || return
case "$OSTYPE" in
	darwin*)
		# shuck: source=macos.zsh
		source "$ZDOTDIR/macos.zsh" || return
		;;
	linux*)
		# shuck: source=linux.zsh
		source "$ZDOTDIR/linux.zsh" || return
		;;
esac

# 初始化 Zsh 自带的可编程补全，让 Tab 能根据当前命令和参数位置提供候选项。
# 补全转储是可重新生成的初始化缓存；按 Zsh 版本分文件，避免升级后复用旧结果。
zcompdump="$HOME/.cache/zsh/zcompdump-$ZSH_VERSION"
mkdir -p "${zcompdump:h}"

# 从 Zsh 函数搜索路径自动加载 compinit，并用指定转储文件初始化当前会话的补全系统。
autoload -Uz compinit
compinit -d "$zcompdump"

# 只清理临时路径变量，磁盘上的补全转储仍保留供下次启动复用。
unset zcompdump

# Antidote 本体放在 XDG data；可重新下载的插件仓库与生成脚本放在 XDG cache。
export ANTIDOTE_HOME="$HOME/.cache/antidote"
antidote_dir="$HOME/.local/share/antidote"
plugin_manifest="$ZDOTDIR/.zsh_plugins.txt"
plugin_static="$HOME/.cache/antidote/zsh_plugins.zsh"

# 入口可读时把 Antidote 管理函数加载到当前 Shell，再处理共享插件清单。
if [[ -r "$antidote_dir/antidote.zsh" ]]; then
	# 外部插件管理器不纳入仓库的静态分析。
	# shuck: source=/dev/null
	source "$antidote_dir/antidote.zsh"

	# 在自动建议插件加载前设置低亮度显示样式。
	ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE='fg=8' # shuck: ignore=C001 # 由 zsh-autosuggestions 插件读取。

	# 生成脚本是可重建缓存；显式放入 XDG cache，避免在受管配置目录旁生成未跟踪文件。
	mkdir -p "${plugin_static:h}"

	# 下载缺失的插件仓库并加载普通插件；kind:clone 项只下载，留待后文手动加载。
	antidote load "$plugin_manifest" "$plugin_static"
else
	# 本体缺失时只向标准错误报告，保留无插件但仍可使用的基础 Zsh。
	print -u2 "Antidote is missing: $antidote_dir"
fi

# 只清理本体、清单与生成脚本路径变量；ANTIDOTE_HOME 继续供 Antidote 的后续命令使用。
unset antidote_dir plugin_manifest plugin_static

# 仅在 fzf 存在且支持 --zsh 时加载按键绑定与模糊补全，避免缺失或旧版本打断 Shell 启动。
# 保留 Ctrl-T 文件选择；禁用 fzf 的 Ctrl-R 与 Alt-C，分别由 Atuin 和 zoxide 的 zi 承担历史搜索与目录选择。
if (($+commands[fzf])) && fzf --zsh > /dev/null 2>&1; then
	# cd **<Tab> 等目录补全也搜索隐藏目录，与 Ctrl-T 的目录候选保持一致。
	export FZF_COMPLETION_DIR_OPTS='--walker dir,follow,hidden'
	FZF_CTRL_R_COMMAND= FZF_ALT_C_COMMAND= source <(fzf --zsh) # shuck: ignore=C002,C024 # fzf 官方初始化方式：空值禁用对应按键，脚本由 fzf 动态生成。
fi

# 若已安装 Atuin，则加载用于记录命令的钩子与历史搜索按键，并由它接管 Ctrl-R。
# 不让 Atuin 覆盖上箭头或绑定 ? 的 AI 入口，使原有历史翻阅与普通输入行为保持不变。
(($+commands[atuin])) && eval "$(atuin init zsh --disable-up-arrow --disable-ai)"

# 若已安装 zoxide，则在 compinit 后加载用于记录目录访问的钩子、z/zi 命令及补全。
# z 按访问记录排名跳转，zi 通过 fzf 交互选择；未使用 --cmd cd，因此不替换原生 cd。
(($+commands[zoxide])) && eval "$(zoxide init zsh)"

# 若已安装 Starship，则加载提示符钩子，按当前目录、Git 状态和上一条命令结果渲染提示符。
# 它只接管提示符；放在其他提示符配置之后，避免初始化结果再次被覆盖。
(($+commands[starship])) && eval "$(starship init zsh)"

# 本机交互设置最后覆盖默认值和工具初始化；登录环境放在 local.zprofile。
# 高亮插件仍在本机按键绑定等调整之后加载。
# 本机可选文件不纳入仓库的静态分析。
# shuck: source=/dev/null
[[ -r "$ZDOTDIR/local.zsh" ]] && source "$ZDOTDIR/local.zsh"

# 语法高亮在插件清单中以 kind:clone 只下载；Antidote 可用时再查询其本地目录。
if (($+functions[antidote])); then
	zsh_highlight_root="$(antidote path zsh-users/zsh-syntax-highlighting 2> /dev/null)"

	# 主脚本可读时才最后手动加载，使高亮钩子在其他 Zsh 行编辑器（ZLE）组件之后注册。
	if [[ -r "$zsh_highlight_root/zsh-syntax-highlighting.zsh" ]]; then
		# 外部高亮插件不纳入仓库的静态分析。
		# shuck: source=/dev/null
		source "$zsh_highlight_root/zsh-syntax-highlighting.zsh"
	fi

	# 清理仅用于定位插件的临时变量。
	unset zsh_highlight_root
fi
