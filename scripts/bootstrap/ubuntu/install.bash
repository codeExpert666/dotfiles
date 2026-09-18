#!/usr/bin/env bash

# 平台操作只在入口调用时执行。清单数组由 ubuntu_load_packages 每次重置，
# 避免 desktop 预览留下的条目混入随后执行的基础安装阶段。
# shellcheck disable=SC2154 # 入口提供运行状态；清单提供软件数组。
ubuntu_load_packages() {
	apt_packages=() apt_commands=() release_tools=() release_commands=() apt_fallbacks=()
	apt_packages_24_04=() apt_packages_26_04=() deb_releases_24_04=() deb_releases_26_04=()
	deb_releases=()
	case $1 in
		base)
			# shellcheck source-path=SCRIPTDIR
			# shellcheck source=packages.bash
			source "$script_dir/bootstrap/ubuntu/packages.bash"
			;;
		desktop)
			# shellcheck source-path=SCRIPTDIR
			# shellcheck source=packages.desktop.bash
			source "$script_dir/bootstrap/ubuntu/packages.desktop.bash"
			;;
		*) die "unknown Ubuntu software scope: $1" ;;
	esac
	case $os_version in
		24.04)
			apt_packages+=("${apt_packages_24_04[@]}")
			deb_releases=("${deb_releases_24_04[@]}")
			;;
		26.04)
			apt_packages+=("${apt_packages_26_04[@]}")
			deb_releases=("${deb_releases_26_04[@]}")
			;;
	esac
}

ubuntu_preview_packages() {
	local file=packages.bash
	[[ $1 != desktop ]] || file=packages.desktop.bash
	ubuntu_load_packages "$1"
	say "PLAN: Ubuntu $1 software (ubuntu/$file)"
	if ((${#apt_packages[@]})); then say "  apt: ${apt_packages[*]}"; fi
	if ((${#release_tools[@]})); then say "  pinned upstream: ${release_tools[*]}"; fi
	if ((${#apt_fallbacks[@]})); then say "  upstream fallback after apt: ${apt_fallbacks[*]}"; fi
	if ((${#deb_releases[@]})); then say "  pinned community deb via apt (Ubuntu $os_version): ${deb_releases[*]}"; fi
}

preview_software() {
	ubuntu_preview_packages base
	if [[ $profile == desktop ]]; then ubuntu_preview_packages desktop; fi
}

prepare_platform() {
	apt_updated=no
}

ubuntu_package_installed() {
	[[ $(dpkg-query -W -f='${Status}' "$1" 2> /dev/null) == 'install ok installed' ]]
}

apt_install() {
	if [[ $apt_updated != yes ]]; then
		authorize_sudo
		run 'refresh apt package index' 600 sudo -n apt-get update
		apt_updated=yes
	fi
	authorize_sudo
	run "apt install: $*" 1800 sudo -n apt-get install -y --no-install-recommends "$@"
}

ubuntu_entry_ready() {
	local name="$1" commands="$1" entry command names=()
	shift
	for entry in "$@"; do
		if [[ ${entry%%|*} == "$name" ]]; then
			commands="${entry#*|}"
			break
		fi
	done
	if [[ -z $commands ]]; then
		ubuntu_package_installed "$name"
		return
	fi
	read -r -a names <<< "$commands"
	for command in "${names[@]}"; do
		if ! required_tool_ready "$command"; then return 1; fi
	done
}

ubuntu_install_packages() {
	local package resource needs=()
	ubuntu_load_packages "$1"
	for package in "${apt_packages[@]}"; do
		if ! ubuntu_entry_ready "$package" "${apt_commands[@]}"; then needs+=("$package"); fi
	done
	if ((${#needs[@]})); then apt_install "${needs[@]}"; fi
	for resource in "${release_tools[@]}" "${apt_fallbacks[@]}"; do
		if ! ubuntu_entry_ready "$resource" "${release_commands[@]}"; then
			run "install pinned $resource" 1200 python3 -B "$script_dir/bootstrap/resources.py" install "$resource" "linux-$arch"
		fi
	done
	for resource in "${deb_releases[@]}"; do
		if ! ubuntu_entry_ready "$resource" "${release_commands[@]}"; then
			run "download verified $resource community package" 1200 python3 -B "$script_dir/bootstrap/resources.py" fetch "$resource" "ubuntu-$os_version-$arch" "$scratch/$resource.deb"
			apt_install "$scratch/$resource.deb"
		fi
	done
}

install_software() {
	ubuntu_install_packages base
	if ! command -v fd > /dev/null; then
		run 'fd command alias for Ubuntu fdfind' 30 python3 -B "$script_dir/bootstrap/resources.py" link-fd
	fi
}

install_desktop() {
	ubuntu_install_packages desktop
}
