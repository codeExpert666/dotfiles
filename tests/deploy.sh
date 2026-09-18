#!/usr/bin/env bash

# deploy.sh 集成回归测试。运行：bash tests/deploy.sh
# 入口和支持函数使用 Bash 3.2 语法；部署沿用启动测试的 Bash，HOME 位于隔离夹具。
# 仓库副本和目标均位于 mktemp 目录，命令替身复制到各自夹具中。
# 默认使用真实 Git/Stow；模拟 Darwin 不代表 macOS 实机验证。
# 静态检查覆盖本文件和 deploy/ 下的支持文件；格式：shfmt -ci -sr。
set -eE
set -o pipefail
unset CDPATH

# ===== 初始化 =====

# --- 按入口位置加载支持函数 ---

script_parent="${BASH_SOURCE[0]%/*}"
[[ $script_parent != "${BASH_SOURCE[0]}" ]] || script_parent=.
test_dir=$(cd -- "$script_parent" && pwd -P)
# shellcheck source-path=SCRIPTDIR
# shellcheck source=deploy/support.bash
source "$test_dir/deploy/support.bash"

# --- 建立运行环境和资源生命周期 ---

initialize_test_state Deployment
install_test_traps
prepare_suite "${test_dir%/*}"
printf 'Test environment: %s; Bash %s (%s)\n' "$host_kernel" "$BASH_VERSION" "$real_bash"

# ===== 回归用例 =====

# --- 基础部署、隔离与幂等性 ---
snapshot_tree "$base_repo" > "$test_root/repo.before"

new_case 'native default dry-run; HOME, work and unrelated files unchanged'
case_separate_output=yes
printf 'outside sentinel\n' > "$case_outside/sentinel"
snapshot_tree "$case_outside" > "$case_root/outside.before"
snapshot_tree "$case_work" > "$case_root/work.before"
run_deploy 0
assert_deployment_output dry-run CREATE
grep -q '^LINK:' "$case_log" || fail 'Stow details are missing from the deployment report'
assert_empty "$case_target"
assert_unchanged "$case_outside" "$case_root/outside.before"
assert_unchanged "$case_work" "$case_root/work.before"
pass

case_label='native explicit dry-run; report matches default mode and HOME remains empty'
run_deploy 0 --dry-run
assert_deployment_output dry-run CREATE
assert_empty "$case_target"
pass

case_label='native apply to HOME; platform packages, no folding, paths with spaces'
run_deploy 0 --apply
assert_deployment_output apply CREATE
verify_layout "$host_kernel"
assert_unchanged "$case_outside" "$case_root/outside.before"
assert_unchanged "$case_work" "$case_root/work.before"
pass

case_label='shared skills and Claude aliases read the same instructions and resources'
for name in coding-mentor shell-script-review; do
	shared="$case_target/.agents/skills/$name/SKILL.md"
	alias="$case_target/.claude/skills/$name/SKILL.md"
	# Codex 跟随 Skill 目录链接，但跳过文件级链接形式的 SKILL.md。
	[[ ! -L $shared && ! -L $alias ]] || fail "skill entrypoint must remain a regular file: $name"
	[[ $shared -ef $alias && $shared -ef $case_repo/skills/src/$name/SKILL.md ]] ||
		fail "skill entries do not share the managed source: $name"
done
for relative in references/review-lenses.md agents/openai.yaml; do
	[[ $case_target/.claude/skills/shell-script-review/$relative -ef $case_repo/skills/src/shell-script-review/$relative ]] ||
		fail "skill resource is not available through the Claude alias: $relative"
done
pass

case_label='Codex workflow has a skill directory and a separate agent file without a Claude alias'
assert_link "$case_target/.agents/skills/astra-sol" "$case_repo/skills/src/astra-sol"
[[ ! -L $case_target/.agents/skills/astra-sol/SKILL.md ]] || fail 'skill entrypoint must remain a regular file'
assert_codex_agent_copy
assert_absent "$case_target/.claude/skills/astra-sol"
pass

printf '\n# 原样保留个人配置夹具。\n[core]\n\teditor = regression-test-editor\n' >> "$case_target/.config/git/config"
# 文件级链接必须允许应用状态和本机插件与受管配置共存。
printf 'recentrepos: []\n' > "$case_target/.config/lazygit/state.yml"
printf 'return {}\n' > "$case_target/.config/nvim/lua/plugins/local.lua"
# 个人客户端配置、其他代理和本机专属 Skill 与受管文件共存。
mkdir -p "$case_target/.codex" "$case_target/.dsh" "$case_target/.agents/skills/local-only"
printf 'model = "local-model"\n' > "$case_target/.codex/config.toml"
printf 'name = "local_worker"\n' > "$case_target/.codex/agents/local_worker.toml"
printf '{"theme":"dark"}\n' > "$case_target/.claude/settings.json"
printf 'local: true\n' > "$case_target/.dsh/cordis.patch.yml"
printf 'local skill\n' > "$case_target/.agents/skills/local-only/SKILL.md"
snapshot_tree "$case_target" > "$case_root/target.before"
for mode in --apply --dry-run; do
	case_label="native repeated $mode; content, links, inode, mode and mtime preserved"
	run_deploy 0 "$mode"
	assert_deployment_output "${mode#--}" UNCHANGED
	assert_unchanged "$case_target" "$case_root/target.before"
	assert_unchanged "$case_outside" "$case_root/outside.before"
	assert_unchanged "$case_work" "$case_root/work.before"
	assert_no_log '^CREATE:'
	actual_editor=$(run_git --get core.editor)
	[[ $actual_editor == regression-test-editor ]] || fail 'personal Git setting was lost'
	pass
done

new_case 'Codex partial copy failure leaves no final file or staging directory and permits retry'
run_deploy 0 --apply
rm "$case_target/.codex/agents/sol_worker.toml"
cat > "$case_bin/cat" << 'CAT'
#!/bin/sh
printf 'partial agent content\n'
exit 73
CAT
chmod +x "$case_bin/cat"
run_deploy 1 --apply
assert_failure_result 'codex agent' 1
assert_empty "$case_target/.codex/agents"
rm "$case_bin/cat"
run_deploy 0 --apply
assert_codex_agent_copy
pass

for kind in symlink dangling directory different; do
	new_case "Codex conflicting entry is preserved before any deployment: $kind"
	entry="$case_target/.codex/agents/sol_worker.toml"
	mkdir -p "${entry%/*}"
	case $kind in
		symlink) ln -s "$case_repo/codex/agents/sol_worker.toml" "$entry" ;;
		dangling) ln -s missing "$entry" ;;
		directory) mkdir "$entry" ;;
		different) printf 'personal agent\n' > "$entry" ;;
	esac
	snapshot_tree "$case_target" > "$case_root/target.before"
	for mode in --dry-run --apply; do
		run_deploy 1 "$mode"
		assert_failure_result preflight 1 "${mode#--}"
		assert_unchanged "$case_target" "$case_root/target.before"
	done
	pass
done

new_case 'missing Codex agent source stops deployment before writes'
case_repo="$case_root/repo copy"
cp -R "$base_repo" "$case_repo"
rm "$case_repo/codex/agents/sol_worker.toml"
run_deploy 1 --apply
assert_failure_result preflight 1
assert_empty "$case_target"
pass

