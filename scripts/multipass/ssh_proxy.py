#!/usr/bin/env python3
"""为单次 SSH 连接查询受管 Multipass 实例的地址。"""

import ipaddress
import json
import os
import subprocess
import sys


def address(payload, name):
    entry = payload.get("info", {}).get(name)
    if isinstance(entry, list):
        entry = entry[0] if len(entry) == 1 else None
    if not isinstance(entry, dict):
        raise ValueError(f"{name} is missing; inspect the saved instance identity before recovery")
    if entry.get("state") != "Running":
        state = entry.get("state", "unknown")
        hint = (f"use multipass start {name}" if state in ("Stopped", "Suspended") else
                "inspect Multipass state and daemon logs; restore the management connection first")
        raise ValueError(f"{name} is not running (state={state}); {hint}")
    candidates = entry.get("ipv4", [])
    if isinstance(candidates, str):
        candidates = [candidates]
    valid = []
    for candidate in candidates:
        ip = ipaddress.ip_address(candidate)
        if ip.version == 4 and not ip.is_loopback and not ip.is_link_local:
            valid.append(str(ip))
    # 地址缺失或有多个候选时停止连接，避免将 SSH 流量导向错误的实例。
    if len(valid) != 1:
        raise ValueError(f"{name} must have exactly one usable default-network IPv4 address")
    return valid[0]


def main():
    if len(sys.argv) != 3:
        raise ValueError("usage: ssh_proxy.py INSTANCE MULTIPASS_PATH")
    name, multipass = sys.argv[1:]
    result = subprocess.run([multipass, "info", name, "--format", "json"],
                            capture_output=True, text=True, timeout=15, check=True)
    ip = address(json.loads(result.stdout), name)
    os.execv("/usr/bin/nc", ["/usr/bin/nc", ip, "22"])


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(f"dotfiles-multipass proxy: {exc}", file=sys.stderr)
        raise SystemExit(1)
