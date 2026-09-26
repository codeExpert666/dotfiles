"""Offline sudo substitute and driver for real controlling-terminal regression tests."""

import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import termios
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/multipass"))
from host import Host
from runtime import CommandFailure, Runner


def sudo(root, mode, argv):
    with (root / "calls").open("a") as calls:
        calls.write(json.dumps(argv) + "\n")
    try:
        descriptor = os.open("/dev/tty", os.O_RDWR)
    except OSError:
        print("sudo fixture: a terminal is required", file=sys.stderr)
        return 1
    with os.fdopen(descriptor, "r+b", buffering=0) as terminal:
        identity = {"session": os.getsid(0), "tty": os.ttyname(terminal.fileno())}
        if argv == ["-v"]:
            if mode == "auth-failure":
                print("sudo fixture: authentication rejected", file=sys.stderr)
                return 17
            original = termios.tcgetattr(terminal)
            hidden = termios.tcgetattr(terminal)
            hidden[3] &= ~termios.ECHO
            try:
                termios.tcsetattr(terminal, termios.TCSANOW, hidden)
                terminal.write(b"Password: ")
                terminal.flush()
                (root / "ready").touch()
                password = terminal.readline().rstrip(b"\n")
            finally:
                termios.tcsetattr(terminal, termios.TCSANOW, original)
            if password != b"fixture-password":
                return 18
            (root / "authorization.json").write_text(json.dumps(identity))
            print("sudo fixture: authorized")
            return 0
        expected = ["-n", "installer", "-pkg", str(root / "multipass-1.0.0.pkg"), "-target", "/"]
        if argv != expected or json.loads((root / "authorization.json").read_text()) != identity:
            print("sudo fixture: installer cannot reuse authorization", file=sys.stderr)
            return 19
        (root / "installer-started").touch()
        print("installer fixture: package output")
        if mode == "installer-failure":
            print("installer fixture: installation failed", file=sys.stderr)
            return 23
        return 0


def drive(root, mode):
    runner = Runner()
    runner.log = root / "host.log"
    outcome = {"code": 0}
    try:
        if mode in ("timeout", "interrupt"):
            runner([sys.executable, "-B", __file__, "wait", str(root), mode],
                   timeout=.5 if mode == "timeout" else 120, stream=True, interactive=True)
        else:
            package = root / "multipass-1.0.0.pkg"
            package.write_bytes(b"offline package fixture")
            releases = {"minimum": "1.0.0", "macos_pkg": {
                "version": "1.0.0", "sha256": hashlib.sha256(package.read_bytes()).hexdigest()}}
            Host(runner, releases, root).install_pkg()
    except CommandFailure as exc:
        outcome.update(code=exc.returncode, error=str(exc))
    except KeyboardInterrupt:
        outcome["code"] = 130
    if mode in ("timeout", "interrupt"):
        pid = int((root / "child-pid").read_text())
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            outcome["child_running"] = False
        else:
            outcome["child_running"] = True
        # The caller must survive cleanup and retain its controlling terminal.
        descriptor = os.open("/dev/tty", os.O_RDWR)
        os.close(descriptor)
        outcome["followup"] = runner([sys.executable, "-c", "print('still running')"]).stdout.strip()
    (root / "result.json").write_text(json.dumps(outcome))


def main():
    action, directory, mode, *argv = sys.argv[1:]
    root = Path(directory)
    if action == "sudo":
        return sudo(root, mode, argv)
    if action == "wait":
        # Force the runner to clean up even when Ctrl-C also reaches the child.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        (root / "child-pid").write_text(str(os.getpid()))
        print("command fixture: waiting", flush=True)
        (root / "ready").touch()
        time.sleep(60)
    else:
        drive(root, mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