new_case 'skill sources and Stow metadata are not deployed; existing HOME/src survives deployment and unstow'
case_repo="$case_root/repo copy"
cp -R "$base_repo" "$case_repo"
mkdir -p "$case_target/src/coding-mentor"
printf 'personal project\n' > "$case_target/src/coding-mentor/SKILL.md"
snapshot_tree "$case_target" > "$case_root/target.before"
snapshot_tree "$case_target/src" > "$case_root/src.before"
# 局部规则替代 Stow 默认项，仍须保留文档、版本控制和编辑器临时文件的忽略行为。
for name in README.md LICENSE COPYING .gitignore notes~; do
	printf 'package metadata\n' > "$case_repo/skills/$name"
done
snapshot_tree "$case_repo/skills" > "$case_root/skills.before"
run_deploy 0 --dry-run
assert_unchanged "$case_target" "$case_root/target.before"
run_deploy 0 --apply
assert_unchanged "$case_target/src" "$case_root/src.before"
for name in .stow-local-ignore README.md LICENSE COPYING .gitignore notes~; do
	assert_absent "$case_target/$name"
done
for client in .agents .claude; do
	for name in coding-mentor shell-script-review; do
		assert_link "$case_target/$client/skills/$name" "$case_repo/skills/src/$name"
	done
done
assert_link "$case_target/.agents/skills/astra-sol" "$case_repo/skills/src/astra-sol"
case_log="$case_root/unstow.log"
(cd "$case_work" && env -i HOME="$case_home" PATH="$case_path" stow --no-folding \
	"--dir=$case_repo" "--target=$case_target" --delete skills) > "$case_log" 2>&1
assert_unchanged "$case_target/src" "$case_root/src.before"
assert_unchanged "$case_repo/skills" "$case_root/skills.before"
for client in .agents .claude; do
	for name in coding-mentor shell-script-review; do
		assert_absent "$case_target/$client/skills/$name"
	done
done
assert_absent "$case_target/.agents/skills/astra-sol"
assert_codex_agent_copy
pass

new_case 'HOME directory alias resolves to the physical home'
case_home="$case_root/home alias"
ln -s "$case_target" "$case_home"
case_environment=("XDG_CONFIG_HOME=$case_home/.config")
run_deploy 0 --apply
verify_layout "$host_kernel"
pass

new_case 'an absolute XDG directory alias to the default is accepted'
mkdir "$case_home/.config"
ln -s "$case_home/.config" "$case_root/config alias"
case_environment=("XDG_CONFIG_HOME=$case_root/config alias")
run_deploy 0 --apply
verify_layout "$host_kernel"
pass

new_case 'a relative XDG path to the default is rejected'
mkdir "$case_home/.config"
case_environment=('XDG_CONFIG_HOME=../home with spaces/.config')
snapshot_tree "$case_target" > "$case_root/target.before"
run_deploy 1 --apply
grep -Fq 'error: XDG_CONFIG_HOME must be unset, empty, or point to' "$case_log" || fail 'wrong XDG diagnostic'
assert_unchanged "$case_target" "$case_root/target.before"
assert_failure_result initialization 1
pass

for kernel in Linux Darwin; do
	new_case "simulated $kernel via uname; real Stow dry-run/apply/repeated apply"
	case_kernel="$kernel"
	install_mock uname
	run_deploy 0
	assert_empty "$case_target"
	run_deploy 0 --apply
	verify_layout "$kernel"
	snapshot_tree "$case_target" > "$case_root/target.before"
	run_deploy 0 --apply
	assert_unchanged "$case_target" "$case_root/target.before"
	pass
done

# --- 命令行、目录约定与依赖 ---
for help_flag in -h --help; do
	new_case "$help_flag works with empty PATH and invalid HOME"
	case_separate_output=yes
	case_path=''
	case_home="$case_root/nonexistent-home"
	run_deploy 0 "$help_flag"
	grep -Fq 'Usage: deploy.sh' "$case_stdout" || fail 'missing help text on stdout'
	grep -Fq 'Deployment reports and diagnostics go to stderr; --help goes to stdout.' "$case_stdout" ||
		fail 'help does not document the output channels'
	[[ ! -s $case_log ]] || fail 'help leaked to stderr'
	assert_no_log --target "$case_stdout"
	assert_empty "$case_target"
	pass
done

new_case 'help remains available with external Stow configuration and empty PATH'
printf '%s\n' --adopt > "$case_home/.stowrc"
case_path=''
run_deploy 0 --help
pass

for argument_case in unknown removed-target repeated-dry repeated-apply mixed help-mixed; do
	new_case "argument validation: $argument_case"
	case_separate_output=yes
	diagnostic='error: choose only one of --dry-run and --apply'
	declare -a arguments=()
	case $argument_case in
		unknown)
			arguments=(--unknown)
			diagnostic='error: unknown argument: --unknown'
			;;
		removed-target)
			arguments=(--target "$case_outside")
			diagnostic='error: unknown argument: --target'
			;;
		repeated-dry) arguments=(--dry-run --dry-run) ;;
		repeated-apply) arguments=(--apply --apply) ;;
		mixed) arguments=(--apply --dry-run) ;;
		help-mixed)
			arguments=(--help --apply)
			diagnostic='error: help must be used alone'
			;;
	esac
	run_deploy 2 "${arguments[@]}"
	[[ ! -s $case_stdout ]] || fail 'argument error leaked to stdout'
	assert_log_sequence "$diagnostic" 'Usage: deploy.sh [--dry-run|--apply]' "Run 'deploy.sh --help' for details."
	assert_no_log '^deploy:|^Modes:|^Requirements:|^Deployment constraints:|^Failure and retry:|^Examples:|^dotfiles deployment|^initialization:|^preflight:'
	assert_empty "$case_target"
	assert_empty "$case_outside"
	assert_no_log '^stow output:|^CREATE:|^result:'
	pass
done

for home_case in empty relative missing file; do
	new_case "HOME validation: $home_case"
	case_separate_output=yes
	case $home_case in
		empty) case_home='' ;;
		relative) case_home='../home with spaces' ;;
		missing) case_home="$case_root/missing" ;;
		file)
			case_home="$case_root/home-file"
			printf 'not a directory\n' > "$case_home"
			;;
	esac
	run_deploy 1 --apply
	[[ ! -s $case_stdout ]] || fail 'HOME diagnostic leaked to stdout'
	grep -Fq 'HOME must be an existing absolute directory:' "$case_log" || fail 'wrong HOME diagnostic'
	assert_empty "$case_target"
	assert_failure_result initialization 1
	pass
done

for name in XDG_CONFIG_HOME XDG_DATA_HOME XDG_STATE_HOME XDG_CACHE_HOME; do
	for value in outside relative; do
		new_case "nondefault $name rejected: $value"
		if [[ $value == outside ]]; then actual="$case_outside"; else actual=relative; fi
		case_environment=("$name=$actual")
		snapshot_tree "$case_outside" > "$case_root/outside.before"
		for mode in --dry-run --apply; do
			run_deploy 1 "$mode"
			grep -Fq "error: $name must be unset, empty, or point to" "$case_log" || fail 'wrong XDG diagnostic'
			assert_empty "$case_target"
			assert_unchanged "$case_outside" "$case_root/outside.before"
			assert_failure_result initialization 1 "${mode#--}"
		done
		pass
	done
