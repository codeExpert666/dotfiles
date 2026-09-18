#!/usr/bin/env bash

# server 和 desktop 共用。这里只声明数据；由 install.bash 加载和执行。
# shellcheck disable=SC2034 # 安装器读取这些清单数组。
apt_packages=(
	git stow zsh vim curl python3 build-essential
	tar unzip xz-utils ncurses-bin less ripgrep fd-find
	git-delta zoxide shellcheck shfmt ca-certificates lesspipe
)

# 包名与命令不一致时声明「包名|命令列表」；空列表按 dpkg 安装状态检查。
# 其余包默认检查同名命令，最低版本统一读取 requirements.tsv。
apt_commands=(
	'build-essential|cc make'
	'xz-utils|xz'
	'ncurses-bin|infocmp tic'
	'ripgrep|rg'
	'fd-find|fd'
	'git-delta|delta'
	'ca-certificates|'
	'lesspipe|'
)

# ID 对应 releases.json；仅在实际命令不满足要求时安装固定资源。
release_tools=(node nvim fzf lazygit atuin starship shuck tree-sitter)
release_commands=('node|node npm')

# 先尝试上面的 apt 包，再为仍不兼容的命令使用上游资源。
apt_fallbacks=(delta zoxide)
