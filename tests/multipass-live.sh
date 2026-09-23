#!/usr/bin/env bash

# 显式在线验收；不由 tests/all.sh 调用。
set -e
unset CDPATH
script_parent="${BASH_SOURCE[0]%/*}"
[[ $script_parent != "${BASH_SOURCE[0]}" ]] || script_parent=.
test_dir=$(cd -- "$script_parent" && pwd -P)
exec python3 -B "$test_dir/multipass/live.py" "$@"
