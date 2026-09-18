"""测试私有设施：文件快照、自有进程管理，以及有超时和输出上限的伪终端会话。

Bash 配置加载套件也通过本文件运行应用会话。
测试执行器独立管理进程，不导入生产脚本的运行器。
"""

from contextlib import contextmanager
import errno
import fcntl
import hashlib
import os
from pathlib import Path
import pty
import select
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import termios
import time


REPO = Path(__file__).resolve().parents[2]
PS = shutil.which('ps')
_active = []
_deferred = 0
_interrupted = 0
_cleaning = False


def snapshot(root):
    """生成供调用方比较的快照，记录类型、权限、链接目标、内容摘要和修改时间。

    忽略访问时间，因为读取文件本身可能改变它。
    """
    result = {}
    for path in sorted(root.rglob('*')):
        metadata = path.lstat()
        content = None
        if path.is_symlink():
            content = os.readlink(path)
        elif path.is_file():
            content = hashlib.sha256(path.read_bytes()).hexdigest()
        result[str(path.relative_to(root))] = (metadata.st_mode, metadata.st_mtime_ns, content)
    return result


def log_result(result):
    # unittest 用例会缓冲这些记录，并在后续断言失败时显示。
    print('Command:', shlex.join(map(str, result.args)), 'exit:', result.returncode)
    for name in ('stdout', 'stderr'):
        output = getattr(result, name, None)
        if output:
            if isinstance(output, bytes):
                output = output.decode(errors='replace')
            print(name + ':\n' + output[-8000:])


def _interrupt(signum, _frame):
    global _interrupted
    if not _interrupted:
        _interrupted = signum
    if not _deferred and not _cleaning:
        raise KeyboardInterrupt


@contextmanager
def defer_interrupts():
    global _deferred
    _deferred += 1
    try:
        yield
    finally:
        _deferred -= 1
        if not _deferred and _interrupted and not _cleaning:
            raise KeyboardInterrupt


@contextmanager
def cleanup_on_exit():
    """让可捕获信号走统一退出流程，确保 unittest 和伪终端资源得到清理。"""
    global _interrupted, _cleaning
    previous = {sig: signal.signal(sig, _interrupt)
                for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)}
    try:
        yield
    finally:
        _cleaning = True
        try:
            for process in list(reversed(_active)):
                process.close()
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
            signum, _interrupted = _interrupted, 0
            _cleaning = False
        if signum:
            raise SystemExit(128 + signum)


def process_table():
    result = subprocess.run([PS, '-axo', 'pid=,ppid=,pgid=,stat='],
                            capture_output=True, text=True, check=True, timeout=5, start_new_session=True)
    return {int(pid): (int(parent), int(group), state)
            for pid, parent, group, state in (line.split() for line in result.stdout.splitlines())}


def running(pid):
    row = process_table().get(pid)
    return row is not None and not row[2].startswith('Z')


