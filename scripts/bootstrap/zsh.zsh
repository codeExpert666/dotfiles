#!/usr/bin/env zsh

# 专用非交互入口，仅加载 Antidote；不执行 .zshrc 或本机覆盖。
setopt err_exit pipe_fail
manifest=$1
export ANTIDOTE_HOME="$HOME/.cache/antidote"
source "$HOME/.local/share/antidote/antidote.zsh" # shuck: ignore=C003 # Antidote 由 bootstrap 安装到运行用户的 HOME。
mkdir -p "$ANTIDOTE_HOME"
generated="$ANTIDOTE_HOME/zsh_plugins.zsh"
temporary=$(mktemp "$ANTIDOTE_HOME/.bootstrap.XXXXXX")
trap 'rm -f -- "$temporary"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# bundle 会下载缺失的仓库；缓存只有生成与语法检查都成功才发布。
antidote bundle < "$manifest" > "$temporary"
zsh -d -f -n "$temporary"
while read -r plugin rest; do
	[[ -n $plugin && $plugin != \#* ]] || continue
	root=$(antidote path "$plugin")
	case $plugin in
		zsh-users/zsh-autosuggestions)
			entry="$root/zsh-autosuggestions.zsh"
			;;
		zsh-users/zsh-syntax-highlighting)
			entry="$root/zsh-syntax-highlighting.zsh"
			;;
		*)
			[[ -d $root ]] || exit 1
			continue
			;;
	esac
	[[ -r $entry ]] || {
		print -u2 "Missing plugin entry: $entry"
		exit 1
	}
	zsh -d -f -n "$entry"
done < "$manifest"
if [[ -f $generated && $generated -nt $manifest ]] && cmp -s "$temporary" "$generated"; then
	print 'Antidote plugin cache is current.'
else
	mv -f -- "$temporary" "$generated"
	print 'Antidote plugin cache prepared.'
fi
