#!/usr/bin/env bash

# desktop 追加内容，使用与基础清单相同的数组结构。
# shellcheck disable=SC2034 # 安装器读取这些清单数组。
apt_packages=(fontconfig wl-clipboard xclip 7zip)
apt_commands=(
	'fontconfig|fc-list fc-cache'
	'wl-clipboard|wl-copy wl-paste'
	'7zip|7zz'
)

# 发行版来源差异也留在清单中。24.04 的社区 deb 使用 releases.json 校验。
deb_releases_24_04=(ghostty)
apt_packages_26_04=(ghostty)

# IosevkaTerm Nerd Font / Sarasa Term SC 由公共字体阶段准备。
