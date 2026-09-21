"""用真实 PTY 模拟 Lazygit 编辑器交接时丢失退出键，或忽略渲染器失败。"""

import os
from pathlib import Path
import select
import subprocess
import sys
import termios
import time
import tty


def read_keys(expected):
    received = bytearray()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if select.select([sys.stdin], [], [], .05)[0]:
            received.extend(os.read(sys.stdin.fileno(), len(expected) - len(received)))
            if bytes(received) == expected:
                return
            if not expected.startswith(received):
                break
    raise RuntimeError("expected terminal input: " + repr(expected) + "; received: " + repr(bytes(received)))


def main():
    mode, *args = sys.argv[1:]
    if args == ["--print-config-dir"]:
        print(Path.home() / ".config/lazygit")
        return
    original = termios.tcgetattr(sys.stdin)
    try:
        tty.setraw(sys.stdin)
        # 故意忽略渲染器退出码，验证 doctor 能独立识别失败，不能只信 TUI 的退出码。
        subprocess.run(["delta", "--dark", "--paging=never"], input=b"+sample\n",
                       stdout=subprocess.DEVNULL, check=False, timeout=5)
        read_keys(b"2e")
        subprocess.run(["nvim", "sample.txt"], check=True, timeout=5)
        read_keys(b"q")
        if mode == "lost-quit":
            # 第一次 q 在编辑器交接期间丢失；继续等待恢复界面之后的退出键。
            read_keys(b"q")
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSANOW, original)


if __name__ == "__main__":
    main()
