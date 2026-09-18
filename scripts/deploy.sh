#!/usr/bin/env bash

# 个人 dotfiles：只部署到 HOME，使用默认 XDG 目录；本脚本不安装依赖或执行完整应用诊断。
# 校验：bash -n、shellcheck；格式：shfmt -ci -sr。
# 未处理的命令失败时终止，避免在无效状态下继续部署。
set -e

# 全程显式设置中断退出码；参数解析完成后启用 EXIT 失败汇总。
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# 避免继承的 CDPATH 改变 cd 行为或污染命令替换输出。
unset CDPATH

# ===== 支持函数 =====

# --- 帮助与预检 ---

# 帮助中的 $HOME 展示变量名，保持帮助文本与当前环境无关。
# shellcheck disable=SC2016
usage() {
	printf '%s\n' 'Usage: deploy.sh [--dry-run|--apply]
       deploy.sh {-h|--help}

Modes:
  --dry-run previews deployment without filesystem changes (default).
  --apply performs deployment.

Output:
  Deployment reports and diagnostics go to stderr; --help goes to stdout.
  Save a complete deployment report with 2>deploy.log.

Deployment paths:
  Deploys to $HOME, which must be an existing absolute directory, and assumes
  the default XDG locations: $HOME/.config, $HOME/.local/share, $HOME/.local/state
  and $HOME/.cache. XDG_CONFIG_HOME, XDG_DATA_HOME, XDG_STATE_HOME and
  XDG_CACHE_HOME must be unset, empty, or point to these defaults.
  ZDOTDIR must not be exported.

Requirements:
  Linux or macOS, Bash 3.2+, GNU Stow with --no-folding, Git with --fixed-value,
  and cmp to compare the Codex agent copy. Creating regular files requires ln,
  mktemp, rm, and a filesystem supporting hard links; copying the Codex agent
  also needs mkdir and cat.

Deployment constraints:
  The following paths must be absent, including symbolic links:
    .stowrc in the calling directory, $HOME/.stowrc, $HOME/.stow-global-ignore.
  .config, .config/git and .config/ghostty must be real directories if present.
  .config/nvim (including lua, lua/config and lua/plugins), .config/lazygit and
  .config/shuck must also be real directories if present.
  .agents, .agents/skills, .claude, .claude/skills, .codex and .codex/agents
  must also be real directories if present.
  An existing .config/git/config must be a regular file, not a symbolic link,
  and contain a direct include.path value of config.shared; it is never edited automatically.
  Codex rejects symbolic links for agent files, so $HOME/.codex/agents/sol_worker.toml
  is deployed as a regular copy; identical content is kept, while a symbolic link,
  a directory, or a file with different content must be reviewed and moved aside first.
  Ghostty platform.ghostty is deployed by the ghostty-macos package on macOS only.
  Legacy ~/.gitconfig and Ghostty config entries require manual migration first;
  preserve personal settings in .config/git/config or the managed Ghostty configuration.
  Neovim init.vim, alternate Lazygit configs and Shuck .shuck.toml require migration
  before deployment; each application must resolve the managed configuration.

Failure and retry:
  If apply fails partway through, completed changes remain; there is no automatic rollback.
  Resolve the reported conflict or write failure, preview again, then retry --apply.

Examples:
  bash scripts/deploy.sh
  bash scripts/deploy.sh --apply
  bash scripts/deploy.sh --dry-run'
}

argument_error() {
	printf 'error: %s\n' "$1" >&2
	printf '%s\n' 'Usage: deploy.sh [--dry-run|--apply]' \
		"Run 'deploy.sh --help' for details." >&2
	exit 2
}

require_command() {
	local name="$1"

	if ! command -v "$name" > /dev/null 2>&1; then
		printf 'error: required command not found: %s\n' "$name" >&2
		return 1
	fi

	return 0
}

check_environment() {
	local row name relative expected
	for row in "${layout_xdg_paths[@]}"; do
		IFS='|' read -r name relative <<< "$row"
		expected="$HOME/$relative"
		if ! layout_is_default_path "${!name}" "$expected"; then
			printf 'error: %s must be unset, empty, or point to %s (got %s)\n' "$name" "$expected" "${!name}" >&2
			return 1
		fi
	done
	# 根 .zshenv 设置未导出的 ZDOTDIR；继承该变量会使新 Zsh 跳过根入口。
	if [[ ${ZDOTDIR+x} ]]; then
		printf 'error: ZDOTDIR must not be exported; new Zsh sessions must read %s/.zshenv\n' "$HOME" >&2
		return 1
	fi
	return 0
}

