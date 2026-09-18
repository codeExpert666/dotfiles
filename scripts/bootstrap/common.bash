#!/usr/bin/env bash

# 仅供 bootstrap.sh 和平台安装器使用；Bash 3.2+，加载时只定义函数。
# 入口拥有 phase、scratch、probe_home、log_file、owns_lock、completed；
# 执行器维护 active_pid、timer_pid、registering、interrupted_status。
# 目标检查写 target_dir；平台识别写 platform、arch、os_version；工具查询写 tool_path。

# ===== 报告与生命周期 =====
# shellcheck disable=SC2154 # 入口脚本初始化并持有共享运行状态。
say() {
	printf '%s\n' "$*" >&2
	if [[ -n $log_file ]]; then printf '%s\n' "$*" >> "$log_file"; fi
}

die() {
	say "FAIL: $*"
	exit 1
}

stop_children() {
	if [[ -n $timer_pid ]]; then
		kill -KILL -- "-$timer_pid" 2> /dev/null || :
		wait "$timer_pid" 2> /dev/null || :
	fi
	if [[ -n $active_pid ]]; then
		if kill -TERM -- "-$active_pid" 2> /dev/null; then sleep 0.2; fi
		kill -KILL -- "-$active_pid" 2> /dev/null || :
		wait "$active_pid" 2> /dev/null || :
	fi
	active_pid='' timer_pid=''
}

# shellcheck disable=SC2317 # trap 入口。
interrupt_bootstrap() {
	interrupted_status="$1"
	[[ $registering == yes ]] || exit "$interrupted_status"
}

# shellcheck disable=SC2317 # trap 入口。
finish_bootstrap() {
	local status="$1" cleanup_status=0
	[[ $BASH_SUBSHELL == 0 ]] || return "$status"
	trap - EXIT
	trap '' HUP INT TERM
	stop_children
	if [[ -n $scratch ]] && ! rm -rf -- "$scratch"; then
		say "FAIL cleanup: could not remove temporary directory: $scratch" || :
		cleanup_status=1
	fi
	if [[ $owns_lock == yes ]]; then
		if ! rm -f -- "$state_dir/lock/pid" || ! rmdir -- "$state_dir/lock"; then
			say "FAIL cleanup: could not release lock: $state_dir/lock" || :
			cleanup_status=1
		fi
	fi
	# 主体失败和中断码优先；仅主体成功时由清理错误决定最终状态。
	if [[ $status == 0 && $cleanup_status != 0 ]]; then
		status="$cleanup_status"
		phase=cleanup
	fi
	if [[ $status != 0 ]]; then
		say "FAILED phase: $phase (exit $status). Completed changes remain; fix the reported problem and rerun the same command." || :
	elif [[ $completed == yes ]]; then
		if [[ $mode == dry-run ]]; then
			say 'Preview complete; no installation, downloads or initialization performed.'
		else
			say 'Bootstrap complete. Review any doctor WARN/SKIP details above; GUI rendering, clipboard and local overrides may need manual verification.'
			say 'Open a new terminal. Personal follow-up: Git identity, optional login-shell change, and optional Atuin history import/login.'
		fi
	fi
	[[ -z $log_file ]] || printf 'Log: %s\n' "$log_file" >&2
	exit "$status"
}

initialize_scratch() {
	# 命令替换忽略组信号，确保目录路径交回父进程后再处理已记录的中断。
	registering=yes
	scratch=$(
		trap '' HUP INT TERM
		mktemp -d "${TMPDIR:-/tmp}/dotfiles-bootstrap.XXXXXX"
	) || die 'could not allocate bootstrap temporary directory'
	registering=no
	[[ $interrupted_status == 0 ]] || exit "$interrupted_status"
	scratch=$(cd -- "$scratch" && pwd -P)
	probe_home="$scratch/home"
	mkdir -p "$probe_home/.config" "$probe_home/.local/share" "$probe_home/.local/state" "$probe_home/.cache"
}

