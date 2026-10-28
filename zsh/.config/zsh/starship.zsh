#!/usr/bin/env zsh

# Starship 没有系统外观选项；显示提示符前选择配置，不改写仓库源文件。
_dotfiles_starship_theme() {
	emulate -L zsh
	local config="$HOME/.config/starship.toml"
	local cache_dir="${XDG_CACHE_HOME:-$HOME/.cache}/starship"
	local light_config="$cache_dir/catppuccin-latte.toml"
	local appearance=dark preference temporary

	# local.zsh 或父进程显式选择的其他配置始终优先。
	[[ -z ${STARSHIP_CONFIG-} || $STARSHIP_CONFIG == "$config" ||
		$STARSHIP_CONFIG == "$light_config" ]] || return 0

	case "$OSTYPE" in
		darwin*)
			# macOS 亮色模式下不存在 AppleInterfaceStyle 键。
			[[ $(defaults read -g AppleInterfaceStyle 2> /dev/null) == Dark ]] || appearance=light
			;;
		linux*)
			if (($+commands[gsettings])); then
				preference=$(gsettings get org.gnome.desktop.interface color-scheme 2> /dev/null)
				case "$preference" in
					"'prefer-light'" | "'default'")
						appearance=light
						;;
				esac
			fi
			;;
	esac

	# 无桌面或检测失败时使用默认 Macchiato；缓存写入失败也保留可用提示符。
	export STARSHIP_CONFIG="$config"
	[[ $appearance == light && -r $config ]] || return 0
	if [[ ! -s $light_config || $config -nt $light_config ]]; then
		mkdir -p -- "$cache_dir" 2> /dev/null || return 0
		temporary=$(mktemp "$cache_dir/.catppuccin-latte.XXXXXX" 2> /dev/null) || return 0
		# 两种外观共用模块与布局；只替换顶层 palette。
		if ! sed "s/^palette = 'catppuccin_macchiato'$/palette = 'catppuccin_latte'/" "$config" > "$temporary" 2> /dev/null ||
			! mv -f -- "$temporary" "$light_config" 2> /dev/null; then
			rm -f -- "$temporary" 2> /dev/null
			return 0
		fi
	fi
	export STARSHIP_CONFIG="$light_config"
}