check_competing_config() {
	local label="$1" entry="$2" expected="$3"

	if [[ -e $entry || -L $entry ]]; then
		printf 'error: %s config needs migration: %s\n' "$label" "$entry" >&2
		printf 'hint: review its settings alongside %s, then move the competing entry aside\n' "$expected" >&2
		return 1
	fi
	return 0
}

check_competing_entries() {
	local target_home="$1" row id label relative expected
	for row in "${layout_competing[@]}"; do
		IFS='|' read -r id label relative expected <<< "$row"
		if ! check_competing_config "$label" "$target_home/$relative" "$target_home/$expected"; then
			# Git 个人设置必须放在共享 include 之后，保持覆盖顺序。
			if [[ $id == git-legacy ]]; then
				printf 'hint: preserve its personal settings in %s after the config.shared include\n' \
					"$target_home/$expected" >&2
			fi
			return 1
		fi
	done
	return 0
}

check_stow_external_config() {
	local config_path

	# Stow 会合并调用目录和 HOME 中的配置；显式命令行参数不能清除所有外部选项。
	for config_path in "$PWD/.stowrc" "$HOME/.stowrc" "$HOME/.stow-global-ignore"; do
		if [[ -e $config_path || -L $config_path ]]; then
			printf 'error: external Stow configuration is not supported: %s\n' "$config_path" >&2
			return 1
		fi
	done

	return 0
}

check_git_fixed_value_support() {
	local query_status

	# 使用空配置只读探测能力；Git 启动仍可能被本机配置等环境错误阻断。
	# 退出码 1 表示查询无匹配，仍说明该选项可用。
	if git config --file /dev/null --no-includes --fixed-value \
		--get-all include.path config.shared > /dev/null; then
		return 0
	else
		query_status="$?"
	fi

	if [[ $query_status == 1 ]]; then
		return 0
	fi
	printf 'error: could not verify Git --fixed-value support (git exit %s)\n' \
		"$query_status" >&2
	return 1
}

check_directory_path() {
	local dir_path="$1"

	if [[ -L $dir_path ]]; then
		printf 'error: directory path must not be a symbolic link: %s\n' "$dir_path" >&2
		return 1
	elif [[ -e $dir_path && ! -d $dir_path ]]; then
		printf 'error: path exists but is not a directory: %s\n' "$dir_path" >&2
		return 1
	else
		return 0
	fi
}

check_ghostty_platform() {
	local platform_name="$1"
	local entry="$2"
	local expected="$3"

	if [[ $platform_name == macos && ! -f $expected ]]; then
		printf 'error: Ghostty platform source is missing or not a regular file: %s\n' "$expected" >&2
		return 1
	fi

	if [[ $platform_name == linux && (-e $entry || -L $entry) ]]; then
		printf 'error: Ghostty platform entry must be absent on Linux: %s\n' "$entry" >&2
		return 1
	fi
	# macOS 的入口由 Stow 管理，已有路径的冲突由 Stow 统一预检。
	return 0
}

check_git_config_path() {
	local config_path="$1"

	if [[ ! -e $config_path && ! -L $config_path ]]; then
		return 0
	elif [[ -L $config_path ]]; then
		printf 'error: Git config path must not be a symbolic link: %s\n' "$config_path" >&2
		return 1
	elif [[ -f $config_path ]]; then
		return 0
	else
		printf 'error: Git config path exists but is not a regular file: %s\n' "$config_path" >&2
		return 1
	fi
}

check_git_config_syntax() {
	local config_path="$1"

	if [[ ! -e $config_path ]]; then
		return 0
	elif git config --file "$config_path" --no-includes --list > /dev/null; then
		return 0
	else
		printf 'error: Git config could not be read or parsed: %s\n' "$config_path" >&2
		return 1
	fi
}

