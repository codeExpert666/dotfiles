#!/usr/bin/env bash

# 当前机器的诊断入口。默认只读取用户配置，所有探测输出写入私有临时目录。
# Bash 3.2+；校验：bash -n、shellcheck；格式：shfmt -ci -sr。
# 每项检查显式处理失败，不使用 set -e，以便一次报告所有独立问题。
# shellcheck disable=SC2317,SC2329 # check_* 函数由已验证的模块白名单动态调度。
unset CDPATH
umask 077

# ===== 命令行与模块清单 =====
doctor_modules=(environment deployment dependencies zsh git lazygit nvim starship atuin shuck vim ghostty terminal state)

valid_module() {
	local module
	for module in "${doctor_modules[@]}"; do
		[[ $1 != "$module" ]] || return 0
	done
	return 1
}

usage() {
	local line
	while IFS= read -r line; do
		if [[ $line == @MODULES@ ]]; then
			printf '  %s\n' "${doctor_modules[*]}"
		else
			printf '%s\n' "$line"
		fi
	done << 'HELP'
Usage: doctor.sh [--verbose] [--runtime] [--only MODULE ...]
       doctor.sh {-h|--help}

Modules:
@MODULES@

Options:
  --only MODULE  Select a module; repeat to select several (default: all).
  --verbose      Include bounded native error output and override details.
  --runtime      Also run controlled Zsh, Neovim, Vim and Lazygit sessions in
                 temporary homes. Python 3.7+ is needed for staging/PTYs. Plugins
                 are reused; automatic installation/update is disabled.
                 Local executable overrides or missing prerequisites cause
                 runtime checks to be skipped. These are new test sessions,
                 not observations of an already running shell/editor.

Default checks are offline. They never install, deploy or repair anything.
Configuration queries use temporary state; actual path discovery and Git
queries inspect the inherited environment. Explicit-path parsing is labeled
separately from discovery and runtime validation. GUI rendering/clipboard
and remote client fonts require manual verification.
Dependency checks require a complete, consistent JDK 21+ for JDTLS and stable
Maven 3.9.x using that JDK; their version and runtime queries remain offline.

Requirements: Linux or macOS, Bash 3.2+, standard Unix utilities.
Native checks without their application are skipped after reporting it missing.
Native commands have an 8-second timeout; runtime checks have 30 seconds.
Only private temporary files are created and removed by doctor.

Output: reports go to stderr; this help goes to stdout. No color is required.
Exit: 0 = no FAIL (WARN/SKIP may remain), 1 = health failures,
      2 = invalid invocation or checker could not run; signals use 128+signal.

Examples:
  bash scripts/doctor.sh
  bash scripts/doctor.sh --only zsh --only nvim --runtime
  bash scripts/doctor.sh --verbose 2>doctor.log
HELP
}

argument_error() {
	printf 'error: %s\nRun doctor.sh --help for usage.\n' "$1" >&2
	exit 2
}

verbose=no runtime=no selected=' '
if (($# == 1)) && [[ $1 == -h || $1 == --help ]]; then
	usage
	exit 0
fi
while (($#)); do
	case $1 in
		--verbose)
			verbose=yes
			shift
			;;
		--runtime)
			runtime=yes
			shift
			;;
		--only)
			(($# >= 2)) || argument_error '--only requires a module'
			if valid_module "$2"; then
				selected+="$2 "
				shift 2
			else
				argument_error "unknown module: $2"
			fi
			;;
		*) argument_error "unknown argument or combined help: $1" ;;
	esac
done

# ===== 报告、状态与生命周期 =====
# 本入口拥有临时目录和计数；capture 维护 probe_* 及当前任务/计时器 PID。
pass_count=0 warn_count=0 fail_count=0 skip_count=0 summary_ready=no
scratch='' active_pid='' timer_pid='' registering=no interrupted_status=0 probe_number=0
probe_status=0 probe_stdout='' probe_stderr=''

report() {
	local level="$1" id="$2" message="$3" hint="${4-}"
	case $level in
		PASS) pass_count=$((pass_count + 1)) ;;
		WARN) warn_count=$((warn_count + 1)) ;;
		FAIL) fail_count=$((fail_count + 1)) ;;
		SKIP) skip_count=$((skip_count + 1)) ;;
		*)
			printf 'error: invalid diagnostic status\n' >&2
			exit 2
			;;
	esac
	printf '%-4s %s: %s\n' "$level" "$id" "$message" >&2
	[[ -z $hint ]] || printf '     hint: %s\n' "$hint" >&2
}

# shellcheck disable=SC2317 # 由信号 trap 调用。
interrupt_doctor() {
	interrupted_status="$1"
	[[ $registering == yes ]] || exit "$interrupted_status"
}

stop_probe() {
	# 每个任务有独立进程组；即使命令本身退出，也收回它遗留的子进程。
	if [[ -n $timer_pid ]]; then
		kill -KILL -- "-$timer_pid" 2> /dev/null || :
		wait "$timer_pid" 2> /dev/null || :
	fi
	if [[ -n $active_pid ]]; then
		if kill -TERM -- "-$active_pid" 2> /dev/null; then sleep 0.2; fi
		kill -KILL -- "-$active_pid" 2> /dev/null || :
		wait "$active_pid" 2> /dev/null || :
	fi
	timer_pid='' active_pid=''
}

# shellcheck disable=SC2317 # 由 EXIT trap 调用。
finish_doctor() {
	local status="$1"
	[[ $BASH_SUBSHELL == 0 ]] || return "$status"
	trap - EXIT
	trap '' HUP INT TERM
	stop_probe
	if [[ -n $scratch ]]; then
		if ! rm -rf -- "$scratch"; then
			report FAIL doctor.cleanup "could not remove $scratch"
			[[ $status != 0 ]] || status=2
		fi
	fi
	if [[ $summary_ready == yes ]]; then
		printf '\nresult: PASS=%s WARN=%s FAIL=%s SKIP=%s\n' "$pass_count" "$warn_count" "$fail_count" "$skip_count" >&2
	fi
	if [[ $status -ge 128 ]]; then printf 'doctor: interrupted (exit %s)\n' "$status" >&2; fi
	exit "$status"
}

trap 'finish_doctor "$?"' EXIT
trap 'interrupt_doctor 129' HUP
trap 'interrupt_doctor 130' INT
trap 'interrupt_doctor 143' TERM

# ===== 探测与诊断辅助 =====

# 用 Bash 作业控制创建进程组，无需 GNU timeout/setsid，兼容 macOS。
# 默认通过 ulimit -f 限制子进程写入文件的大小；报告不执行日志内容。
capture() {
	local seconds="$1" marker limit=yes
	shift
	if [[ ${1-} == --allow-large-files ]]; then
		limit=no
		shift
	fi
	probe_number=$((probe_number + 1))
	probe_stdout="$scratch/probe.$probe_number.out"
	probe_stderr="$scratch/probe.$probe_number.err"
	marker="$scratch/probe.$probe_number.timeout"
	registering=yes
	set -m
	(
		set +m
		trap - EXIT HUP INT TERM
		# 受控运行需复制已安装的二进制插件，通过 --allow-large-files 关闭文件大小限制。
		if [[ $limit == yes ]]; then ulimit -f 2048; fi
		exec env TMPDIR="$scratch" "$@"
	) < /dev/null > "$probe_stdout" 2> "$probe_stderr" &
	active_pid=$!
	(
		set +m
		trap - EXIT HUP INT TERM
		sleep "$seconds"
		printf 'timeout\n' > "$marker"
		kill -TERM -- "-$active_pid" 2> /dev/null || :
		sleep 1
		kill -KILL -- "-$active_pid" 2> /dev/null || :
	) > /dev/null 2>&1 &
	timer_pid=$!
	set +m
	registering=no
	[[ $interrupted_status == 0 ]] || exit "$interrupted_status"
	if wait "$active_pid" 2> /dev/null; then probe_status=0; else probe_status=$?; fi
	stop_probe
	if [[ -f $marker ]]; then probe_status=124; fi
	return "$probe_status"
}

