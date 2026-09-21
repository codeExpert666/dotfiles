#!/usr/bin/env bash

# 仅定义平台操作；运行状态、日志和清理由 bootstrap.sh / common.bash 持有。
# shellcheck disable=SC2154 # 入口提供 script_dir、profile、scratch 等运行状态。
preview_brewfile() {
	local file="$1" line
	say "PLAN: Homebrew manifest $file (install missing packages; upgrade only incompatible tools)"
	# 直接展示静态清单，预览不调用 Homebrew，也不执行 Brewfile 中的 Ruby。
	while IFS= read -r line; do
		[[ -n $line && $line != \#* ]] || continue
		say "  $line"
	done < "$file"
}

preview_software() {
	say 'PLAN: verify Command Line Tools and Homebrew; Brewfiles describe Homebrew-managed installations.'
	preview_brewfile "$script_dir/bootstrap/macos/Brewfile"
	if [[ $profile == desktop ]]; then preview_brewfile "$script_dir/bootstrap/macos/Brewfile.desktop"; fi
}

prepare_platform() {
	local installer="$scratch/homebrew-install.sh" digest
	if ! xcode-select -p > /dev/null 2>&1; then
		xcode-select --install || :
		die 'complete the Apple Command Line Tools installer, then rerun bootstrap'
	fi
	if ! command -v brew > /dev/null; then
		# 固定官方安装脚本的提交与哈希；初始阶段尚不能依赖 Python。
		run 'download Homebrew installer' 180 curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' --retry 2 --connect-timeout 15 --max-time 120 \
			--output "$installer" https://raw.githubusercontent.com/Homebrew/install/8949852f785a3bacaba2a979d0790337950b0a4a/install.sh
		digest=$(shasum -a 256 "$installer")
		[[ ${digest%% *} == 25548e1da7930c1563dbbe2cb05834a4131c4da09234540b6fdac812fda3c287 ]] || die 'Homebrew installer SHA256 mismatch'
		authorize_sudo
		run 'install Homebrew' 1800 env NONINTERACTIVE=1 /bin/bash "$installer"
	fi
	command -v brew > /dev/null || die 'Homebrew is unavailable after preparation'
}

# 仅用于把不兼容命令定位到待升级的包；安装清单以 Brewfile 为准。
macos_package_for() {
	case $1 in
		cc | tar | unzip) return 1 ;;
		python3) printf '%s\n' python ;;
		rg) printf '%s\n' ripgrep ;;
		node | npm) printf '%s\n' node@24 ;;
		nvim) printf '%s\n' neovim ;;
		delta) printf '%s\n' git-delta ;;
		infocmp | tic) printf '%s\n' ncurses ;;
		shuck) printf '%s\n' ewhauser/tap/shuck-cli ;;
		tree-sitter) printf '%s\n' tree-sitter-cli ;;
		7zz) printf '%s\n' sevenzip ;;
		*) printf '%s\n' "$1" ;;
	esac
}

macos_install_bundle() {
	local scope="$1" file="$2" name minimum package kind
	if brew bundle check --no-upgrade --file="$file" > /dev/null 2>&1; then
		say "READY: Homebrew dependencies in $file"
	else
		run "install Homebrew manifest $file" 3600 brew bundle install --no-upgrade --file="$file"
	fi
	# --no-upgrade 不保证最低兼容版本；只修复实际命令未满足的要求。
	while IFS=$'\t' read -r name minimum; do
		if tool_ready "$name" "$minimum"; then continue; fi
		package=$(macos_package_for "$name") || die "$name is missing; check macOS and Command Line Tools"
		kind=--formula
		[[ $name != ghostty ]] || kind=--cask
		brew list "$kind" --versions "$package" > /dev/null 2>&1 || die "$name is unavailable and $package is not installed; check $file and PATH"
		run "upgrade incompatible Homebrew $package" 1800 brew upgrade "$kind" "$package"
		tool_ready "$name" "$minimum" || die "$name >= $minimum is still unavailable after upgrading $package; check PATH and the installed version"
	done < <(requirements_for "$scope")
}

install_software() {
	macos_install_bundle base "$script_dir/bootstrap/macos/Brewfile"
	if [[ $(type -P node) == /opt/homebrew/opt/node@24/bin/node ]]; then
		run 'Node commands for new Zsh sessions' 30 python3 -B "$script_dir/bootstrap/resources.py" link-brew-node
	fi
}

install_desktop() {
	macos_install_bundle desktop "$script_dir/bootstrap/macos/Brewfile.desktop"
}