has_git_shared_include() {
	local config_path="$1"
	local query_status

	# 返回 0 表示存在精确匹配，1 表示无匹配，2 表示查询失败。
	if git config --file "$config_path" --no-includes --fixed-value \
		--get-all include.path config.shared > /dev/null; then
		return 0
	else
		query_status="$?"
	fi

	if [[ $query_status == 1 ]]; then
		return 1
	fi
	printf 'error: could not query Git shared include in %s (git exit %s)\n' "$config_path" "$query_status" >&2
	return 2
}

# --- 部署核验与入口创建 ---

check_deployed_link() {
	local label="$1"
	local entry="$2"
	local expected="$3"

	# -ef 比较最终文件身份，既接受不同链接写法，也能拒绝内容相同的独立副本。
	if [[ ! -L $entry || ! $entry -ef $expected ]]; then
		printf 'error: %s was not deployed as expected: %s\n' "$label" "$entry" >&2
		return 1
	fi
	return 0
}

check_codex_agent() {
	local entry="$1" source_path="$2"
	if [[ -L $source_path || ! -f $source_path || ! -r $source_path ]]; then
		printf 'error: Codex agent source must be a readable regular file: %s\n' "$source_path" >&2
		return 1
	fi
	if [[ ! -e $entry && ! -L $entry ]]; then return 0; fi
	if [[ -L $entry || ! -f $entry ]] || ! cmp -s "$source_path" "$entry"; then
		printf 'error: Codex agent needs review; preserve and move the existing entry aside: %s\n' "$entry" >&2
		return 1
	fi
}

publish_new_entry() {
	local staged_path="$1"
	local entry="$2"

	if [[ -e $entry || -L $entry ]]; then
		printf 'error: cannot create local entry; path already exists: %s\n' "$entry" >&2
		return 1
	fi

	# 暂存入口与最终入口同名，目标参数固定为父目录；即使入口此时被目录或链接占用，
	# ln 也只会因已存在而失败，不会向占位目录内部写入。
	ln -P "$staged_path" "${entry%/*}/"
}

create_regular_config() (
	local config_path="$1" label="$2" source_path="${3-}" staged_name="${1##*/}" staging_prefix=.deploy-git
	[[ -z $source_path ]] || staging_prefix=.deploy-codex
	local temp_dir='' creating=yes interrupted_status=0 creation_status

	# 处理器只在本子 Shell 中定义，共用本次创建的局部状态；仅由下方 trap 调用。
	# shellcheck disable=SC2317
	finish_regular_config() {
		local status="$1" cleanup_status
		trap - EXIT
		trap '' HUP INT TERM
		if [[ -n $temp_dir ]]; then
			if rm -rf -- "$temp_dir"; then
				:
			else
				cleanup_status=$?
				printf 'error: failed to remove %s staging directory (exit %s): %s\n' \
					"$label" "$cleanup_status" "$temp_dir" >&2
				[[ $status != 0 ]] || status="$cleanup_status"
			fi
		fi
		exit "$status"
	}

	# shellcheck disable=SC2317
	interrupt_regular_config() {
		interrupted_status="$1"
		[[ $creating == yes ]] || exit "$interrupted_status"
	}

	if [[ -e $config_path || -L $config_path ]]; then
		printf 'error: cannot create %s config; path already exists: %s\n' "$label" "$config_path" >&2
		return 1
	fi

	trap 'finish_regular_config "$?"' EXIT
	trap 'interrupt_regular_config 129' HUP
	trap 'interrupt_regular_config 130' INT
	trap 'interrupt_regular_config 143' TERM

	# mktemp 在输出路径前也可能已创建目录。创建命令暂时忽略中断以完成路径交接；
	# 外层记录信号，拿到目录后再统一清理并退出。
	if temp_dir="$(
		trap '' HUP INT TERM
		mktemp -d "${config_path%/*}/$staging_prefix.XXXXXX"
	)"; then
		creation_status=0
	else
		creation_status=$?
	fi
	creating=no
	[[ $interrupted_status == 0 ]] || return "$interrupted_status"
	[[ $creation_status == 0 ]] || return 1

	# 先在同一文件系统的私有目录中写完，再用硬链接发布；最终路径不会暴露半成品。
	# 内层子 Shell 承担写入；SIGXFSZ（文件大小超限）终止写入进程时，
	# 外层仍能接收失败状态、报告错误并返回，由 EXIT 处理器清理临时目录。
	if ! (
		if [[ -n $source_path ]]; then
			cat "$source_path"
		else
			printf '[include]\n\tpath = config.shared\n'
		fi > "$temp_dir/$staged_name"
	); then
		printf 'error: failed to create %s config: %s\n' "$label" "$config_path" >&2
		return 1
	fi
	publish_new_entry "$temp_dir/$staged_name" "$config_path" || return "$?"
	if [[ -L $config_path || ! $config_path -ef $temp_dir/$staged_name ]]; then
		printf 'error: %s config was not created as expected: %s\n' "$label" "$config_path" >&2
		return 1
	fi
	return 0
)

