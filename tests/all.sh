#!/usr/bin/env bash

# 全量回归入口；单套回归直接调用 deploy/doctor/bootstrap/config-loading.sh。
# 固定顺序运行五个独立套件；夹具、断言和清理由各套件持有。
# 用目标 Bash 启动本文件即可将它传到 Python 测试及被测子入口。
set -e
unset CDPATH
active_pid=''
registering=no
interrupted_status=0
stop_signal=TERM

finish() {
	local status="$1"
	trap - EXIT
	trap '' HUP INT TERM
	if [[ -n $active_pid ]]; then
		# 套件拥有夹具及后代；转交信号并等待它完成清理。
		kill -s "$stop_signal" "$active_pid" 2> /dev/null || :
		wait "$active_pid" 2> /dev/null || :
	fi
	exit "$status"
}

interrupt() {
	stop_signal="$1"
	interrupted_status="$2"
	[[ $registering == yes ]] || exit "$interrupted_status"
}

trap 'finish "$?"' EXIT
trap 'interrupt HUP 129' HUP
trap 'interrupt INT 130' INT
trap 'interrupt TERM 143' TERM

script_parent="${BASH_SOURCE[0]%/*}"
[[ $script_parent != "${BASH_SOURCE[0]}" ]] || script_parent=.
test_dir=$(cd -- "$script_parent" && pwd -P)
printf 'Test interpreter: %s (%s)\n' "$BASH" "$BASH_VERSION"
for suite in deploy doctor bootstrap config-loading multipass; do
	printf '\nRunning %s tests\n' "$suite"
	registering=yes
	# 作业控制保留子入口接收 INT 的能力；父入口用 wait 及时处理信号。
	set -m
	"$BASH" "$test_dir/$suite.sh" &
	active_pid=$!
	set +m
	registering=no
	[[ $interrupted_status == 0 ]] || exit "$interrupted_status"
	wait "$active_pid"
	active_pid=''
done