class Process:
    """管理命令会话、已观察到的后代会话和可选的伪终端。

    等待时轮询，记录自行建立新会话的后代；发信号前再次检查。
    删除夹具前先停止并回收进程。
    管理范围限于测试子进程，不包括任意脱离进程树的守护程序。
    """

    def __init__(self, args, *, terminal=False, **kwargs):
        self.child = None
        self.master = self.slave = None
        self.sessions = set()
        self.members = set()
        self.closed = False
        try:
            with defer_interrupts():
                if terminal:
                    self.master, self.slave = pty.openpty()
                    fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 120, 0, 0))

                    def attach():
                        os.setsid()
                        fcntl.ioctl(self.slave, termios.TIOCSCTTY, 0)

                    kwargs.update(stdin=self.slave, stdout=self.slave, stderr=self.slave, preexec_fn=attach)
                else:
                    kwargs.setdefault('stdin', subprocess.DEVNULL)
                    kwargs['start_new_session'] = True
                self.child = subprocess.Popen(args, **kwargs)
                self.sessions.add(self.child.pid)
                self.members.add(self.child.pid)
                _active.append(self)
                if self.slave is not None:
                    os.close(self.slave)
                    self.slave = None
        except BaseException:
            self.close()
            raise

    def __getattr__(self, name):
        return getattr(self.child, name)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def groups(self):
        rows = process_table()
        owned = self.members.intersection(rows)
        for pid in rows:
            try:
                if os.getsid(pid) in self.sessions:
                    owned.add(pid)
            except (ProcessLookupError, PermissionError):
                pass
        while True:
            descendants = {pid for pid, (parent, _, _) in rows.items() if parent in owned}
            if descendants <= owned:
                break
            owned.update(descendants)
        self.members = owned
        for pid in owned:
            try:
                self.sessions.add(os.getsid(pid))
            except (ProcessLookupError, PermissionError):
                pass
        return {rows[pid][1] for pid in owned if not rows[pid][2].startswith('Z')}

    def send_signal(self, signum):
        self.groups()
        self.child.send_signal(signum)

    def wait(self, timeout=None):
        deadline = time.monotonic() + timeout if timeout is not None else float('inf')
        while True:
            self.groups()
            try:
                return self.child.wait(timeout=max(0, min(.1, deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(self.args, timeout) from None

    def communicate(self, timeout=None):
        deadline = time.monotonic() + timeout if timeout is not None else float('inf')
        while True:
            self.groups()
            try:
                return self.child.communicate(timeout=max(0, min(.1, deadline - time.monotonic())))
            except subprocess.TimeoutExpired as error:
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(self.args, timeout, error.output, error.stderr) from None

    def close(self):
        if self.closed:
            return
        with defer_interrupts():
            try:
                if self.child is not None:
                    # 向所有自有进程组发送 TERM，包括组长进程已经退出的组。
                    groups = self.groups()
                    if self.child.poll() is None:
                        self.child.send_signal(signal.SIGTERM)
                    deadline = time.monotonic() + 1
                    while groups and time.monotonic() < deadline:
                        for group in groups:
                            try:
                                os.killpg(group, signal.SIGTERM)
                            except ProcessLookupError:
                                pass
                        time.sleep(.02)
                        groups = self.groups()
                    for group in groups:
                        try:
                            os.killpg(group, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    self.child.wait(timeout=5)
                    deadline = time.monotonic() + 2
                    while self.groups():
                        if time.monotonic() >= deadline:
                            raise RuntimeError('test command left running descendants: ' + shlex.join(self.args))
                        time.sleep(.02)
            finally:
                if self.child is not None:
                    for stream in (self.child.stdin, self.child.stdout, self.child.stderr):
                        if stream:
                            stream.close()
                for name in ('slave', 'master'):
                    descriptor = getattr(self, name)
                    if descriptor is not None:
                        os.close(descriptor)
                        setattr(self, name, None)
            self.closed = True
            if self in _active:
                _active.remove(self)


def run(args, *, timeout=30, check=False, **kwargs):
    if kwargs.pop('capture_output', False):
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    kwargs.setdefault('stdout', subprocess.PIPE)
    kwargs.setdefault('stderr', subprocess.PIPE)
    kwargs.setdefault('text', True)
    try:
        with Process(args, **kwargs) as process:
            stdout, stderr = process.communicate(timeout=timeout)
            result = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired as error:
        log_result(subprocess.CompletedProcess(args, 'timeout', error.output, error.stderr))
        raise
    log_result(result)
    if check:
        result.check_returncode()
    return result


def terminal(args, *, timeout=30, env=None, cwd=None, observe=None):
    output = bytearray()
    try:
        with Process(args, terminal=True, env=env, cwd=cwd) as process:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                process.groups()
                if observe:
                    observe(process.master)
                if select.select([process.master], [], [], .05)[0]:
                    try:
                        block = os.read(process.master, 65536)
                    except OSError as error:
                        if error.errno != errno.EIO:
                            raise
                        break
                    if not block:
                        break
                    output.extend(block)
                    if len(output) > 1024 * 1024:
                        raise RuntimeError('test terminal output exceeded 1 MiB')
                if process.poll() is not None:
                    break
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                raise subprocess.TimeoutExpired(args, timeout) from None
            if process.returncode:
                raise subprocess.CalledProcessError(process.returncode, args)
    except BaseException:
        # 夹具清理后仍需保留诊断；repr 将终端转义序列显示为文本，避免其生效。
        print('Terminal output (tail):', repr(bytes(output[-8000:])), file=sys.stderr)
        raise
    return bytes(output).replace(b'\r\n', b'\n')


def main():
    # 供 Bash 套件调用；日常运行入口位于 tests/*.sh。
    mode, seconds, *args = sys.argv[1:]
    if mode == 'pty':
        sys.stdout.buffer.write(terminal(args, timeout=float(seconds)))
    elif mode == 'run':
        with Process(args) as process:
            status = process.wait(timeout=float(seconds))
        raise SystemExit(status if status >= 0 else 128 - status)
    else:
        raise ValueError('unknown test process mode: ' + mode)


if __name__ == '__main__':
    with cleanup_on_exit():
        main()
