#!/usr/bin/env bash

# 部署专用设施：命令替身、调用环境、报告与完整布局断言。
# 共享设施只定义函数；入口和运行器自检仍各自持有运行状态。
# 路径提示兼容入口/支持文件的直接检查，以及编辑器从仓库根目录传入标准输入的检查。
# shellcheck source-path=SCRIPTDIR/..
# shellcheck source-path=SCRIPTDIR/../..
# shellcheck source=tests/support/harness.bash
source "${BASH_SOURCE[0]%/*}/../support/harness.bash"

new_case() {
	new_fixture "$1"
	real_ln=$(command -v ln)
	real_mktemp=$(command -v mktemp)
	case_separate_output=no
	case_kernel="$host_kernel"
	case_git_mode='query-failure'
	case_ln_mode=''
	case_rm_mode=''
	case_mktemp_mode=''
	case_stow_options=''
	case_limit_writes='no'
	case_run_count=0
}

# ===== 命令替身与部署执行 =====

# --- 替身安装与受限 PATH ---

install_mock() {
	local mock_path="$case_bin/$1"
	case $1 in
		uname | stow | git | ln | rm | mktemp) ;;
		*) fail "unsupported mock command: $1" ;;
	esac
	# 先移除临时目录中的链接，避免写入时沿链接覆盖真实命令。
	rm -f "$mock_path"
	cp "$source_repo/tests/deploy/mock-command.sh" "$mock_path"
	chmod +x "$mock_path"
}

restricted_path_without() {
	local omitted="$1"
	local name command_path
	for name in bash dirname uname git stow ln mktemp rm cmp cat mkdir; do
		if [[ $name != "$omitted" ]]; then
			if [[ $name == bash ]]; then command_path="$real_bash"; else command_path=$(command -v "$name"); fi
			ln -s "$command_path" "$case_bin/$name"
		fi
	done
	case_path="$case_bin"
}

# --- 部署调用与等待 ---

start_deploy() {
	[[ -z $active_pid ]] || fail 'another deployment is still active'
	case_run_count=$((case_run_count + 1))
	case_phase="deployment invocation $case_run_count"
	case_log="$case_root/deploy.$case_run_count.log"
	case_stdout="$case_root/deploy.$case_run_count.stdout"
	registering=yes
	# Bash 的作业控制为这一后台任务建立私有进程组，无需依赖 setsid。
	# 子 Shell 关闭作业控制，使部署、Stow 和日志管道留在同一个组中。
	set -m
	(
		set +m
		trap - EXIT ERR
		trap 'exit 129' HUP
		trap 'exit 130' INT
		trap 'exit 143' TERM
		cd "$case_work" || exit 98
		# 管道让日志不受写入限制影响，pipefail 保留退出码；单引号中的变量交给子 Bash 展开。
		# 通道检查用例单独捕获 stdout，其余用例沿用合并管道以覆盖写入限制和信号清理。
		# shellcheck disable=SC2016
		env -i PATH="$case_path" HOME="$case_home" "${case_environment[@]}" \
			LC_ALL=C GIT_CONFIG_NOSYSTEM=1 \
			DOTFILES_TEST_KERNEL="$case_kernel" \
			DOTFILES_TEST_REAL_GIT="$real_git" DOTFILES_TEST_REAL_LN="$real_ln" \
			DOTFILES_TEST_REAL_RM="$real_rm" DOTFILES_TEST_REAL_MKTEMP="$real_mktemp" \
			DOTFILES_TEST_GIT_MODE="$case_git_mode" \
			DOTFILES_TEST_LN_MODE="$case_ln_mode" DOTFILES_TEST_TARGET="$case_target" \
			DOTFILES_TEST_RM_MODE="$case_rm_mode" DOTFILES_TEST_MKTEMP_MODE="$case_mktemp_mode" \
			DOTFILES_TEST_OUTSIDE="$case_outside" \
			DOTFILES_TEST_STOW_ARGS="$case_root/stow.args" stow_options="$case_stow_options" \
			DOTFILES_TEST_READY="$case_root/mock.ready" \
			DOTFILES_TEST_RELEASE="$case_root/mock.release" DOTFILES_TEST_STAGING_PATH="$case_root/staging.path" \
			DOTFILES_TEST_LIMIT_WRITES="$case_limit_writes" \
			DOTFILES_TEST_SEPARATE_OUTPUT="$case_separate_output" DOTFILES_TEST_STDOUT_LOG="$case_stdout" \
			"$real_bash" -c '
				if [[ $DOTFILES_TEST_SEPARATE_OUTPUT == yes ]]; then
					exec > "$DOTFILES_TEST_STDOUT_LOG"
				fi
				if [[ $DOTFILES_TEST_LIMIT_WRITES == yes ]]; then
					ulimit -c 0 || exit 98
					ulimit -f 0 || exit 98
				fi
				exec "$@"
			' bash "$real_bash" "$case_repo/scripts/deploy.sh" "$@" 2>&1 | (
			# 日志接收端等生产者关闭管道再退出；同时杀掉 cat 会使清理日志触发 SIGPIPE。
			trap '' HUP INT TERM
			exec cat
		)
	) > "$case_log" 2>&1 &
	active_pid=$!
	register_group "$active_pid"
	set +m
	registering=no
	[[ $interrupted_status == 0 ]] || exit "$interrupted_status"
}

