#!/usr/bin/env bash

# Multipass 开发机入口；Bash 3.2+，实际编排由 Python 标准库完成。
set -e
unset CDPATH
script_parent="${BASH_SOURCE[0]%/*}"
[[ $script_parent != "${BASH_SOURCE[0]}" ]] || script_parent=.
script_dir=$(cd -- "$script_parent" && pwd -P)
if ! command -v python3 > /dev/null 2>&1 || ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
	printf 'error: multipass.sh requires Python 3.9 or newer\n' >&2
	exit 1
fi
exec python3 -B "$script_dir/multipass/runtime.py" "$@"
