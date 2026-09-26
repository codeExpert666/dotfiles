#!/usr/bin/env bash

# 从仓库准备当前配置的依赖；部署与诊断分别委托给 deploy 和 doctor。
# Bash 3.2+；校验：bash -n、shellcheck；格式：shfmt -ci -sr。
set -e
unset CDPATH
umask 077

usage() {
	local line
	while IFS= read -r line; do printf '%s\n' "$line"; done << 'HELP'
Usage: bootstrap.sh [--dry-run|--apply] --profile {desktop|server}
       bootstrap.sh {-h|--help}

The default is an offline, read-only preview. --apply prepares platform software,
deploys configuration, prepares plugins, and runs doctor.
desktop adds Ghostty and IosevkaTerm Nerd Font / Sarasa Term SC.
Both profiles prepare terminfo utilities and a resolvable xterm-ghostty entry.
server prepares CLI tools; Ghostty GUI and fonts belong on the client.

Supported: Ubuntu 24.04/26.04 (x86_64, arm64), macOS 15/26 (Apple Silicon).
Requires Bash 3.2+, an existing absolute HOME and the default XDG layout.
Git repository context overrides (such as GIT_DIR, GIT_WORK_TREE and
GIT_INDEX_FILE) must be unset, including empty values; user Git config is kept.
Run as your normal user; system installation authenticates with sudo as needed.
Sudo credentials are refreshed before each privileged step and may prompt again.
macOS may require completing Apple's Command Line Tools installer, then rerunning.

Ubuntu uses apt plus pinned, SHA256-verified upstream archives. Ghostty on
Ubuntu 24.04 uses a pinned mkasberg/ghostty-ubuntu community .deb; 26.04 uses apt.
macOS uses Brewfile plus Brewfile.desktop for desktop, ensuring Homebrew-managed
packages are installed even when compatible commands exist elsewhere. Bundle
uses --no-upgrade; only tools below the compatibility requirements are upgraded.
Ubuntu uses packages.bash plus packages.desktop.bash and reuses compatible tools.
On both platforms, compatible Go is reused; otherwise the latest stable Go is
resolved from go.dev and installed from a SHA256-verified official archive.
Likewise, a complete JDK 21+ is reused; otherwise the latest stable Eclipse
Temurin JDK 25 is resolved from Adoptium and SHA256-verified. A stable Maven 3.9 release
using that JDK is reused or the latest 3.9.x archive is resolved from Maven
Central and SHA512-verified. Dry-run never performs these network lookups.
Antidote and fonts use pinned archives on both platforms.
Conflicting user-owned install paths and dirty plugin checkouts require manual review.
No whole-system upgrade, configuration migration, account login, history import,
or chsh is performed.

Apply logs: ~/.local/state/dotfiles-bootstrap/run.*
Downloads:  ~/.cache/dotfiles-bootstrap/
Tools:      ~/.local/share/dotfiles-bootstrap/, linked from ~/.local/bin/
Plugins:    the paths already configured by this repository.
Failures keep completed installations. Fix the reported problem and rerun.
A concurrent apply is refused; an abandoned lock directory is reported.

Output goes to stderr; --help goes to stdout.
Exit: 0 = selected preparation and applicable checks succeeded (or previewed),
      1 = failure, 2 = invalid arguments; signals use 128+signal.
Preview defers deployment checks if Git/Stow are unavailable or incompatible.
HELP
}

argument_error() {
	printf 'error: %s\nRun bootstrap.sh --help for usage.\n' "$1" >&2
	exit 2
}

