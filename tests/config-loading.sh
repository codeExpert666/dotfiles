#!/usr/bin/env bash

# 从隔离 HOME 验证配置加载与生效。运行：bash tests/config-loading.sh
# Zsh/Vim/Python 3 是必要依赖；Python 的 pty 模块提供交互式终端。
# 未安装的可选应用明确标记为 SKIP。
# 使用共享设施准备隔离仓库；应用检查使用原生平台，不模拟 macOS。
# 校验：bash -n、shellcheck；格式：shfmt -ci -sr。
set -eE
set -o pipefail
unset CDPATH

script_parent="${BASH_SOURCE[0]%/*}"
[[ $script_parent != "${BASH_SOURCE[0]}" ]] || script_parent=.
test_dir=$(cd -- "$script_parent" && pwd -P)
# shellcheck source-path=SCRIPTDIR
# shellcheck source=support/harness.bash
source "$test_dir/support/harness.bash"

initialize_test_state 'Configuration loading'
install_test_traps
prepare_suite "${test_dir%/*}"
real_zsh=$(command -v zsh) || fail 'required test command not found: zsh'
real_vim=$(command -v vim) || fail 'required test command not found: vim'
real_python=$(command -v python3) || fail 'required test command not found: python3'
snapshot_tree "$base_repo" > "$test_root/repo.before"

run_application() {
	run_test_command run 30 "$@"
}

run_interactive_zsh() {
	# ZLE 初始化需要终端；共享执行器规范化 CRLF 并保留应用退出码。
	run_test_command pty 30 "$real_zsh" -ic "$1"
}

assert_git_setting() {
	# 应用调用留在入口 Shell 中等待；命令替换会推迟入口处理信号。
	run_application "$real_git" config --get "$1" > "$case_root/git-value" 2>> "$case_log" || fail "Git could not read $1"
	[[ $(< "$case_root/git-value") == "$2" ]] || fail "unexpected Git setting: $1 (expected $2)"
}

# 部署仅准备输入夹具；链接布局、幂等性和冲突属于 deploy 套件。
new_fixture 'prepare deployed configuration'
run_application "$real_bash" "$case_repo/scripts/deploy.sh" --apply > "$case_log" 2>&1 || fail 'could not prepare deployed configuration'

# 模拟 bootstrap 发布的稳定入口；这些命令只用于检查 Zsh 的选择顺序，不执行。
managed_jdk="$case_home/.local/share/dotfiles-bootstrap/jdk/jdk-25-fixture"
mkdir -p "$managed_jdk/bin" "$case_home/.local/bin"
for command in java javac jar; do
	printf '#!/bin/sh\nexit 0\n' > "$managed_jdk/bin/$command"
	chmod +x "$managed_jdk/bin/$command"
done
ln -s "$managed_jdk" "$case_home/.local/share/dotfiles-bootstrap/jdk/current"
printf '#!/bin/sh\nexit 0\n' > "$case_home/.local/bin/mvn"
chmod +x "$case_home/.local/bin/mvn"

case_label='Zsh noninteractive startup and child shells read the root .zshenv'
case_phase='application startup and assertions'
case_log="$case_root/zsh-noninteractive.log"
# shellcheck disable=SC2016
run_application "$real_zsh" -c '[[ $ZDOTDIR == "$HOME/.config/zsh" && ${skip_global_compinit-} == 1 && -z ${HISTFILE-} ]]' > "$case_log" 2>&1 || fail 'Zsh did not load the root startup settings'
# shellcheck disable=SC2016
run_application "$real_zsh" -c 'unset skip_global_compinit; exec zsh -c "$1"' zsh '[[ ${skip_global_compinit-} == 1 ]]' >> "$case_log" 2>&1 || fail 'child Zsh skipped the root startup file'
# shellcheck disable=SC2016
run_application "$real_zsh" -c 'exec sh -c "$1"' zsh \
	'[ "$EDITOR" = nvim ] && [ "$VISUAL" = nvim ] && [ "$SUDO_EDITOR" = nvim ] &&
	 [ "$JAVA_HOME" = "$HOME/.local/share/dotfiles-bootstrap/jdk/current" ]' \
	>> "$case_log" 2>&1 || fail 'editor defaults were not exported to child processes'