done

for setting in empty defaults; do
	new_case "XDG paths accepted: $setting"
	if [[ $setting == empty ]]; then
		case_environment=(XDG_CONFIG_HOME= XDG_DATA_HOME= XDG_STATE_HOME= XDG_CACHE_HOME=)
	else
		case_environment=("XDG_CONFIG_HOME=$case_home/.config/" "XDG_DATA_HOME=$case_home/.local/share"
			"XDG_STATE_HOME=$case_home/.local/state" "XDG_CACHE_HOME=$case_home/.cache")
	fi
	run_deploy 0 --apply
	verify_layout "$host_kernel"
	pass
done

for value in '' exported; do
	new_case "exported ZDOTDIR rejected, including an empty value: $value"
	case_environment=("ZDOTDIR=$value")
	run_deploy 1 --apply
	grep -Fq 'ZDOTDIR must not be exported' "$case_log" || fail 'wrong ZDOTDIR diagnostic'
	assert_empty "$case_target"
	assert_failure_result initialization 1
	pass
done

new_case 'unsupported platform fails before Stow'
case_kernel=UnsupportedTestOS
install_mock uname
run_deploy 1
grep -Fxq 'error: unsupported operating system: UnsupportedTestOS' "$case_log" || fail 'wrong platform diagnostic'
assert_empty "$case_target"
assert_failure_result initialization 1 dry-run
pass

# --- 初始化异常与各阶段中断 ---
# uname 位于命令替换中；即使没有命令诊断，主 Shell 也必须汇总失败。
for output in silent diagnostic; do
	for mode in --dry-run --apply; do
		new_case "initialization command failure: $output/$mode; report preserves status and any raw diagnostic"
		case_separate_output=yes
		case_environment=("DOTFILES_TEST_INIT_OUTPUT=$output")
		cat > "$case_bin/uname" << 'UNAME'
#!/bin/sh
if [ "$DOTFILES_TEST_INIT_OUTPUT" = diagnostic ]; then
    printf 'test uname: injected platform detection failure\n' >&2
fi
exit 69
UNAME
		chmod +x "$case_bin/uname"
		run_deploy 69 "$mode"
		if [[ $output == diagnostic ]]; then
			assert_log_sequence 'initialization: checking environment, paths and platform' \
				'test uname: injected platform detection failure'
		else
			assert_no_log 'test uname:'
		fi
		assert_failure_result initialization 69 "${mode#--}"
		assert_empty "$case_target"
		assert_empty "$case_work"
		assert_empty "$case_outside"
		pass
	done
done

# 在各阶段的外部命令中等待；最终核验用例先完成真实部署，再拦截个人 Git 配置读取。
# 向自有进程组发信号，覆盖终端中断时的父子进程；日志接收端沿用运行器的保护。
# Git 入口的发布和暂存清理在后面的专门用例覆盖。
for phase in initialization preflight stow verification; do
	for mode in --dry-run --apply; do
		[[ $phase != verification || $mode != --dry-run ]] || continue
		for signal in HUP INT TERM; do
			new_case "$phase interrupted by $signal: $mode reports completed stages and the interrupted phase"
			case_separate_output=yes
			case_environment=("DOTFILES_TEST_SIGNAL_PHASE=$phase")
			case $phase in
				initialization) mock_command='uname' ;;
				preflight | verification) mock_command='git' ;;
				stow) mock_command='stow' ;;
			esac
			cat > "$case_bin/$mock_command" << 'CHECKPOINT'
#!/bin/sh
if [ "$DOTFILES_TEST_SIGNAL_PHASE" = verification ] && [ "${3-}" != "$DOTFILES_TEST_TARGET/.config/git/config" ]; then
    exec "$DOTFILES_TEST_REAL_GIT" "$@"
fi
printf '%s\n' "$$" > "$DOTFILES_TEST_READY"
sleep 30
printf 'test stage: signal checkpoint timed out\n' >&2
exit 98
CHECKPOINT
			chmod +x "$case_bin/$mock_command"
			case $signal in HUP) expected_status=129 ;; INT) expected_status=130 ;; TERM) expected_status=143 ;; esac
			start_deploy "$mode"
			wait_for_checkpoint
			kill -s "$signal" -- "-$active_pid"
			wait_deploy "$expected_status"
			assert_no_log 'signal checkpoint timed out'
			assert_failure_result "$phase" "$expected_status" "${mode#--}"
			if [[ $phase == verification ]]; then
				verify_layout "$host_kernel"
			else
				assert_empty "$case_target"
			fi
			assert_empty "$case_work"
			assert_empty "$case_outside"
			pass
		done
	done
done

# --- 部署依赖 ---
for dependency in stow git ln mktemp rm cmp cat mkdir; do
	new_case "required dependency missing: $dependency"
	restricted_path_without "$dependency"
	run_deploy 1 --apply
	grep -Fq "required command not found: $dependency" "$case_log" || fail 'wrong dependency diagnostic'
	assert_empty "$case_target"
	assert_failure_result preflight 1
	pass
done

new_case 'simulated Darwin dry-run does not require ln'
restricted_path_without ln
case_kernel=Darwin
install_mock uname
run_deploy 0
assert_empty "$case_target"
pass

# --- 旧配置入口的迁移保护 ---
for location in git ghostty-xdg ghostty-macos-config ghostty-macos-config-ghostty \
	nvim-init-vim shuck-hidden lazygit-legacy lazygit-macos-native lazygit-macos-legacy \
	lazygit-macos-preferences lazygit-macos-preferences-legacy; do
	for kind in file symlink dangling directory; do
		new_case "legacy entry preserved: $location/$kind"
		case_separate_output=yes
		case $location in
			git)
				entry="$case_target/.gitconfig"
				diagnostic='legacy Git config needs migration:'
				;;
			ghostty-xdg)
				mkdir -p "$case_target/.config/ghostty"
				entry="$case_target/.config/ghostty/config"
				diagnostic='legacy Ghostty config needs migration:'
				;;
			ghostty-macos-*)
				case_kernel=Darwin
				install_mock uname
				mkdir -p "$case_target/Library/Application Support/com.mitchellh.ghostty"
				entry="$case_target/Library/Application Support/com.mitchellh.ghostty/config"
				if [[ $location == ghostty-macos-config-ghostty ]]; then entry="$entry.ghostty"; fi
				diagnostic='legacy Ghostty config needs migration:'
				;;
			nvim-init-vim)
				entry="$case_target/.config/nvim/init.vim"
				diagnostic='Neovim config needs migration:'
				;;
			shuck-hidden)
				entry="$case_target/.config/shuck/.shuck.toml"
				diagnostic='Shuck config needs migration:'
				;;
			lazygit-*)
				diagnostic='Lazygit config needs migration:'
				case $location in
					lazygit-legacy) entry="$case_target/.config/jesseduffield/lazygit/config.yml" ;;
					lazygit-macos-native) entry="$case_target/Library/Application Support/lazygit/config.yml" ;;
					lazygit-macos-legacy) entry="$case_target/Library/Application Support/jesseduffield/lazygit/config.yml" ;;
					lazygit-macos-preferences) entry="$case_target/Library/Preferences/lazygit/config.yml" ;;
					lazygit-macos-preferences-legacy) entry="$case_target/Library/Preferences/jesseduffield/lazygit/config.yml" ;;
				esac
				if [[ $location == lazygit-macos-* ]]; then
					case_kernel=Darwin
					install_mock uname
				fi
				;;
		esac
		mkdir -p "${entry%/*}"
		case $kind in
			file) printf 'keep personal configuration\n' > "$entry" ;;
			symlink)
				printf 'keep external configuration\n' > "$case_outside/config"
				ln -s "$case_outside/config" "$entry"
				;;
			dangling) ln -s missing-config "$entry" ;;
			directory) mkdir "$entry" ;;
		esac
		snapshot_tree "$case_target" > "$case_root/target.before"
		snapshot_tree "$case_outside" > "$case_root/outside.before"
		for mode in --dry-run --apply; do
			run_deploy 1 "$mode"
			grep -Fxq "error: $diagnostic $entry" "$case_log" || fail 'wrong legacy entry diagnostic'
			grep -Fq 'hint: review its settings alongside ' "$case_log" || fail 'missing migration hint'
			if [[ $location == git ]]; then
				assert_log_sequence "error: $diagnostic $entry" \
					"hint: review its settings alongside $case_target/.config/git/config, then move the competing entry aside" \
					"hint: preserve its personal settings in $case_target/.config/git/config after the config.shared include"
			fi
			assert_unchanged "$case_target" "$case_root/target.before"
			assert_unchanged "$case_outside" "$case_root/outside.before"
			assert_failure_result preflight 1 "${mode#--}"
		done
		pass
	done