# 对原生解析使用隔离状态和中立工作目录；显式传入待验证的真实配置路径。
# 路径发现检查另行保留真实环境，避免把修正后的环境误报为当前配置生效。
clean_probe() {
	local seconds="$1"
	shift
	capture "$seconds" env -i HOME="$scratch/home" PATH="$PATH" TMPDIR="$scratch" \
		XDG_CONFIG_HOME="$scratch/home/.config" XDG_DATA_HOME="$scratch/home/.local/share" \
		XDG_STATE_HOME="$scratch/home/.local/state" XDG_CACHE_HOME="$scratch/home/.cache" \
		TERM="${TERM:-xterm-256color}" LC_ALL=C "$@"
}

native_error() {
	local id="$1" message="$2" hint="$3"
	if [[ $probe_status == 124 ]]; then message+=" (timed out)"; fi
	report FAIL "$id" "$message; command exit $probe_status" "$hint"
	native_details
}

native_details() {
	local line count=0
	if [[ $verbose == yes && -r $probe_stderr ]]; then
		while IFS= read -r line && ((count < 12)); do
			printf '     native: %s\n' "$line" >&2
			count=$((count + 1))
		done < "$probe_stderr"
	fi
}

find_tool() {
	tool_path=$(type -P "$1") || tool_path=''
	if [[ -z $tool_path && $1 == ghostty && $platform == macos ]]; then
		for tool_path in /Applications/Ghostty.app/Contents/MacOS/ghostty "$HOME/Applications/Ghostty.app/Contents/MacOS/ghostty"; do
			[[ ! -x $tool_path ]] || return 0
		done
		tool_path=''
	fi
	[[ -n $tool_path ]]
}

need_tool() {
	if find_tool "$1"; then return 0; fi
	report "${2:-WARN}" "$3" "$1 is unavailable; dependent native checks are skipped" \
		"Install $1 and make it executable through PATH (software preparation belongs to bootstrap)."
	return 1
}

check_file() {
	if [[ -f $2 && -r $2 ]]; then return 0; fi
	report FAIL "$1" "missing, unreadable or invalid file: $2" "$deploy_hint"
	return 1
}

check_override() {
	local name="$1" expected="${2-}" value="${!1}"
	if [[ -n $value && $value != "$expected" ]]; then
		report WARN "environment.$name" "$name changes the application's default configuration or behavior" \
			"Review the override before interpreting explicit-path or controlled-session checks."
		if [[ $verbose == yes ]]; then printf '     value: %q\n' "$value" >&2; fi
	fi
}

consume_records() {
	local file="$1" level id message hint
	record_count=0
	while IFS=$'\t' read -r level id message hint; do
		case $level in
			PASS | WARN | FAIL | SKIP)
				report "$level" "$id" "$message" "$hint"
				record_count=$((record_count + 1))
				;;
		esac
	done < "$file"
}

run_runtime() {
	local module="$1" result row name relative expected failures_before="$fail_count"
	shift
	if [[ $runtime != yes ]]; then
		report SKIP "$module.runtime" 'controlled session was not requested' "Use --only $module --runtime to validate a new session."
		return 0
	fi
	for row in "${layout_xdg_paths[@]}"; do
		IFS='|' read -r name relative <<< "$row"
		expected="$HOME/$relative"
		if ! layout_is_default_path "${!name}" "$expected"; then
			report SKIP "$module.runtime" "$name selects a nondefault layout; controlled session would test a different environment" 'Resolve or review this environment override first.'
			return 0
		fi
	done
	if [[ ($module == zsh && ${ZDOTDIR+x}) || ($module == nvim && -n ${NVIM_APPNAME-} && $NVIM_APPNAME != nvim) || ($module == vim && -n ${VIMINIT-}${EXINIT-}) ]]; then
		report SKIP "$module.runtime" 'an inherited startup override changes the selected configuration' 'Review ZDOTDIR, NVIM_APPNAME or VIMINIT/EXINIT before running a baseline session.'
		return 0
	fi
	need_tool python3 WARN "$module.runtime-python" || return 0
	if capture 30 --allow-large-files "$tool_path" -B "$script_dir/doctor/runtime.py" "$module" "$repo_root" "$scratch" "$HOME" "$@"; then
		result="$probe_stdout"
		consume_records "$result"
		if ((fail_count > failures_before)); then native_details; fi
		if [[ $record_count == 0 ]]; then report FAIL "$module.runtime" 'runtime helper returned no evidence' 'Inspect the helper installation.'; fi
	else
		consume_records "$probe_stdout"
		native_error "$module.runtime" 'controlled session failed' 'Use --verbose to inspect startup diagnostics; no installation was attempted.'
	fi
}

# ===== 环境、部署与应用检查 =====

check_environment() {
	local row name relative expected entry
	report PASS environment.platform "$platform; Bash $BASH_VERSION; repository $repo_root"
	if [[ $relative_path == yes ]]; then
		report WARN environment.PATH 'PATH includes relative or empty components; command lookup is anchored to the calling directory' 'Prefer absolute executable directories in PATH.'
	fi
	for row in "${layout_xdg_paths[@]}"; do
		IFS='|' read -r name relative <<< "$row"
		expected="$HOME/$relative"
		if layout_is_default_path "${!name}" "$expected"; then
			report PASS "environment.$name" "uses the default directory"
		else
			report FAIL "environment.$name" "does not resolve to $expected" "Unset $name or point it to the default before deploying."
		fi
	done
	if [[ ${ZDOTDIR+x} ]]; then
		report FAIL environment.ZDOTDIR 'ZDOTDIR is exported, so a child Zsh can bypass ~/.zshenv' 'Remove the export; the managed ~/.zshenv sets an unexported ZDOTDIR.'
	else
		report PASS environment.ZDOTDIR 'ZDOTDIR is not inherited'
	fi
	for name in STARSHIP_CONFIG NVIM_APPNAME LG_CONFIG_FILE LG_CONFIG_DIR ATUIN_CONFIG_DIR ATUIN_DB_PATH ATUIN_HISTORY_FILTER SHUCK_CONFIG TERMINFO TERMINFO_DIRS; do
		expected=''
		case $name in
			STARSHIP_CONFIG) expected="$HOME/.config/starship.toml" ;;
			NVIM_APPNAME) expected=nvim ;;
			LG_CONFIG_FILE) expected="$HOME/.config/lazygit/config.yml" ;;
			LG_CONFIG_DIR) expected="$HOME/.config/lazygit" ;;
			ATUIN_CONFIG_DIR) expected="$HOME/.config/atuin" ;;
		esac
		check_override "$name" "$expected"
	done
	for name in VIMINIT EXINIT; do
		if [[ -n ${!name} ]]; then report WARN "environment.$name" 'an executable Vim startup override is set' 'Review how it changes .vimrc discovery.'; fi
	done
	for name in GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM GIT_CONFIG_NOSYSTEM GIT_CONFIG_COUNT GIT_CONFIG_PARAMETERS GIT_EDITOR GIT_PAGER; do
		# 不打印任意 Git -c 配置值，可能含凭据或 HTTP 请求头。
		if [[ -n ${!name} ]]; then report WARN "environment.$name" "$name is set; Git may override shared defaults" 'Inspect this override and the reported key origins.'; fi
	done
	for name in EDITOR VISUAL SUDO_EDITOR; do check_override "$name" nvim; done
	for entry in "$calling_dir/.stowrc" "$HOME/.stowrc" "$HOME/.stow-global-ignore"; do
		if [[ -e $entry || -L $entry ]]; then
			report FAIL deployment.stow-config "deploy rejects external Stow configuration: $entry" 'Review and move this entry aside before deployment.'
		fi
	done
}