wait_deploy() {
	local expected="$1" actual
	if wait "$active_pid"; then
		actual=0
	else
		actual=$?
	fi
	wait_for_group "$active_pid" || fail 'deployment left running descendants or process inspection failed'
	forget_group "$active_pid"
	active_pid=''
	[[ $actual == "$expected" ]] || fail "expected exit $expected, got $actual"
	case_phase="assertions after invocation $case_run_count"
}

run_deploy() {
	local expected="$1"
	shift
	start_deploy "$@"
	wait_deploy "$expected"
}

wait_for_checkpoint() {
	local attempt
	for ((attempt = 0; attempt < 100; attempt++)); do
		[[ ! -s $case_root/mock.ready ]] || return 0
		sleep 0.05
	done
	fail 'deployment did not reach the mock checkpoint within five seconds'
}

run_git() (
	# 从仓库外走 Git 的正常查找流程，避免当前项目的 .git/config 干扰断言。
	cd "$case_work" || exit 1
	env -i PATH="$base_path" HOME="$case_home" "${case_environment[@]}" \
		LC_ALL=C GIT_CONFIG_NOSYSTEM=1 "$real_git" config "$@"
)

# ===== 断言与布局核验 =====

# --- 路径与日志断言 ---

assert_no_temporary_entries() {
	local entries
	entries=$(find "$case_target" -name '.deploy-git.*')
	[[ -z $entries ]] || fail "temporary deployment entries remain: $entries"
}

assert_no_log() {
	local status
	if grep -Eq -- "$1" "${2:-$case_log}"; then
		fail "unexpected output matching: $1"
	else
		status=$?
		[[ $status == 1 ]] || fail "could not inspect deployment log"
	fi
}

# 每条指定行恰好出现一次，并保持给定顺序；不固定 Stow 的完整原生明细。
assert_log_sequence() {
	local expected match position previous=0
	for expected in "$@"; do
		match=$(grep -Fnx -- "$expected" "$case_log") || fail "missing output line: $expected"
		[[ $match != *$'\n'* ]] || fail "repeated output line: $expected"
		position="${match%%:*}"
		((position > previous)) || fail "output line is out of order: $expected"
		previous="$position"
	done
}

assert_result() {
	local expected="$1" line last_line='' count=0
	while IFS= read -r line; do
		if [[ $line == result:* ]]; then
			[[ $line == "$expected" ]] || fail "unexpected result: $line"
			count=$((count + 1))
		fi
		last_line="$line"
	done < "$case_log"
	[[ $count == 1 && $last_line == "$expected" ]] || fail 'expected exactly one result at the end of the report'
}