done

# --- Git 能力预检 ---
# 新目标与已有配置都应在部署前拒绝不支持的选项或探测异常。
for git_mode in unsupported-fixed-value probe-failure; do
	for config_state in absent existing; do
		new_case "mock Git $git_mode with $config_state config; dry-run and apply stop before Stow"
		case_separate_output=yes
		case_git_mode="$git_mode"
		install_mock git
		if [[ $config_state == existing ]]; then
			mkdir -p "$case_target/.config/git"
			printf '[include]\n\tpath = config.shared\n' > "$case_target/.config/git/config"
		fi
		if [[ $git_mode == unsupported-fixed-value ]]; then
			git_status=129
			git_diagnostic='test git: unknown option: fixed-value'
		else
			git_status=69
			git_diagnostic='test git: injected capability query failure'
		fi
		snapshot_tree "$case_repo" > "$case_root/repo.before"
		snapshot_tree "$case_target" > "$case_root/target.before"
		snapshot_tree "$case_outside" > "$case_root/outside.before"
		snapshot_tree "$case_work" > "$case_root/work.before"
		for mode in --dry-run --apply; do
			run_deploy 1 "$mode"
			assert_log_sequence 'preflight: checking target and dependencies' "$git_diagnostic" \
				"error: could not verify Git --fixed-value support (git exit $git_status)"
			assert_unchanged "$case_repo" "$case_root/repo.before"
			assert_unchanged "$case_target" "$case_root/target.before"
			assert_unchanged "$case_outside" "$case_root/outside.before"
			assert_unchanged "$case_work" "$case_root/work.before"
			assert_failure_result preflight 1 "${mode#--}"
		done
		pass
	done
done

# --- 外部 Stow 配置 ---
# 使用真实 Stow 和独立仓库副本，验证拒绝策略及文件状态。
for config_location in work home global; do
	for entry_kind in policy harmless symlink dangling directory; do
		new_case "external Stow configuration: $config_location/$entry_kind; dry-run and apply preserve files"
		cp -R "$base_repo" "$case_root/repo copy"
		case_repo="$case_root/repo copy"
		case $config_location in
			work) config_path="$case_work/.stowrc" ;;
			home) config_path="$case_home/.stowrc" ;;
			global) config_path="$case_home/.stow-global-ignore" ;;
		esac
		case $entry_kind in
			policy)
				if [[ $config_location == global ]]; then
					printf '%s\n' '^\.vimrc$' > "$config_path"
				else
					printf '%s\n' --adopt '--ignore=\.zshenv' > "$config_path"
				fi
				;;
			harmless)
				if [[ $config_location == global ]]; then
					printf '%s\n' '^never-match-fixture$' > "$config_path"
				else
					printf '%s\n' --verbose=2 > "$config_path"
				fi
				;;
			symlink)
				: > "$case_home/stow-settings"
				ln -s "$case_home/stow-settings" "$config_path"
				;;
			dangling) ln -s missing-stow-settings "$config_path" ;;
			directory) mkdir "$config_path" ;;
		esac
		printf 'keep personal Vim configuration\n' > "$case_target/.vimrc"
		snapshot_tree "$case_repo" > "$case_root/repo.before"
		snapshot_tree "$case_target" > "$case_root/target.before"
		snapshot_tree "$case_outside" > "$case_root/outside.before"
		snapshot_tree "$case_work" > "$case_root/work.before"
		for mode in --dry-run --apply; do
			run_deploy 1 "$mode"
			grep -Fxq "error: external Stow configuration is not supported: $config_path" "$case_log" ||
				fail 'external Stow configuration path was not reported'
			assert_unchanged "$case_repo" "$case_root/repo.before"
			assert_unchanged "$case_target" "$case_root/target.before"
			assert_unchanged "$case_outside" "$case_root/outside.before"
			assert_unchanged "$case_work" "$case_root/work.before"
			assert_failure_result preflight 1 "${mode#--}"
		done
		pass
	done
done