mode='' profile=''
if (($# == 1)) && [[ $1 == -h || $1 == --help ]]; then
	usage
	exit 0
fi
while (($#)); do
	case $1 in
		--dry-run | --apply)
			[[ -z $mode ]] || argument_error 'choose only one mode'
			mode="${1#--}"
			shift
			;;
		--profile)
			if [[ -n $profile ]] || (($# < 2)); then argument_error '--profile requires one value and cannot be repeated'; fi
			case $2 in desktop | server) profile="$2" ;; *) argument_error 'profile must be desktop or server' ;; esac
			shift 2
			;;
		*) argument_error "unknown argument or combined help: $1" ;;
	esac
done
[[ -n $profile ]] || argument_error '--profile is required'
mode="${mode:-dry-run}"
# ===== 初始化与预览 =====
phase=preflight scratch='' probe_home='' log_file='' owns_lock=no completed=no active_pid='' timer_pid='' forward_pid='' forward_status=0
registering=no interrupted_status=0
script_parent="${BASH_SOURCE[0]%/*}"
[[ $script_parent != "${BASH_SOURCE[0]}" ]] || script_parent=.
script_dir=$(cd -- "$script_parent" && pwd -P)
repo_root=$(cd -- "$script_dir/.." && pwd -P)
# shellcheck source-path=SCRIPTDIR
# shellcheck source=layout.bash
source "$script_dir/layout.bash"
# shellcheck source-path=SCRIPTDIR
# shellcheck source=bootstrap/common.bash
source "$script_dir/bootstrap/common.bash"
trap 'finish_bootstrap "$?"' EXIT
trap 'interrupt_bootstrap 129' HUP
trap 'interrupt_bootstrap 130' INT
trap 'interrupt_bootstrap 143' TERM
check_environment
detect_platform
prepare_command_path
say "Platform: $platform $os_version $arch; profile: $profile; mode: $mode"
[[ -f $script_dir/bootstrap/requirements.tsv && -r $script_dir/bootstrap/requirements.tsv ]] || die 'compatibility requirements.tsv is missing or unreadable'
case $platform in
	macos)
		# shellcheck source-path=SCRIPTDIR
		# shellcheck source=bootstrap/macos/install.bash
		source "$script_dir/bootstrap/macos/install.bash"
		;;
	linux)
		# shellcheck source-path=SCRIPTDIR
		# shellcheck source=bootstrap/ubuntu/install.bash
		source "$script_dir/bootstrap/ubuntu/install.bash"
		;;
esac
initialize_scratch
preview_software
preview_requirements
if command -v infocmp > /dev/null && (
	unset TERMINFO TERMINFO_DIRS
	infocmp -x xterm-ghostty
) > "$scratch/terminfo.out" 2>&1; then
	say 'FOUND: xterm-ghostty resolves with the target HOME and default terminfo search path; reuse it.'
else
	say 'PLAN: after tools are ready, check xterm-ghostty with the target HOME and default search path; if missing, compile bundled Ghostty 1.3.1 text with tic -x and install only xterm-ghostty into ~/.terminfo, then verify infocmp. No download or target write during preview.'
fi
say 'PLAN: reuse compatible Go; otherwise install the latest stable Go from go.dev (resolved only during --apply).'
say 'PLAN: reuse a complete JDK 21+ and Maven 3.9; otherwise install the latest stable Temurin JDK 25 and verified Maven archive only during --apply.'
if [[ $profile == desktop ]]; then
	say 'PLAN: check/install IosevkaTerm Nerd Font and Sarasa Term SC (pinned upstream font archives).'
fi

if [[ $mode == dry-run ]]; then
	phase='deployment preview'
	if required_tool_ready git && required_tool_ready stow; then
		"$BASH" "$script_dir/deploy.sh" --dry-run >&2
	else
		say 'DEFER: deployment preview requires compatible Git and Stow; it will run after installation.'
	fi
	say 'PLAN: prepare Antidote and .zsh_plugins.txt cache; restore locked Neovim plugins, Mason tools, Treesitter parsers and completion resources.'
	say 'PLAN: run doctor and applicable runtime checks after preparation.'
	completed=yes
	exit 0