assert_failure_result() {
	local phase="$1" status="$2" mode="${3:-apply}" outcome stow_result='stow: completed'
	if [[ $mode == dry-run ]]; then stow_result='stow: simulation completed'; fi
	assert_log_sequence 'dotfiles deployment' 'initialization: checking environment, paths and platform'
	# 已完成阶段保留结果；当前失败阶段不能同时报告完成或通过。
	assert_no_log "^deploy:|^$phase: ((simulation )?completed|passed)(;|$)"
	if [[ $phase == initialization || $phase == preflight ]]; then
		outcome='deployment not started; no changes made'
		if [[ $phase == initialization ]]; then
			assert_no_log '^preflight:'
		else
			assert_log_sequence 'initialization: checking environment, paths and platform' \
				'initialization: completed' 'preflight: checking target and dependencies'
		fi
		assert_no_log '^preflight: passed$|^stow packages:|^stow output:|^stow:|^git entry output:|^git entry:|^(CREATE|UNCHANGED):|^verification:'
	elif [[ $mode == dry-run ]]; then
		outcome='no changes made'
	else
		outcome='any completed changes were kept'
	fi
	case $phase in
		stow)
			assert_log_sequence 'initialization: completed' 'preflight: passed' 'stow output:'
			assert_no_log '^git entry output:|^git entry:|^verification:'
			;;
		'git entry')
			assert_log_sequence 'initialization: completed' 'preflight: passed' "$stow_result" 'git entry output:'
			assert_no_log '^verification:'
			;;
		'codex agent')
			assert_log_sequence 'preflight: passed' "$stow_result" 'git entry: completed' 'codex agent output:'
			assert_no_log '^verification:'
			;;
		verification)
			assert_log_sequence 'initialization: completed' 'preflight: passed' "$stow_result" \
				'git entry: completed' 'verification: checking deployment'
			;;
	esac
	if [[ $case_separate_output == yes ]]; then
		[[ -f $case_stdout && ! -s $case_stdout ]] || fail 'failure report leaked to stdout'
	fi
	assert_result "result: failed during $phase (exit $status); $outcome"
}

assert_deployment_output() {
	local mode="$1" action="$2" mode_line action_count platform=linux stow_result git_result
	local action_line="$action: Git config: $case_target/.config/git/config (include.path = config.shared)"
	if [[ $case_kernel == Darwin ]]; then platform=macos; fi
	[[ -f $case_stdout && ! -s $case_stdout ]] || fail 'deployment report leaked to stdout'
	assert_no_log '^plan:|^created:|^deploy:|^simulation:'
	if [[ $mode == dry-run ]]; then
		mode_line='mode: dry-run (no filesystem changes)'
		stow_result='stow: simulation completed'
		git_result='git entry: simulation completed; no filesystem changes'
	else
		mode_line='mode: apply (filesystem changes allowed)'
		stow_result='stow: completed'
		git_result='git entry: completed'
	fi
	assert_log_sequence 'dotfiles deployment' "$mode_line" \
		'initialization: checking environment, paths and platform' \
		"repository: $case_repo" "platform: $platform" "target: $case_target" \
		'initialization: completed' \
		'preflight: checking target and dependencies' 'preflight: passed' \
		'stow packages:' 'stow output:' "$stow_result" 'git entry output:' "$action_line" "$git_result"
	action_count=$(grep -Ec '^(CREATE|UNCHANGED): Git config:' "$case_log") || fail 'missing Git action'
	[[ $action_count == 1 ]] || fail 'Git action was reported more than once'
	if [[ $mode == dry-run ]]; then
		assert_no_log '^stow: completed$|^git entry: completed$|^verification:'
		assert_result 'result: dry run completed; no changes made'
	else
		assert_log_sequence "$git_result" 'verification: checking deployment' 'verification: passed'
		assert_no_log '^(stow|git entry): simulation completed|^WARNING: in simulation mode'
		assert_result 'result: deployment completed'
	fi
}