# --- 冲突保护与源文件预检 ---
for conflict in vim-file shared-file config-link git-link ghostty-link config-file \
	nvim-file lazygit-file shuck-file nvim-link nvim-lua-link nvim-config-link nvim-plugins-link lazygit-link shuck-link \
	skill-file claude-skill-dir astra-skill-dir sol-worker-file agents-link claude-skills-link codex-link codex-agents-link; do
	new_case "existing path preserved: $conflict"
	case $conflict in
		vim-file) printf 'keep vim file\n' > "$case_target/.vimrc" ;;
		shared-file)
			mkdir -p "$case_target/.config/git"
			printf 'keep shared path\n' > "$case_target/.config/git/config.shared"
			;;
		config-link)
			entry="$case_target/.config"
			ln -s "$case_outside" "$entry"
			;;
		git-link | ghostty-link)
			mkdir "$case_target/.config"
			if [[ $conflict == git-link ]]; then name=git; else name=ghostty; fi
			entry="$case_target/.config/$name"
			ln -s "$case_outside" "$entry"
			;;
		config-file) printf 'keep parent file\n' > "$case_target/.config" ;;
		nvim-file | lazygit-file | shuck-file | skill-file | claude-skill-dir | astra-skill-dir | sol-worker-file)
			case $conflict in
				nvim-file) entry="$case_target/.config/nvim/init.lua" ;;
				lazygit-file) entry="$case_target/.config/lazygit/config.yml" ;;
				shuck-file) entry="$case_target/.config/shuck/shuck.toml" ;;
				skill-file) entry="$case_target/.agents/skills/coding-mentor/SKILL.md" ;;
				claude-skill-dir) entry="$case_target/.claude/skills/coding-mentor/SKILL.md" ;;
				astra-skill-dir) entry="$case_target/.agents/skills/astra-sol/SKILL.md" ;;
				sol-worker-file) entry="$case_target/.codex/agents/sol_worker.toml" ;;
			esac
			mkdir -p "${entry%/*}"
			printf 'keep application config\n' > "$entry"
			;;
		agents-link | claude-skills-link | codex-link | codex-agents-link)
			case $conflict in
				agents-link) name=.agents ;;
				claude-skills-link) name=.claude/skills ;;
				codex-link) name=.codex ;;
				codex-agents-link) name=.codex/agents ;;
			esac
			entry="$case_target/$name"
			mkdir -p "${entry%/*}"
			ln -s "$case_outside" "$entry"
			;;
		nvim-link | nvim-lua-link | nvim-config-link | nvim-plugins-link | lazygit-link | shuck-link)
			case $conflict in
				nvim-link) name=nvim ;;
				nvim-lua-link) name=nvim/lua ;;
				nvim-config-link) name=nvim/lua/config ;;
				nvim-plugins-link) name=nvim/lua/plugins ;;
				lazygit-link) name=lazygit ;;
				shuck-link) name=shuck ;;
			esac
			entry="$case_target/.config/$name"
			mkdir -p "${entry%/*}"
			ln -s "$case_outside" "$entry"
			;;
	esac
	snapshot_tree "$case_target" > "$case_root/target.before"
	snapshot_tree "$case_outside" > "$case_root/outside.before"
	run_deploy 1 --apply
	if [[ $conflict == *-link ]]; then
		grep -Fxq "error: directory path must not be a symbolic link: $entry" "$case_log" ||
			fail 'directory link was not rejected by the directory precheck'
		assert_no_log '^stow output:'
	fi
	assert_unchanged "$case_target" "$case_root/target.before"
	assert_unchanged "$case_outside" "$case_root/outside.before"
	assert_no_log '^CREATE:'
	case $conflict in
		vim-file | shared-file | nvim-file | lazygit-file | shuck-file | skill-file | claude-skill-dir | astra-skill-dir) assert_failure_result stow 1 ;;
		*) assert_failure_result preflight 1 ;;
	esac
	pass
done

for config_case in empty no-include dot-relative absolute conditional malformed symlink dangling directory; do
	new_case "Git entry needs review and remains unchanged: $config_case"
	case_separate_output=yes
	mkdir -p "$case_target/.config/git"
	entry="$case_target/.config/git/config"
	case $config_case in
		empty) : > "$entry" ;;
		no-include) printf '[core]\n\teditor = keep-me\n' > "$entry" ;;
		dot-relative) printf '[include]\n\tpath = ./config.shared\n' > "$entry" ;;
		absolute) printf '[include]\n\tpath = "%s"\n' "$case_repo/git/.config/git/config.shared" > "$entry" ;;
		conditional) printf '[includeIf "gitdir:~/"]\n\tpath = config.shared\n' > "$entry" ;;
		malformed) printf '[include\n\tpath = config.shared\n' > "$entry" ;;
		symlink)
			printf '[include]\n\tpath = config.shared\n' > "$case_outside/config"
			ln -s "$case_outside/config" "$entry"
			;;
		dangling) ln -s missing-config "$entry" ;;
		directory) mkdir "$entry" ;;
	esac
	snapshot_tree "$case_target" > "$case_root/target.before"
	snapshot_tree "$case_outside" > "$case_root/outside.before"
	run_deploy 1 --apply
	case $config_case in
		empty | no-include | dot-relative | absolute | conditional)
			assert_log_sequence "error: existing Git config needs review before adding the shared include: $entry" \
				'hint: expected a direct include.path value of config.shared; existing file left unchanged'
			;;
		malformed)
			grep -Fq "fatal: bad config line 1 in file $entry" "$case_log" || fail 'Git parse error was lost'
			assert_no_log 'git config --fixed-value is required'
			;;
	esac
	assert_unchanged "$case_target" "$case_root/target.before"
	assert_unchanged "$case_outside" "$case_root/outside.before"
	assert_failure_result preflight 1
	pass
done

new_case 'existing personal Git file with one matching include among multiple values'
mkdir -p "$case_target/.config/git"
entry="$case_target/.config/git/config"
printf '# 保留注释和配置顺序。\n[include]\n\tpath = ./config.shared\n\tpath = config.shared\n[core]\n\teditor = personal-fixture-editor\n' > "$entry"
cp "$entry" "$case_root/config.before"
metadata_before=$(file_metadata "$entry")
run_deploy 0 --apply
cmp -s "$entry" "$case_root/config.before" || fail 'personal config content changed'
[[ $(file_metadata "$entry") == "$metadata_before" ]] || fail 'personal config metadata changed'
actual_editor=$(run_git --get core.editor)
[[ $actual_editor == personal-fixture-editor ]] || fail 'personal override did not load'
assert_no_log '^CREATE: Git config:'
pass

for source_case in git-missing git-directory git-malformed ghostty-missing ghostty-directory; do
	new_case "repository source rejected before Stow: $source_case"
	cp -R "$base_repo" "$case_root/repo copy"
	case_repo="$case_root/repo copy"
	case $source_case in
		git-*) source_path="$case_repo/git/.config/git/config.shared" ;;
		ghostty-*)
			source_path="$case_repo/ghostty-macos/.config/ghostty/platform.ghostty"
			case_kernel=Darwin
			install_mock uname
			;;
	esac
	rm "$source_path"
	case $source_case in
		*-directory) mkdir "$source_path" ;;
		git-malformed) printf '[invalid\n' > "$source_path" ;;
	esac
	run_deploy 1 --apply
	assert_empty "$case_target"
	assert_failure_result preflight 1
	pass
done

for kernel in Linux Darwin; do
	for entry_kind in regular wrong-link dangling; do
		new_case "simulated $kernel Ghostty entry conflict preserved: $entry_kind"
		case_kernel="$kernel"
		install_mock uname
		mkdir -p "$case_target/.config/ghostty"
		entry="$case_target/.config/ghostty/platform.ghostty"
		case $entry_kind in
			regular) printf 'keep platform file\n' > "$entry" ;;
			wrong-link) ln -s "$case_repo/ghostty/.config/ghostty/common.ghostty" "$entry" ;;
			dangling) ln -s missing-platform "$entry" ;;
		esac
		snapshot_tree "$case_target" > "$case_root/target.before"
		run_deploy 1 --apply
		assert_unchanged "$case_target" "$case_root/target.before"
		assert_no_log '^CREATE:'
		if [[ $kernel == Darwin ]]; then
			grep -Fq 'CONFLICT when stowing ghostty-macos:' "$case_log" || fail 'Stow did not diagnose the platform conflict'
			assert_failure_result stow 1
		else
			grep -Fq 'Ghostty platform entry must be absent on Linux:' "$case_log" || fail 'wrong platform diagnostic'
			assert_failure_result preflight 1
		fi
		pass
	done
done