# 私有执行器只有两种输出用途：隔离的短查询，或流式记录安装任务。
# 每次任务和看门狗各有独立进程组；sudo 认证由前台 authorize_sudo 完成。
execute() {
	local seconds="$1" output="$2" status=0
	shift 2
	rm -f -- "$scratch/timed-out" || die 'could not reset command timeout marker'
	registering=yes
	set -m
	(
		set +m
		trap - EXIT HUP INT TERM
		if [[ $output == probe ]]; then
			# 版本/能力查询也会创建应用状态。选中的二进制来自真实 PATH，
			# 配置、状态、日志及工作目录全部指向本次私有目录。
			ulimit -f 2048 || exit 1
			cd -- "$probe_home" || exit 1
			exec env -i HOME="$probe_home" PATH="$PATH" TMPDIR="$scratch" LC_ALL=C \
				XDG_CONFIG_HOME="$probe_home/.config" XDG_DATA_HOME="$probe_home/.local/share" \
				XDG_STATE_HOME="$probe_home/.local/state" XDG_CACHE_HOME="$probe_home/.cache" \
				NVIM_LOG_FILE="$scratch/nvim.log" "$@" > "$scratch/probe.log" 2> "$scratch/probe.err"
		else
			# 命令与 tee 同属当前任务组。及时写日志，并分别取得两个退出码；
			# 命令已经失败时保留它的状态，不能被后续日志转发失败覆盖。
			"$@" 2>&1 | tee -a "$log_file" >&2
			pipeline_status=("${PIPESTATUS[@]}")
			if [[ ${pipeline_status[0]} != 0 ]]; then exit "${pipeline_status[0]}"; fi
			if [[ ${pipeline_status[1]} != 0 ]]; then
				printf 'FAIL logging: command output could not be forwarded (exit %s)\n' "${pipeline_status[1]}" >&2
			fi
			exit "${pipeline_status[1]}"
		fi
	) < /dev/null &
	active_pid=$!
	(
		set +m
		trap - EXIT HUP INT TERM
		sleep "$seconds"
		: > "$scratch/timed-out"
		kill -TERM -- "-$active_pid" 2> /dev/null || :
		sleep 1
		kill -KILL -- "-$active_pid" 2> /dev/null || :
	) > /dev/null 2>&1 &
	timer_pid=$!
	set +m
	registering=no
	[[ $interrupted_status == 0 ]] || exit "$interrupted_status"
	wait "$active_pid" 2> /dev/null || status=$?
	stop_children
	[[ ! -f $scratch/timed-out ]] || status=124
	return "$status"
}

run() {
	local label="$1" seconds="$2" status
	shift 2
	say "RUN: $label"
	if execute "$seconds" log "$@"; then
		say "READY: $label"
	else
		status=$?
		die "$label failed (exit $status)"
	fi
}

# ===== 目标环境与平台 =====

check_real_directory() {
	local directory="$1" parent
	parent="${directory%/*}"
	[[ -z $parent || $parent == "$directory" ]] || check_real_directory "$parent"
	[[ ! -L $directory ]] || die "directory must not be a symbolic link: $directory"
	if [[ -e $directory ]]; then
		[[ -d $directory ]] || die "not a directory: $directory"
	fi
}

