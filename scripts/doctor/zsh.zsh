#!/usr/bin/env zsh

# 断言有意读取紧邻条件的退出状态；保留其他 Shuck 检查。
# shuck: disable-file=C056

# 只在 runtime.py 准备的临时 HOME 中 source；记录证据，不向终端输出报告。
doctor_assert() {
	local level=PASS hint=''
	if [[ $1 != 0 ]]; then
		level=FAIL
		doctor_zsh_failed=1
		hint='Review the managed startup files and installed plugin versions; inspect initialization errors with --verbose.'
	fi
	print -r -- "$level"$'\t'"zsh.runtime.$2"$'\t'"$3"$'\t'"$hint" >> "$DOTFILES_DOCTOR_REPORT"
}

doctor_zsh_failed=0
[[ $ZDOTDIR == "$HOME/.config/zsh" && ${skip_global_compinit-} == 1 ]]
doctor_assert $? root '.zshenv sets ZDOTDIR and the early compinit switch'
[[ $EDITOR == nvim && $VISUAL == nvim && $SUDO_EDITOR == nvim ]]
doctor_assert $? editors 'editor defaults are nvim'

if [[ $1 == noninteractive ]]; then
	[[ -z ${HISTFILE-} ]]
	doctor_assert $? noninteractive 'noninteractive startup does not initialize interactive history'
	# 不手工清除 ZDOTDIR，验证真实的未导出属性使子进程重读根入口。
	(
		unset skip_global_compinit
		command zsh -c '[[ ${skip_global_compinit-} == 1 && $EDITOR == nvim ]]'
	)
	doctor_assert $? child 'child Zsh reads the root startup file again'
elif [[ $1 == interactive ]]; then
	[[ ${aliases[ll]-} == 'ls -alF' && $HISTFILE == "$HOME/.local/state/zsh/history" ]]
	doctor_assert $? shared 'interactive shared settings and the history path loaded'
	[[ -o append_history && -o hist_ignore_space ]]
	doctor_assert $? history 'managed history options are active'
	(($+functions[compdef] && $+functions[_zsh_autosuggest_start] && $+functions[_zsh_highlight]))
	doctor_assert $? plugins 'completion, autosuggestions and syntax highlighting functions loaded'
	if (($+commands[fzf])); then
		(($+widgets[fzf-file-widget]))
		doctor_assert $? fzf 'fzf file-selection widget is registered'
		[[ $(bindkey -M emacs '^[c') != *fzf* ]]
		doctor_assert $? fzf-alt-c 'fzf does not take Alt-C'
	fi
	if (($+commands[atuin])); then
		[[ $(bindkey -M emacs '^R') == *atuin* ]]
		doctor_assert $? atuin 'Atuin owns Ctrl-R'
		[[ $(bindkey -M emacs '^[[A') != *atuin* && $(bindkey -M emacs '?') != *atuin* ]]
		doctor_assert $? atuin-keys 'Atuin leaves Up and ? unchanged'
	fi
	if (($+commands[zoxide])); then
		(($+functions[z] && $+functions[zi] && !$+functions[cd]))
		doctor_assert $? zoxide 'z/zi exist and native cd is retained'
	fi
	if (($+commands[starship])); then
		[[ ${precmd_functions[*]-} == *starship* ]]
		doctor_assert $? starship 'Starship prompt hook is registered'
	fi
	if [[ $OSTYPE == darwin* ]]; then
		[[ $CLICOLOR == 1 ]]
		doctor_assert $? platform 'macOS color configuration loaded'
	elif [[ -x /usr/bin/dircolors ]]; then
		[[ ${aliases[ls]-} == 'ls --color=auto' ]]
		doctor_assert $? platform 'Linux color configuration loaded'
	fi
fi
print -r -- "zsh.$1" > "$DOTFILES_DOCTOR_COMPLETE" || return 1
return "$doctor_zsh_failed"