# --- 命令失败、发布保护与恢复 ---
new_case 'Git query error preserves raw Git exit status in diagnostic'
mkdir -p "$case_target/.config/git"
printf '[include]\n\tpath = config.shared\n' > "$case_target/.config/git/config"
install_mock git
snapshot_tree "$case_target" > "$case_root/target.before"
run_deploy 1 --apply
grep -Fq 'git exit 69' "$case_log" || fail 'Git query status was lost'
assert_unchanged "$case_target" "$case_root/target.before"
assert_failure_result preflight 1
pass

new_case 'record-only Stow: inherited stow_options cannot add --adopt'
install_mock stow
case_stow_options=--adopt
run_deploy 0
for argument in --simulate --no-folding --stow "${packages[@]}"; do
	grep -Fxq -- "$argument" "$case_root/stow.args" || fail "missing Stow argument: $argument"
done
if grep -Fxq -- --adopt "$case_root/stow.args"; then
	fail 'inherited stow_options reached Stow'
fi
assert_empty "$case_target"
pass

new_case 'record-only Stow succeeds without deploying files; Git creation is stopped'
case_kernel=Linux
install_mock uname
install_mock stow
run_deploy 1 --apply
assert_empty "$case_target"
assert_no_log '^CREATE:'
assert_failure_result 'git entry' 1
pass

for mode in --dry-run --apply; do
	new_case "mock Stow failure: $mode preserves raw output and status, reports the phase, and leaves HOME empty"
	case_separate_output=yes
	cat > "$case_bin/stow" << 'STOW'
#!/bin/sh
printf 'test stow: stdout before failure\n'
printf 'test stow: stderr before failure\n' >&2
exit 73
STOW
	chmod +x "$case_bin/stow"
	run_deploy 73 "$mode"
	[[ ! -s $case_stdout ]] || fail 'Stow output leaked to stdout'
	assert_log_sequence 'stow output:' 'test stow: stdout before failure' 'test stow: stderr before failure'
	assert_empty "$case_target"
	assert_no_log '^CREATE:|^git entry output:|^verification:'
	assert_failure_result stow 73 "${mode#--}"
	pass
done

new_case 'Git entry appears after Stow; preserve it and emit only one failure result before staging starts'
case_environment=("DOTFILES_TEST_REAL_STOW=$(command -v stow)")
cat > "$case_bin/stow" << 'STOW'
#!/bin/sh
set -e
"$DOTFILES_TEST_REAL_STOW" "$@"
printf '# 保留已有的 Git 占位文件。\n' > "$DOTFILES_TEST_TARGET/.config/git/config"
STOW
chmod +x "$case_bin/stow"
run_deploy 1 --apply
printf '# 保留已有的 Git 占位文件。\n' > "$case_root/occupant.expected"
cmp -s "$case_target/.config/git/config" "$case_root/occupant.expected" || fail 'Git occupant was modified'
grep -Fq 'error: cannot create Git config; path already exists:' "$case_log" || fail 'Git occupant was not diagnosed'
assert_no_temporary_entries
assert_no_log '^CREATE:|^verification:'
assert_failure_result 'git entry' 1
pass

for ln_mode in git-file git-directory git-directory-link git-dangling git-fail git-noop; do
	new_case "Git publication preserves occupants and cleans staging: $ln_mode"
	case_kernel=Linux
	case_ln_mode="$ln_mode"
	install_mock uname
	install_mock ln
	if [[ $ln_mode == git-fail ]]; then expected_status=77; else expected_status=1; fi
	run_deploy "$expected_status" --apply
	entry="$case_target/.config/git/config"
	case $ln_mode in
		git-file)
			printf 'keep Git occupant\n' > "$case_root/occupant.expected"
			cmp -s "$entry" "$case_root/occupant.expected" || fail 'Git occupant was modified'
			;;
		git-directory) assert_empty "$entry" ;;
		git-directory-link)
			assert_link "$entry" "$case_outside"
			assert_empty "$case_outside"
			;;
		git-dangling)
			[[ -L $entry && $(readlink "$entry") == missing-config ]] || fail 'Git occupant was replaced'
			;;
		git-fail | git-noop) assert_absent "$entry" ;;
	esac
	assert_no_temporary_entries
	assert_no_log '^CREATE: Git config:'
	assert_failure_result 'git entry' "$expected_status"
	if [[ $ln_mode == git-fail || $ln_mode == git-noop ]]; then
		rm "$case_bin/ln"
		run_deploy 0 --apply
		verify_layout Linux
	fi
	pass
done

new_case 'Git write failure leaves no partial config or staging and permits retry'
case_kernel=Linux
case_limit_writes=yes
install_mock uname
run_deploy 1 --apply
grep -Fq 'error: failed to create Git config:' "$case_log" || fail 'write failure was not diagnosed'
assert_link "$case_target/.vimrc" "$case_repo/vim/.vimrc"
assert_absent "$case_target/.config/git/config"
assert_no_temporary_entries
assert_no_log '^CREATE: Git config:'
assert_failure_result 'git entry' 1
case_limit_writes=no
run_deploy 0 --apply
verify_layout Linux
pass

# 创建失败可能发生在目录已经分配且路径已输出之后，EXIT 仍须回收它。
for mktemp_mode in fail fail-after-output; do
	new_case "Git staging creation failure cleans allocated directories and permits retry: $mktemp_mode"
	case_mktemp_mode="$mktemp_mode"
	install_mock mktemp
	run_deploy 1 --apply
	grep -Fq 'test mktemp: injected' "$case_log" || fail 'staging creation did not reach the injected failure'
	assert_link "$case_target/.vimrc" "$case_repo/vim/.vimrc"
	assert_absent "$case_target/.config/git/config"
	if [[ $mktemp_mode == fail-after-output ]]; then
		read -r staging_path < "$case_root/staging.path"
		assert_absent "$staging_path"
	else
		assert_absent "$case_root/staging.path"
	fi
	assert_no_temporary_entries
	assert_no_log '^CREATE:'
	assert_failure_result 'git entry' 1
	rm "$case_bin/mktemp"
	run_deploy 0 --apply
	verify_layout "$host_kernel"
	pass
done

for publication in success failure; do
	new_case "Git staging cleanup failure after $publication; preserve primary status and report retained path"
	case_rm_mode=git-fail
	install_mock rm
	expected_status=72
	if [[ $publication == failure ]]; then
		case_ln_mode=git-fail
		install_mock ln
		expected_status=77
	fi
	run_deploy "$expected_status" --apply
	read -r staging_path < "$case_root/staging.path"
	[[ $staging_path == "$case_target/.config/git/.deploy-git."* && -d $staging_path && ! -L $staging_path ]] ||
		fail 'cleanup failure did not retain the isolated staging directory'
	grep -Fxq "error: failed to remove Git staging directory (exit 72): $staging_path" "$case_log" ||
		fail 'cleanup failure diagnostic omitted the status or retained path'
	assert_link "$case_target/.vimrc" "$case_repo/vim/.vimrc"
	entry="$case_target/.config/git/config"
	if [[ $publication == success ]]; then
		[[ -f $entry && ! -L $entry && $entry -ef $staging_path/config ]] || fail 'Git publication did not complete'
		run_git --file "$entry" --no-includes --fixed-value --get-all include.path config.shared > /dev/null ||
			fail 'published Git config is incomplete'
	else
		assert_absent "$entry"
	fi
	assert_no_log '^CREATE:'
	assert_failure_result 'git entry' "$expected_status"
	# 明确清理替身报告的自有残留，再恢复真实命令重试；部署不批量清扫历史暂存目录。
	"$real_rm" -rf -- "$staging_path"
	rm "$case_bin/rm"
	if [[ $publication == failure ]]; then rm "$case_bin/ln"; fi
	run_deploy 0 --apply
	verify_layout "$host_kernel"
	assert_no_temporary_entries
	pass