# shellcheck disable=SC2016
run_application "$real_zsh" -c \
	'[[ $commands[java] == "$JAVA_HOME/bin/java" && $commands[javac] == "$JAVA_HOME/bin/javac" &&
	   $commands[jar] == "$JAVA_HOME/bin/jar" && $commands[mvn] == "$HOME/.local/bin/mvn" ]]' \
	>> "$case_log" 2>&1 || fail 'noninteractive Zsh did not select the managed JDK and Maven commands'
[[ ! -s $case_log ]] || fail 'noninteractive startup produced output'
pass

case_label='Zsh interactive startup loads shared settings and the default history path'
case_log="$case_root/zsh-interactive.log"
# shellcheck disable=SC2016
run_interactive_zsh '[[ ${aliases[ll]-} == "ls -alF" && ${aliases[lg]-} == lazygit &&
  ${aliases[mp]-} == multipass && $HISTFILE == "$HOME/.local/state/zsh/history" ]]' > "$case_log" 2>&1 || fail 'Zsh did not load the interactive settings'
printf 'Antidote is missing: %s/.local/share/antidote\n' "$case_home" > "$case_root/zsh-interactive.expected"
cmp -s "$case_log" "$case_root/zsh-interactive.expected" || fail 'unexpected interactive startup output'
pass

case_label='Zsh login startup loads local.zprofile'
printf 'DOTFILES_TEST_LOGIN=loaded\n' > "$case_home/.config/zsh/local.zprofile"
case_log="$case_root/zsh-login.log"
# shellcheck disable=SC2016
run_application "$real_zsh" -lc \
	'[[ ${DOTFILES_TEST_LOGIN-} == loaded && $EDITOR == nvim && $VISUAL == nvim &&
	   $commands[java] == "$JAVA_HOME/bin/java" && $commands[javac] == "$JAVA_HOME/bin/javac" &&
	   $commands[jar] == "$JAVA_HOME/bin/jar" && $commands[mvn] == "$HOME/.local/bin/mvn" ]]' \
	> "$case_log" 2>&1 || fail 'Zsh login startup did not retain managed Java and Maven command precedence'
pass

case_label='Zsh local overrides run after defaults and before syntax highlighting'
# 仅替换插件管理边界，避免测试下载插件；用高亮入口观察真正的加载顺序。
mkdir -p "$case_home/.local/share/antidote" "$case_home/.local/share/test-highlighting"
cat > "$case_home/.local/share/antidote/antidote.zsh" << 'ANTIDOTE'
antidote() {
  if [[ $1 == path ]]; then
    print -r -- "$HOME/.local/share/test-highlighting"
  fi
}
ANTIDOTE
# shellcheck disable=SC2016
printf 'DOTFILES_TEST_HIGHLIGHT_STYLE=$ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE\n' > "$case_home/.local/share/test-highlighting/zsh-syntax-highlighting.zsh"
printf "ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE='fg=red'\n" > "$case_home/.config/zsh/local.zsh"
case_log="$case_root/zsh-local.log"
# shellcheck disable=SC2016
run_interactive_zsh '[[ $ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE == fg=red && ${DOTFILES_TEST_HIGHLIGHT_STYLE-} == fg=red ]]' > "$case_log" 2>&1 || fail 'Zsh local overrides ran in the wrong order'
[[ ! -s $case_log ]] || fail 'plugin initialization produced unexpected output'
pass

case_label='Zsh declares truecolor only for Ghostty SSH sessions'
case_log="$case_root/zsh-truecolor.log"
# 共享执行器清空环境并固定 TERM，SSH 变量因此由 env 覆写；断言放进启动命令，
# 由 Zsh 退出码体现，不依赖终端输出。SSH_TTY 只要求非空，不访问该设备。
# shellcheck disable=SC2016
run_test_command pty 30 env TERM=xterm-ghostty SSH_TTY=/dev/pts/9 "$real_zsh" -ic \
	'[[ $COLORTERM == truecolor ]]' > "$case_log" 2>&1 || fail 'Ghostty SSH session did not declare truecolor'
