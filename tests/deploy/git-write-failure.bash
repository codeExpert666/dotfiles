#!/usr/bin/env bash

# 仅由 Git 写入失败用例通过 BASH_ENV 加载；其他 printf 调用仍走真实内建命令。
# 不在 deploy 启动前限制写入：Bash 3.2 的 here-string 也要写临时文件。
printf() {
	if [[ ${1-} == '[include]\n\tpath = config.shared\n' ]]; then
		# 此时 stdout 已重定向到 Git 暂存文件。先留下部分内容，再让真实写入触发
		# SIGXFSZ；不另建子 Shell，由生产脚本自己的写入子 Shell 承担信号。
		builtin printf '[include]\n' || return 98
		ulimit -c 0 || return 98
		ulimit -f 0 || return 98
		builtin printf 'test printf: limiting Git config write after partial output\n' >&2
	fi
	# 透明转发被测脚本的格式串和参数。
	# shellcheck disable=SC2059
	builtin printf "$@"
}