done

for config_state in absent existing; do
	new_case "real Stow ignores shared Git source with $config_state config; apply cannot report success"
	cp -R "$base_repo" "$case_root/repo copy"
	case_repo="$case_root/repo copy"
	case_kernel=Linux
	install_mock uname
	printf '%s\n' '^config\.shared$' > "$case_repo/git/.stow-local-ignore"
	entry="$case_target/.config/git/config"
	if [[ $config_state == existing ]]; then
		mkdir -p "$case_target/.config/git"
		printf '[include]\n\tpath = config.shared\n' > "$entry"
		cp "$entry" "$case_root/config.before"
		metadata_before=$(file_metadata "$entry")
	fi
	run_deploy 1 --apply
	grep -Fq 'Git shared config was not deployed as expected:' "$case_log" || fail 'missing shared source was not diagnosed'
	assert_absent "$case_target/.config/git/config.shared"
	if [[ $config_state == existing ]]; then
		cmp -s "$entry" "$case_root/config.before" || fail 'personal Git config was modified'
		[[ $(file_metadata "$entry") == "$metadata_before" ]] || fail 'personal Git config metadata changed'
		assert_failure_result verification 1
	else
		assert_absent "$entry"
		assert_failure_result 'git entry' 1
	fi
	assert_no_temporary_entries
	assert_no_log '^CREATE: Git config:'
	pass
done

for missing in nvim-entry nvim-lock nvim-extras lazygit-config shuck-config; do
	new_case "ignored application config cannot report success: $missing"
	cp -R "$base_repo" "$case_root/repo copy"
	case_repo="$case_root/repo copy"
	case $missing in
		nvim-entry) package=nvim file=init.lua label='Neovim entry' ;;
		nvim-lock) package=nvim file=lazy-lock.json label='Neovim lockfile' ;;
		nvim-extras) package=nvim file=lazyvim.json label='LazyVim extras' ;;
		lazygit-config) package=lazygit file=config.yml label='Lazygit config' ;;
		shuck-config) package=shuck file=shuck.toml label='Shuck config' ;;
	esac
	printf '^%s$\n' "${file//./\\.}" > "$case_repo/$package/.stow-local-ignore"
	run_deploy 1 --apply
	grep -Fq "$label was not deployed as expected:" "$case_log" || fail 'missing application config was not diagnosed'
	assert_absent "$case_target/.config/$package/$file"
	assert_log_sequence "CREATE: Git config: $case_target/.config/git/config (include.path = config.shared)" \
		'verification: checking deployment' "error: $label was not deployed as expected: $case_target/.config/$package/$file"
	assert_failure_result verification 1
	pass
done

new_case 'simulated Darwin ignored platform package cannot report success'
cp -R "$base_repo" "$case_root/repo copy"
case_repo="$case_root/repo copy"
case_kernel=Darwin
install_mock uname
printf '%s\n' '^platform\.ghostty$' > "$case_repo/ghostty-macos/.stow-local-ignore"
run_deploy 1 --apply
grep -Fq 'Ghostty platform link was not deployed as expected:' "$case_log" || fail 'missing platform link was not diagnosed'
assert_absent "$case_target/.config/ghostty/platform.ghostty"
grep -Fq 'CREATE: Git config:' "$case_log" || fail 'failure occurred before final verification'
assert_failure_result verification 1
pass

# --- 发布后的最终核验 ---

new_case 'final Git query failure reports the query error while the correct include remains present'
# 新目标的预检不查询个人文件；替身在实际发布后的 include 查询处才失败。
install_mock git
run_deploy 1 --apply
grep -Fq 'CREATE: Git config:' "$case_log" || fail 'failure occurred before final verification'
grep -Fq "could not query Git shared include in $case_target/.config/git/config (git exit 69)" "$case_log" ||
	fail 'final verification lost the Git query error'
run_git --file "$case_target/.config/git/config" --no-includes --fixed-value --get-all include.path config.shared > /dev/null ||
	fail 'correct shared include was not preserved'
assert_no_log 'Git config does not contain the expected shared include:'
assert_failure_result verification 1
assert_no_temporary_entries
rm "$case_bin/git"
snapshot_tree "$case_target" > "$case_root/target.before"
run_deploy 0 --apply
assert_unchanged "$case_target" "$case_root/target.before"
verify_layout "$host_kernel"
pass

new_case 'final Git verification diagnoses a missing include in a valid published config'
case_ln_mode=git-remove-include
install_mock ln
run_deploy 1 --apply
grep -Fq 'CREATE: Git config:' "$case_log" || fail 'failure occurred before final verification'
grep -Fq 'Git config does not contain the expected shared include:' "$case_log" || fail 'missing include was not diagnosed'
run_git --file "$case_target/.config/git/config" --no-includes --list > /dev/null || fail 'fixture config is not valid'
if run_git --file "$case_target/.config/git/config" --no-includes --fixed-value --get-all include.path config.shared > /dev/null; then
	fail 'fixture did not remove the shared include'
else
	[[ $? == 1 ]] || fail 'fixture include query failed unexpectedly'
fi
assert_no_log 'could not query Git shared include'
assert_failure_result verification 1
assert_no_temporary_entries
pass

for ln_mode in git-remove-shared git-remove-platform git-replace-platform; do
	new_case "simulated Darwin final verification detects changed entries: $ln_mode"
	case_kernel=Darwin
	case_ln_mode="$ln_mode"
	install_mock uname
	install_mock ln
	run_deploy 1 --apply
	assert_link "$case_target/.vimrc" "$case_repo/vim/.vimrc"
	entry="$case_target/.config/git/config"
	[[ -f $entry && ! -L $entry ]] || fail 'Git publication did not complete before final verification'
	grep -Fq 'CREATE: Git config:' "$case_log" || fail 'failure occurred before final verification'
	case $ln_mode in
		git-remove-shared)
			assert_absent "$case_target/.config/git/config.shared"
			diagnostic='error: Git shared config was not deployed as expected:'
			;;
		git-remove-platform)
			assert_absent "$case_target/.config/ghostty/platform.ghostty"
			diagnostic='error: Ghostty platform link was not deployed as expected:'
			;;
		git-replace-platform)
			assert_link "$case_target/.config/ghostty/platform.ghostty" "$case_repo/ghostty/.config/ghostty/common.ghostty"
			diagnostic='error: Ghostty platform link was not deployed as expected:'
			;;
	esac
	grep -Fq "$diagnostic" "$case_log" || fail 'wrong final verification diagnostic'
	assert_no_temporary_entries
	assert_failure_result verification 1
	pass
done

# --- 暂存创建、路径交接与发布阶段的信号中断 ---