check_link() {
	local relative="$1" source="$2" target="$HOME/$1"
	if [[ -L $target && $target -ef $source ]]; then
		report PASS "deployment.$relative" 'link resolves to this repository'
	elif [[ -L $target && ! -e $target ]]; then
		report FAIL "deployment.$relative" 'broken symbolic link' "$deploy_hint"
	elif [[ ! -e $target && ! -L $target ]]; then
		report FAIL "deployment.$relative" 'not deployed' "$deploy_hint"
	else
		report FAIL "deployment.$relative" 'entry is not a symbolic link to the managed source' \
			"Preserve personal content, review the conflicting entry, then preview deployment."
	fi
}

walk_package() {
	local base="$1" directory="$2" entry name relative
	for entry in "$directory"/* "$directory"/.[!.]* "$directory"/..?*; do
		[[ -e $entry || -L $entry ]] || continue
		name="${entry##*/}"
		relative="${entry#"$base/"}"
		# skills 的包内源目录不部署；其余沿用 Stow 默认忽略项。
		if [[ $base == "$repo_root/skills" && $relative == src ]]; then continue; fi
		case $name in .git | .gitignore | .gitmodules | .stow-local-ignore | CVS | .svn | .hg | .hgignore | RCS | *,v | *~ | .\#* | \#*\#) continue ;;
		esac
		case $relative in README* | LICENSE* | COPYING*) continue ;; esac
		if [[ -d $entry && ! -L $entry ]]; then
			walk_package "$base" "$entry"
		else
			check_link "$relative" "$entry"
		fi
	done
}

check_skill_sources() {
	local root="$repo_root/skills" directory name client relative entry row client_list
	local clients=()
	# 只核验本仓库约定的字面规则，不解释任意 Stow 忽略表达式。
	if [[ ! -r $root/.stow-local-ignore ]] || ! grep -Fxq '^/src$' "$root/.stow-local-ignore"; then
		report FAIL deployment.ignore.skills 'skills/.stow-local-ignore must contain ^/src$' 'Restore the source-directory exclusion before deployment.'
	fi
	if [[ ! -d $root/src || -L $root/src ]]; then
		report FAIL deployment.skill-source 'skills/src must be a real directory' 'Restore the shared Skill sources in this repository.'
		return
	fi
	for row in "${layout_skill_clients[@]}"; do
		IFS='|' read -r name client_list <<< "$row"
		read -r -a clients <<< "$client_list"
		directory="$root/src/$name"
		entry="$directory/SKILL.md"
		if [[ ! -d $directory || -L $directory || ! -f $entry || ! -r $entry || -L $entry ]]; then
			report FAIL "deployment.skill-source.$name" 'Skill source must be a real directory with a readable, regular SKILL.md' 'Restore the Skill directory and its ordinary SKILL.md file.'
		else
			report PASS "deployment.skill-source.$name" 'managed Skill entrypoint is a readable regular file'
		fi
		for client in "${clients[@]}"; do
			relative="$client/skills/$name"
			entry="$root/$relative"
			if [[ ! -L $entry || ! $entry -ef $directory ]]; then
				report FAIL "deployment.skill-alias.$relative" 'package entry must link to the matching Skill source' "Point skills/$relative to ../../src/$name."
			fi
		done
	done
}

competing_entry() {
	if [[ -e $2 || -L $2 ]]; then
		report FAIL "$1" "competing configuration: $2" "Review its settings and migrate them to $3 before deployment."
	fi
}

check_deployment() {
	local package directory entry row id _label relative expected
	for directory in "${layout_directories[@]}"; do
		entry="$HOME/$directory"
		if [[ -L $entry || (-e $entry && ! -d $entry) ]]; then
			report FAIL "deployment.directory.$directory" 'deploy requires a real directory' "$deploy_hint"
		fi
	done
	for package in "${layout_packages[@]}"; do
		if [[ ! -d $repo_root/$package ]]; then
			report FAIL "deployment.package.$package" 'package source directory is missing' 'Restore the repository source before deployment.'
			continue
		fi
		if [[ $package == skills ]]; then
			check_skill_sources
		elif [[ -e $repo_root/$package/.stow-local-ignore ]]; then
			report WARN "deployment.ignore.$package" 'custom Stow ignore rules are not interpreted by doctor' 'Review the inventory against deploy --dry-run.'
		fi
		walk_package "$repo_root/$package" "$repo_root/$package"
	done
	entry="$HOME/$layout_codex_agent_target"
	expected="$repo_root/$layout_codex_agent_source"
	if [[ -L $expected || ! -f $expected || ! -r $expected ]]; then
		report FAIL deployment.codex-agent-source 'Codex agent source must be a readable regular file' 'Restore codex/agents/sol_worker.toml.'
	elif [[ -L $entry || ! -f $entry ]] || ! cmp -s "$entry" "$expected"; then
		report FAIL "deployment.$layout_codex_agent_target" 'Codex agent must be a regular copy matching the repository' 'Review and back up differences, move the existing entry aside, then preview deployment.'
	else
		report PASS "deployment.$layout_codex_agent_target" 'regular Codex agent copy matches the repository'
	fi
	for row in "${layout_competing[@]}"; do
		IFS='|' read -r id _label relative expected <<< "$row"
		competing_entry "deployment.$id" "$HOME/$relative" "$HOME/$expected"
	done
	if [[ $platform == linux ]]; then
		competing_entry deployment.ghostty-platform "$HOME/$layout_ghostty_platform" 'the macOS-only package (this entry must be absent on Linux)'
	fi
	entry="$HOME/.config/git/config"
	if [[ -L $entry || ! -f $entry || ! -r $entry ]]; then
		report FAIL deployment.git-entry 'Git personal entry must be a readable regular file, not a link' "$deploy_hint"
	else
		report PASS deployment.git-entry 'personal Git entry is a regular file'
	fi
}

