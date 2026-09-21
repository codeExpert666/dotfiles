#!/usr/bin/env python3
"""在原任务进程组内执行非交互命令，解除控制终端关联。"""

import errno
import fcntl
import os
import sys
import termios


def main(args):
    if not args:
        raise ValueError('missing command')
    try:
        terminal = os.open('/dev/tty', os.O_RDWR | os.O_NOCTTY)
    except OSError as error:
        if error.errno != errno.ENXIO:  # 无控制终端时直接执行。
            raise
    else:
        try:
            # 入口应由 execute 的后台任务调用。会话首进程执行 TIOCNOTTY
            # 会断开整个会话的终端，因此拒绝在此上下文中使用。
            if os.getsid(0) == os.getpid():
                raise RuntimeError('refusing to detach a terminal session leader')
            # Antidote 子 Zsh 的 read -d 即使读取 here-doc 也会操作 /dev/tty，
            # 导致后台任务组收到 SIGTTOU 并暂停。仅重定向 stdin 不能避免。
            # 不使用 setsid：保留原进程组，让 execute 继续负责超时与中断清理。
            fcntl.ioctl(terminal, termios.TIOCNOTTY)
        finally:
            os.close(terminal)
    os.execvp(args[0], args)


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except (OSError, RuntimeError, ValueError) as error:
        print(f'FAIL: could not start non-interactive command: {error}', file=sys.stderr)
        sys.exit(1)
