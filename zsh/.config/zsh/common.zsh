# 常用 ls 别名
alias ll='ls -alF'
alias la='ls -A'

# 常用交互命令的简写。
alias lg='lazygit'
alias mp='multipass'

# GNU/Linux 与 macOS 自带的 grep 都支持按终端自动着色匹配内容。
alias grep='grep --color=auto'

# SSH 服务端可能不接收 COLORTERM；仅凭 xterm-ghostty，部分程序仍只识别基本颜色。
# 为 Ghostty 交互式 SSH 会话补充真彩色声明，避免 Codex 等程序省略输入框底色。
if [[ -o interactive && -n ${SSH_TTY-} && $TERM == xterm-ghostty &&
	-z ${COLORTERM-} ]]; then
	export COLORTERM=truecolor
fi

# SSH 交互会话：输入时使用闪烁竖线，运行程序前恢复终端默认光标。
# 适用于 Ghostty 等 xterm 兼容终端，以及 screen/tmux 会话。
if [[ -o interactive && -o zle && -n ${SSH_TTY-} &&
	$TERM == (xterm*|screen*|tmux*) ]]; then
	autoload -Uz add-zle-hook-widget add-zsh-hook

	_dotfiles_ssh_cursor_bar() {
		builtin printf '\e[5 q' > /dev/tty
	}

	_dotfiles_ssh_cursor_default() {
		builtin printf '\e[0 q' > /dev/tty
	}

	add-zle-hook-widget line-init _dotfiles_ssh_cursor_bar
	add-zsh-hook preexec _dotfiles_ssh_cursor_default
	add-zsh-hook zshexit _dotfiles_ssh_cursor_default
fi
