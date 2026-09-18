#!/usr/bin/env bash

# Bash 测试共享设施：隔离仓库、夹具、日志、快照和自有进程组。
# 加载时仅定义函数；入口负责初始化与安装 trap。临时量使用 local。
# shellcheck disable=SC2034 # 公共夹具状态也由入口和套件专用设施使用。

initialize_test_state() {
	# EXIT 可能在应用调用的日志重定向内触发；保留入口通道供收尾诊断使用。
	exec 3>&1 4>&2
	base_path="$PATH"
	real_bash="$BASH"
	[[ $real_bash == /* ]] || real_bash="$PWD/$real_bash"
	test_root=''
	active_pid=''
	managed_groups=()
	registering=no
	interrupted_status=0
	stop_signal=TERM
	pass_count=0
	skip_count=0
	suite_label="${1:-Tests}"
	fixture_count=0
	case_label='test setup'
	case_phase='initialization'
	case_log=''
	case_stdout=''
	failure_reported=no
	suite_complete=no
}

install_test_traps() {
	trap 'finish_suite "$?"' EXIT
	trap 'report_error "$?" "${BASH_SOURCE[0]}" "$LINENO" "$BASH_COMMAND"' ERR
	trap 'interrupt_suite HUP 129' HUP
	trap 'interrupt_suite INT 130' INT
	trap 'interrupt_suite TERM 143' TERM
}

prepare_suite() {
	local command_name package canonical_root
	source_repo="$1"
	for command_name in git stow uname dirname cp ln rm find sort cmp diff readlink stat cksum grep env chmod mkdir mktemp cat ps sleep; do
		command -v "$command_name" > /dev/null 2>&1 || fail "required test command not found: $command_name"
	done
	real_git=$(command -v git)
	real_rm=$(command -v rm)
	host_kernel=$(uname -s)
	case $host_kernel in
		Linux | Darwin) ;;
		*) fail "unsupported test host: $host_kernel" ;;
	esac
	registering=yes
	test_root=$(mktemp -d "${2:-/tmp}/dotfiles-test.XXXXXX")
	registering=no
	[[ $interrupted_status == 0 ]] || exit "$interrupted_status"
	# 先保留 mktemp 原路径，规范化失败时 EXIT 仍能删除它。
	canonical_root=$(cd -- "$test_root" && pwd -P)
	test_root="$canonical_root"
	base_repo="$test_root/repo with spaces"
	mkdir "$base_repo"
	packages=(atuin ghostty git lazygit nvim shuck skills starship vim zsh)
	for package in scripts codex "${packages[@]}" ghostty-macos; do
		cp -R "$source_repo/$package" "$base_repo/$package"
	done
}

# --- 失败报告与统一收尾 ---

report_error() {
	printf 'ERROR: %s:%s: exit %s: %s\n' "$2" "$3" "$1" "$4" >&2
	exit "$1"
}

fail() {
	printf 'FAIL: %s (%s): %s\n' "$case_label" "$case_phase" "$*" >&2
	failure_reported=yes
	exit 1
}

interrupt_suite() {
	stop_signal="$1"
	interrupted_status="$2"
	# 临时目录创建、后台任务启动与资源登记之间也可能收到信号。
	# 先保存清理所需的路径或进程组，再由调用处退出。
	[[ $registering == yes ]] || exit "$interrupted_status"
}

finish_suite() {
	local status="$1" cleanup_status can_remove=yes
	# ERR 会继承到命令替换和快照子 Shell，资源只由入口 Shell 收尾。
	[[ $BASH_SUBSHELL == 0 ]] || return "$status"
	trap - EXIT ERR
	trap '' HUP INT TERM
	exec 1>&3 2>&4
	if ! stop_managed_groups; then
		printf 'FAIL: could not stop test processes; keeping %s\n' "$test_root" >&2
		can_remove=no
		[[ $status != 0 ]] || status=1
	fi
	if [[ $status == 0 && $suite_complete != yes ]]; then
		printf 'FAIL: test suite exited before completion\n' >&2
		status=1
	fi
	if [[ $status != 0 ]]; then
		if [[ $failure_reported != yes ]]; then
			printf 'FAIL: %s (%s): exited %s\n' "$case_label" "$case_phase" "$status" >&2
		fi
		if [[ -s $case_stdout ]]; then
			printf 'Command stdout:\n' >&2
			cat "$case_stdout" >&2 || :
		fi
		if [[ -f $case_log ]]; then cat "$case_log" >&2 || :; fi
	fi
	if [[ -n $test_root && $can_remove == yes ]]; then
		if "$real_rm" -rf -- "$test_root"; then
			test_root=''
		else
			cleanup_status=$?
			printf 'FAIL: could not remove test directory (exit %s): %s\n' "$cleanup_status" "$test_root" >&2
			[[ $status != 0 ]] || status="$cleanup_status"
		fi
	fi
	if [[ $status == 0 ]]; then
		printf '%s tests passed: %s; skipped: %s (native host: %s; Bash: %s; simulated branches labeled above)\n' \
			"$suite_label" "$pass_count" "$skip_count" "$host_kernel" "$BASH_VERSION"
	fi
	exit "$status"
}

pass() {
	pass_count=$((pass_count + 1))
	printf 'PASS: %s\n' "$case_label"
}

skip() {
	skip_count=$((skip_count + 1))
	printf 'SKIP: %s: %s\n' "$case_label" "$*"
}

new_fixture() {
	fixture_count=$((fixture_count + 1))
	case_label="$1"
	case_phase='fixture setup'
	case_root="$test_root/case $fixture_count"
	case_repo="$base_repo"
	case_home="$case_root/home with spaces"
	case_outside="$case_root/outside"
	case_work="$case_root/work"
	# HOME 是被测进程的入口；case_target 保留物理路径，以便核验 HOME 目录别名。
	case_target="$case_home"
	case_bin="$case_root/bin"
	case_log="$case_root/command.log"
	case_stdout=''
	case_path="$case_bin:$base_path"
	case_environment=()
	mkdir -p "$case_home" "$case_outside" "$case_work" "$case_bin" "$case_root/tmp"
}

# ===== 自有进程组 =====

register_group() {
	managed_groups+=("$1")
}

forget_group() {
	local group
	local retained=()
	for group in "${managed_groups[@]}"; do
		if [[ $group != "$1" ]]; then retained+=("$group"); fi
	done
	managed_groups=("${retained[@]}")
}

group_is_running() {
	local rows group state
	rows=$(ps -axo pgid=,stat=) || return 2
	while read -r group state; do
		# 已退出、等待回收的僵尸进程不会再访问夹具。
		if [[ $group == "$1" && $state != Z* ]]; then return 0; fi
	done <<< "$rows"
	return 1
}

wait_for_group() {
	local attempt status
	for ((attempt = 0; attempt < 100; attempt++)); do
		if group_is_running "$1"; then
			sleep 0.02
		else
			status=$?
			[[ $status == 1 ]] && return 0
			return "$status"
		fi
	done
	return 1
}

stop_managed_groups() {
	local group status=0
	for group in "${managed_groups[@]}"; do
		kill -s "$stop_signal" -- "-$group" 2> /dev/null || :
		if ! wait_for_group "$group"; then
			kill -KILL -- "-$group" 2> /dev/null || :
			wait_for_group "$group" || status=1
		fi
		wait "$group" 2> /dev/null || :
	done
	active_pid=''
	managed_groups=()
	return "$status"
}

# 配置入口通过 Python 管理应用会话和超时；入口 wait 后台进程，信号 trap 可及时执行。
# 部署替身执行器保留自身的管道与写入限制场景。
run_test_command() {
	local mode="$1" timeout="$2" actual test_python
	shift 2
	test_python=$(command -v python3) || fail 'required test command not found: python3'
	[[ -z $active_pid ]] || fail 'another test command is still active'
	registering=yes
	set -m
	(
		set +m
		trap - EXIT ERR HUP INT TERM
		cd "$case_work" || exit 98
		# 不继承宿主的 XDG、ZDOTDIR、STARSHIP_CONFIG 或 Git 配置覆盖变量。
		exec env -i PATH="$base_path" HOME="$case_home" TMPDIR="$case_root/tmp" \
			TERM=xterm-256color LC_ALL=C GIT_CONFIG_NOSYSTEM=1 \
			"$test_python" -B "$source_repo/tests/support/harness.py" "$mode" "$timeout" "$@"
	) &
	active_pid=$!
	register_group "$active_pid"
	set +m
	registering=no
	[[ $interrupted_status == 0 ]] || exit "$interrupted_status"
	if wait "$active_pid"; then actual=0; else actual=$?; fi
	wait_for_group "$active_pid" || fail 'test command left running descendants or process inspection failed'
	forget_group "$active_pid"
	active_pid=''
	return "$actual"
}

# ===== 路径断言与文件快照 =====

assert_absent() {
	[[ ! -e $1 && ! -L $1 ]] || fail "path should be absent: $1"
}

assert_empty() {
	local entries
	[[ -d $1 && ! -L $1 ]] || fail "not a real directory: $1"
	entries=$(find "$1" -mindepth 1 -print)
	[[ -z $entries ]] || fail "directory should be empty: $1"
}

assert_link() {
	[[ -L $1 && $1 -ef $2 ]] || fail "unexpected link: $1"
}

file_metadata() {
	# 不比较 atime：只读检查本身可能更新它。
	if [[ $host_kernel == Linux ]]; then
		stat -c '%d|%i|%f|%s|%y' "$1"
	else
		stat -f '%d|%i|%p|%z|%m' "$1"
	fi
}

snapshot_tree() (
	cd "$1" || exit 1
	# 这些夹具的路径受测试控制，不含换行。排序使遍历顺序稳定。
	find . -print | LC_ALL=C sort | while IFS= read -r entry; do
		printf 'path: %s\n' "$entry"
		file_metadata "$entry"
		if [[ -L $entry ]]; then
			readlink "$entry"
		elif [[ -f $entry ]]; then
			cksum < "$entry"
		fi
	done
)

assert_unchanged() {
	snapshot_tree "$1" > "$case_root/after.snapshot"
	if ! cmp -s "$2" "$case_root/after.snapshot"; then
		diff -u "$2" "$case_root/after.snapshot" >&2 || :
		fail "filesystem state changed: $1"
	fi
}
