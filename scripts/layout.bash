#!/usr/bin/env bash

# 三个入口共用的布局事实；Bash 3.2+。加载时只定义数据和函数。
# layout_* 数组使用固定的相对路径；调用者决定诊断、退出和检查时机。
# shellcheck disable=SC2034 # 数据由加载本文件的入口消费。
layout_xdg_paths=(
	'XDG_CONFIG_HOME|.config'
	'XDG_DATA_HOME|.local/share'
	'XDG_STATE_HOME|.local/state'
	'XDG_CACHE_HOME|.cache'
)
layout_directories=(
	.config .config/nvim .config/nvim/lua .config/nvim/lua/config
	.config/nvim/lua/plugins .config/lazygit .config/shuck .config/ghostty .config/git
	.agents .agents/skills .claude .claude/skills .codex .codex/agents
)
# 每项声明一个受管 Skill 及所需客户端入口；缺失入口仍须报告。
layout_skill_clients=(
	'astra-sol|.agents'
	'coding-mentor|.agents .claude'
	'shell-script-review|.agents .claude'
)
# Codex 拒绝代理 TOML 的符号链接，此文件单独发布为普通副本。
layout_codex_agent_source='codex/agents/sol_worker.toml'
layout_codex_agent_target='.codex/agents/sol_worker.toml'
layout_ghostty_platform='.config/ghostty/platform.ghostty'

layout_is_default_path() {
	local actual="$1" expected="$2"
	[[ -z $actual || ${actual%/} == "${expected%/}" ||
		($actual == /* && -d $actual && -d $expected && $actual -ef $expected) ]]
}

load_dotfiles_layout() {
	local platform="$1" directory entry
	layout_packages=(atuin ghostty git lazygit nvim shuck skills starship vim zsh)
	[[ $platform != macos ]] || layout_packages+=(ghostty-macos)
	# 记录字段：诊断 ID | 部署提示名称 | 旧入口 | 迁移目标。路径均相对 HOME。
	layout_competing=(
		'git-legacy|legacy Git|.gitconfig|.config/git/config'
		'ghostty-legacy|legacy Ghostty|.config/ghostty/config|.config/ghostty/config.ghostty'
	)
	if [[ $platform == macos ]]; then
		for entry in config config.ghostty; do
			layout_competing+=("ghostty-native|legacy Ghostty|Library/Application Support/com.mitchellh.ghostty/$entry|.config/ghostty/config.ghostty")
		done
	fi
	layout_competing+=(
		'nvim-legacy|Neovim|.config/nvim/init.vim|.config/nvim/init.lua'
		'shuck-legacy|Shuck|.config/shuck/.shuck.toml|.config/shuck/shuck.toml'
		'lazygit-legacy|Lazygit|.config/jesseduffield/lazygit/config.yml|.config/lazygit/config.yml'
	)
	if [[ $platform == macos ]]; then
		for directory in 'Application Support' Preferences; do
			for entry in lazygit jesseduffield/lazygit; do
				layout_competing+=("lazygit-native|Lazygit|Library/$directory/$entry/config.yml|.config/lazygit/config.yml")
			done
		done
	fi
}
