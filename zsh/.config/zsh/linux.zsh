# 未指定其他 agent 时发现系统 OpenSSH socket；保留 SSH 转发或本机代理的既有选择。
if [[ -z ${SSH_AUTH_SOCK-} && -n ${XDG_RUNTIME_DIR-} && -S "$XDG_RUNTIME_DIR/openssh_agent" ]]; then
	export SSH_AUTH_SOCK="$XDG_RUNTIME_DIR/openssh_agent"
fi

# 让 less 能预处理压缩包等非纯文本文件。
if [ -x /usr/bin/lesspipe ]; then
	eval "$(SHELL=/bin/sh /usr/bin/lesspipe)"
fi

# 使用 GNU dircolors，并为 ls 启用自动颜色。
if [ -x /usr/bin/dircolors ]; then
	if [ -r "$HOME/.dircolors" ]; then
		eval "$(/usr/bin/dircolors -b "$HOME/.dircolors")"
	else
		eval "$(/usr/bin/dircolors -b)"
	fi

	alias ls='ls --color=auto'
fi