# 服务端或本机已提供的非空声明必须保留，避免覆盖用户环境。
# shellcheck disable=SC2016
run_test_command pty 30 env TERM=xterm-ghostty SSH_TTY=/dev/pts/9 COLORTERM=24bit "$real_zsh" -ic \
	'[[ $COLORTERM == 24bit ]]' >> "$case_log" 2>&1 || fail 'existing COLORTERM was overwritten'
# 其他 TERM 的 SSH 会话与本地 Ghostty 会话都保持原值，不引入终端定义之外的声明。
# shellcheck disable=SC2016
run_test_command pty 30 env TERM=xterm-256color SSH_TTY=/dev/pts/9 "$real_zsh" -ic \
	'[[ -z ${COLORTERM-} ]]' >> "$case_log" 2>&1 || fail 'non-Ghostty SSH session declared truecolor'
# shellcheck disable=SC2016
run_test_command pty 30 env TERM=xterm-ghostty "$real_zsh" -ic \
	'[[ -z ${COLORTERM-} ]]' >> "$case_log" 2>&1 || fail 'local Ghostty session declared truecolor'
pass

case_label='Git loads the shared file normally and writes global changes to the personal file'
case_log="$case_root/git.log"
run_application "$real_git" config --show-origin --get init.defaultBranch > "$case_log" 2>&1 || fail 'Git could not read the shared setting'
grep -Fq '.config/git/config.shared' "$case_log" || fail 'Git did not load the shared entry'
assert_git_setting init.defaultBranch main
assert_git_setting core.editor nvim
run_application "$real_git" config --global core.editor personal-test-editor >> "$case_log" 2>&1 || fail 'Git could not write the personal setting'
assert_git_setting core.editor personal-test-editor
[[ -f $case_home/.config/git/config && ! -L $case_home/.config/git/config ]] || fail 'Git personal entry is no longer a regular file'
assert_absent "$case_home/.gitconfig"
pass

case_label='Vim discovers .vimrc and uses the default state directory'
case_log="$case_root/vim.log"
# 静默 Ex 模式会跳过正常启动文件；使用普通启动并从命令参数退出。
# shellcheck disable=SC2016
run_application "$real_vim" -N -n --not-a-term \
	-c 'call writefile([$MYVIMRC, string(&number), &viminfofile, get(g:, "netrw_home", "")], "vim-settings")' \
	-c 'qa!' > "$case_log" 2>&1 || fail 'Vim startup failed'
printf '%s\n' "$case_home/.vimrc" 1 "$case_home/.local/state/vim/viminfo" "$case_home/.local/state/vim" > "$case_root/vim.expected"
cmp -s "$case_root/vim.expected" "$case_work/vim-settings" || fail 'Vim did not load the expected configuration'
pass

case_label='Neovim discovers the default configuration and parses all Lua files offline'
if real_nvim=$(command -v nvim); then
	case_log="$case_root/nvim-syntax.log"
	# -u NONE 避免启动插件安装；Lua 错误显式使用非零退出码，防止 qa 掩盖错误。
	run_application "$real_nvim" --headless -u NONE -n -i NONE \
		-l "$test_dir/config-loading/nvim.lua" > "$case_log" 2>&1 || fail 'Neovim configuration path, Lua syntax or settings differ'
	pass
else
	skip 'nvim is not installed'
fi

