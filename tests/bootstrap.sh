#!/usr/bin/env bash

# 隔离 HOME、模拟包管理器；不安装系统软件或访问网络。
set -e
unset CDPATH
script_parent="${BASH_SOURCE[0]%/*}"
[[ $script_parent != "${BASH_SOURCE[0]}" ]] || script_parent=.
test_dir=$(cd -- "$script_parent" && pwd -P)
export DOTFILES_TEST_BASH="$BASH"
exec python3 -B "$test_dir/bootstrap/cases.py" "$@"
