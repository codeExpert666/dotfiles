#!/usr/bin/env zsh

# 在隔离 HOME 中替换系统外观查询，使用真实 Starship 验证最终选色与渲染。
emulate -LR zsh
setopt errexit nounset pipefail
starship_command=$1
# shuck: source=../../zsh/.config/zsh/starship.zsh
source "$HOME/.config/zsh/starship.zsh"

config="$HOME/.config/starship.toml"
light_config="$HOME/.cache/starship/catppuccin-latte.toml"
fixture_appearance=dark
defaults() {
	[[ $* == 'read -g AppleInterfaceStyle' ]] || return 97
	[[ $fixture_appearance == dark ]] || return 1
	print -r -- Dark
}

assert_palette() {
	local palette=$1 color=$2
	[[ $("$starship_command" print-config palette) == *"palette = \"$palette\""* ]]
	"$starship_command" prompt --path "$HOME" --status 0 > "$HOME/prompt"
	[[ $(< "$HOME/prompt") == *"$color"* ]]
}

# 不依赖运行测试的宿主桌面；macOS / GNOME 分支均为模拟系统回复。
OSTYPE=darwin
unset STARSHIP_CONFIG
rm -f "$light_config"
source_config=${config:A}
cp "$config" "$HOME/source-before.toml"
# 更新缓存的用例修改 HOME 中的独立副本，保留 Stow 指向的仓库源文件。
rm "$config"
cp "$HOME/source-before.toml" "$config"
_dotfiles_starship_theme
[[ $STARSHIP_CONFIG == "$config" && ! -e $light_config ]]
assert_palette catppuccin_macchiato '38;2;237;135;150'

fixture_appearance=light
_dotfiles_starship_theme
[[ $STARSHIP_CONFIG == "$light_config" ]]
assert_palette catppuccin_latte '38;2;210;15;57'
cmp -s "$config" "$HOME/source-before.toml"
# 除顶层 palette 外，亮色副本必须与源配置完全一致。
cmp -s <(sed "s/^palette = 'catppuccin_macchiato'$/palette = 'catppuccin_latte'/" "$config") "$light_config"
ln "$light_config" "$HOME/first-cache.toml"
_dotfiles_starship_theme
[[ $light_config -ef "$HOME/first-cache.toml" ]]

# 缓存可重用；源配置更新后应重建，且后续暗色选择仍指向默认配置。
touch -t 200001010000 "$light_config"
print -r -- '# cache refresh fixture' >> "$config"
_dotfiles_starship_theme
[[ ! $light_config -ef "$HOME/first-cache.toml" ]]
grep -Fxq '# cache refresh fixture' "$light_config"
fixture_appearance=dark
_dotfiles_starship_theme
assert_palette catppuccin_macchiato '38;2;237;135;150'

# 用户显式配置优先；缓存目录不可写时返回成功，继续使用默认深色配置。
STARSHIP_CONFIG="$HOME/personal.toml"
fixture_appearance=light
_dotfiles_starship_theme
[[ $STARSHIP_CONFIG == "$HOME/personal.toml" ]]
unset STARSHIP_CONFIG
print -r -- blocked > "$HOME/cache-file"
export XDG_CACHE_HOME="$HOME/cache-file"
_dotfiles_starship_theme
[[ $STARSHIP_CONFIG == "$config" ]]
unset XDG_CACHE_HOME

# 支持自定义 XDG cache 路径，不把运行时生成内容写入配置目录。
export XDG_CACHE_HOME="$HOME/cache with spaces"
_dotfiles_starship_theme
[[ $STARSHIP_CONFIG == "$XDG_CACHE_HOME/starship/catppuccin-latte.toml" ]]
assert_palette catppuccin_latte '38;2;210;15;57'
unset STARSHIP_CONFIG XDG_CACHE_HOME

# GNOME 查询成功、失败与没有桌面工具的路径，均不读取真实桌面状态。
mkdir -p "$HOME/mock-bin"
cat > "$HOME/mock-bin/gsettings" << 'MOCK'
#!/bin/sh
[ "$*" = 'get org.gnome.desktop.interface color-scheme' ] || exit 97
[ "$DOTFILES_TEST_APPEARANCE" != error ] || exit 1
printf '%s\n' "$DOTFILES_TEST_APPEARANCE"
MOCK
chmod +x "$HOME/mock-bin/gsettings"
original_path=$PATH
PATH="$HOME/mock-bin:$PATH"
OSTYPE=linux-gnu
for preference in "'prefer-light'" "'default'" "'prefer-dark'" error; do
	export DOTFILES_TEST_APPEARANCE="$preference"
	_dotfiles_starship_theme
	case "$preference" in
		"'prefer-light'" | "'default'")
			[[ $STARSHIP_CONFIG == "$light_config" ]]
			;;
		*)
			[[ $STARSHIP_CONFIG == "$config" ]]
			;;
	esac
done
PATH="$HOME/empty-bin"
_dotfiles_starship_theme
[[ $STARSHIP_CONFIG == "$config" ]]
PATH=$original_path

# 缺失配置时不生成空白缓存，也不打断 Shell。
mv "$config" "$config.saved"
OSTYPE=darwin
_dotfiles_starship_theme
[[ $STARSHIP_CONFIG == "$config" ]]
mv "$config.saved" "$config"
cmp -s "$source_config" "$HOME/source-before.toml"