check_environment() {
	local row name relative expected
	[[ ${HOME-} == /* && -d $HOME ]] || die 'HOME must be an existing absolute directory'
	for row in "${layout_xdg_paths[@]}"; do
		IFS='|' read -r name relative <<< "$row"
		expected="$HOME/$relative"
		layout_is_default_path "${!name}" "$expected" || die "$name must use $expected"
	done
	[[ ! ${ZDOTDIR+x} ]] || die 'ZDOTDIR must not be exported (deploy requires the root .zshenv entry)'
	[[ -z ${NVIM_APPNAME-} || $NVIM_APPNAME == nvim ]] || die 'NVIM_APPNAME must be unset or nvim'
	# git -C 不能消除继承的仓库上下文。此时 Git 可能尚未安装，因此直接检查环境；
	# 连空值也拒绝，普通用户配置和鉴权配置仍由 Git 读取。
	for name in GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_INDEX_FILE \
		GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_SHALLOW_FILE \
		GIT_GRAFT_FILE GIT_NAMESPACE GIT_IMPLICIT_WORK_TREE GIT_PREFIX; do
		if declare -p "$name" > /dev/null 2>&1; then
			die "$name must be unset; remove this Git repository context override and rerun bootstrap from a normal shell"
		fi
	done
	# 保留传给 deploy/doctor 的 HOME/XDG 表示，只用物理目标检查目录归属。
	target_dir=$(cd -- "$HOME" && pwd -P)
	for expected in "$target_dir/.local" "$target_dir/.local/bin" "$target_dir/.local/share/dotfiles-bootstrap" "$target_dir/.local/state/dotfiles-bootstrap" "$target_dir/.cache/dotfiles-bootstrap"; do
		check_real_directory "$expected"
	done
}

detect_platform() {
	local kernel machine key value distro='' release=''
	kernel=$(uname -s)
	machine=$(uname -m)
	case $machine in x86_64) arch=x86_64 ;; aarch64 | arm64) arch=arm64 ;; *) die "unsupported architecture: $machine" ;; esac
	case $kernel in
		Linux)
			platform=linux
			# 只解析需要的字段，不执行发行版描述文件。
			while IFS='=' read -r key value; do
				value="${value#\"}" value="${value%\"}"
				case $key in ID) distro="$value" ;; VERSION_ID) release="$value" ;; esac
			done < /etc/os-release
			[[ $distro == ubuntu && ($release == 24.04 || $release == 26.04) ]] || die "supported Linux systems: Ubuntu 24.04/26.04 (found $distro $release)"
			os_version="$release"
			;;
		Darwin)
			platform=macos
			os_version=$(sw_vers -productVersion)
			[[ $arch == arm64 && (${os_version%%.*} == 15 || ${os_version%%.*} == 26) ]] || die 'supported macOS systems: macOS 15/26 on Apple Silicon'
			;;
		*) die "unsupported operating system: $kernel" ;;
	esac
}

prepare_command_path() {
	local remaining entry normalized=''
	# macOS 沿用 .zshenv 中用户命令目录优先于 Homebrew 公共命令目录的规则。
	# 准备阶段额外加入 Homebrew 的 sbin，并优先从 node@24 专用目录查找 Node 命令。
	if [[ $platform == macos ]]; then
		PATH="/opt/homebrew/opt/node@24/bin:/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
	fi
	remaining="$HOME/.local/bin:$PATH:"
	# 查询会切换工作目录，先固定相对/空 PATH 分量的原始查找含义。
	while [[ $remaining == *:* ]]; do
		entry="${remaining%%:*}" remaining="${remaining#*:}"
		case $entry in
			/*) ;;
			'') entry="$PWD" ;;
			*) entry="$PWD/$entry" ;;
		esac
		normalized+="${normalized:+:}$entry"
	done
	export PATH="$normalized"
}

# ===== 工具要求与能力验证 =====

version_ge() {
	local left="$1" right="$2" a b i
	for ((i = 0; i < 3; i++)); do
		a="${left%%.*}" b="${right%%.*}"
		[[ -n $a ]] || a=0
		[[ -n $b ]] || b=0
		((10#$a < 10#$b)) && return 1
		((10#$a > 10#$b)) && return 0
		if [[ $left == *.* ]]; then left="${left#*.}"; else left=0; fi
		if [[ $right == *.* ]]; then right="${right#*.}"; else right=0; fi
	done
	return 0
}

tool_ready() {
	local name="$1" minimum="$2" output version
	tool_path=$(type -P "$name") || tool_path=''
	if [[ -z $tool_path && $name == fd ]]; then tool_path=$(type -P fdfind) || tool_path=''; fi
	if [[ -z $tool_path && $name == ghostty && $platform == macos ]]; then
		for tool_path in /Applications/Ghostty.app/Contents/MacOS/ghostty "$HOME/Applications/Ghostty.app/Contents/MacOS/ghostty"; do
			[[ ! -x $tool_path ]] || break
		done
	fi
	[[ -n $tool_path && -x $tool_path ]] || return 1
	# 无版本约束的系统工具先按可执行文件判断，实际功能由安装/验收阶段验证。
	[[ $minimum != 0 ]] || return 0
	execute 8 probe "$tool_path" --version || return 1
	output=$(< "$scratch/probe.log")
	if [[ $name == lazygit ]]; then
		[[ $output =~ version=([0-9]+\.[0-9]+\.[0-9]+) ]] || return 1
	else
		[[ $output =~ ([0-9]+\.[0-9]+(\.[0-9]+)?) ]] || return 1
	fi
	version="${BASH_REMATCH[1]}"
	version_ge "$version" "$minimum" || return 1
	if [[ $name == nvim ]]; then [[ $output == *LuaJIT* ]] || return 1; fi
	return 0
}

authorize_sudo() {
	command -v sudo > /dev/null || die 'sudo is required to install system packages'
	# 下载/插件准备期间认证可能过期；每次特权步骤前在前台刷新，有效时无需再输入密码。
	say 'Checking sudo authorization for system package installation.'
	sudo -v || die 'sudo authentication failed'
}

requirements_for() {
	local selection="$1" name minimum scope target
	while IFS=$'\t' read -r name minimum scope target; do
		[[ -n $name && $name != \#* ]] || continue
		[[ $target == any || $target == "$platform" ]] || continue
		if [[ $selection == all ]]; then
			[[ $scope == base || $profile == desktop ]] || continue
		else
			[[ $scope == "$selection" ]] || continue
		fi
		printf '%s\t%s\n' "$name" "$minimum"
	done < "$script_dir/bootstrap/requirements.tsv"
}

required_tool_ready() {
	local command="$1" name minimum scope target
	while IFS=$'\t' read -r name minimum scope target; do
		[[ $name == "$command" && ($target == any || $target == "$platform") ]] || continue
		if tool_ready "$name" "$minimum"; then return 0; else return 1; fi
	done < "$script_dir/bootstrap/requirements.tsv"
	die "no compatibility requirement declared for command: $command"
}

preview_requirements() {
	local name minimum
	while IFS=$'\t' read -r name minimum; do
		if tool_ready "$name" "$minimum"; then
			# 命令兼容与包管理器安装状态分别判断；FOUND 不承诺跳过 Brewfile。
			say "FOUND: $name ($tool_path; minimum $minimum)"
		else
			say "PLAN: $name >= $minimum is required by the selected configuration"
		fi
	done < <(requirements_for all)
}

verify_requirements() {
	local name minimum
	while IFS=$'\t' read -r name minimum; do
		tool_ready "$name" "$minimum" || die "$name >= $minimum is still unavailable; check the package source and whether PATH shadows the prepared version"
		say "READY: $name ($tool_path)"
	done < <(requirements_for "$1")
}

verify_build_tools() {
	local label seconds binary source="$scratch/compiler-probe.c" output="$scratch/compiler-probe"
	printf 'int main(void) { return 0; }\n' > "$source"
	for label in npm compiler executable; do
		case $label in
			npm)
				binary=$(type -P npm)
				seconds=15
				set -- "$binary" --version
				;;
			compiler)
				binary=$(type -P cc)
				seconds=30
				set -- "$binary" -x c "$source" -o "$output"
				;;
			executable)
				seconds=10
				set -- "$output"
				;;
		esac
		if ! execute "$seconds" probe "$@"; then
			cat "$scratch/probe.log" "$scratch/probe.err" >&2
			cat "$scratch/probe.log" "$scratch/probe.err" >> "$log_file"
			die "required build capability failed: $label"
		fi
	done
	say 'READY: npm and native C compilation/execution'
}
