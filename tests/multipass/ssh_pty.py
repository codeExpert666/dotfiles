"""Controlled SSH/Zsh editing probe; runs system ssh, bypassing Ghostty upload."""

from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import re
import select
import shlex
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from harness import Process


PROMPT = "DFPTY> "
INPUT = "printf '<PTY:%s>\\n' abcX"


@contextmanager
def session(argv, env):
    with Process(argv, terminal=True, env=env) as process:
        try:
            yield process
        finally:
            # SSH can defer SIGTERM while its remote PTY is still open. Reap our
            # client before the harness cleans its proxy/descendant processes.
            if process.poll() is None:
                process.kill()
                process.child.wait(timeout=5)


def screen_line(data):
    """Replay the ASCII probe's horizontal edits, rejecting unknown screen motion.

    This is deliberately limited to a single unwrapped line. Startup output is
    discarded at the explicit clear-screen marker before measuring ZLE edits.
    """
    data = data.rsplit(b"\x1b[2J\x1b[H", 1)[-1].decode("utf-8", errors="replace")
    data = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", data)
    line, column, index = [], 0, 0
    while index < len(data):
        char = data[index]
        if char == "\x1b":
            if data[index:index + 2] in ("\x1b=", "\x1b>"):
                index += 2
                continue
            if data[index:index + 3] in ("\x1b(B", "\x1b(0"):
                index += 3
                continue
            match = re.match(r"\x1b\[([?0-9;:]*)([ -/]*)([@-~])", data[index:])
            if not match:
                raise ValueError(f"unsupported or incomplete terminal escape: {data[index:index+30]!r}")
            arguments, intermediate, action = match.groups()
            count = int(arguments or "1") if arguments.isdigit() or not arguments else 1
            if action in ("m", "h", "l") or (action == "q" and intermediate == " "):
                pass  # Styling, bracketed paste, cursor visibility/style.
            elif action == "D":
                column = max(0, column - count)
            elif action == "C":
                column += count
            elif action == "G":
                column = max(0, count - 1)
            elif action in ("J", "K"):
                if arguments in ("", "0"):
                    del line[column:]
                elif arguments == "2":
                    line = []
                else:
                    raise ValueError("unsupported erase direction")
            elif action == "P":
                del line[column:column + count]
            elif action == "@":
                line[column:column] = [" "] * count
            elif action == "X":
                line[column:column + count] = [" "] * min(count, max(0, len(line) - column))
            else:
                raise ValueError(f"unsupported screen motion: {match.group()!r}")
            index += len(match.group())
            continue
        if char == "\r":
            column = 0
        elif char == "\n":
            line, column = [], 0
        elif char == "\b":
            column = max(0, column - 1)
        elif char == "\x07":
            pass
        elif char >= " " and char != "\x7f":
            while len(line) <= column:
                line.append(" ")
            line[column] = char
            column += 1
        else:
            raise ValueError(f"unsupported control byte: {ord(char)}")
        index += 1
    return "".join(line).rstrip()


def probe(argv, report, *, env=None):
    report.mkdir(parents=True, exist_ok=True)
    output = bytearray()
    evidence = {"command": argv, "input": INPUT, "erase": "DEL (0x7f)"}
    try:
        with session(argv, env) as process:
            def drain(seconds=8, until=None):
                deadline, quiet = time.monotonic() + seconds, time.monotonic()
                while time.monotonic() < deadline:
                    if select.select([process.master], [], [], .05)[0]:
                        try:
                            block = os.read(process.master, 65536)
                        except OSError as exc:
                            if exc.errno != errno.EIO:
                                raise
                            block = b""
                        if not block:
                            raise RuntimeError("PTY closed before verification completed")
                        output.extend(block)
                        if len(output) > 1024 * 1024:
                            raise RuntimeError("PTY output exceeded 1 MiB")
                        quiet = time.monotonic()
                    elif time.monotonic() - quiet > .3 and (until is None or until in output):
                        return
                raise RuntimeError("PTY output did not reach the expected settled state")

            # Keep the real Zsh startup/plugins/key bindings, changing only the
            # prompt and this probe's persistent shell history destination.
            drain(seconds=20, until=b"\x1b[?2004h")
            os.write(process.master, b"unset HISTFILE; PROMPT='DFPTY> '; RPROMPT=''; "
                     b"printf '\\033[2J\\033[H'\n")
            drain(until=b"\x1b[2J\x1b[H")
            baseline = screen_line(bytes(output))
            if baseline != PROMPT.rstrip():
                raise RuntimeError(f"unexpected probe prompt: {baseline!r}")
            for char in INPUT.encode():
                os.write(process.master, bytes([char]))
                drain()
            evidence["after_input"] = screen_line(bytes(output))
            os.write(process.master, b"\x7f")
            drain()
            evidence["after_backspace"] = screen_line(bytes(output))
            boundary = len(output)
            os.write(process.master, b"\n")
            drain()
            evidence["executed_expected_argument"] = bool(re.search(
                rb"<PTY:abc>\r*\n", bytes(output[boundary:])))
            evidence["input_display_ok"] = evidence["after_input"] == PROMPT + INPUT
            evidence["backspace_display_ok"] = evidence["after_backspace"] == PROMPT + INPUT[:-1]
            os.write(process.master, b"exit\n")
            # Continue reading while SSH drains its final output and closes.
            deadline = time.monotonic() + 10
            while process.poll() is None and time.monotonic() < deadline:
                if select.select([process.master], [], [], .05)[0]:
                    try:
                        output.extend(os.read(process.master, 65536))
                    except OSError as exc:
                        if exc.errno != errno.EIO:
                            raise
            if process.poll() is None:
                process.kill()
                process.child.wait(timeout=5)
                raise RuntimeError("SSH did not exit after the probe")
            evidence["exit_ok"] = process.returncode == 0
            if not all(evidence[key] for key in ("input_display_ok", "backspace_display_ok",
                                                  "executed_expected_argument", "exit_ok")):
                raise RuntimeError(f"SSH PTY editing assertions failed: {evidence}")
    except BaseException as exc:
        evidence["error"] = str(exc)
        raise
    finally:
        (report / "ssh-pty.raw").write_bytes(output)
        (report / "ssh-pty.json").write_text(json.dumps(evidence, indent=2) + "\n")


def verify(name, report, term="xterm-ghostty"):
    # No shell function, RemoteCommand or multiplexed session can upload data.
    probe(["/usr/bin/ssh", "-tt", "-o", "BatchMode=yes", "-o", "ControlPath=none",
           "-o", "RemoteCommand=none", name,
           "test \"$(id -un)\" = ubuntu && test \"$HOME\" = /home/ubuntu && "
           f"exec env -u TERMINFO -u TERMINFO_DIRS TERM={shlex.quote(term)} zsh -i"], report)