# --- 完整部署布局 ---

verify_layout() {
	local kernel="$1"
	local package source_path prefix relative_path dir_path actual expected
	local selected_packages=("${packages[@]}")
	if [[ $kernel == Darwin ]]; then selected_packages+=(ghostty-macos); fi
	printf './.config/git/config\n./.codex\n./.codex/agents\n./.codex/agents/sol_worker.toml\n' > "$case_root/expected-paths.unsorted"
	for package in "${selected_packages[@]}"; do
		prefix="$case_repo/$package"
		# 固定排除包内源目录；不读取忽略规则，避免规则失效时测试也跟着放行。
		find "$prefix" -mindepth 1 -path "$case_repo/skills/src" -prune -o -type d -print > "$case_root/source-dirs"
		while IFS= read -r source_path; do
			relative_path="${source_path#"$prefix/"}"
			dir_path="$case_target/$relative_path"
			[[ -d $dir_path && ! -L $dir_path ]] || fail "folded or missing directory: $dir_path"
			printf './%s\n' "$relative_path" >> "$case_root/expected-paths.unsorted"
		done < "$case_root/source-dirs"
		find "$prefix" \( -path "$case_repo/skills/src" -o -name .stow-local-ignore \) -prune -o \
			\( -type f -o -type l \) -print > "$case_root/source-files"
		while IFS= read -r source_path; do
			relative_path="${source_path#"$prefix/"}"
			assert_link "$case_target/$relative_path" "$source_path"
			printf './%s\n' "$relative_path" >> "$case_root/expected-paths.unsorted"
		done < "$case_root/source-files"
	done
	assert_codex_agent_copy
	[[ -f $case_target/.config/git/config && ! -L $case_target/.config/git/config ]] ||
		fail 'Git entry is not a regular file'
	printf '[include]\n\tpath = config.shared\n' > "$case_root/git-entry.expected"
	cmp -s "$case_root/git-entry.expected" "$case_target/.config/git/config" ||
		fail 'new Git entry contains unexpected content'
	run_git --file "$case_target/.config/git/config" --no-includes --fixed-value \
		--get-all include.path config.shared > /dev/null || fail 'missing direct Git include'
	expected=$(run_git --file "$case_repo/git/.config/git/config.shared" --no-includes --get init.defaultBranch)
	actual=$(run_git --get init.defaultBranch)
	[[ $actual == "$expected" ]] || fail 'shared Git setting did not load'
	assert_absent "$case_target/.gitconfig"
	assert_absent "$case_target/.config/ghostty/config"
	if [[ $kernel == Darwin ]]; then
		assert_link "$case_target/.config/ghostty/platform.ghostty" "$case_repo/ghostty-macos/.config/ghostty/platform.ghostty"
	else
		assert_absent "$case_target/.config/ghostty/platform.ghostty"
		assert_absent "$case_target/.config/ghostty/shaders"
	fi
	LC_ALL=C sort -u "$case_root/expected-paths.unsorted" > "$case_root/expected-paths"
	(cd "$case_target" && find . -mindepth 1 -print) | LC_ALL=C sort > "$case_root/actual-paths"
	if ! cmp -s "$case_root/expected-paths" "$case_root/actual-paths"; then
		diff -u "$case_root/expected-paths" "$case_root/actual-paths" >&2 || :
		fail 'target contains missing or unexpected paths'
	fi
}

# Codex 的角色文件必须是独立普通副本，不能是仓库文件链接。
assert_codex_agent_copy() {
	local entry="$case_target/.codex/agents/sol_worker.toml"
	[[ -f $entry && ! -L $entry ]] || fail 'Codex agent must be a regular file'
	[[ ! $entry -ef $case_repo/codex/agents/sol_worker.toml ]] || fail 'Codex agent must be an independent copy'
	cmp -s "$entry" "$case_repo/codex/agents/sol_worker.toml" || fail 'Codex agent content differs from source'
}
