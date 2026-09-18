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
