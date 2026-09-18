#!/usr/bin/env bash

# Python 标准库负责隔离目录、文件快照和中断测试；不安装任何应用或插件。
set -e
unset CDPATH
script_parent="${BASH_SOURCE[0]%/*}"
[[ $script_parent != "${BASH_SOURCE[0]}" ]] || script_parent=.
test_dir=$(cd -- "$script_parent" && pwd -P)
export DOTFILES_TEST_BASH="$BASH"
exec python3 -B "$test_dir/doctor/cases.py" "$@"
