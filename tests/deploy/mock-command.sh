#!/bin/sh

set -e

# 测试专用命令替身；复制到 case_bin 后按命令名分派，所有路径来自隔离用例。
# 按 POSIX sh 单独检查本文件，不能 source 到测试入口。
case ${0##*/} in
	# --- 平台选择与 Stow 参数记录 ---
	uname)
		printf '%s\n' "$DOTFILES_TEST_KERNEL"
		;;
	stow)
		# 只记录参数，不调用真实 Stow，尤其不执行可能被污染的 --adopt。
		printf '%s\n' "$@" > "$DOTFILES_TEST_STOW_ARGS"
		;;
	git)
		# --- Git 能力探测与已有配置查询故障 ---
		query=0
		personal_config=0
		for argument; do
			case $DOTFILES_TEST_GIT_MODE:$argument in
				unsupported-fixed-value:--fixed-value)
					printf 'test git: unknown option: fixed-value\n' >&2
					exit 129
					;;
				probe-failure:--fixed-value)
					printf 'test git: injected capability query failure\n' >&2
					exit 69
					;;
			esac
			if [ "$argument" = --get-all ]; then
				query=1
			elif [ "$argument" = "$DOTFILES_TEST_TARGET/.config/git/config" ]; then
				personal_config=1
			fi
		done
		if [ "$DOTFILES_TEST_GIT_MODE" = query-failure ] && [ "$query" = 1 ] && [ "$personal_config" = 1 ]; then
			printf 'test git: injected query failure\n' >&2
			exit 69
		fi
		exec "$DOTFILES_TEST_REAL_GIT" "$@"
		;;
	mktemp)
		# --- 暂存目录创建与路径交接检查点 ---
		: "${DOTFILES_TEST_TARGET:?missing isolated test target}"
		if [ "$1" != -d ] || [ "$2" != "$DOTFILES_TEST_TARGET/.config/git/.deploy-git.XXXXXX" ]; then
			exec "$DOTFILES_TEST_REAL_MKTEMP" "$@"
		fi
		case $DOTFILES_TEST_MKTEMP_MODE in
			fail)
				printf 'test mktemp: injected creation failure\n' >&2
				exit 71
				;;
			wait-before-output | wait-after-output | fail-after-output) ;;
			*) exec "$DOTFILES_TEST_REAL_MKTEMP" "$@" ;;
		esac
		staging_path=$("$DOTFILES_TEST_REAL_MKTEMP" "$@") || exit "$?"
		printf '%s\n' "$staging_path" > "$DOTFILES_TEST_STAGING_PATH"
		if [ "$DOTFILES_TEST_MKTEMP_MODE" != wait-before-output ]; then
			printf '%s\n' "$staging_path"
		fi
		if [ "$DOTFILES_TEST_MKTEMP_MODE" = fail-after-output ]; then
			printf 'test mktemp: injected failure after path output\n' >&2
			exit 71
		fi
		# ready 确认目录已创建；用例发信号后通过 release 允许路径交接完成。
		# 沿用被测命令的信号状态，避免替身自行忽略信号而掩盖回归。
		printf '%s\n' "$$" > "$DOTFILES_TEST_READY"
		attempt=0
		while [ ! -e "$DOTFILES_TEST_RELEASE" ]; do
			attempt=$((attempt + 1))
			if [ "$attempt" -gt 250 ]; then
				printf 'test mktemp: signal checkpoint timed out\n' >&2
				exit 98
			fi
			sleep 0.02
		done
		if [ "$DOTFILES_TEST_MKTEMP_MODE" = wait-before-output ]; then
			printf '%s\n' "$staging_path"
		fi
		;;
	rm)
		# 只拒绝删除本用例的 Git 暂存目录；运行器始终使用保存的真实 rm 收尾。
		: "${DOTFILES_TEST_TARGET:?missing isolated test target}"
		if [ "$DOTFILES_TEST_RM_MODE" = git-fail ]; then
			for argument; do
				case $argument in
					"$DOTFILES_TEST_TARGET/.config/git/.deploy-git."*)
						printf '%s\n' "$argument" > "$DOTFILES_TEST_STAGING_PATH"
						printf 'test rm: injected staging cleanup failure\n' >&2
						exit 72
						;;
				esac
			done
		fi
		exec "$DOTFILES_TEST_REAL_RM" "$@"
		;;
	ln)
		# --- 发布检查点与入口占位故障 ---
		: "${DOTFILES_TEST_TARGET:?missing isolated test target}"
		if [ "$3" != "$DOTFILES_TEST_TARGET/.config/git/" ]; then
			exec "$DOTFILES_TEST_REAL_LN" "$@"
		fi
		case $DOTFILES_TEST_LN_MODE:$3 in
			"wait-git:$DOTFILES_TEST_TARGET/.config/git/" | "wait-git-stubborn:$DOTFILES_TEST_TARGET/.config/git/")
				# ready 是同步检查点；sleep 仅限制故障夹具的最长寿命。
				if [ "$DOTFILES_TEST_LN_MODE" = wait-git-stubborn ]; then trap '' HUP INT TERM; fi
				printf '%s\n' "$$" > "$DOTFILES_TEST_READY"
				sleep 30
				printf 'test ln: signal checkpoint timed out\n' >&2
				exit 98
				;;
		esac
		# 只在本机 Git 入口的最终发布处注入占位和失败。
		git_entry="$DOTFILES_TEST_TARGET/.config/git/config"
		case $DOTFILES_TEST_LN_MODE in
			git-file) printf 'keep Git occupant\n' > "$git_entry" ;;
			git-directory) mkdir "$git_entry" ;;
			git-directory-link) "$DOTFILES_TEST_REAL_LN" -s "$DOTFILES_TEST_OUTSIDE" "$git_entry" ;;
			git-dangling) "$DOTFILES_TEST_REAL_LN" -s missing-config "$git_entry" ;;
			git-fail) exit 77 ;;
			git-noop) exit 0 ;;
		esac
		"$DOTFILES_TEST_REAL_LN" "$@" || exit "$?"
		case $DOTFILES_TEST_LN_MODE in
			git-remove-include) printf '# 发布后移除共享配置引用。\n' > "$git_entry" ;;
			git-remove-shared) "$DOTFILES_TEST_REAL_RM" "$DOTFILES_TEST_TARGET/.config/git/config.shared" ;;
			git-remove-platform) "$DOTFILES_TEST_REAL_RM" "$DOTFILES_TEST_TARGET/.config/ghostty/platform.ghostty" ;;
			git-replace-platform)
				"$DOTFILES_TEST_REAL_RM" "$DOTFILES_TEST_TARGET/.config/ghostty/platform.ghostty"
				"$DOTFILES_TEST_REAL_LN" -s common.ghostty "$DOTFILES_TEST_TARGET/.config/ghostty/platform.ghostty"
				;;
		esac
		;;
	*)
		printf 'test fixture: unsupported command name\n' >&2
		exit 98
		;;
esac