case_label='Shuck CLI loads global formatting settings from the deployed config'
if real_shuck=$(command -v shuck); then
	case_log="$case_root/shuck.log"
	# 用实际格式化结果验证全局配置发现；输入和输出均位于隔离目录。
	# shellcheck disable=SC2016
	printf 'case "$1" in\nfoo)\necho ok>out\n;;\nesac\n' > "$case_work/format.zsh"
	run_application env SHUCK_EXPERIMENTAL=1 "$real_shuck" format --no-cache "$case_work/format.zsh" > "$case_log" 2>&1 || fail 'Shuck could not format with the global config'
	# shellcheck disable=SC2016
	printf 'case "$1" in\n\tfoo)\n\t\techo ok > out\n\t\t;;\nesac\n' > "$case_root/shuck.expected"
	cmp -s "$case_root/shuck.expected" "$case_work/format.zsh" || fail 'Shuck did not apply the global formatting settings'
	pass
else
	skip 'shuck is not installed'
fi

case_label='Lazygit discovers its config, renders a diff with delta and invokes Neovim'
if real_lazygit=$(command -v lazygit) && real_delta=$(command -v delta); then
	case_log="$case_root/lazygit.log"
	run_application "$real_python" -B "$test_dir/config-loading/lazygit.py" "$real_lazygit" "$real_delta" \
		> "$case_log" 2>&1 || fail 'Lazygit configuration or external command integration failed'
	pass
else
	skip 'lazygit or delta is not installed'
fi

case_label='Starship loads the sole default Tokyo Night configuration'
if real_starship=$(command -v starship); then
	case_log="$case_root/starship.log"
	run_application "$real_starship" print-config format > "$case_log" 2>&1 || fail 'Starship could not read the default configuration'
	grep -Fq '[░▒▓](#a3aed2)' "$case_log" || fail 'Starship did not load Tokyo Night by default'
	pass
else
	skip 'starship is not installed'
fi

case_label='Atuin loads personal settings and the default log directory'
if real_atuin=$(command -v atuin); then
	case_log="$case_root/atuin.log"
	run_application "$real_atuin" config get --resolved auto_sync > "$case_log" 2>&1 || fail 'Atuin could not read its settings'
	grep -Fxq false "$case_log" || fail 'Atuin did not load the personal config'
	run_application "$real_atuin" config get --resolved logs.dir > "$case_root/atuin-logs" 2>> "$case_log" || fail 'Atuin could not resolve its log directory'
	grep -Fxq "$case_home/.local/state/atuin/logs" "$case_root/atuin-logs" || fail 'Atuin log directory differs'
	run_application "$real_atuin" init zsh --disable-up-arrow --disable-ai > "$case_root/atuin-init.zsh" 2>> "$case_log" || fail 'Atuin Zsh initialization failed'
	run_application "$real_zsh" -n "$case_root/atuin-init.zsh" >> "$case_log" 2>&1 || fail 'Atuin generated invalid Zsh initialization'
	pass
else
	skip 'atuin is not installed'
fi

case_label='native Ghostty parses deployed includes'
real_ghostty=$(command -v ghostty || :)
if [[ -z $real_ghostty && $host_kernel == Darwin ]]; then
	for ghostty_candidate in /Applications/Ghostty.app/Contents/MacOS/ghostty "$HOME/Applications/Ghostty.app/Contents/MacOS/ghostty"; do
		if [[ -x $ghostty_candidate ]]; then
			real_ghostty="$ghostty_candidate"
			break
		fi
	done
fi
if [[ -n $real_ghostty ]]; then
	case_log="$case_root/ghostty.log"
	# 显式 --config-file 只验证所选入口及其相对 include，不读取默认配置、不启动窗口。
	run_application "$real_ghostty" +validate-config \
		"--config-file=$case_home/.config/ghostty/config.ghostty" > "$case_log" 2>&1 || fail 'Ghostty rejected the deployed configuration'
	pass
else
	skip 'ghostty is not installed; shader rendering requires native graphical validation'
fi

case_label='application startup leaves the repository source unchanged'
case_phase='source verification'
case_log=''
snapshot_tree "$base_repo" > "$test_root/repo.after"
cmp -s "$test_root/repo.before" "$test_root/repo.after" || fail 'application startup modified repository files'
pass
suite_complete=yes