doctor_version_ge() {
	local actual="$1" required="$2" actual_part required_part
	while [[ -n $actual || -n $required ]]; do
		actual_part="${actual%%.*}"
		required_part="${required%%.*}"
		[[ $actual_part != "$actual" ]] && actual="${actual#*.}" || actual=''
		[[ $required_part != "$required" ]] && required="${required#*.}" || required=''
		actual_part="${actual_part:-0}"
		required_part="${required_part:-0}"
		if ((10#$actual_part > 10#$required_part)); then return 0; fi
		if ((10#$actual_part < 10#$required_part)); then return 1; fi
	done
	return 0
}

doctor_required_minimum() {
	local wanted="$1" name minimum _scope target
	while IFS=$'\t' read -r name minimum _scope target; do
		[[ $name == "$wanted" && ($target == any || $target == "$platform") ]] || continue
		printf '%s\n' "$minimum"
		return 0
	done < "$script_dir/bootstrap/requirements.tsv"
	return 1
}

doctor_physical_directory() {
	(cd -P -- "$1" 2> /dev/null && pwd -P)
}

check_java_dependencies() {
	local java_minimum maven_minimum java_binary='' javac_binary='' maven_binary=''
	local output='' java_version='' javac_version='' maven_version='' runtime_home='' maven_runtime='' line
	local java_pattern='version "([0-9]+([.][0-9]+){0,3})' javac_pattern='javac[[:space:]]+([0-9]+([.][0-9]+){0,3})'
	local maven_pattern='Apache Maven (3[.]9[.][0-9]+)([[:space:]]|$)'
	java_minimum=$(doctor_required_minimum java) || {
		report FAIL dependencies.java 'Java requirement is missing from bootstrap/requirements.tsv'
		return
	}
	maven_minimum=$(doctor_required_minimum mvn) || {
		report FAIL dependencies.maven 'Maven requirement is missing from bootstrap/requirements.tsv'
		return
	}
	if [[ -n ${JAVA_HOME-} ]]; then
		if [[ $JAVA_HOME != /* || ! -x $JAVA_HOME/bin/java || ! -x $JAVA_HOME/bin/javac ]]; then
			report FAIL dependencies.java-home "JAVA_HOME is not a complete JDK: $JAVA_HOME" 'Run bootstrap to prepare Temurin JDK 25, or point JAVA_HOME at a complete JDK 21+ with java and javac.'
			return
		fi
		java_binary="$JAVA_HOME/bin/java"
	else
		java_binary=$(type -P java) || java_binary=''
		if [[ -z $java_binary || ! -x $java_binary ]]; then
			report FAIL dependencies.java 'java is unavailable and JAVA_HOME is unset' 'Run bootstrap to prepare the latest stable Temurin JDK 25.'
			return
		fi
	fi
	if ! clean_probe 8 "$java_binary" -XshowSettings:properties -version; then
		native_error dependencies.java "$java_binary could not report its runtime properties" 'Repair this Java installation or rerun bootstrap.'
		return
	fi
	output=$(cat "$probe_stdout" "$probe_stderr")
	if [[ $output =~ $java_pattern ]]; then java_version="${BASH_REMATCH[1]}"; fi
	while IFS= read -r line; do
		line="${line%$'\r'}"
		case $line in *'java.home = '*)
			runtime_home="${line#*java.home = }"
			break
			;;
		esac
	done < "$probe_stderr"
	if [[ -z $java_version ]]; then
		report FAIL dependencies.java "$java_binary did not report a supported Java version" 'Repair this Java installation or rerun bootstrap.'
		return
	fi
	if [[ $runtime_home != /* || ! -x $runtime_home/bin/java || ! -x $runtime_home/bin/javac ]]; then
		report FAIL dependencies.java-home "selected Java is not a complete JDK: ${runtime_home:-missing java.home}" 'Run bootstrap to prepare Temurin JDK 25, or select a complete JDK 21+ with both java and javac.'
		return
	fi
	if [[ -n ${JAVA_HOME-} && $(doctor_physical_directory "$JAVA_HOME") != "$(doctor_physical_directory "$runtime_home")" ]]; then
		report FAIL dependencies.java-home "JAVA_HOME and the selected Java runtime disagree: $JAVA_HOME / $runtime_home" 'Point JAVA_HOME at the JDK reported by java.home, or rerun bootstrap.'
		return
	fi
	if ! doctor_version_ge "$java_version" "$java_minimum"; then
		report FAIL dependencies.java "Java $java_version is older than required $java_minimum for JDTLS" 'Run bootstrap to prepare the latest stable Temurin JDK 25.'
		return
	fi
	javac_binary="$runtime_home/bin/javac"
	if ! clean_probe 8 "$javac_binary" -version; then
		native_error dependencies.javac "$javac_binary could not report its version" 'Repair the selected JDK or rerun bootstrap.'
		return
	fi
	output=$(cat "$probe_stdout" "$probe_stderr")
	if [[ $output =~ $javac_pattern ]]; then javac_version="${BASH_REMATCH[1]}"; fi
	if [[ -z $javac_version || $javac_version != "$java_version" ]] || ! doctor_version_ge "${javac_version:-0}" "$java_minimum"; then
		report FAIL dependencies.javac "java and javac versions disagree or are too old: $java_version / ${javac_version:-unknown}" 'Use java and javac from the same complete JDK 21+ installation.'
		return
	fi
	if [[ -n ${JAVA_HOME-} ]]; then
		java_binary=$(type -P java) || java_binary=''
		javac_binary=$(type -P javac) || javac_binary=''
		if [[ -z $java_binary || -z $javac_binary || ! $java_binary -ef $runtime_home/bin/java || ! $javac_binary -ef $runtime_home/bin/javac ]]; then
			report FAIL dependencies.java-path "PATH does not select java and javac from JAVA_HOME: ${java_binary:-missing} / ${javac_binary:-missing}" "Put \$JAVA_HOME/bin before other Java command directories in PATH."
			return
		fi
	fi
	report PASS dependencies.java "complete JDK $java_version selected from $runtime_home"
	report PASS dependencies.javac "$javac_binary matches the selected Java runtime"
	maven_binary=$(type -P mvn) || maven_binary=''
	if [[ -z $maven_binary || ! -x $maven_binary ]]; then
		report FAIL dependencies.maven 'mvn is unavailable on PATH' 'Run bootstrap to prepare the latest stable Maven 3.9 release.'
		return
	fi
	if ! clean_probe 8 env MAVEN_SKIP_RC=1 JAVA_HOME="$runtime_home" PATH="$runtime_home/bin:$PATH" "$maven_binary" --version; then
		native_error dependencies.maven "$maven_binary could not report its version" 'Repair Maven or rerun bootstrap.'
		return
	fi
	output=$(cat "$probe_stdout" "$probe_stderr")
	if [[ $output =~ $maven_pattern ]]; then maven_version="${BASH_REMATCH[1]}"; fi
	if [[ -z $maven_version ]] || ! doctor_version_ge "$maven_version" "$maven_minimum"; then
		report FAIL dependencies.maven "$maven_binary is not a stable Maven 3.9 release at or above $maven_minimum" 'Run bootstrap to prepare the latest stable Maven 3.9 release.'
		return
	fi
	while IFS= read -r line; do
		line="${line%$'\r'}"
		case $line in *'runtime: '*)
			maven_runtime="${line##*runtime: }"
			break
			;;
		esac
	done < <(cat "$probe_stdout" "$probe_stderr")
	if [[ $maven_runtime != /* || $(doctor_physical_directory "$maven_runtime") != "$(doctor_physical_directory "$runtime_home")" ]]; then
		report FAIL dependencies.maven-runtime "Maven runtime does not match the selected JDK: ${maven_runtime:-missing runtime} / $runtime_home" 'Ensure mvn uses JAVA_HOME, then rerun bootstrap if the installation remains inconsistent.'
		return
	fi
	report PASS dependencies.maven "Maven $maven_version uses the selected JDK ($maven_binary)"
}

check_dependencies() {
	local name severity version location alternatives alternative distinct
	check_java_dependencies
	for name in git stow zsh nvim delta vim lazygit fzf atuin zoxide starship shuck rg fd curl node cc tree-sitter shellcheck shfmt; do
		severity=WARN
		case $name in git | zsh | nvim | delta) severity=FAIL ;; esac
		if [[ $name == fd ]] && ! find_tool fd && find_tool fdfind; then name=fdfind; fi
		need_tool "$name" "$severity" "dependencies.$name" || continue
		location="$tool_path"
		if [[ $name == cc && $platform == macos ]]; then
			if ! capture 8 xcode-select -p; then
				report WARN dependencies.cc 'compiler shim exists but Command Line Tools are not selected' 'Prepare Xcode Command Line Tools during bootstrap.'
				continue
			fi
		fi
		if clean_probe 8 "$location" --version; then
			IFS= read -r version < "$probe_stdout" || :
			if [[ -z $version ]]; then IFS= read -r version < "$probe_stderr" || :; fi
			report PASS "dependencies.$name" "$location (${version:0:180})"
		else
			native_error "dependencies.$name" "$location could not report its version" "Check this installation and PATH selection."
		fi
		alternatives=$(type -aP "$name") || alternatives=''
		distinct=no
		while IFS= read -r alternative; do
			if [[ -n $alternative && ! $alternative -ef $location ]]; then distinct=yes; fi
		done <<< "$alternatives"
		if [[ $distinct == yes ]]; then
			report WARN "dependencies.$name.path" 'multiple executable candidates; the first PATH match is used' "Selected: $location"
		fi
	done
	if need_tool stow WARN dependencies.stow-capability; then
		mkdir -p "$scratch/stow/doctor-probe" || exit 2
		if clean_probe 8 "$tool_path" --no-folding --simulate "--dir=$scratch/stow" "--target=$scratch/home" --stow doctor-probe; then
			report PASS dependencies.stow-capability '--no-folding is accepted for an empty temporary package'
		else
			native_error dependencies.stow-capability 'Stow could not use --no-folding' 'Check the GNU Stow installation.'
		fi
	fi
}

check_zsh() {
	local zsh_bin entry root cache plugin plugin_dir
	need_tool zsh FAIL zsh.command || return 0
	zsh_bin="$tool_path"
	for entry in "$HOME/.zshenv" "$HOME/.config/zsh/.zprofile" "$HOME/.config/zsh/.zshrc" "$HOME/.config/zsh/common.zsh" "$HOME/.config/zsh/$platform.zsh"; do
		check_file zsh.source "$entry" || continue
		if clean_probe 8 "$zsh_bin" -d -f -n "$entry"; then
			report PASS zsh.syntax "parsed $entry"
		else
			native_error zsh.syntax "syntax check failed: $entry" 'Correct the indicated Zsh syntax.'
		fi
	done
	for entry in "$HOME/.config/zsh/local.zsh" "$HOME/.config/zsh/local.zprofile"; do
		if [[ -f $entry && -r $entry ]]; then
			if clean_probe 8 "$zsh_bin" -d -f -n "$entry"; then
				report PASS zsh.local-syntax "parsed optional override: $entry"
			else
				native_error zsh.local-syntax "override syntax failed: $entry" 'Correct this local override.'
			fi
		fi
	done
	root="$HOME/.local/share/antidote"
	cache="$HOME/.cache/antidote"
	if [[ -r $root/antidote.zsh ]]; then
		report PASS zsh.antidote 'Antidote entry is readable'
	else
		report WARN zsh.antidote 'Antidote is missing; Zsh starts without managed plugins' "Prepare Antidote in $root during bootstrap."
	fi
	check_file zsh.manifest "$HOME/.config/zsh/.zsh_plugins.txt" || return 0
	while read -r plugin _ || [[ -n $plugin ]]; do
		[[ -n $plugin && $plugin != \#* ]] || continue
		plugin_dir=''
		for entry in "$cache/github.com/$plugin" "$cache/$plugin" "$cache/${plugin//\//-}"; do
			if [[ -d $entry ]]; then
				plugin_dir="$entry"
				break
			fi
		done
		if [[ -n $plugin_dir ]]; then
			report PASS "zsh.plugin.$plugin" "$plugin_dir"
		else
			report WARN "zsh.plugin.$plugin" 'plugin checkout is missing or uses an unrecognized cache layout' 'Prepare plugins with Antidote during bootstrap; doctor never downloads them.'
		fi
	done < "$HOME/.config/zsh/.zsh_plugins.txt"
	entry="$cache/zsh_plugins.zsh"
	if [[ ! -r $entry ]]; then
		report WARN zsh.plugin-cache 'generated plugin script is missing' 'Generate the Antidote cache during bootstrap.'
	elif [[ ! $entry -nt $HOME/.config/zsh/.zsh_plugins.txt ]]; then
		report WARN zsh.plugin-cache 'plugin script is older than or as old as its manifest' 'Regenerate the Antidote cache during bootstrap.'
	elif clean_probe 8 "$zsh_bin" -d -f -n "$entry"; then
		report PASS zsh.plugin-cache 'generated script exists, is newer than its manifest and parses'
	else
		native_error zsh.plugin-cache 'generated plugin script does not parse' 'Regenerate the Antidote cache after reviewing the manifest.'
	fi
	for plugin in fzf atuin zoxide starship; do
		need_tool "$plugin" WARN "zsh.$plugin" || continue
		case $plugin in
			fzf) clean_probe 8 "$tool_path" --zsh ;;
			atuin) clean_probe 8 "$tool_path" init zsh --disable-up-arrow --disable-ai ;;
			*) clean_probe 8 "$tool_path" init zsh ;;
		esac
		if [[ $probe_status != 0 ]]; then
			native_error "zsh.$plugin" 'could not generate the configured initialization script' 'Check the installed version against the arguments in .zshrc.'
			continue
		fi
		entry="$probe_stdout"
		if clean_probe 8 "$zsh_bin" -d -f -n "$entry"; then
			report PASS "zsh.$plugin" 'generated initialization parses (hooks are checked with --runtime)'
		else
			native_error "zsh.$plugin" 'generated Zsh initialization is invalid' 'Check the application installation.'
		fi
	done
	run_runtime zsh
}

check_git() {
	local git_bin config key values last origin expected row shared_found editor='' pager=''
	need_tool git FAIL git.command || return 0
	git_bin="$tool_path"
	config="$HOME/.config/git/config"
	if ! capture 8 "$git_bin" config --file /dev/null --no-includes --fixed-value --get-all include.path config.shared; then
		if [[ $probe_status != 1 ]]; then
			native_error git.fixed-value 'cannot use Git --fixed-value' 'Install a Git version supporting --fixed-value.'
		else
			report PASS git.fixed-value '--fixed-value is supported'
		fi
	else
		report PASS git.fixed-value '--fixed-value is supported'
	fi
	if check_file git.entry "$config"; then
		if capture 8 "$git_bin" config --file "$config" --no-includes --fixed-value --get-all include.path config.shared; then
			report PASS git.include 'personal entry directly includes config.shared'
		else
			native_error git.include 'direct config.shared include is missing or the personal file does not parse' 'Review the personal file; shared include must precede personal overrides.'
		fi
	fi
	# 从中立工作目录读取全局配置，避免运行 doctor 的仓库配置污染全局诊断。
	for key in core.editor core.pager interactive.diffFilter init.defaultBranch pull.ff merge.conflictStyle; do
		case $key in
			core.editor) expected='nvim' ;; core.pager) expected='delta' ;;
			interactive.diffFilter) expected='delta --color-only' ;; init.defaultBranch) expected=main ;;
			pull.ff) expected='only' ;; merge.conflictStyle) expected=zdiff3 ;;
		esac
		if capture 8 "$git_bin" config --global --includes --show-origin --get-all "$key"; then
			values=$(< "$probe_stdout")
			last="${values##*$'\n'}"
			origin="${last%%$'\t'*}"
			last="${last#*$'\t'}"
			shared_found=no
			while IFS= read -r row; do
				row="${row%%$'\t'*}"
				row="${row#file:}"
				if [[ $row -ef $repo_root/git/.config/git/config.shared ]]; then shared_found=yes; fi
			done <<< "$values"
			if [[ $shared_found != yes ]]; then
				report FAIL "git.$key.source" 'global lookup did not read the shared entry for this key' 'Review include.path, legacy entries and GIT_CONFIG_GLOBAL.'
			elif [[ $last == "$expected" ]]; then
				report PASS "git.$key" "$last; origin $origin"
			else
				report WARN "git.$key" "shared default is overridden; origin $origin" 'A personal override can be intentional; confirm its command or value is usable.'
			fi
		else
			native_error "git.$key" 'global config query failed or the key is absent' 'Review the personal entry, its includes and configuration overrides.'
		fi
	done
	# --global 不体现系统配置及环境覆盖；另查最终生效的已知键，不枚举个人配置。
	for key in GIT_EDITOR GIT_PAGER; do
		if capture 8 "$git_bin" var "$key"; then
			last=$(< "$probe_stdout")
			if [[ $key == GIT_EDITOR ]]; then
				editor="$last"
				expected='nvim'
			else
				pager="$last"
				expected='delta'
			fi
			if [[ $last == "$expected" ]]; then
				report PASS "git.$key.effective" "Git selects $expected"
			else
				report WARN "git.$key.effective" 'Git selects a personal command override' 'Confirm that the selected command is usable; doctor does not execute arbitrary editor/pager commands.'
			fi
		else
			native_error "git.$key.effective" 'effective command lookup failed' 'Review inherited Git configuration overrides.'
		fi
	done
	if [[ $editor == nvim ]]; then need_tool nvim FAIL git.editor || :; fi
	if [[ $pager == delta ]]; then need_tool delta FAIL git.pager || :; fi
	if capture 8 "$git_bin" config --includes --get interactive.diffFilter && [[ $(< "$probe_stdout") == 'delta --color-only' ]]; then
		if [[ $pager != delta ]]; then need_tool delta FAIL git.diff-filter || :; fi
	fi
	if find_tool delta; then
		if clean_probe 8 "$tool_path" --color-only --paging=never; then
			report PASS git.delta-capability 'delta accepts the configured filter and non-paging options'
		else
			native_error git.delta-capability 'delta rejected the configured options' 'Install a compatible delta version.'
		fi
	fi
	if capture 8 "$git_bin" config --includes --get merge.conflictStyle; then
		last=$(< "$probe_stdout")
		printf 'ours\n' > "$scratch/merge.ours"
		printf 'base\n' > "$scratch/merge.base"
		printf 'theirs\n' > "$scratch/merge.theirs"
		# --stdout 不改文件；冲突样例返回 1 是 Git 的正常结果。
		clean_probe 8 "$git_bin" -c "merge.conflictStyle=$last" merge-file --stdout \
			"$scratch/merge.ours" "$scratch/merge.base" "$scratch/merge.theirs"
		if [[ $probe_status == 0 || $probe_status == 1 ]]; then
			report PASS git.merge-capability 'Git can render the selected conflict style on a temporary sample'
		else
			native_error git.merge-capability 'Git cannot use the selected conflict style' 'Review merge.conflictStyle and install a compatible Git version.'
		fi
	fi
}

check_lazygit() {
	local lazygit_bin directory config
	need_tool lazygit WARN lazygit.command || return 0
	lazygit_bin="$tool_path"
	if capture 8 "$lazygit_bin" --print-config-dir; then
		directory=$(< "$probe_stdout")
		if [[ $directory == "$HOME/.config/lazygit" || (-d $directory && $directory -ef $HOME/.config/lazygit) ]]; then
			report PASS lazygit.discovery 'native lookup selects ~/.config/lazygit'
		else
			report WARN lazygit.discovery "native lookup selects $directory" 'Review LG_CONFIG_DIR and legacy macOS configuration directories.'
		fi
	else
		native_error lazygit.discovery 'could not query the config directory' 'Check Lazygit and its environment.'
		return 0
	fi
	config="${LG_CONFIG_FILE:-$directory/config.yml}"
	if [[ $config == *,* ]]; then
		report SKIP lazygit.parse 'multiple LG_CONFIG_FILE layers require interactive validation' 'Review the ordered files; doctor does not replace them with one managed file.'
	else
		if [[ $config != /* ]]; then config="$calling_dir/$config"; fi
		check_file lazygit.config "$config" || return 0
		# --config 输出内置默认值，不证明用户 YAML 已加载；由受控 TUI 真正解析。
		report PASS lazygit.config "configuration file is readable: $config"
		run_runtime lazygit "$config"
	fi
	need_tool nvim FAIL lazygit.editor || :
	need_tool delta FAIL lazygit.renderer || :
}

check_nvim() {
	local nvim_bin kind name commit directory records actual tool location lazyvim_commit='' data_dir="$HOME/.local/share/nvim"
	need_tool nvim FAIL nvim.command || return 0
	nvim_bin="$tool_path"
	# -u NONE/-i NONE 跳过初始化和 ShaDa；保留配置与数据目录发现，日志转入临时目录。
	if capture 8 env NVIM_LOG_FILE="$scratch/nvim.log" XDG_STATE_HOME="$scratch/home/.local/state" \
		XDG_CACHE_HOME="$scratch/home/.cache" "$nvim_bin" --headless -u NONE --noplugin -n -i NONE \
		-l "$script_dir/doctor/nvim.lua" "$HOME/.config/nvim"; then
		records="$probe_stdout"
		consume_records "$records"
		if [[ $record_count == 0 ]]; then
			report FAIL nvim.probe 'offline probe returned no structured evidence' 'Check the Neovim probe installation.'
			return 0
		fi
	else
		native_error nvim.parse 'Neovim could not execute the offline Lua/JSON probe' 'Check Neovim compatibility and configuration syntax.'
		return 0
	fi
	while IFS=$'\t' read -r kind name commit directory; do
		if [[ $kind == DATA ]]; then
			data_dir="$name"
			continue
		fi
		[[ $kind == PLUGIN ]] || continue
		if [[ ! -d $directory/$name ]]; then
			report WARN "nvim.plugin.$name" 'plugin has not been prepared' 'Prepare the locked plugin set during bootstrap.'
		elif find_tool git; then
			if capture 8 "$tool_path" -C "$directory/$name" rev-parse --verify HEAD; then
				actual=$(< "$probe_stdout")
				if [[ $actual == "$commit" ]]; then
					report PASS "nvim.plugin.$name" 'checkout matches lazy-lock.json'
				else
					report WARN "nvim.plugin.$name" 'installed commit differs from lazy-lock.json' 'Review intentional plugin updates or restore the locked version during bootstrap.'
				fi
			else
				native_error "nvim.plugin.$name" 'could not read the installed plugin commit' 'Review this plugin checkout before restoring it.'
			fi
		else
			report SKIP "nvim.plugin.$name" 'Git is unavailable; commit comparison was skipped'
		fi
		if [[ $name == LazyVim ]]; then lazyvim_commit="$commit"; fi
	done < "$records"
	# 优先查询锁定提交的 README，不把当前最新 LazyVim 的要求套到旧锁文件。
	if [[ -n $lazyvim_commit ]] && find_tool git; then
		if capture 8 "$tool_path" -C "$data_dir/lazy/LazyVim" show "$lazyvim_commit:README.md"; then
			actual=$(< "$probe_stdout")
			if [[ $actual =~ Neovim\ \>\=\ \*\*([0-9]+\.[0-9]+\.[0-9]+) ]]; then
				commit="${BASH_REMATCH[1]}"
				if clean_probe 8 "$nvim_bin" --headless -u NONE -n -i NONE --cmd "lua if vim.fn.has('nvim-$commit') == 0 or not jit then vim.cmd('cquit 1') end" +qa; then
					report PASS nvim.requirements "locked LazyVim requires Neovim >= $commit with LuaJIT"
				else
					native_error nvim.requirements "requires Neovim >= $commit with LuaJIT" 'Prepare a compatible Neovim build.'
				fi
			else
				report SKIP nvim.requirements 'locked README has no recognized minimum-version declaration'
			fi
		else
			report SKIP nvim.requirements 'locked LazyVim documentation is unavailable locally; no fetch attempted'
		fi
	fi
	for tool in bash-language-server taplo lua-language-server shfmt stylua shellcheck shuck; do
		location=''
		if [[ $tool != shuck && -x $data_dir/mason/bin/$tool ]]; then
			location="$data_dir/mason/bin/$tool"
		elif find_tool "$tool"; then location="$tool_path"; fi
		if [[ -n $location ]]; then
			report PASS "nvim.tool.$tool" "executable available at $location (activation requires --runtime)"
		else
			report WARN "nvim.tool.$tool" 'configured editor capability is not prepared' 'Prepare the tool through Mason; install Shuck separately on PATH.'
		fi
	done
	run_runtime nvim
}

check_starship() {
	local config="${STARSHIP_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/starship.toml}" value
	need_tool starship WARN starship.command || return 0
	check_file starship.config "$config" || return 0
	if clean_probe 8 STARSHIP_CONFIG="$config" "$tool_path" print-config format; then
		value=$(< "$probe_stdout")
		if [[ -s $probe_stderr ]]; then
			native_error starship.parse 'Starship reported diagnostics while reading configuration' 'Use --verbose to inspect parser diagnostics.'
		elif [[ $value == *'[░▒▓](#a3aed2)'* ]]; then
			report PASS starship.parse "selected config parses and contains the managed format: $config"
		else
			report WARN starship.format 'selected config uses a different format' 'Review STARSHIP_CONFIG and intentional prompt changes.'
		fi
	else
		native_error starship.parse 'configuration query failed' 'Review the selected Starship TOML file.'
	fi
}

check_atuin() {
	local config="${ATUIN_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/atuin}/config.toml" atuin_bin key value
	need_tool atuin WARN atuin.command || return 0
	atuin_bin="$tool_path"
	check_file atuin.config "$config" || return 0
	mkdir -p "$scratch/atuin" || exit 2
	cp "$config" "$scratch/atuin/config.toml" || exit 2
	for key in auto_sync enter_accept logs.dir; do
		if clean_probe 8 ATUIN_CONFIG_DIR="$scratch/atuin" "$atuin_bin" config get --resolved "$key"; then
			value=$(< "$probe_stdout")
			if [[ $key == logs.dir ]]; then
				value="${value//"$scratch/home"/"$HOME"}"
				report PASS atuin.logs "configured log path resolves to $value (tilde resolved for the real HOME)"
			elif [[ $value == false ]]; then
				report PASS "atuin.$key" 'selected TOML parses; managed value is false'
			else
				report WARN "atuin.$key" 'selected TOML overrides the managed false value' 'Confirm this Atuin behavior is intentional.'
			fi
		else
			native_error "atuin.$key" 'configuration query failed' 'Review ATUIN_CONFIG_DIR, the TOML file and installed Atuin version.'
		fi
	done
	report SKIP atuin.environment 'resolved queries used a copied config and temporary state; arbitrary ATUIN_* overrides are not replayed' 'Inspect intentional ATUIN_* overrides separately; no history database is opened by doctor.'
}

check_shuck() {
	local config="${XDG_CONFIG_HOME:-$HOME/.config}/shuck/shuck.toml" shuck_bin
	need_tool shuck WARN shuck.command || return 0
	shuck_bin="$tool_path"
	check_file shuck.config "$config" || return 0
	# 格式化一个临时样例，既验证全局配置发现，也避免修改仓库中的脚本。
	mkdir -p "$scratch/home/.config/shuck" || exit 2
	cp "$config" "$scratch/home/.config/shuck/shuck.toml" || exit 2
	# shellcheck disable=SC2016 # 样例中的 $1 必须保留为字面量。
	printf 'case "$1" in\nfoo)\necho ok>out\n;;\nesac\n' > "$scratch/sample.zsh"
	# shellcheck disable=SC2016
	printf 'case "$1" in\n\tfoo)\n\t\techo ok > out\n\t\t;;\nesac\n' > "$scratch/shuck.expected"
	if clean_probe 8 SHUCK_EXPERIMENTAL=1 "$shuck_bin" format --no-cache "$scratch/sample.zsh"; then
		if cmp -s "$scratch/sample.zsh" "$scratch/shuck.expected"; then
			report PASS shuck.format 'copied global config is discovered and applies tab/case/redirect formatting'
		else
			report WARN shuck.format 'global configuration parsed but formatting differs from the managed baseline' 'Review global overrides or a change in Shuck formatting behavior.'
		fi
	else
		native_error shuck.format 'sample formatting or configuration parsing failed' 'Review Shuck and its global TOML settings.'
	fi
	report SKIP shuck.project 'project-level configuration is outside this global diagnostic' 'Project shuck.toml settings can legitimately override global formatting.'
}

check_vim() {
	need_tool vim WARN vim.command || return 0
	check_file vim.config "$HOME/.vimrc" || return 0
	report PASS vim.entry 'the default ~/.vimrc is readable (execution requires --runtime)'
	run_runtime vim
}

check_ghostty() {
	local config="${XDG_CONFIG_HOME:-$HOME/.config}/ghostty/config.ghostty" ghostty_bin
	need_tool ghostty WARN ghostty.command || return 0
	ghostty_bin="$tool_path"
	check_file ghostty.config "$config" || return 0
	# validate-config 的 --config-file 已限定入口，不接受 GUI 的 config-default-files 参数。
	if clean_probe 8 "$ghostty_bin" +validate-config "--config-file=$config"; then
		report PASS ghostty.parse 'explicit entry and relative includes pass native validation'
	else
		native_error ghostty.parse 'configuration validation failed' 'Review Ghostty version, include files and platform-specific settings.'
	fi
	report SKIP ghostty.rendering 'CLI parsing does not verify GUI discovery, shader rendering or clipboard interaction' 'Open Ghostty locally and check appearance, Option keys/quick terminal on macOS, and clipboard behavior.'
}

check_terminal() {
	local font fonts line alias found ghostty_bin='' aliases=() command_args=()
	if [[ -z ${TERM-} || $TERM == dumb ]]; then
		report SKIP terminal.terminfo 'no interactive terminal type is available'
	elif need_tool infocmp WARN terminal.infocmp; then
		if capture 8 "$tool_path" "$TERM"; then
			report PASS terminal.terminfo "terminfo can resolve $TERM"
		else
			native_error terminal.terminfo "terminfo cannot resolve $TERM" 'Install the terminal entry on this machine; Ghostty SSH integration can prepare it on remote hosts.'
		fi
	fi
	if [[ -n ${SSH_CONNECTION-}${SSH_TTY-} ]]; then
		report SKIP terminal.fonts 'SSH cannot determine which fonts the client terminal uses' 'Check IosevkaTerm Nerd Font and Sarasa Term SC on the client machine.'
		return 0
	fi
	if find_tool ghostty; then
		ghostty_bin="$tool_path"
	elif [[ $platform == linux ]] && find_tool fc-list; then
		command_args=("$tool_path" --format '%{family}\n')
	else
		report SKIP terminal.fonts 'no native font enumerator is available' 'Check IosevkaTerm Nerd Font and Sarasa Term SC in Ghostty on the desktop.'
		return 0
	fi
	for font in 'IosevkaTerm Nerd Font' 'Sarasa Term SC'; do
		if [[ -n $ghostty_bin ]]; then
			# 默认列表只显示等宽字体；按族名查询与 Ghostty 的 font-family 配置一致。
			command_args=("$ghostty_bin" +list-fonts "--family=$font")
		fi
		# 保留真实 HOME 及数据、配置目录以枚举用户字体；缓存和状态仍指向临时目录。
		if capture 8 env XDG_CACHE_HOME="$scratch/home/.cache" XDG_STATE_HOME="$scratch/home/.local/state" "${command_args[@]}"; then
			fonts=$(< "$probe_stdout")
			found=no
			while IFS= read -r line; do
				if [[ -n $ghostty_bin ]]; then
					# Ghostty 的缩进行是样式名，只有无缩进的族名行参与匹配。
					[[ $line != [[:space:]]* ]] || continue
					aliases=("$line")
				else
					IFS=, read -r -a aliases <<< "$line"
				fi
				for alias in "${aliases[@]}"; do
					alias="${alias#"${alias%%[![:space:]]*}"}"
					alias="${alias%"${alias##*[![:space:]]}"}"
					if [[ $alias == "$font" ]]; then found=yes; fi
				done
			done <<< "$fonts"
			if [[ $found == yes ]]; then
				report PASS "terminal.font.$font" 'font family is present in native enumeration'
			else
				report WARN "terminal.font.$font" 'font family is absent from native enumeration' 'Install this font during bootstrap; font fallback can hide a missing family.'
			fi
		else
			native_error "terminal.font.$font" 'font enumeration failed' 'Use the desktop font manager to verify configured font families.'
		fi
	done
}

check_state() {
	local relative path ancestor
	for relative in .local/state/zsh .local/state/vim .local/state/atuin/logs .cache/zsh .cache/antidote .local/share/antidote .local/share/nvim .local/state/nvim .cache/nvim; do
		path="$HOME/$relative"
		if [[ -e $path || -L $path ]]; then
			if [[ ! -d $path || ! -r $path || ! -w $path || ! -x $path ]]; then
				report FAIL "state.$relative" 'expected an accessible, writable directory' 'Review ownership, permissions, links and any non-directory occupant.'
			else
				report PASS "state.$relative" 'directory access and write-permission checks pass (no write attempted)'
			fi
		else
			ancestor="${path%/*}"
			while [[ ! -e $ancestor && ! -L $ancestor && $ancestor != / ]]; do ancestor="${ancestor%/*}"; done
			if [[ -d $ancestor && -w $ancestor && -x $ancestor ]]; then
				report SKIP "state.$relative" 'not initialized; nearest existing parent permits creation'
			else
				report FAIL "state.$relative" 'missing directory cannot be created under its existing parent' "Review $ancestor permissions and path type."
			fi
		fi
	done
	for relative in .local/state/zsh/history .local/state/lesshst .local/state/vim/viminfo; do
		path="$HOME/$relative"
		if [[ -e $path || -L $path ]]; then
			if [[ ! -f $path || ! -r $path || ! -w $path ]]; then
				report FAIL "state.$relative" 'existing state entry must be a readable, writable file' 'Review its type and ownership; preserve history content.'
			else
				report PASS "state.$relative" 'state file is accessible (contents were not read)'
			fi
		fi
	done
}

# ===== 初始化、模块调度与最终状态 =====

# 检查器或临时文件准备失败时终止；普通诊断失败按依赖处理，并继续汇总其他独立检查。
calling_dir="$PWD"
# 保持切换到中立工作目录前后的 PATH 查找含义一致。
relative_path=no normalized_path='' path_remaining="${PATH-}:"
while [[ $path_remaining == *:* ]]; do
	path_entry="${path_remaining%%:*}"
	path_remaining="${path_remaining#*:}"
	case $path_entry in
		/*) ;;
		'')
			path_entry="$calling_dir"
			relative_path=yes
			;;
		*)
			path_entry="$calling_dir/$path_entry"
			relative_path=yes
			;;
	esac
	normalized_path+="${normalized_path:+:}$path_entry"
done
export PATH="$normalized_path"
# 这些变量允许相对文件名，按调用时的目录解析，避免查询过程中改变其含义。
for config_variable in STARSHIP_CONFIG ATUIN_CONFIG_DIR LG_CONFIG_DIR GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM; do
	config_value="${!config_variable}"
	if [[ -n $config_value && $config_value != /* ]]; then
		printf -v "$config_variable" '%s/%s' "$calling_dir" "$config_value"
		export "${config_variable?}"
	fi
done
for required in uname mkdir mktemp rm sleep env cp cmp cat; do
	if ! type -P "$required" > /dev/null; then
		printf 'error: checker requires %s\n' "$required" >&2
		exit 2
	fi
done
if [[ ${HOME-} != /* || ! -d $HOME ]]; then
	report FAIL environment.HOME 'HOME must be an existing absolute directory'
	exit 1
fi
script_parent="${BASH_SOURCE[0]%/*}"
[[ $script_parent != "${BASH_SOURCE[0]}" ]] || script_parent=.
script_dir=$(cd -- "$script_parent" && pwd -P) || exit 2
repo_root=$(cd -- "$script_dir/.." && pwd -P) || exit 2
case $(uname -s) in Linux) platform=linux ;; Darwin) platform=macos ;; *)
	printf 'error: unsupported platform\n' >&2
	exit 2
	;;
esac
# shellcheck source-path=SCRIPTDIR
# shellcheck source=layout.bash
source "$script_dir/layout.bash" || exit 2
load_dotfiles_layout "$platform"
printf -v deploy_hint 'Preview with bash %q --dry-run; resolve conflicts, then run --apply.' "$script_dir/deploy.sh"
registering=yes
scratch=$(
	trap '' HUP INT TERM
	mktemp -d "${TMPDIR:-/tmp}/dotfiles-doctor.XXXXXX"
) || exit 2
registering=no
[[ $interrupted_status == 0 ]] || exit "$interrupted_status"
scratch=$(cd -- "$scratch" && pwd -P) || exit 2
mkdir -p "$scratch/home/.config" "$scratch/home/.local/share" "$scratch/home/.local/state" "$scratch/home/.cache" || exit 2
cd -- "$scratch" || exit 2
printf 'dotfiles doctor\nrepository: %s\nplatform: %s\ntarget: %s\nruntime: %s\n\n' "$repo_root" "$platform" "$HOME" "$runtime" >&2
summary_ready=yes
for module in "${doctor_modules[@]}"; do
	if [[ $selected == ' ' || $selected == *" $module "* ]]; then
		printf '\n%s:\n' "$module" >&2
		"check_$module"
	fi
done
[[ $fail_count == 0 ]] || exit 1
exit 0