# --- 主流程失败汇总 ---

report_failure() {
	local status="$1" outcome

	# EXIT 保留原始退出状态；子 Shell 的暂存清理由自己的处理器负责。
	[[ $BASH_SUBSHELL == 0 && $status != 0 ]] || return 0
	trap '' HUP INT TERM
	if [[ $deployment_started == no ]]; then
		outcome='deployment not started; no changes made'
	elif [[ $mode == dry-run ]]; then
		outcome='no changes made'
	else
		outcome='any completed changes were kept'
	fi
	printf '\nresult: failed during %s (exit %s); %s\n' \
		"$deployment_phase" "$status" "$outcome" >&2 || :
}

# ===== 部署流程 =====

# --- 参数解析 ---

if (($# == 1)) && [[ $1 == -h || $1 == --help ]]; then
	usage
	exit 0
fi

for arg in "$@"; do
	if [[ $arg == -h || $arg == --help ]]; then
		argument_error 'help must be used alone'
	fi
done

mode=''

while (($# > 0)); do
	case $1 in
		--dry-run | --apply)
			if [[ -n $mode ]]; then
				argument_error 'choose only one of --dry-run and --apply'
			fi
			mode="${1#--}"
			shift
			;;
		*)
			argument_error "unknown argument: $1"
			;;
	esac
done

if [[ -z $mode ]]; then
	mode='dry-run'
fi

# 只在合法调用中生成执行报告；进入 Stow 前的失败都明确表示尚未部署。
deployment_phase='initialization'
deployment_started=no
trap 'report_failure "$?"' EXIT

# 与 Stow 的原生明细共用 stderr，使重定向后的报告仍保持完整顺序。
exec 1>&2

printf 'dotfiles deployment\n'
if [[ $mode == dry-run ]]; then
	printf 'mode: dry-run (no filesystem changes)\n'
else
	printf 'mode: apply (filesystem changes allowed)\n'
fi

# --- 定位路径与识别平台 ---

printf '\ninitialization: checking environment, paths and platform\n'
if [[ ${HOME-} != /* || ! -d $HOME ]]; then
	printf 'error: HOME must be an existing absolute directory: %s\n' "${HOME-}" >&2
	exit 1
fi

# 将 HOME 规范化为物理绝对路径，保持 Stow 链接和核验的路径一致。
target_dir="$(cd -- "$HOME" && pwd -P)"

# 根据脚本位置定位仓库，使调用不依赖当前工作目录。
script_parent="$(dirname "${BASH_SOURCE[0]}")"
script_dir="$(cd -- "$script_parent" && pwd -P)"
repo_root="$(cd -- "$script_dir/.." && pwd -P)"
# shellcheck source-path=SCRIPTDIR
# shellcheck source=layout.bash
source "$script_dir/layout.bash"
check_environment || exit 1

kernel_name="$(uname -s)"
case $kernel_name in
	Linux)
		platform='linux'
		;;
	Darwin)
		platform='macos'
		;;
	*)
		printf 'error: unsupported operating system: %s\n' "$kernel_name" >&2
		exit 1
		;;
esac

load_dotfiles_layout "$platform"

printf 'repository: %s\n' "$repo_root"
printf 'platform: %s\n' "$platform"
printf 'target: %s\n' "$target_dir"

# 跨预检、创建和最终核验的路径集中定义，保持各阶段指向同一入口。
config_root="$target_dir/.config"
git_config_file="$config_root/git/config"
git_shared_entry="$config_root/git/config.shared"
git_shared_source="$repo_root/git/.config/git/config.shared"
ghostty_platform_entry="$target_dir/$layout_ghostty_platform"
ghostty_platform_source="$repo_root/ghostty-macos/.config/ghostty/platform.ghostty"
codex_agent_file="$target_dir/$layout_codex_agent_target"
codex_agent_source="$repo_root/$layout_codex_agent_source"

printf 'initialization: completed\n'

# --- 目标预检与操作判定 ---
# 根据现有配置确定创建操作，并提前检查所需命令。

deployment_phase='preflight'
printf '\npreflight: checking target and dependencies\n'
check_competing_entries "$target_dir" || exit 1

require_command stow || exit 1
check_stow_external_config || exit 1

# 保留真实目录，让文件级链接与插件管理器、应用和本机创建的文件共存。
# 先检查 .config 本身，再按父目录优先检查子目录，避免沿目录链接读取或部署。
for config_dir in "${layout_directories[@]}"; do
	check_directory_path "$target_dir/$config_dir" || exit 1
done

# 用户代理文件必须是普通文件；内容不一致时不覆盖。
require_command cmp || exit 1
check_codex_agent "$codex_agent_file" "$codex_agent_source" || exit 1
codex_agent_action=unchanged
[[ -e $codex_agent_file ]] || codex_agent_action=create

# Ghostty：平台包只在 macOS 部署。
check_ghostty_platform \
	"$platform" \
	"$ghostty_platform_entry" \
	"$ghostty_platform_source" || exit 1

# Git：检查共享配置和个人入口，判断是否需要新建个人配置。
check_git_config_path "$git_config_file" || exit 1

require_command git || exit 1
check_git_fixed_value_support || exit 1
check_git_config_syntax "$git_config_file" || exit 1

if [[ ! -f $git_shared_source ]]; then
	printf 'error: Git shared config source is missing or not a regular file: %s\n' "$git_shared_source" >&2
	exit 1
elif ! check_git_config_syntax "$git_shared_source"; then
	exit 1
fi

git_config_action='none'

# 只接受明确的直接 include；自动插入或改写可能改变个人配置的覆盖顺序。
# 条件 include 和其他路径写法由用户确认，保留原文件的内容、注释和元数据。
if [[ ! -e $git_config_file ]]; then
	git_config_action='create'
else
	if has_git_shared_include "$git_config_file"; then
		git_query_status=0
	else
		git_query_status=$?
	fi
	case $git_query_status in
		0) ;;
		1)
			printf 'error: existing Git config needs review before adding the shared include: %s\n' "$git_config_file" >&2
			printf 'hint: expected a direct include.path value of config.shared; existing file left unchanged\n' >&2
			exit 1
			;;
		2) exit 1 ;;
	esac
fi

# 按已确定的创建操作检查写入工具，确保在部署前发现缺失的命令。
if [[ $mode == apply && ($git_config_action == create || $codex_agent_action == create) ]]; then
	for required_command in ln mktemp rm; do
		require_command "$required_command" || exit 1
	done
fi
if [[ $mode == apply && $codex_agent_action == create ]]; then
	for required_command in mkdir cat; do
		require_command "$required_command" || exit 1
	done
fi

# --- 构造 Stow 参数 ---

# 明确指定初始值，清空环境变量可能带入的非预期值。
declare -a stow_options=()

# 只有显式 --apply 才省略 --simulate，其他调用保持无副作用。
if [[ $mode == dry-run ]]; then
	stow_options+=("--simulate")
fi

# 禁止目录折叠，使受管链接可与目标目录中的本机文件共存。
stow_options+=(
	"--verbose=2"
	"--no-folding"
	"--dir=$repo_root"
	"--target=$target_dir"
	"--stow"
)

printf 'preflight: passed\n'

# --- 执行 Stow 与创建本机入口 ---

deployment_phase='stow'
printf '\nstow packages:\n'
for package in "${layout_packages[@]}"; do
	printf '  - %s\n' "$package"
done

printf 'stow output:\n'
# 此后 apply 可能已产生修改，失败时沿用保留已完成操作的说明。
deployment_started=yes
stow "${stow_options[@]}" "${layout_packages[@]}"
if [[ $mode == dry-run ]]; then
	printf 'stow: simulation completed\n'
else
	printf 'stow: completed\n'
fi

deployment_phase='git entry'
printf '\ngit entry output:\n'
if [[ $git_config_action == create ]]; then
	if [[ $mode == apply ]]; then
		check_deployed_link 'Git shared config' \
			"$git_shared_entry" "$git_shared_source" || exit 1
		create_regular_config "$git_config_file" Git || exit "$?"
	fi
	printf 'CREATE: Git config: %s (include.path = config.shared)\n' "$git_config_file"
else
	printf 'UNCHANGED: Git config: %s (include.path = config.shared)\n' "$git_config_file"
fi
if [[ $mode == dry-run ]]; then
	printf 'git entry: simulation completed; no filesystem changes\n'
else
	# create_regular_config 返回成功时，发布与暂存清理均已完成。
	printf 'git entry: completed\n'
fi

deployment_phase='codex agent'
printf '\ncodex agent output:\n'
if [[ $codex_agent_action == create ]]; then
	if [[ $mode == apply ]]; then
		check_directory_path "$target_dir/.codex" || exit 1
		check_directory_path "${codex_agent_file%/*}" || exit 1
		mkdir -p "${codex_agent_file%/*}"
		create_regular_config "$codex_agent_file" 'Codex agent' "$codex_agent_source" || exit "$?"
	fi
	printf 'CREATE: Codex agent: %s (regular copy)\n' "$codex_agent_file"
else
	printf 'UNCHANGED: Codex agent: %s (regular copy)\n' "$codex_agent_file"
fi
if [[ $mode == dry-run ]]; then
	printf 'codex agent: simulation completed; no filesystem changes\n'
else
	printf 'codex agent: completed\n'
fi

if [[ $mode == apply ]]; then
	# --- 核验部署结果 ---
	deployment_phase='verification'
	printf '\nverification: checking deployment\n'
	[[ -f $codex_agent_file ]] || {
		printf 'error: Codex agent was not deployed\n' >&2
		exit 1
	}
	check_codex_agent "$codex_agent_file" "$codex_agent_source" || exit 1
	check_deployed_link 'Neovim entry' \
		"$config_root/nvim/init.lua" "$repo_root/nvim/.config/nvim/init.lua" || exit 1
	check_deployed_link 'Neovim lockfile' \
		"$config_root/nvim/lazy-lock.json" "$repo_root/nvim/.config/nvim/lazy-lock.json" || exit 1
	check_deployed_link 'LazyVim extras' \
		"$config_root/nvim/lazyvim.json" "$repo_root/nvim/.config/nvim/lazyvim.json" || exit 1
	check_deployed_link 'Lazygit config' \
		"$config_root/lazygit/config.yml" "$repo_root/lazygit/.config/lazygit/config.yml" || exit 1
	check_deployed_link 'Shuck config' \
		"$config_root/shuck/shuck.toml" "$repo_root/shuck/.config/shuck/shuck.toml" || exit 1

	# Git：已有个人配置也必须加载真实部署的共享文件。
	check_deployed_link 'Git shared config' \
		"$git_shared_entry" "$git_shared_source" || exit 1
	check_git_config_path "$git_config_file" || exit 1
	check_git_config_syntax "$git_config_file" || exit 1
	if has_git_shared_include "$git_config_file"; then
		git_query_status=0
	else
		git_query_status=$?
	fi
	case $git_query_status in
		0) ;;
		1)
			printf 'error: Git config does not contain the expected shared include: %s\n' "$git_config_file" >&2
			exit 1
			;;
		2) exit 1 ;;
	esac

	# Ghostty：核验当前平台对应的包选择结果。
	check_ghostty_platform "$platform" "$ghostty_platform_entry" "$ghostty_platform_source" || exit 1
	if [[ $platform == macos ]]; then
		check_deployed_link 'Ghostty platform link' \
			"$ghostty_platform_entry" "$ghostty_platform_source" || exit 1
	fi
	printf 'verification: passed\n'
fi

# --- 输出执行结果 ---

if [[ $mode == dry-run ]]; then
	printf '\nresult: dry run completed; no changes made\n'
else
	printf '\nresult: deployment completed\n'
fi