fi
[[ $EUID != 0 ]] || die 'run bootstrap as your normal user, not as root'
# ===== 安装与部署 =====
state_dir="$target_dir/.local/state/dotfiles-bootstrap"
mkdir -p "$state_dir"
registering=yes
if (
	trap '' HUP INT TERM
	mkdir "$state_dir/lock"
) 2> /dev/null; then
	owns_lock=yes
else
	registering=no
	[[ $interrupted_status == 0 ]] || exit "$interrupted_status"
	die "another apply or an abandoned lock exists: $state_dir/lock (check its pid before removing it)"
fi
registering=no
[[ $interrupted_status == 0 ]] || exit "$interrupted_status"
printf '%s\n' "$$" > "$state_dir/lock/pid"
registering=yes
log_file=$(
	trap '' HUP INT TERM
	mktemp "$state_dir/run.XXXXXX"
)
registering=no
[[ $interrupted_status == 0 ]] || exit "$interrupted_status"
printf 'Repository: %s\nPlatform: %s %s %s\nProfile: %s\n' "$repo_root" "$platform" "$os_version" "$arch" "$profile" >> "$log_file"
start_entry_logging
if [[ ${DOTFILES_BOOTSTRAP_STREAM-} == 1 ]]; then
	say "Guest bootstrap log: $log_file"
else
	say "Apply log: $log_file"
fi
phase='software installation'
prepare_platform
install_software
if ! required_tool_ready go; then
	run 'install latest stable Go' 1200 python3 -B "$script_dir/bootstrap/resources.py" install-go "$platform-$arch"
	hash -r
fi
prepare_java_and_maven
verify_requirements base
verify_build_tools
verify_java_build_tools

phase='terminal definition'
run 'prepare xterm-ghostty terminfo' 120 python3 -B "$script_dir/bootstrap/terminfo.py"

phase='deployment'
output_source=deploy run 'deployment preflight' 120 "$BASH" "$script_dir/deploy.sh" --dry-run
output_source=deploy run 'configuration deployment' 120 "$BASH" "$script_dir/deploy.sh" --apply

# ===== 应用与终端资源 =====
phase='Zsh plugins'
if [[ ! -r $HOME/.local/share/antidote/antidote.zsh ]]; then
	run 'Antidote installation' 600 python3 -B "$script_dir/bootstrap/resources.py" install antidote universal
fi
run 'Zsh plugin preparation' 900 python3 -B "$script_dir/bootstrap/without_tty.py" \
	zsh -d -f "$script_dir/bootstrap/zsh.zsh" "$HOME/.config/zsh/.zsh_plugins.txt"

phase='Neovim plugins'
run 'stage Neovim configuration and locked managers' 900 python3 -B "$script_dir/bootstrap/resources.py" stage-nvim "$repo_root" "$scratch/config"
output_source=nvim run 'Neovim plugins, tools and parsers' 2400 env XDG_CONFIG_HOME="$scratch/config" NVIM_LOG_FILE="$scratch/nvim.log" nvim --headless -u NONE -n -i NONE -l "$script_dir/bootstrap/nvim.lua"

phase='terminal resources'
if [[ $profile == desktop ]]; then
	install_desktop
	verify_requirements desktop
	for font in iosevka sarasa; do
		run "prepare font family: $font" 1800 python3 -B "$script_dir/bootstrap/resources.py" font "$font" "$platform"
	done
else
	say 'SKIP: Ghostty and fonts (server profile; install fonts on the terminal client).'
fi

# ===== 最终验收；总体结果由 EXIT 清理后报告 =====
phase=verification
verify_requirements all
doctor_args=(--runtime)
if [[ $profile == server ]]; then
	# 不把服务器上刻意省略的桌面程序/字体当成安装遗漏。
	for module in environment deployment dependencies zsh git lazygit nvim starship atuin shuck vim state; do
		doctor_args+=(--only "$module")
	done
fi
output_source=doctor run 'doctor checks' 600 "$BASH" "$script_dir/doctor.sh" "${doctor_args[@]}"
completed=yes
