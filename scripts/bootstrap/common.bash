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

physical_directory() {
	(cd -P -- "$1" 2> /dev/null && pwd -P)
}

java_jdk_ready() {
	local minimum="$1" java_binary='' runtime_home='' output='' java_version='' javac_version='' line
	local version_pattern='version "([0-9]+([.][0-9]+){0,3})'
	local javac_pattern='javac[[:space:]]+([0-9]+([.][0-9]+){0,3})'
	java_problem=''
	selected_java_home=''
	if [[ -n ${JAVA_HOME-} ]]; then
		if [[ $JAVA_HOME != /* || ! -x $JAVA_HOME/bin/java ]]; then
			java_problem="JAVA_HOME does not contain an executable bin/java: $JAVA_HOME"
			return 1
		fi
		java_binary="$JAVA_HOME/bin/java"
	else
		java_binary=$(type -P java) || java_binary=''
		if [[ -z $java_binary || ! -x $java_binary ]]; then
			java_problem='java is unavailable on PATH and JAVA_HOME is unset'
			return 1
		fi
	fi
	if ! execute 12 probe "$java_binary" -XshowSettings:properties -version; then
		java_problem="Java runtime query failed: $java_binary"
		return 1
	fi
	output=$(cat "$scratch/probe.log" "$scratch/probe.err")
	if [[ $output =~ $version_pattern ]]; then java_version="${BASH_REMATCH[1]}"; else
		java_problem="Java runtime did not report a supported version: $java_binary"
		return 1
	fi
	while IFS= read -r line; do
		line="${line%$'\r'}"
		case $line in *'java.home = '*)
			runtime_home="${line#*java.home = }"
			break
			;;
		esac
	done < "$scratch/probe.err"
	if [[ $runtime_home != /* || ! -x $runtime_home/bin/java || ! -x $runtime_home/bin/javac ]]; then
		java_problem="selected Java is not a complete JDK: ${runtime_home:-unknown java.home}"
		return 1
	fi
	if [[ -n ${JAVA_HOME-} && $(physical_directory "$JAVA_HOME") != "$(physical_directory "$runtime_home")" ]]; then
		java_problem="JAVA_HOME and the selected Java runtime disagree: $JAVA_HOME / $runtime_home"
		return 1
	fi
	if ! version_ge "$java_version" "$minimum"; then
		java_problem="selected Java $java_version is older than $minimum"
		return 1
	fi
	if ! execute 12 probe "$runtime_home/bin/javac" -version; then
		java_problem="javac query failed in selected JDK: $runtime_home"
		return 1
	fi
	output=$(cat "$scratch/probe.log" "$scratch/probe.err")
	if [[ $output =~ $javac_pattern ]]; then javac_version="${BASH_REMATCH[1]}"; else
		java_problem="javac did not report a supported version in selected JDK: $runtime_home"
		return 1
	fi
	if [[ $javac_version != "$java_version" ]] || ! version_ge "$javac_version" "$minimum"; then
		java_problem="java and javac versions disagree in selected JDK: $java_version / $javac_version"
		return 1
	fi
	selected_java_home="$runtime_home"
	return 0
}

activate_java_home() {
	local home="$1" entry normalized=''
	local -a entries=()
	[[ $home == /* && -x $home/bin/java && -x $home/bin/javac ]] || return 1
	JAVA_HOME="$home"
	IFS=: read -r -a entries <<< "$PATH"
	for entry in "${entries[@]}"; do
		[[ $entry != "$JAVA_HOME/bin" && $entry != "$HOME/.local/bin" ]] || continue
		normalized="${normalized:+$normalized:}$entry"
	done
	PATH="$JAVA_HOME/bin:$HOME/.local/bin${normalized:+:$normalized}"
	export JAVA_HOME PATH
}

maven_ready() {
	local minimum="$1" output version runtime='' line pattern='Apache Maven (3[.]9[.][0-9]+)([[:space:]]|$)'
	maven_problem=''
	if ! java_jdk_ready "$(required_minimum java)"; then
		maven_problem="Java selection failed: $java_problem"
		return 1
	fi
	tool_path=$(type -P mvn) || tool_path=''
	if [[ -z $tool_path || ! -x $tool_path ]]; then
		maven_problem='mvn is unavailable on PATH'
		return 1
	fi
	if ! execute 15 probe env MAVEN_SKIP_RC=1 JAVA_HOME="$selected_java_home" PATH="$selected_java_home/bin:$PATH" "$tool_path" --version; then
		maven_problem="Maven version query failed: $tool_path"
		return 1
	fi
	output=$(cat "$scratch/probe.log" "$scratch/probe.err")
	if [[ ! $output =~ $pattern ]]; then
		maven_problem="Maven is not a stable 3.9.x release: $tool_path: ${output%%$'\n'*}"
		return 1
	fi
	version="${BASH_REMATCH[1]}"
	if ! version_ge "$version" "$minimum"; then
		maven_problem="Maven $version is older than $minimum"
		return 1
	fi
	while IFS= read -r line; do
		line="${line%$'\r'}"
		case $line in *'runtime: '*)
			runtime="${line##*runtime: }"
			break
			;;
		esac
	done < <(cat "$scratch/probe.log" "$scratch/probe.err")
	if [[ $runtime != /* || $(physical_directory "$runtime") != "$(physical_directory "$selected_java_home")" ]]; then
		maven_problem="Maven runtime does not match selected JAVA_HOME: ${runtime:-missing runtime} / $selected_java_home"
		return 1
	fi
	return 0
}

tool_ready() {
	local name="$1" minimum="$2" output version
	case $name in
		java | javac)
			java_jdk_ready "$minimum" || return 1
			tool_path="$selected_java_home/bin/$name"
			return 0
			;;
		mvn)
			maven_ready "$minimum"
			return
			;;
	esac
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
	if [[ $name == go ]]; then
		# Go 使用 version 子命令；只查询本地工具链，避免预览触发工具链下载。
		execute 8 probe env GOTOOLCHAIN=local "$tool_path" version || return 1
	else
		execute 8 probe "$tool_path" --version || return 1
	fi
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
	# 已获免密命令权限时直接复用；sudo-rs 的 -v 仍可能要求密码。
	# 其余环境在前台刷新凭据，下载/插件准备期间过期时可再次认证。
	say 'Checking sudo authorization for system package installation.'
	if sudo -n true 2> /dev/null; then return 0; fi
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

required_minimum() {
	local command="$1" name minimum scope target
	while IFS=$'\t' read -r name minimum scope target; do
		[[ $name == "$command" && ($target == any || $target == "$platform") ]] || continue
		printf '%s\n' "$minimum"
		return
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
			if [[ $name == java && -n ${java_problem-} ]]; then say "  Java selection: $java_problem"; fi
		fi
	done < <(requirements_for all)
}

prepare_java_and_maven() {
	local managed="$target_dir/.local/share/dotfiles-bootstrap/jdk/current"
	local java_minimum
	java_minimum=$(required_minimum java)
	if java_jdk_ready "$java_minimum"; then
		activate_java_home "$selected_java_home" || die 'could not activate the selected JDK'
	else
		if [[ -x $managed/bin/java && -x $managed/bin/javac ]]; then activate_java_home "$managed"; fi
		if ! java_jdk_ready "$java_minimum"; then
			run 'install latest stable JDK 25' 1800 python3 -B "$script_dir/bootstrap/resources.py" install-jdk "$platform-$arch"
			hash -r
			activate_java_home "$managed" || die 'managed JDK installation did not publish a complete current JDK'
		fi
	fi
	java_jdk_ready "$java_minimum" || die "JDK >= $java_minimum is still unavailable: ${java_problem:-unknown Java selection error}"
	activate_java_home "$selected_java_home" || die 'could not activate the verified JDK'
	if ! required_tool_ready mvn; then
		run 'install latest stable Maven 3.9' 1200 python3 -B "$script_dir/bootstrap/resources.py" install-maven "$platform-$arch"
		activate_java_home "$JAVA_HOME" || die 'could not restore the selected JDK after Maven installation'
		hash -r
	fi
	required_tool_ready mvn || die "Maven 3.9 is still unavailable or does not use the selected JAVA_HOME: ${maven_problem:-unknown Maven selection error}"
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

verify_java_build_tools() {
	local source="$scratch/BootstrapJavaProbe.java" classes="$scratch/java-classes" pom="$scratch/pom.xml"
	local user_settings="$scratch/maven-user-settings.xml" global_settings="$scratch/maven-global-settings.xml"
	local user_toolchains="$scratch/maven-user-toolchains.xml" global_toolchains="$scratch/maven-global-toolchains.xml"
	local repository="$scratch/maven-repository" java_path='' javac_path='' selected_bin=''
	java_path=$(type -P java) || java_path=''
	javac_path=$(type -P javac) || javac_path=''
	selected_bin=$(physical_directory "$JAVA_HOME/bin") || selected_bin=''
	if [[ -z $selected_bin || -z $java_path || -z $javac_path ]] ||
		[[ $(physical_directory "${java_path%/*}") != "$selected_bin" ]] ||
		[[ $(physical_directory "${javac_path%/*}") != "$selected_bin" ]]; then
		die "PATH does not select java and javac from JAVA_HOME: ${java_path:-missing} / ${javac_path:-missing} / $JAVA_HOME"
	fi
	mkdir "$classes" "$repository"
	cat > "$source" << 'JAVA'
public final class BootstrapJavaProbe {
    public static void main(String[] args) { System.out.println("java-bootstrap-ready"); }
}
JAVA
	cat > "$pom" << 'XML'
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>local.bootstrap</groupId><artifactId>probe</artifactId><version>1</version>
</project>
XML
	cat > "$user_settings" << 'XML'
<settings xmlns="http://maven.apache.org/SETTINGS/1.2.0" />
XML
	cp "$user_settings" "$global_settings"
	cat > "$user_toolchains" << 'XML'
<toolchains xmlns="http://maven.apache.org/TOOLCHAINS/1.1.0" />
XML
	cp "$user_toolchains" "$global_toolchains"
	if ! execute 30 probe env JAVA_HOME="$JAVA_HOME" PATH="$JAVA_HOME/bin:$PATH" javac -d "$classes" "$source" ||
		! execute 15 probe env JAVA_HOME="$JAVA_HOME" PATH="$JAVA_HOME/bin:$PATH" java -cp "$classes" BootstrapJavaProbe ||
		! grep -Fxq 'java-bootstrap-ready' "$scratch/probe.log" ||
		! execute 60 probe env MAVEN_SKIP_RC=1 JAVA_HOME="$JAVA_HOME" PATH="$JAVA_HOME/bin:$PATH" mvn \
			--offline --quiet --settings "$user_settings" --global-settings "$global_settings" \
			--toolchains "$user_toolchains" --global-toolchains "$global_toolchains" \
			"-Dmaven.repo.local=$repository" --file "$pom" validate; then
		cat "$scratch/probe.log" "$scratch/probe.err" >&2
		cat "$scratch/probe.log" "$scratch/probe.err" >> "$log_file"
		die 'required Java/Javac/Maven capability check failed'
	fi
	say "READY: Java compilation/execution and offline Maven validation ($JAVA_HOME)"
}