for kernel in Linux Darwin; do
	for stage in publication creation-before-output creation-after-output; do
		for signal in HUP INT TERM; do
			new_case "simulated $kernel Git $stage interrupted by $signal; staging cleaned and retry succeeds"
			case_kernel="$kernel"
			install_mock uname
			if [[ $stage == publication ]]; then
				case_ln_mode=wait-git
				mock_command='ln'
			else
				case_mktemp_mode="wait-${stage#creation-}"
				mock_command=mktemp
			fi
			install_mock "$mock_command"
			case $signal in HUP) expected_status=129 ;; INT) expected_status=130 ;; TERM) expected_status=143 ;; esac
			start_deploy --apply
			wait_for_checkpoint
			if [[ $stage != publication ]]; then
				read -r staging_path < "$case_root/staging.path"
				assert_empty "$staging_path"
			fi
			kill -s "$signal" -- "-$active_pid"
			if [[ $stage != publication ]]; then : > "$case_root/mock.release"; fi
			wait_deploy "$expected_status"
			assert_link "$case_target/.vimrc" "$case_repo/vim/.vimrc"
			assert_absent "$case_target/.config/git/config"
			if [[ $kernel == Darwin ]]; then
				assert_link "$case_target/.config/ghostty/platform.ghostty" "$case_repo/ghostty-macos/.config/ghostty/platform.ghostty"
			else
				assert_absent "$case_target/.config/ghostty/platform.ghostty"
			fi
			assert_no_temporary_entries
			assert_no_log 'signal checkpoint timed out|^CREATE:'
			assert_failure_result 'git entry' "$expected_status"
			rm "$case_bin/$mock_command"
			run_deploy 0 --apply
			verify_layout "$kernel"
			pass
		done
	done
done

# --- 测试运行器自身的隔离、诊断与收尾 ---

# 将此函数复制成临时程序，以独立 Bash 验证运行器；不递归运行整套用例。
# 控制文件位于外层夹具，便于在内层清理完成后验证根目录和子进程状态。
runner_probe() {
	local probe_mode="$1" original_repo="$2"
	local probe_root="$DOTFILES_TEST_TARGET" ready_file="$DOTFILES_TEST_READY" query_result
	mkdir "$probe_root/physical"
	ln -s "$probe_root/physical" "$probe_root/alias"
	initialize_test_state Deployment
	install_test_traps
	prepare_suite "$original_repo" "$probe_root/alias"
	printf '%s\n' "$test_root" > "$probe_root/owned-root"
	new_case "runner probe: $probe_mode"
	case_kernel=Linux
	install_mock uname
	case $probe_mode in
		signal-* | group-* | stubborn-*)
			case_ln_mode=wait-git
			if [[ $probe_mode == stubborn-* ]]; then case_ln_mode=wait-git-stubborn; fi
			install_mock ln
			start_deploy --apply
			wait_for_checkpoint
			printf '%s\n' "$$" "$active_pid" > "$probe_root/control"
			printf 'ready\n' > "$ready_file"
			wait_deploy 0
			fail 'runner continued after the signal checkpoint'
			;;
	esac
	run_deploy 0 --apply
	verify_layout Linux
	case $probe_mode in
		cleanup-failure | failure-and-cleanup)
			real_rm="$probe_root/rm"
			printf '#!/bin/sh\nexit 72\n' > "$real_rm"
			chmod +x "$real_rm"
			;;
	esac
	case $probe_mode in
		command-failure | failure-and-cleanup)
			query_result=$("$real_bash" -c 'exit 70')
			[[ -z $query_result ]] || fail 'unexpected helper output'
			;;
	esac
	suite_complete=yes
}

for probe_mode in normal command-failure cleanup-failure failure-and-cleanup signal-HUP signal-INT signal-TERM group-HUP group-INT group-TERM stubborn-TERM; do
	new_case "runner lifecycle: $probe_mode; symlinked temporary parent, alternate PATH bash, simulated Linux child"
	# PATH 中的同名 Bash 故意失败，内层初始化及部署必须沿用启动它的解释器。
	printf '#!/bin/sh\nexit 91\n' > "$case_bin/bash"
	chmod +x "$case_bin/bash"
	case_repo="$case_root/probe repo"
	mkdir -p "$case_repo/scripts"
	{
		printf '#!/usr/bin/env bash\nset -eE\nset -o pipefail\n'
		printf 'source %q\n' "$source_repo/tests/deploy/support.bash"
		declare -f runner_probe
		printf 'runner_probe %q %q\n' "$probe_mode" "$source_repo"
	} > "$case_repo/scripts/deploy.sh"
	case $probe_mode in
		normal) expected_status=0 ;;
		command-failure | failure-and-cleanup) expected_status=70 ;;
		cleanup-failure) expected_status=72 ;;
		*-HUP) expected_status=129 ;;
		*-INT) expected_status=130 ;;
		*-TERM) expected_status=143 ;;
	esac
	start_deploy
	case $probe_mode in
		signal-* | group-* | stubborn-*)
			wait_for_checkpoint
			{
				read -r probe_pid
				read -r probe_group
			} < "$case_target/control"
			register_group "$probe_group"
			if [[ $probe_mode == group-* ]]; then
				kill -s "${probe_mode##*-}" -- "-$active_pid"
			else
				kill -s "${probe_mode##*-}" "$probe_pid"
			fi
			;;
	esac
	wait_deploy "$expected_status"
	read -r owned_root < "$case_target/owned-root"
	[[ $owned_root == "$case_target/physical/"* ]] || fail 'temporary root was not canonicalized'
	case $probe_mode in
		signal-* | group-* | stubborn-*)
			# 此处不代替内层运行器终止进程；它必须在退出前完成清理。
			if group_is_running "$probe_group"; then
				fail 'runner exited while its deployment descendants were still running'
			else
				[[ $? == 1 ]] || fail 'could not inspect runner descendants'
			fi
			forget_group "$probe_group"
			;;
	esac
	case $probe_mode in
		cleanup-failure | failure-and-cleanup)
			[[ -d $owned_root ]] || fail 'cleanup failure fixture was not retained'
			grep -Fq 'FAIL: could not remove test directory (exit 72):' "$case_log" || fail 'missing cleanup diagnostic'
			;;
		*) assert_absent "$owned_root" ;;
	esac
	if [[ $probe_mode == normal ]]; then
		grep -Fq 'Deployment tests passed:' "$case_log" || fail 'missing runner success summary'
	else
		assert_no_log '^Deployment tests passed:'
		grep -Fq 'FAIL:' "$case_log" || fail 'missing runner failure diagnostic'
	fi
	case $probe_mode in
		command-failure | failure-and-cleanup)
			grep -Fq 'ERROR:' "$case_log" || fail 'missing helper error location'
			grep -Fq "runner probe: $probe_mode" "$case_log" || fail 'missing failing case context'
			grep -Fq 'result: deployment completed' "$case_log" || fail 'last deployment log was not reported'
			;;
	esac
	pass
done

# ===== 源文件核验与收尾 =====

case_label='test repository source remains unchanged'
case_phase='source verification'
case_log=''
snapshot_tree "$base_repo" > "$test_root/repo.after"
cmp -s "$test_root/repo.before" "$test_root/repo.after" || fail 'deployment modified the repository copy'
pass
# EXIT 统一等待活动进程、删除夹具，再决定是否输出成功汇总。
suite_complete=yes
