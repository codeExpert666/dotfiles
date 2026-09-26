#!/usr/bin/env python3
"""创建、重新配置、销毁、检查并连接按指定 Git 提交构建的 Multipass 开发机。"""

import argparse
from collections import deque
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
from urllib.parse import urlsplit
import uuid

from host import Host, size_bytes
from errors import CommandFailure, Failure
from ssh_config import SSHConfig, atomic


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MULTIPASS_DIR = ROOT / "multipass"
DEFAULTS = json.loads((MULTIPASS_DIR / "defaults.json").read_text())
RELEASES = json.loads((MULTIPASS_DIR / "host-releases.json").read_text())
TEMPLATE = MULTIPASS_DIR / "cloud-init.yaml.tmpl"
REF = re.compile(r"^[0-9a-f]{40}$")
NAME = re.compile(r"^[a-z][a-z0-9-]{0,61}[a-z0-9]$")
SIZE = re.compile(r"^([1-9][0-9]*)([GM])$")
KEY = re.compile(r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-[\w-]+) ([A-Za-z0-9+/]+={0,2})(?: .*)?$")
STAGES = ("host", "launch", "cloud-init", "guest", "ssh", "repository", "bootstrap-preview",
          "bootstrap", "finalize", "verified")
STAGE_DESCRIPTIONS = {
    "host": "Prepare and verify host Multipass",
    "launch": "Create and start the Ubuntu instance",
    "cloud-init": "Verify first boot and complete any required reboot",
    "guest": "Verify guest identity and package readiness",
    "ssh": "Publish and verify ordinary SSH access",
    "repository": "Prepare the pinned guest repository",
    "bootstrap-preview": "Preview the guest server bootstrap",
    "bootstrap": "Apply the guest server bootstrap",
    "finalize": "Apply guest account settings",
    "verified": "Verify the configured development machine",
    "destroy": "Verify ownership and retire the managed instance",
}
PROGRESS_INTERVAL = 30
CONNECT_RETRY_SECONDS = 120
CONNECT_RETRY_INTERVAL = 5
REBOOT_PHASES = ("planned", "stop_requested", "stopped", "start_requested", "running", "verified")
SERVER_MODULES = ("environment", "deployment", "dependencies", "zsh", "git", "lazygit",
                  "nvim", "starship", "atuin", "shuck", "vim", "state")


def clean_output(text):
    """保留终端覆盖前后的正文，移除装饰性 CSI/OSC 与其他控制符。"""
    text = re.sub(r"\x1b\].*?(?:\x07|\x1b\\)", "", text, flags=re.DOTALL)
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_]", "", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\b", "\n")
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)


def brief(value, limit=400):
    return " ".join(clean_output(str(value)).split())[:limit]


class Runner:
    def __init__(self):
        self.log = None
        self.active = None
        self.active_interactive = False
        self.on_wait = None
        self.output_mode = "native"
        self.last_log = None

    def __call__(self, argv, *, timeout=15, check=True, stream=False, heartbeat=None,
                 on_wait=None, show_output=True, interactive=False):
        argv = [str(part) for part in argv]
        if heartbeat is None and timeout >= 60:
            heartbeat = PROGRESS_INTERVAL
        on_wait = on_wait or self.on_wait
        failure_events = deque(maxlen=3)
        if stream or heartbeat or interactive:
            # sudo 从 /dev/tty 认证；保留调用方会话和前台进程组，输出仍写入日志。
            process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, bufsize=1, start_new_session=not interactive,
                                       env={**os.environ, "NO_COLOR": "1", "CLICOLOR": "0",
                                            "HOMEBREW_NO_COLOR": "1"})
            self.active_interactive = interactive
            self.active = process
            output = deque(maxlen=200) if stream else []
            last_visible = [time.monotonic()]
            event_driven = [False]
            forwarding_errors = []

            def forward():
                log = None
                try:
                    if self.log:
                        log = open(self.log, "a")
                except OSError as exc:
                    forwarding_errors.append(exc)
                try:
                    with process.stdout:
                        for raw in process.stdout:
                            if not stream:
                                output.append(raw)
                            for line in clean_output(raw).splitlines():
                                visible = stream and show_output
                                if line.startswith("@@DOTFILES/1 DETAIL "):
                                    line = line[len("@@DOTFILES/1 DETAIL "):]
                                    visible = False
                                elif line.startswith("@@DOTFILES/1 EVENT "):
                                    line = line[len("@@DOTFILES/1 EVENT "):]
                                    event_driven[0] = True
                                    if line.startswith(("FAIL:", "FAIL ", "FAILED phase:")):
                                        failure_events.append(line)
                                elif self.output_mode == "quiet":
                                    visible = bool(re.match(r"(?:Warning:|WARN[: ]|Error:|FAIL[: ])", line))
                                if stream:
                                    output.append(line + "\n")
                                if log:
                                    try:
                                        log.write(line + "\n")
                                        log.flush()
                                    except OSError as exc:
                                        forwarding_errors.append(exc)
                                        try:
                                            log.close()
                                        except OSError as close_error:
                                            forwarding_errors.append(close_error)
                                        log = None
                                if visible:
                                    try:
                                        print(line[:800], file=sys.stderr, flush=True)
                                        last_visible[0] = time.monotonic()
                                    except OSError as exc:
                                        forwarding_errors.append(exc)
                except Exception as exc:
                    forwarding_errors.append(exc)
                finally:
                    if log:
                        try:
                            log.close()
                        except OSError as exc:
                            forwarding_errors.append(exc)

            worker = threading.Thread(target=forward, daemon=True)
            worker.start()
            started = time.monotonic()
            primary = None
            try:
                while True:
                    remaining = timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(argv, timeout)
                    # 给已采用事件协议的客户机心跳留少量传输余量，避免在同一秒双重提醒。
                    quiet_limit = heartbeat + min(2, heartbeat / 10) if heartbeat and event_driven[0] else heartbeat
                    wait_for = max(.01, quiet_limit - (time.monotonic() - last_visible[0])) if quiet_limit and on_wait else remaining
                    try:
                        code = process.wait(timeout=min(remaining, wait_for))
                        break
                    except subprocess.TimeoutExpired:
                        if (heartbeat and on_wait and process.poll() is None and
                                time.monotonic() - last_visible[0] >= quiet_limit):
                            on_wait(time.monotonic() - started, timeout)
                            last_visible[0] = time.monotonic()
            except subprocess.TimeoutExpired:
                primary = CommandFailure(argv, 124, "command timed out")
            except BaseException as exc:
                primary = exc
            finally:
                # 停止命令后再排空日志，包括 EOF 前没有换行的正文。
                self.stop()
                worker.join(timeout=5)
                self.active = None
                self.active_interactive = False
            if worker.is_alive():
                forwarding_errors.append(RuntimeError("forwarder did not finish"))
            if forwarding_errors:
                message = f"command output could not be saved/forwarded to {self.log}: {forwarding_errors[0]}"
                try:
                    print("FAIL logging: " + brief(message), file=sys.stderr)
                except OSError:
                    pass
                if primary is None and code == 0:
                    primary = Failure(message)
            if primary:
                raise primary
            result = subprocess.CompletedProcess(argv, code, "".join(output), "")
        else:
            try:
                result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                try:
                    for data in (exc.stdout, exc.stderr):
                        if data:
                            self.save_output(data.decode(errors="replace") if isinstance(data, bytes) else data)
                except OSError as log_error:
                    print(f"FAIL logging: {brief(log_error)}", file=sys.stderr)
                raise CommandFailure(argv, 124, "command timed out") from None
            try:
                self.save_output(result.stdout)
                self.save_output(result.stderr)
            except OSError:
                if not result.returncode:
                    raise
                print("FAIL logging: command diagnostics could not be saved", file=sys.stderr)
        if check and result.returncode:
            raise CommandFailure(argv, result.returncode, result.stdout + result.stderr,
                                 summary="\n".join(failure_events) if failure_events else None)
        return result

    def save_output(self, text):
        if self.log and text:
            with open(self.log, "a") as log:
                log.write(clean_output(text).rstrip("\n") + "\n")

    def stop(self):
        if self.active:
            def send(signum):
                if self.active_interactive:
                    # 前台 sudo 负责向安装器转交信号；不能终止调用方共享的进程组。
                    self.active.send_signal(signum)
                else:
                    os.killpg(self.active.pid, signum)

            try:
                send(signal.SIGTERM)
                self.active.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass
            finally:
                try:
                    send(signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.active.wait()


def parser():
    def formatter(prog):
        return argparse.HelpFormatter(prog, max_help_position=32)

    root = argparse.ArgumentParser(
        prog="multipass.sh",
        description="Create, reprovision, retire, inspect and connect to a pinned Multipass dev machine.",
        formatter_class=formatter,
        epilog="Run 'bash scripts/multipass.sh <command> --help' for command options.")
    commands = root.add_subparsers(dest="action", required=True)
    descriptions = {
        "create": "create or resume a pinned Ubuntu instance",
        "provision": "reconfigure a managed instance, optionally at a new Git commit",
        "destroy": "permanently remove a managed instance and retire its host state",
        "check": "inspect a managed instance and optionally run guest diagnostics",
        "ssh": "connect to a running managed instance over SSH",
    }
    declaration_notes = {
        "create": ("For a new instance, CLI values override the local config, then defaults.json. "
                   "On a retry, the commit, image, CPU, memory, disk and repository URL "
                   "must match the saved declaration."),
        "provision": ("Image, CPU, memory, disk and repository URL must match the saved declaration. "
                      "Use --ref to select a new commit."),
    }
    for action, description in descriptions.items():
        sub = commands.add_parser(
            action, help=description, description=description, formatter_class=formatter,
            epilog=declaration_notes.get(action))
        sub.add_argument("--name", help=f"Multipass instance name (config or {DEFAULTS['name']} by default)")
        sub.add_argument("--config", type=Path, metavar="FILE",
                         help="local JSON config (default: ~/.config/dotfiles-multipass/config.json)")
        if action in ("create", "provision", "destroy"):
            modes = sub.add_mutually_exclusive_group()
            modes.add_argument("--dry-run", action="store_true",
                               help="preview only (the default when --apply is absent)")
            modes.add_argument("--apply", action="store_true",
                               help=("permanently remove the managed VM and retire its host state"
                                     if action == "destroy" else
                                     "perform changes; may install or upgrade Multipass"))
        if action in ("create", "provision"):
            ref_help = ("target Git commit: 40 lowercase hex digits (required here or in config)"
                        if action == "create" else
                        "target Git commit: 40 lowercase hex digits (default: saved commit)")
            sub.add_argument("--ref", metavar="SHA", help=ref_help)
            sub.add_argument("--ssh-public-key", type=Path, metavar="FILE",
                             help="existing absolute public key file; matching key must be in ssh-agent "
                                  "(fingerprint fixed after creation)")
            sub.add_argument("--image", metavar="VERSION", help="Ubuntu release: 24.04 or 26.04")
            sub.add_argument("--cpus", metavar="COUNT", help="CPU count: 1-64")
            sub.add_argument("--memory", metavar="SIZE", help="RAM: integer M or G units, at least 2G")
            sub.add_argument("--disk", metavar="SIZE", help="disk: integer M or G units, at least 20G")
            sub.add_argument("--repo-url", metavar="URL",
                             help="public HTTPS Git repository URL without credentials")
            sub.add_argument("--git-name", metavar="NAME",
                             help="guest Git user.name (requires --git-email; overrides config as a pair)")
            sub.add_argument("--git-email", metavar="EMAIL",
                             help="guest Git user.email (requires --git-name; overrides config as a pair)")
        if action == "check":
            sub.add_argument("--runtime", action="store_true",
                             help="run guest doctor diagnostics in addition to instance checks")
        if action == "create":
            sub.add_argument("--creation-record", type=Path, metavar="FILE",
                             help="on --apply for a fresh instance, write name/UUID JSON before launch "
                                  "(new absolute file in an existing directory)")
    return root


def require_default_layout(home):
    if not home.is_absolute() or not home.is_dir() or home.is_symlink():
        raise Failure("HOME must be an existing absolute real directory")
    for key, relative in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"),
                          ("XDG_STATE_HOME", ".local/state"), ("XDG_CACHE_HOME", ".cache")):
        current = os.environ.get(key)
        if current and Path(current) != home / relative:
            raise Failure(f"{key} must use the default {home / relative}")


def host_preflight():
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise Failure("this entrypoint requires macOS on Apple Silicon")
    if int(platform.mac_ver()[0].split(".")[0]) < 15:
        raise Failure("macOS 15 or later is required by this repository")
    if os.geteuid() == 0:
        raise Failure("run as a normal user, not root")
    for name in ("git", "curl", "ssh", "ssh-keygen", "ssh-add", "pkgutil", "sudo"):
        if not shutil.which(name):
            raise Failure(f"required host command is missing: {name}")
    if not Path("/usr/bin/nc").is_file():
        raise Failure("the macOS /usr/bin/nc command is required for SSH ProxyCommand")


def read_config(path):
    if path.is_symlink():
        raise Failure(f"local config must not be a symlink: {path}")
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (ValueError, OSError) as exc:
        raise Failure(f"invalid local config {path}: {exc}") from exc
    allowed = {"name", "image", "cpus", "memory", "disk", "repo_url", "ref", "ssh_public_key",
               "git_identity"}
    if not isinstance(data, dict) or set(data) - allowed:
        raise Failure("local config must be an object with documented keys only", 2)
    validate_git_identity(data.get("git_identity", {}), "git_identity")
    return data


def validate_git_identity(identity, label):
    if not isinstance(identity, dict) or set(identity) - {"name", "email"}:
        raise Failure(f"{label} must contain only name and email", 2)
    if identity and set(identity) != {"name", "email"}:
        raise Failure(f"{label} requires both name and email", 2)
    for field, value in identity.items():
        if not isinstance(value, str) or not value.strip() or any(
                ord(char) < 32 or ord(char) == 127 for char in value):
            raise Failure(f"{label}.{field} must be a nonempty string without control characters", 2)
    return identity


def resolve_git_identity(args, config):
    name = args.git_name
    email = args.git_email
    if (name is None) != (email is None):
        raise Failure("--git-name and --git-email must be supplied together", 2)
    if name is not None:
        return validate_git_identity({"name": name, "email": email}, "CLI Git identity"), "CLI"
    identity = validate_git_identity(config.get("git_identity", {}), "git_identity")
    return identity, "config" if identity else "none"


def value(args, config, name, fallback=None):
    cli = getattr(args, name, None)
    return cli if cli is not None else config.get(name, fallback)


def validate_name(name):
    if name == "primary" or not NAME.fullmatch(name) or "--" in name:
        raise Failure(f"invalid Multipass instance name: {name!r}", 2)


def validate_declaration(data):
    validate_name(data["name"])
    if data["image"] not in ("24.04", "26.04"):
        raise Failure("image must be 24.04 or 26.04", 2)
    try:
        cpus = int(data["cpus"])
    except (TypeError, ValueError):
        raise Failure("cpus must be a positive integer", 2)
    if str(cpus) != str(data["cpus"]) or cpus < 1 or cpus > 64:
        raise Failure("cpus must be an integer from 1 to 64", 2)
    data["cpus"] = cpus
    for field, minimum in (("memory", 2), ("disk", 20)):
        match = SIZE.fullmatch(str(data[field]))
        if not match or int(match.group(1)) * (1024 if match.group(2) == "G" else 1) < minimum * 1024:
            raise Failure(f"{field} must be at least {minimum}G and use M or G units", 2)
    parsed = urlsplit(data["repo_url"])
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or \
            parsed.query or parsed.fragment:
        raise Failure("repo-url must be a public HTTPS URL without embedded credentials", 2)
    if not REF.fullmatch(data["target_ref"] or ""):
        raise Failure("--ref must be a complete lowercase 40-character Git commit SHA", 2)


def public_key(path, run):
    if not path or not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise Failure("--ssh-public-key must name one existing absolute regular public-key file", 2)
    content = path.read_text().strip()
    if "\n" in content or not KEY.fullmatch(content):
        raise Failure("SSH public key must contain exactly one parseable public key", 2)
    canonical = " ".join(content.split()[:2])
    result = run(["ssh-keygen", "-lf", str(path), "-E", "sha256"], check=False)
    if result.returncode or "SHA256:" not in result.stdout:
        raise Failure("ssh-keygen could not parse the SSH public key", 2)
    fingerprint = next(part for part in result.stdout.split() if part.startswith("SHA256:"))
    return canonical, fingerprint


def require_agent(canonical, run):
    result = run(["ssh-add", "-L"], check=False)
    if result.returncode or canonical not in [" ".join(line.split()[:2]) for line in result.stdout.splitlines()]:
        raise Failure("dedicated SSH key is not loaded in ssh-agent; unlock it with ssh-add first")


def state_paths(home, name):
    base = home / ".local/state/dotfiles-multipass"
    return base, base / "instances" / name


def safe_directory(path, create=False):
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise Failure(f"expected a real state directory: {path}")
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)


def load_json(path):
    if path.is_symlink():
        raise Failure(f"refusing symlinked state file: {path}")
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except ValueError as exc:
        raise Failure(f"invalid state file {path}: {exc}") from exc


def save_json(path, data):
    atomic(path, (json.dumps(data, indent=2, sort_keys=True) + "\n").encode())


def record_creation(path, declaration):
    # 验收程序将此记录保存在实例状态目录之外；启动失败后仍凭它核对清理归属。
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump({"name": declaration["name"], "uuid": declaration["uuid"]}, output)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())


def parse_json_output(output, label):
    # Multipass 可能在 JSON 前输出启动进度；只接受位于输出末尾的完整对象。
    decoder = json.JSONDecoder()
    for index, char in enumerate(output):
        if char != "{":
            continue
        try:
            value, end = decoder.raw_decode(output[index:])
        except ValueError:
            continue
        if isinstance(value, dict) and not output[index + end:].strip():
            return value
    raise Failure(f"{label} did not return a complete JSON object: {output[-500:]}")


def transient_guest_connection(exc):
    # 仅将传输层暂时不可达视为可重试，来宾机命令本身失败不能被掩盖。
    if not isinstance(exc, CommandFailure) or exc.returncode != 2:
        return False
    output = exc.output.lower()
    if "ssh connection failed" not in output and "failed to connect:" not in output:
        return False
    return any(reason in output for reason in ("no route to host", "connection refused",
                                               "network is unreachable", "timed out",
                                               "timeout connecting to "))


def require_running(name, state):
    if state == "Running":
        return
    if state in ("Stopped", "Suspended"):
        hint = f"use multipass start {name}"
    elif state == "Deleted":
        hint = f"use multipass recover {name} before inspecting the managed instance"
    else:
        hint = ("inspect multipass list/info and daemon logs; restore the management connection "
                "before retrying; automatic start or restart is not attempted")
    raise Failure(f"instance {name} is {state or 'missing'}; {hint}")


def require_create_state(machine, action, state):
    if state == "Running":
        return
    if action == "create":
        operation = machine.reboot_operation()
        if operation and operation["phase"] != "verified":
            phase = operation["phase"]
            if state == "Stopped" and phase in ("stop_requested", "stopped", "start_requested"):
                return
            if state in ("Starting", "Restarting", "Unknown") and \
                    phase in ("stop_requested", "start_requested"):
                return
    require_running(machine.name, state)


class Deadline:
    """One monotonic budget shared by a lifecycle command and all its observations."""

    def __init__(self, seconds, label):
        self.end = time.monotonic() + seconds
        self.label = label

    def remaining(self, cap=None):
        left = self.end - time.monotonic()
        if left <= 0:
            raise Failure(f"{self.label} time budget exhausted")
        return min(left, cap) if cap is not None else left

    def child(self, seconds, label):
        child = Deadline(seconds, label)
        child.end = min(child.end, self.end)
        return child

class HeldLock:
    def __init__(self, path):
        self.path = path


@contextmanager
def lock(path):
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        owner = path / "pid"
        pid = owner.read_text().strip() if owner.is_file() else "unknown"
        raise Failure(f"another run or abandoned lock exists: {path} (PID {pid})")
    held = HeldLock(path)
    try:
        (path / "pid").write_text(f"{os.getpid()}\n")
        yield held
    finally:
        primary = sys.exc_info()[1]
        try:
            (held.path / "pid").unlink(missing_ok=True)
            held.path.rmdir()
        except OSError as exc:
            if primary is not None:
                raise primary from exc
            raise


@contextmanager
def preflight_progress(kind, label, description):
    indent = "  " if kind == "STEP" else ""
    print(f"{indent}{kind} RUN  [{label}] {description}", file=sys.stderr, flush=True)
    try:
        yield
    except Exception as exc:
        if not hasattr(exc, "progress_path"):
            exc.progress_path = label
        print(f"{indent}{kind} FAIL [{label}] {description}", file=sys.stderr, flush=True)
        raise
    else:
        print(f"{indent}{kind} OK   [{label}] {description}", file=sys.stderr, flush=True)


class Machine:
    def __init__(self, home, name, runner):
        self.home = home
        self.name = name
        self.runner = runner
        self.base, self.path = state_paths(home, name)
        self.declaration_file = self.path / "declaration.json"
        self.receipt_file = self.path / "receipt.json"
        self.declaration = load_json(self.declaration_file)
        self.receipt = load_json(self.receipt_file) or {"schema": 1, "stages": {}}
        self.host = Host(runner, RELEASES, home / ".cache/dotfiles-multipass",
                         progress=self.step, notice=self.emit)
        self.helper = None
        self.attempt_row = None
        self.current_stage = None
        self.current_step = None
        self.operation_log = None
        self.observation = None

    def reboot_operation(self):
        operation = self.receipt.get("reboot_operation")
        if operation is None:
            return None
        declaration = self.declaration or {}
        expected = {key: declaration.get(key) for key in ("name", "uuid", "image", "template_sha256")}
        digest = hashlib.sha256(json.dumps(declaration, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if not isinstance(operation, dict) or operation.get("method") != "stop-start" or \
                operation.get("phase") not in REBOOT_PHASES or operation.get("declaration") != expected or \
                (operation.get("phase") != "verified" and operation.get("declaration_sha256") != digest) or \
                not operation.get("from_boot_id") or operation["from_boot_id"] != self.receipt.get("reboot_from") or \
                not operation.get("machine_id") or operation["machine_id"] != self.receipt.get("machine_id") or \
                not operation.get("cloud_instance_id") or \
                operation["cloud_instance_id"] != self.receipt.get("cloud_instance_id") or \
                not isinstance(operation.get("events"), list) or \
                (operation.get("phase") == "verified" and
                 (not self.receipt.get("reboot_to") or self.receipt["reboot_to"] == operation["from_boot_id"])):
            raise Failure("saved stop/start reboot operation does not match the host declaration and "
                          "recorded guest identity; inspect state before retrying")
        return operation

    def pending_stop_start(self):
        operation = self.reboot_operation()
        return operation is not None and operation["phase"] != "verified"

    def reboot_event(self, kind, **details):
        operation = self.reboot_operation()
        operation["events"].append({"at": time.time(), "kind": kind, **details})
        save_json(self.receipt_file, self.receipt)
        summary = ", ".join(f"{key}={value}" for key, value in details.items() if key != "error")
        self.emit(f"  STEP RESULT [cloud-init/reboot] {kind}" + (f"; {summary}" if summary else ""))

    def reboot_phase(self, phase):
        operation = self.reboot_operation()
        operation["phase"] = phase
        operation["updated_at"] = time.time()
        save_json(self.receipt_file, self.receipt)
        self.emit(f"  STEP STATE [cloud-init/reboot] stop/start phase={phase}")

    def refuse_missing_guest(self, instances):
        # 已留下身份或成功阶段记录的实例若消失，禁止同名重建以保留 SSH 信任边界。
        recorded = (self.receipt.get("machine_id") or self.receipt.get("cloud_instance_id") or
                    self.receipt.get("ssh_files") or self.receipt.get("last_successful_ref") or
                    any(self.receipt["stages"].get(stage, {}).get("status") == "ok"
                        for stage in ("launch", "cloud-init")))
        if self.name not in instances and recorded:
            raise Failure(f"previously created instance {self.name} is missing; its identity and SSH trust "
                          f"are retained in {self.path}. Run destroy to retire it before reusing the name, "
                          "or use a different --name for a new instance; "
                          "automatic same-name rebuilding is not supported")

    @contextmanager
    def apply_lock(self, action):
        guard = lock(self.path / "lock")
        held = guard.__enter__()
        released = False

        def release():
            nonlocal released
            if released:
                return
            released = True
            primary = sys.exc_info()[0] is not None
            try:
                guard.__exit__(None, None, None)
            except Exception as exc:
                exc.progress_path = "cleanup/lock"
                self.record_error(exc, exc.progress_path)
                if not primary:
                    raise

        try:
            if load_json(self.declaration_file) != self.declaration or \
                    (load_json(self.receipt_file) or {"schema": 1, "stages": {}}) != self.receipt:
                raise Failure("instance state changed during preflight; rerun after inspecting the other run")
            with self.attempt(action, cleanup=release):
                yield held
        finally:
            release()

    @contextmanager
    def attempt(self, action, cleanup=None):
        row = {"id": f"{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:12]}",
               "action": action, "started": time.time(), "status": "running", "stages": []}
        previous_ref = self.receipt.get("last_successful_ref")
        self.receipt.setdefault("attempts", []).append(row)
        self.attempt_row = row
        log_dir = self.path / "logs" / row["id"]
        safe_directory(log_dir, create=True)
        self.operation_log = log_dir / "operation.log"
        self.runner.log = self.operation_log
        self.runner.last_log = self.operation_log
        row["log"] = str(self.operation_log)
        try:
            self.emit(f"RUN: {action} {self.name}; host logs: {log_dir}")
            save_json(self.receipt_file, self.receipt)
            yield
            # 持锁发布最终业务结果；释放锁失败会回写 failed，且不会显示总体成功。
            row.update(status="ok", ended=time.time())
            save_json(self.receipt_file, self.receipt)
            if cleanup:
                cleanup()
            self.emit(f"RETIRED: {self.name}; archived state: {self.path}" if action == "destroy" else
                      f"READY: {self.name}; SSH alias: ssh {self.name}")
        except Exception as exc:
            if previous_ref is None:
                self.receipt.pop("last_successful_ref", None)
            else:
                self.receipt["last_successful_ref"] = previous_ref
            row.update(status="failed", ended=time.time(), error=str(exc))
            row["failed_at"] = getattr(exc, "progress_path", "operation")
            self.record_error(exc, row["failed_at"], visible=not getattr(exc, "reported", False))
            try:
                save_json(self.receipt_file, self.receipt)
            except OSError as record_error:
                self.record_error(record_error, "receipt")
            raise
        finally:
            if cleanup:
                cleanup()
            try:
                print(f"Host logs: {self.operation_log.parent}", file=sys.stderr)
            except OSError:
                pass  # 转发失败已决定结果，不能覆盖正在传播的主错误。
            self.runner.last_log = self.operation_log
            self.runner.log = None
            self.attempt_row = None

    def emit(self, line, visible=True):
        self.runner.save_output(line)
        if visible:
            print(clean_output(line), file=sys.stderr, flush=True)

    def record_error(self, exc, location, visible=True):
        try:
            self.runner.save_output("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
            self.emit(f"FAIL [{location}]: {brief(exc)}", visible=visible)
            if visible:
                exc.reported = True
        except OSError as log_error:
            try:
                print(f"FAIL logging: {brief(log_error)}; original: {brief(exc)}", file=sys.stderr)
            except OSError:
                pass

    def relocate(self, archive):
        old = self.path
        self.path = archive
        self.declaration_file = archive / "declaration.json"
        self.receipt_file = archive / "receipt.json"
        self.operation_log = archive / self.operation_log.relative_to(old)
        if self.runner.log:
            self.runner.log = archive / self.runner.log.relative_to(old)
        def moved(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == "log" and isinstance(item, str) and item.startswith(str(old) + "/"):
                        value[key] = str(archive / Path(item).relative_to(old))
                    else:
                        moved(item)
            elif isinstance(value, list):
                for item in value:
                    moved(item)
        moved(self.receipt)
        save_json(self.receipt_file, self.receipt)

    @contextmanager
    def stage(self, label):
        if self.attempt_row is None:
            raise Failure("internal error: stage started outside an attempt")
        logs = self.path / "logs"
        safe_directory(logs, create=True)
        log_dir = logs / self.attempt_row["id"]
        safe_directory(log_dir, create=True)
        self.runner.log = log_dir / f"{label}.log"
        previous_output_mode = self.runner.output_mode
        self.runner.output_mode = "native" if label == "bootstrap" else "quiet"
        row = {"id": label, "started": time.time(), "status": "running",
               "log": str(self.runner.log), "steps": []}
        # stages 保存各阶段最新结果；attempts 另行保留每次执行的完整历史。
        self.receipt["stages"][label] = row
        self.attempt_row["stages"].append(row)
        self.current_stage = label
        previous_wait_callback = self.runner.on_wait
        self.runner.on_wait = self.wait_update
        started = time.monotonic()
        self.emit(f"STAGE RUN  [{label}] {STAGE_DESCRIPTIONS[label]}")
        save_json(self.receipt_file, self.receipt)
        try:
            yield
        except Exception as exc:
            if not hasattr(exc, "progress_path"):
                exc.progress_path = label
            command = exc.argv[:3] if isinstance(exc, CommandFailure) else None
            exit_code = exc.returncode if isinstance(exc, CommandFailure) else None
            row.update(status="failed", ended=time.time(), elapsed_seconds=round(time.monotonic() - started, 1),
                       error=str(exc), command=command, exit_code=exit_code)
            self.record_error(exc, getattr(exc, "progress_path", label), visible=False)
            try:
                self.emit(f"STAGE FAIL [{label}] step={row.get('current_step', 'none')}; "
                          f"elapsed={row['elapsed_seconds']}s")
                save_json(self.receipt_file, self.receipt)
            except OSError as log_error:
                self.record_error(log_error, f"{label}/failure-record")
            raise
        else:
            row.update(status="ok", ended=time.time(), elapsed_seconds=round(time.monotonic() - started, 1))
            self.receipt["last_successful_stage"] = label
            self.emit(f"STAGE OK   [{label}] {STAGE_DESCRIPTIONS[label]}; elapsed={row['elapsed_seconds']}s")
            save_json(self.receipt_file, self.receipt)
        finally:
            self.current_stage = None
            self.current_step = None
            self.runner.on_wait = previous_wait_callback
            self.runner.log = self.operation_log
            self.runner.output_mode = previous_output_mode

    @contextmanager
    def step(self, label, description, timeout=None):
        stage = self.current_stage
        name = f"{stage}/{label}" if stage else label
        row = {"id": label, "description": description, "started": time.time(), "status": "running"}
        if timeout is not None:
            row["timeout_seconds"] = timeout
        if stage:
            self.receipt["stages"][stage]["steps"].append(row)
            self.receipt["stages"][stage]["current_step"] = label
            save_json(self.receipt_file, self.receipt)
        self.current_step = label
        started = time.monotonic()
        self.emit(f"  STEP RUN  [{name}] {description}" +
                  (f"; timeout={timeout:.0f}s" if timeout is not None else ""))
        try:
            yield
        except Exception as exc:
            if not hasattr(exc, "progress_path"):
                exc.progress_path = name
            command = exc.argv[:3] if isinstance(exc, CommandFailure) else None
            exit_code = exc.returncode if isinstance(exc, CommandFailure) else None
            row.update(status="failed", ended=time.time(), elapsed_seconds=round(time.monotonic() - started, 1),
                       error=str(exc), command=command, exit_code=exit_code)
            try:
                self.emit(f"  STEP FAIL [{name}] {description}; elapsed={row['elapsed_seconds']}s; reason={brief(exc)}")
                exc.reported = True
                if stage:
                    save_json(self.receipt_file, self.receipt)
            except OSError as log_error:
                self.record_error(log_error, f"{name}/failure-record")
            raise
        else:
            row.update(status="ok", ended=time.time(), elapsed_seconds=round(time.monotonic() - started, 1))
            self.emit(f"  STEP OK   [{name}] {description}; elapsed={row['elapsed_seconds']}s")
            if stage:
                save_json(self.receipt_file, self.receipt)
        finally:
            self.current_step = None

    def wait_update(self, elapsed, timeout):
        name = (f"{self.current_stage}/{self.current_step}" if self.current_step and self.current_stage
                else self.current_stage or self.current_step or "command")
        self.emit(f"  STEP WAIT [{name}] command still running; elapsed={elapsed:.0f}s; timeout={timeout:.0f}s")

    def m(self, *args, timeout=15, stream=False, check=True, heartbeat=None, show_output=True):
        return self.runner([self.host.cli, *args], timeout=timeout, stream=stream, check=check,
                           heartbeat=heartbeat, on_wait=self.wait_update, show_output=show_output)

    def guest(self, action, *args, root=False, timeout=15, stream=False, heartbeat=None):
        if not self.helper:
            raise Failure("guest helper was not transferred")
        prefix = ["sudo", "-n"] if root else ["env", "-i", "HOME=/home/ubuntu",
                                             "USER=ubuntu", "LOGNAME=ubuntu", "PATH=/usr/local/bin:/usr/bin:/bin"]
        return self.m("exec", "--no-map-working-directory", self.name, "--", *prefix,
                      "python3", self.helper, action, *args, timeout=timeout, stream=stream,
                      heartbeat=heartbeat)

    def transfer_helper(self, timeout=120, budget=None):
        budget = budget or Deadline(timeout, "guest helper transfer")
        require_running(self.name, self.read_management_state(budget))
        self.helper = f"/tmp/dotfiles-multipass-{self.declaration['uuid']}.py"
        self.m("transfer", str(HERE / "guest.py"), f"{self.name}:{self.helper}",
               timeout=budget.remaining(timeout))

    def cleanup_helper(self):
        if self.helper:
            try:
                # exec 会隐式启动非 Running 实例；故障退出时只查询状态，不借清理触发生命周期操作。
                entry = self.host.instances().get(self.name, {})
                require_running(self.name, entry.get("state"))
                self.m("exec", "--no-map-working-directory", self.name, "--", "rm", "-f", self.helper)
            except (Failure, ValueError, OSError) as exc:
                self.receipt["pending_helper_cleanup"] = {"path": self.helper, "reason": str(exc)}
                save_json(self.receipt_file, self.receipt)
                self.helper = None
                self.emit(f"CLEANUP DEFER [{self.current_stage or 'guest-helper'}] "
                          f"Temporary guest helper retained; retry after management recovery; reason={brief(exc)}")
                raise Failure(f"temporary guest helper cleanup deferred: {exc}") from exc
            self.helper = None
            self.receipt.pop("pending_helper_cleanup", None)
            save_json(self.receipt_file, self.receipt)

    def cleanup_helper_on_exit(self):
        if not self.helper:
            return
        primary_error = sys.exc_info()[0] is not None
        label = self.current_stage or "guest-helper"
        try:
            self.emit(f"CLEANUP RUN  [{label}] Remove temporary guest helper")
            self.cleanup_helper()
        except Exception as exc:
            self.record_error(exc, f"cleanup/{label}")
            try:
                self.emit(f"CLEANUP FAIL [{label}] {brief(exc)}")
            except OSError as log_error:
                self.record_error(log_error, f"cleanup/{label}/failure-record")
            if not primary_error:
                raise
        else:
            self.emit(f"CLEANUP OK   [{label}] Temporary guest helper removed")

    def verify_guest(self, budget=None):
        probe = json.loads(self.guest("probe", root=True,
                                      timeout=budget.remaining(15) if budget else 15).stdout)
        declaration = self.declaration
        marker = probe.get("marker") or {}
        # 先核对云初始化标记与机器身份，再信任该实例并更新收据。
        if marker.get("uuid") != declaration["uuid"] or marker.get("name") != self.name:
            raise Failure("guest creation marker does not match this instance declaration")
        if probe["os_id"] != "ubuntu" or probe["os_version"] != declaration["image"] or \
                probe["arch"] not in ("aarch64", "arm64") or probe["ubuntu_home"] != "/home/ubuntu":
            raise Failure(f"guest OS, architecture or user does not match declaration: {probe}")
        if self.receipt.get("machine_id") and self.receipt["machine_id"] != probe["machine_id"]:
            raise Failure("guest machine-id changed; refusing to take over replacement instance")
        if self.receipt.get("cloud_instance_id") and self.receipt["cloud_instance_id"] != probe["cloud_instance_id"]:
            raise Failure("cloud instance-id changed; refusing to take over replacement instance")
        self.m("exec", "--no-map-working-directory", self.name, "--", "sudo", "-n", "true",
               timeout=budget.remaining(15) if budget else 15)
        self.receipt.update(machine_id=probe["machine_id"], cloud_instance_id=probe["cloud_instance_id"],
                            guest=probe)
        info = self.host.info(self.name, timeout=budget.remaining(15) if budget else 15)
        if info.get("cpu_count") is not None and int(info["cpu_count"]) != declaration["cpus"]:
            raise Failure("guest CPU count differs from creation declaration")
        resources = self.host.resources(self.name, budget=budget)
        if int(resources["cpus"]) != declaration["cpus"] or \
                any(abs(size_bytes(resources[field]) - size_bytes(declaration[field])) > 1024 ** 2
                    for field in ("memory", "disk")):
            raise Failure(f"Multipass resources differ from creation declaration: {resources}")
        self.receipt["resources"] = resources
        self.receipt["image_hash"] = info.get("image_hash")
        save_json(self.receipt_file, self.receipt)
        return probe

    def cloud_wait(self, timeout=None, budget=None):
        timeout = timeout or DEFAULTS["timeouts"]["cloud_init"]
        budget = budget or Deadline(timeout, "cloud-init status")
        connect_deadline = min(budget.end, time.monotonic() + CONNECT_RETRY_SECONDS)
        retries = 0
        while True:
            try:
                result = self.m("exec", "--no-map-working-directory", self.name, "--", "cloud-init",
                                "status", "--wait", "--format", "json", timeout=budget.remaining(),
                                heartbeat=PROGRESS_INTERVAL)
                break
            except CommandFailure as exc:
                if transient_guest_connection(exc):
                    if time.monotonic() >= budget.end:
                        self.emit(f"  STEP WAIT [{self.current_stage or 'cloud-init'}/"
                                  f"{self.current_step or 'status'}] time budget exhausted after guest "
                                  "connection failure")
                        raise
                    state = self.read_management_state(budget)
                    if state != "Running":
                        self.management_snapshot(budget)
                        raise Failure(f"instance {self.name} is {state or 'missing'} after guest connection "
                                      "failure; refusing another exec until management recovers") from exc
                    remaining = connect_deadline - time.monotonic()
                    if remaining > 0:
                        retries += 1
                        self.emit(f"  STEP RETRY [{self.current_stage or 'cloud-init'}/"
                                  f"{self.current_step or 'status'}] guest connection unavailable "
                                  f"({exc.output.strip()[-160:]}); attempt={retries}; "
                                  f"retrying in {min(CONNECT_RETRY_INTERVAL, remaining):.0f}s")
                        time.sleep(min(CONNECT_RETRY_INTERVAL, remaining, budget.remaining()))
                        continue
                    self.emit(f"  STEP WAIT [{self.current_stage or 'cloud-init'}/"
                              f"{self.current_step or 'status'}] guest connection did not recover "
                              f"within {min(CONNECT_RETRY_SECONDS, timeout)}s")
                else:
                    # 保留原始 cloud-init 错误；只在诊断命令可用时补充日志。
                    self.emit(f"  STEP DIAG [{self.current_stage or 'cloud-init'}/"
                              f"{self.current_step or 'status'}] collect guest cloud-init output if reachable")
                    try:
                        state = self.read_management_state(budget)
                        if state == "Running":
                            self.m("exec", "--no-map-working-directory", self.name, "--", "sudo", "-n",
                                   "tail", "-n", "120", "/var/log/cloud-init-output.log",
                                   timeout=budget.remaining(15), check=False)
                    except (Failure, OSError, ValueError):
                        pass
                raise
        status = parse_json_output(result.stdout, "cloud-init status")
        extended = status.get("extended_status", status.get("status"))
        if extended != "done" or status.get("errors"):
            raise Failure(f"cloud-init did not finish cleanly: {status}")
        self.receipt["cloud_init"] = status
        save_json(self.receipt_file, self.receipt)

    def read_management_state(self, budget):
        result = self.m("list", "--format", "json", timeout=budget.remaining(15))
        data = parse_json_output(result.stdout, "multipass list")
        entry = next((row for row in data.get("list", []) if row.get("name") == self.name), None)
        state = entry.get("state") if isinstance(entry, dict) else None
        now = time.monotonic()
        visible = not self.observation or self.observation[0] != state or now - self.observation[1] >= PROGRESS_INTERVAL
        self.emit(f"  STEP OBSERVE [{self.current_stage or 'cloud-init'}/"
                  f"{self.current_step or 'reboot'}] instance={self.name} state={state or 'missing'}", visible=visible)
        if visible:
            self.observation = (state, now)
        return state

    def wait_management_state(self, expected, budget, seconds):
        accepted = (expected,) if isinstance(expected, str) else expected
        label = "/".join(accepted)
        local = budget.child(seconds, f"wait for {label}")
        last = None
        while True:
            try:
                last = self.read_management_state(local)
            except (CommandFailure, ValueError) as exc:
                self.emit(f"  STEP OBSERVE [{self.current_stage or 'cloud-init'}/"
                          f"{self.current_step or 'reboot'}] read-only state query failed: {exc}")
            else:
                if last in accepted:
                    return last
                if last is None or last == "Deleted":
                    raise Failure(f"instance {self.name} is {last or 'missing'} during stop/start recovery")
            if time.monotonic() >= local.end:
                self.management_snapshot(local)
                raise Failure(f"instance {self.name} did not reach {label} within {seconds}s; "
                              f"last observed state={last or 'unavailable'}")
            pause = min(CONNECT_RETRY_INTERVAL, local.remaining())
            if pause > 0:
                time.sleep(pause)

    def management_snapshot(self, budget=None):
        name = f"{self.current_stage or 'cloud-init'}/{self.current_step or 'restart'}"
        self.emit(f"  STEP DIAG [{name}] collect read-only Multipass state; each query has a 15s limit")
        for command in ("list", "info"):
            argv = (command, "--format", "json") if command == "list" else \
                (command, self.name, "--format", "json")
            try:
                result = self.m(*argv, timeout=budget.remaining(15) if budget else 15, check=False)
            except (Failure, OSError, ValueError) as exc:
                self.emit(f"  STEP DIAG [{name}] multipass {command} unavailable: {exc}")
                continue
            if result.returncode:
                self.emit(f"  STEP DIAG [{name}] multipass {command} exit {result.returncode}; see stage log")
                continue
            try:
                data = parse_json_output(result.stdout, f"multipass {command}")
                entry = (next((item for item in data.get("list", []) if item.get("name") == self.name), None)
                         if command == "list" else data.get("info", {}).get(self.name))
                if isinstance(entry, list):
                    entry = entry[0] if len(entry) == 1 else None
                if isinstance(entry, dict):
                    self.emit(f"  STEP DIAG [{name}] multipass {command}: "
                              f"state={entry.get('state', 'unknown')}, ipv4={entry.get('ipv4', [])}")
                else:
                    self.emit(f"  STEP DIAG [{name}] multipass {command}: instance absent; see stage log")
            except Exception:
                self.emit(f"  STEP DIAG [{name}] multipass {command} output saved in stage log")

    def verify_reboot(self, probe, before):
        if not probe.get("boot_id") or probe["boot_id"] == before:
            raise Failure("previously requested reboot has not been verified: boot ID did not change; "
                          "inspect guest and management state; automatic reboot will not be repeated")
        if probe["reboot_required"]:
            raise Failure("guest still requires reboot after one automatic restart; "
                          "inspect the guest before retrying")
        self.receipt["reboot_to"] = probe["boot_id"]
        save_json(self.receipt_file, self.receipt)
        return probe

    def retry_guest_connection(self, action, budget):
        retries = 0
        while True:
            require_running(self.name, self.read_management_state(budget))
            try:
                return action()
            except CommandFailure as exc:
                if not transient_guest_connection(exc) or time.monotonic() >= budget.end:
                    raise
                require_running(self.name, self.read_management_state(budget))
                delay = min(CONNECT_RETRY_INTERVAL, budget.remaining())
                retries += 1
                self.emit(f"  STEP RETRY [{self.current_stage or 'cloud-init'}/{self.current_step}] "
                          f"guest connection unavailable ({exc.output.strip()[-160:]}); "
                          f"attempt={retries}; retrying in {delay:.0f}s")
                time.sleep(delay)
                if time.monotonic() >= budget.end:
                    raise

    def reboot_identity(self, budget, helper_step, identity_step):
        helper_budget = budget.child(120, "helper retransfer")
        with self.step(helper_step, "Transfer helper before reboot identity verification",
                       timeout=helper_budget.remaining()):
            self.retry_guest_connection(
                lambda: self.transfer_helper(budget=helper_budget), helper_budget)
        verify_budget = budget.child(120, "reboot identity verification")
        with self.step(identity_step, "Verify current guest identity and read boot ID",
                       timeout=verify_budget.remaining()):
            probe = self.retry_guest_connection(lambda: self.verify_guest(budget=verify_budget), verify_budget)
            require_running(self.name, self.read_management_state(verify_budget))
            if not probe.get("boot_id"):
                raise Failure("guest boot ID is missing during reboot recovery")
            return probe

    def reboot_if_required(self, probe):
        operation = self.reboot_operation()
        resuming = operation is not None
        before = self.receipt.get("reboot_from")
        if before and operation is None:
            # 旧收据只含 reboot_from；不把旧 restart 请求解释成新的 stop/start 意图。
            with self.step("reconnect", "Verify the previously requested reboot after management recovery"):
                return self.verify_reboot(probe, before)
        if operation is None:
            if not probe["reboot_required"]:
                self.emit(f"  STEP SKIP [{self.current_stage or 'cloud-init'}/reboot] "
                          "guest does not require a reboot")
                return probe
            if not probe.get("boot_id"):
                raise Failure("guest boot ID is missing before required reboot")
            before = probe["boot_id"]
            operation = {"method": "stop-start", "phase": "planned", "from_boot_id": before,
                         "declaration": {key: self.declaration[key]
                                         for key in ("name", "uuid", "image", "template_sha256")},
                         "declaration_sha256": hashlib.sha256(json.dumps(
                             self.declaration, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                         "machine_id": probe["machine_id"],
                         "cloud_instance_id": probe["cloud_instance_id"],
                         "created_at": time.time(), "events": []}
            self.receipt["reboot_from"] = before
            self.receipt["reboot_operation"] = operation
            save_json(self.receipt_file, self.receipt)
            self.emit(f"  STEP INTENT [cloud-init/reboot] method=stop-start; from_boot_id={before}; "
                      f"instance={self.name}")
        if operation["phase"] == "verified":
            with self.step("reconnect", "Recheck the completed stop/start reboot"):
                return self.verify_reboot(probe, before)

        budget = Deadline(DEFAULTS["timeouts"]["reboot"], "stop/start reboot")
        with self.step("state", "Observe Multipass state before resuming stop/start",
                       timeout=budget.remaining()):
            state = self.read_management_state(budget)
            if state in ("Starting", "Restarting", "Unknown"):
                state = self.wait_management_state(("Running", "Stopped"), budget, 120)
            if state not in ("Running", "Stopped"):
                require_running(self.name, state)

        if resuming and state == "Running" and operation["phase"] in ("planned", "stop_requested"):
            # 本地意图不是当前 VM 的身份证明；人工恢复可能已完成重启，也可能出现同名替换实例。
            current = self.reboot_identity(budget, "resume-helper", "resume-identity")
            if current["boot_id"] != before:
                self.reboot_event("boot-reconciled", observed_state="Running", boot_id=current["boot_id"])
                self.reboot_phase("running")

        phase = operation["phase"]
        if phase in ("planned", "stop_requested"):
            if state == "Stopped":
                if phase == "planned":
                    raise Failure("instance stopped before the recorded stop request; refusing automatic start")
                self.reboot_event("stop-reconciled", observed_state=state)
                self.reboot_phase("stopped")
            else:
                with self.step("stop", "Stop the declared instance normally", timeout=budget.remaining(120)):
                    self.reboot_phase("stop_requested")
                    command_error = None
                    try:
                        # Multipass stop has no --timeout; Runner enforces this limit.
                        self.m("stop", self.name, timeout=budget.remaining(120),
                               heartbeat=PROGRESS_INTERVAL, show_output=False)
                    except CommandFailure as exc:
                        command_error = exc
                    self.reboot_event("stop-return", exit_code=command_error.returncode if command_error else 0,
                                      error=str(command_error)[-500:] if command_error else None)
                with self.step("stopped", "Confirm Stopped using read-only Multipass state",
                               timeout=budget.remaining(120)):
                    self.wait_management_state("Stopped", budget, 120)
                    self.reboot_event("stop-observed", observed_state="Stopped",
                                      command_exit=command_error.returncode if command_error else 0)
                    self.reboot_phase("stopped")
                state = "Stopped"

        phase = operation["phase"]
        if phase in ("stopped", "start_requested"):
            if state == "Running":
                self.reboot_event("start-reconciled", observed_state=state)
                self.reboot_phase("running")
            else:
                with self.step("start", "Start the stopped declared instance",
                               timeout=budget.remaining(120)):
                    self.reboot_phase("start_requested")
                    command_error = None
                    command_limit = budget.remaining(120)
                    try:
                        self.m("start", "--timeout", str(max(1, int(command_limit) - 2)), self.name,
                               timeout=command_limit, heartbeat=PROGRESS_INTERVAL, show_output=False)
                    except CommandFailure as exc:
                        command_error = exc
                    self.reboot_event("start-return", exit_code=command_error.returncode if command_error else 0,
                                      error=str(command_error)[-500:] if command_error else None)
                with self.step("running", "Confirm Running using read-only Multipass state",
                               timeout=budget.remaining(180)):
                    self.wait_management_state("Running", budget, 180)
                    self.reboot_event("start-observed", observed_state="Running",
                                      command_exit=command_error.returncode if command_error else 0)
                    self.reboot_phase("running")
                state = "Running"

        if state != "Running" or operation["phase"] != "running":
            raise Failure(f"stop/start recovery cannot verify from phase={operation['phase']} state={state}")
        reconnect_budget = budget.child(180, "management reconnect")
        with self.step("reconnect", "Confirm management connection and clean cloud-init",
                       timeout=reconnect_budget.remaining()):
            self.cloud_wait(budget=reconnect_budget)
        after = self.reboot_identity(budget, "helper-retransfer", "reboot-identity")
        with self.step("verify-reboot", "Verify identity, new boot ID and cleared reboot flag",
                       timeout=budget.remaining(120)):
            self.verify_reboot(after, before)
            self.reboot_event("verified", observed_state="Running", boot_id=after["boot_id"])
            self.reboot_phase("verified")
            return after

    def ssh_config(self, host_info):
        declaration = self.declaration
        setting = SSHConfig(self.home, self.name, declaration["uuid"],
                            Path(declaration["ssh_public_key"]), HERE / "ssh_proxy.py",
                            self.host.cli)
        with self.step("host-key", "Read and validate the guest SSH host key"):
            host_key_output = self.m("exec", "--no-map-working-directory", self.name, "--", "cat",
                                     "/etc/ssh/ssh_host_ed25519_key.pub").stdout.strip()
            if not KEY.fullmatch(host_key_output):
                raise Failure("guest Ed25519 host public key is invalid")
            host_key = " ".join(host_key_output.split()[:2])
        with self.step("publish", "Publish managed SSH config and pinned host key"):
            previous = self.receipt.get("ssh_files", {})
            hashes = setting.publish(host_key, previous)
            self.receipt["ssh_files"] = hashes
            self.receipt["host_key_sha256"] = hashlib.sha256(host_key.encode()).hexdigest()
            fingerprint = self.runner(["ssh-keygen", "-lf", str(setting.known), "-E", "sha256"]).stdout
            self.receipt["host_key_fingerprint"] = next(
                (part for part in fingerprint.split() if part.startswith("SHA256:")), None)
            save_json(self.receipt_file, self.receipt)
        with self.step("login", "Test ordinary SSH login as ubuntu", timeout=45):
            result = self.runner(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", self.name,
                                  "sh", "-c", "'test \"$(id -un)\" = ubuntu && test \"$HOME\" = /home/ubuntu'"],
                                 timeout=45)
            if result.returncode:
                raise Failure("ordinary SSH did not authenticate as ubuntu")
        self.receipt["image_hash"] = host_info.get("image_hash")
        save_json(self.receipt_file, self.receipt)

    def provision(self, ref, git_identity):
        self.receipt.pop("git_identity_configured", None)
        self.receipt["git_identity_action"] = "pending"
        save_json(self.receipt_file, self.receipt)
        try:
            with self.stage("guest"):
                with self.step("helper", "Transfer the temporary guest helper", timeout=120):
                    self.transfer_helper()
                with self.step("identity", "Verify guest marker, OS and VM resources"):
                    self.verify_guest()
                with self.step("bootstrap-lock", "Check that guest bootstrap is idle"):
                    self.guest("bootstrap-idle")
                with self.step("packages", "Wait for apt/dpkg locks and audit packages", timeout=630):
                    self.guest("packages-ready", root=True, timeout=630, stream=True,
                               heartbeat=PROGRESS_INTERVAL)
            with self.stage("ssh"):
                with lock(self.base / "ssh.lock"):
                    with self.step("info", "Read current Multipass instance address and image"):
                        info = self.host.info(self.name)
                    self.ssh_config(info)
            with self.stage("repository"):
                with self.step("checkout", "Clone or verify the requested Git commit", timeout=1200):
                    self.guest("repository", self.declaration["repo_url"], ref,
                               timeout=1200, stream=True, heartbeat=PROGRESS_INTERVAL)
                    self.receipt["current_ref"] = ref
                    save_json(self.receipt_file, self.receipt)
            with self.stage("bootstrap-preview"):
                with self.step("child", "Run guest bootstrap --dry-run --profile server", timeout=1200):
                    self.emit("    CHILD BEGIN [bootstrap-preview] Preview details saved in host stage log; guest preview is read-only")
                    try:
                        self.guest("bootstrap", "preview", timeout=1200, stream=True,
                                   heartbeat=PROGRESS_INTERVAL)
                    finally:
                        self.emit("    CHILD END   [bootstrap-preview] Guest bootstrap command ended; overall verification follows")
            with self.stage("bootstrap"):
                limit = DEFAULTS["timeouts"]["bootstrap"]
                with self.step("child", "Run guest bootstrap --apply --profile server", timeout=limit):
                    self.emit("    CHILD BEGIN [bootstrap] Guest bootstrap summary; full diagnostics saved on both machines")
                    try:
                        self.guest("bootstrap", "apply", timeout=limit, stream=True,
                                   heartbeat=PROGRESS_INTERVAL)
                    finally:
                        self.emit("    CHILD END   [bootstrap] Guest bootstrap command ended; overall verification follows")
            with self.stage("finalize"):
                with self.step("account", "Set guest login shell and optional Git identity", timeout=120):
                    self.guest("finalize", git_identity.get("name", ""), git_identity.get("email", ""),
                               root=True, timeout=120)
                self.receipt["git_identity_action"] = "applied" if git_identity else "skipped"
                save_json(self.receipt_file, self.receipt)
            with self.stage("verified"):
                with self.step("identity", "Recheck guest identity and Zsh login shell"):
                    final = self.verify_guest()
                    if not final["ubuntu_shell"].endswith("/zsh"):
                        raise Failure("ubuntu login shell was not changed to Zsh")
                with self.step("versions", "Collect installed tool and doctor results", timeout=180):
                    self.receipt["development"] = parse_json_output(
                        self.guest("versions", timeout=180, heartbeat=PROGRESS_INTERVAL).stdout,
                        "guest tool versions")
                with self.step("helper-cleanup", "Remove the temporary guest helper"):
                    self.cleanup_helper()
                self.receipt["last_successful_ref"] = ref
                self.receipt["guest"] = final
                save_json(self.receipt_file, self.receipt)
        finally:
            self.cleanup_helper_on_exit()


def render_cloud(declaration, canonical):
    content = TEMPLATE.read_text()
    marker = json.dumps({"uuid": declaration["uuid"], "name": declaration["name"]},
                        separators=(",", ":"))
    return content.replace("__SSH_PUBLIC_KEY__", json.dumps(canonical)).replace(
        "__INSTANCE_JSON__", json.dumps(marker))


def declaration_from(args, config, saved, canonical, fingerprint):
    if saved:
        data = dict(saved)
        for field in ("image", "cpus", "memory", "disk", "repo_url"):
            current = value(args, {}, field)
            if current is not None and str(current) != str(saved[field]):
                raise Failure(f"existing {field} differs from creation declaration; create another instance")
        if args.action == "create" and value(args, config, "ref") != saved["target_ref"]:
            raise Failure("create cannot change target commit; use provision --ref")
        if fingerprint != saved["ssh_fingerprint"]:
            raise Failure("SSH public key fingerprint changed; key rotation is not automated")
        data["ssh_public_key"] = str(value(args, config, "ssh_public_key", saved["ssh_public_key"]))
    else:
        data = {"schema": 1, "uuid": str(uuid.uuid4()), "name": value(args, config, "name", DEFAULTS["name"]),
                "image": value(args, config, "image", DEFAULTS["image"]),
                "cpus": value(args, config, "cpus", DEFAULTS["cpus"]),
                "memory": value(args, config, "memory", DEFAULTS["memory"]),
                "disk": value(args, config, "disk", DEFAULTS["disk"]),
                "repo_url": value(args, config, "repo_url", DEFAULTS["repo_url"]),
                "target_ref": value(args, config, "ref"),
                "ssh_public_key": str(value(args, config, "ssh_public_key")),
                "ssh_fingerprint": fingerprint,
                "template_sha256": hashlib.sha256(TEMPLATE.read_bytes()).hexdigest()}
    validate_declaration(data)
    return data


def create_or_provision(args, config, home, runner):
    with preflight_progress("STAGE", "preflight", "Check host, SSH identity and saved instance state"):
        with preflight_progress("STEP", "preflight/host", "Validate macOS host and instance name"):
            git_identity, git_identity_source = resolve_git_identity(args, config)
            host_preflight()
            name = value(args, config, "name", DEFAULTS["name"])
            validate_name(name)
            machine = Machine(home, name, runner)
            saved = machine.declaration
            if args.action == "create" and saved:
                # Report a vanished VM before comparing a retry's Git ref or SSH key.
                existing_host = machine.host.probe()
                machine.refuse_missing_guest(machine.host.instances() if existing_host else {})
            creation_record = getattr(args, "creation_record", None)
            if creation_record is not None:
                if machine.path.exists() or machine.path.is_symlink():
                    raise Failure("--creation-record requires a fresh instance name without saved state")
                if not creation_record.is_absolute() or not creation_record.parent.is_dir() or \
                        creation_record.exists() or creation_record.is_symlink():
                    raise Failure("--creation-record must be a new absolute file in an existing directory", 2)
            if args.action == "provision" and not saved:
                raise Failure(f"no managed declaration for {name}")
        with preflight_progress("STEP", "preflight/key", "Validate public key, agent and requested VM"):
            key_path = value(args, config, "ssh_public_key", saved["ssh_public_key"] if saved else None)
            canonical, fingerprint = public_key(Path(key_path) if key_path else None, runner)
            requested = declaration_from(args, config, saved, canonical, fingerprint)
            if args.action == "provision" and args.ref:
                requested["target_ref"] = args.ref
                validate_declaration(requested)
            require_agent(canonical, runner)
        with preflight_progress("STEP", "preflight/instance", "Check SSH files and Multipass ownership"):
            setting = SSHConfig(home, name, requested["uuid"], Path(requested["ssh_public_key"]),
                                HERE / "ssh_proxy.py", shutil.which("multipass") or "/usr/local/bin/multipass")
            setting.preflight(machine.receipt.get("ssh_files"))
            observed = machine.host.probe()
            instances = machine.host.instances() if observed else {}
            if name in instances and not saved:
                raise Failure(f"same-name instance {name} is not managed by this entrypoint")
            if name in instances:
                require_create_state(machine, args.action, instances[name].get("state"))
            if args.action == "provision" and name not in instances:
                raise Failure(f"managed instance {name} is missing")
            if args.action == "provision" and machine.receipt["stages"].get("cloud-init", {}).get("status") != "ok":
                raise Failure("first-boot checks are incomplete; resume with create using the saved "
                              "target commit before provision")
            if args.action == "create":
                machine.refuse_missing_guest(instances)
            if observed and observed["qualified"]:
                machine.host.verify_service()
        if args.apply:
            with preflight_progress("STEP", "preflight/disk", "Check free space for the requested VM disk"):
                free = shutil.disk_usage(home).free
                minimum = int(SIZE.fullmatch(requested["disk"]).group(1)) * \
                    (1024 ** 3 if requested["disk"].endswith("G") else 1024 ** 2)
                if free < minimum + 5 * 1024 ** 3:
                    raise Failure("host disk free space is below requested guest disk plus 5 GiB")
    if not observed:
        print("DEFER: Multipass installation, daemon, image and instance checks until --apply", file=sys.stderr)
    elif not observed["qualified"]:
        print(f"PLAN: upgrade Multipass {observed['client']} to at least {RELEASES['minimum']}",
              file=sys.stderr)
    else:
        print(f"REUSE: Multipass {observed['client']}", file=sys.stderr)
    print(f"PLAN: {args.action} {name}, Ubuntu {requested['image']}, {requested['cpus']} CPU, "
          f"{requested['memory']} RAM, {requested['disk']} disk, ref {requested['target_ref']}",
          file=sys.stderr)
    if git_identity:
        print(f"PLAN: set guest Git identity from {git_identity_source}", file=sys.stderr)
    else:
        print("PLAN: leave guest Git identity unchanged (unset on a new instance)", file=sys.stderr)
    if not args.apply:
        print("Preview complete; no files or instances changed. Log: none (read-only preview). DEFER: image availability and guest checks.",
              file=sys.stderr)
        return
    # 预检只读；从此处才创建状态目录，并在实例锁内复核预检期间的并发变化。
    safe_directory(machine.base, create=True)
    safe_directory(machine.base / "instances", create=True)
    if creation_record is not None:
        try:
            machine.path.mkdir(mode=0o700)
        except FileExistsError:
            raise Failure("instance state appeared during preflight; refusing to register another run")
    else:
        safe_directory(machine.path, create=True)
    with machine.apply_lock(args.action):
        machine.declaration = requested
        save_json(machine.declaration_file, requested)
        machine.receipt["target_ref"] = requested["target_ref"]
        save_json(machine.receipt_file, machine.receipt)
        if creation_record is not None:
            record_creation(creation_record, requested)
        with machine.stage("host"):
            with lock(machine.base / "host-install.lock"):
                observed = machine.host.ensure(observed)
            with machine.step("instances", "Read current Multipass instances"):
                existing = machine.host.instances()
                if name in existing:
                    require_create_state(machine, args.action, existing[name].get("state"))
            machine.receipt["host"] = {"macos": platform.mac_ver()[0],
                                       "multipass": observed, "source": machine.host.source,
                                       "runtime_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
            save_json(machine.receipt_file, machine.receipt)
        if args.action == "create":
            if name not in existing:
                with machine.stage("launch"):
                    with machine.step("image", f"Confirm Ubuntu {requested['image']} image availability"):
                        machine.refuse_missing_guest(existing)
                        machine.host.image(requested["image"])
                    with machine.step("user-data", "Write cloud-init user-data for this instance"):
                        cloud = machine.path / "user-data.yaml"
                        atomic(cloud, render_cloud(requested, canonical).encode())
                        machine.receipt["instances_before_launch"] = sorted(existing)
                        save_json(machine.receipt_file, machine.receipt)
                    limit = DEFAULTS["timeouts"]["cloud_init"] + 60
                    with machine.step("create", "Wait for Multipass launch and first boot", timeout=limit):
                        machine.m("launch", requested["image"], "--name", name,
                                  "--cpus", str(requested["cpus"]), "--memory", requested["memory"],
                                  "--disk", requested["disk"], "--cloud-init", str(cloud),
                                  "--timeout", str(DEFAULTS["timeouts"]["cloud_init"]),
                                  timeout=limit, stream=True, heartbeat=PROGRESS_INTERVAL,
                                  show_output=False)
            else:
                machine.emit(f"STAGE SKIP [launch] Reuse existing managed instance {name}")
            if machine.receipt["stages"].get("cloud-init", {}).get("status") != "ok":
                with machine.stage("cloud-init"):
                    try:
                        if machine.pending_stop_start():
                            machine.emit("  STEP RESUME [cloud-init/reboot] Reconcile saved stop/start "
                                         "operation before guest commands")
                            machine.reboot_if_required(None)
                        else:
                            with machine.step("status", "Wait for cloud-init to complete cleanly",
                                              timeout=DEFAULTS["timeouts"]["cloud_init"]):
                                machine.cloud_wait()
                            with machine.step("helper", "Transfer the temporary guest helper", timeout=120):
                                machine.transfer_helper()
                            with machine.step("identity", "Verify guest marker, OS and VM resources"):
                                probe = machine.verify_guest()
                            machine.reboot_if_required(probe)
                        with machine.step("helper-cleanup", "Remove the temporary guest helper"):
                            machine.cleanup_helper()
                    finally:
                        machine.cleanup_helper_on_exit()
            else:
                machine.emit("STAGE SKIP [cloud-init] First-boot checks already succeeded; "
                             "guest identity will be rechecked")
        machine.provision(requested["target_ref"], git_identity)


def destroy(args, config, home, runner):
    with preflight_progress("STAGE", "preflight", "Check managed state, Multipass and SSH ownership"):
        with preflight_progress("STEP", "preflight/state", "Validate host and saved instance identity"):
            host_preflight()
            name = value(args, config, "name", DEFAULTS["name"])
            validate_name(name)
            machine = Machine(home, name, runner)
            safe_directory(machine.base)
            safe_directory(machine.path)
            saved = machine.declaration
            if not isinstance(saved, dict):
                raise Failure(f"no managed declaration for {name}")
            if not isinstance(machine.receipt, dict):
                raise Failure("saved instance receipt is invalid")
            try:
                identity = str(uuid.UUID(saved["uuid"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise Failure("saved instance UUID is invalid") from exc
            if saved.get("schema") != 1 or saved.get("name") != name or identity != saved["uuid"]:
                raise Failure("saved instance declaration does not match the requested name")
            if not isinstance(saved.get("ssh_public_key"), str) or not saved["ssh_public_key"]:
                raise Failure("saved SSH public key path is invalid")
            archive_dir = machine.base / "retired"
            safe_directory(archive_dir)
            archive = archive_dir / f"{name}-{identity}"
            if archive.exists() or archive.is_symlink():
                raise Failure(f"retired state destination already exists: {archive}")
        with preflight_progress("STEP", "preflight/instance", "Read the Multipass service and instance state"):
            observed = machine.host.probe()
            if not observed or not observed["qualified"]:
                raise Failure("Multipass is missing or below the required version; cannot confirm instance absence")
            machine.host.verify_service()
            instances = machine.host.instances()
            entry = instances.get(name)
            if entry and entry.get("state") == "Deleted":
                raise Failure(f"instance {name} is recoverable; run multipass recover {name} first")
        with preflight_progress("STEP", "preflight/ssh", "Check managed SSH files before removal"):
            setting = SSHConfig(home, name, identity, Path(saved["ssh_public_key"]),
                                HERE / "ssh_proxy.py", machine.host.cli)
            ssh_plan = setting.removal_plan(machine.receipt.get("ssh_files"))

    state = entry.get("state") if entry else "absent"
    print(f"PLAN: destroy {name}; VM state {state}; permanently purge only this managed VM if present; "
          f"retire host state to {archive}", file=sys.stderr)
    ssh_changes = setting.removal_changes(ssh_plan)
    print("PLAN: managed SSH changes: " +
          (", ".join(str(path) for path, _, _ in ssh_changes) if ssh_changes else "none"),
          file=sys.stderr)
    if not args.apply:
        print("Preview complete; no files or instances changed. "
              "Log: none (read-only preview). DEFER: guest identity check until --apply if the VM exists.", file=sys.stderr)
        return

    with machine.apply_lock("destroy") as held:
        with machine.stage("destroy"):
            with machine.step("instance", "Verify and permanently remove the VM if present"):
                machine.host.verify_service()
                current = machine.host.instances().get(name)
                if current:
                    state = current.get("state")
                    if state == "Deleted":
                        raise Failure(f"instance {name} is recoverable; run multipass recover {name} first")
                    if state != "Running":
                        if state not in ("Stopped", "Suspended"):
                            raise Failure(f"cannot verify instance {name} in state {state!r}")
                        machine.m("start", name, timeout=600)
                    prefix = ("exec", "--no-map-working-directory", name, "--")
                    try:
                        marker = json.loads(machine.m(*prefix, "sudo", "-n", "cat",
                                                      "/var/lib/dotfiles-multipass/instance.json").stdout)
                    except ValueError as exc:
                        raise Failure("guest creation marker is unreadable; preserving instance") from exc
                    if marker != {"name": name, "uuid": identity}:
                        raise Failure("guest creation marker does not match this instance declaration")
                    for field, command in (("machine_id", ("cat", "/etc/machine-id")),
                                           ("cloud_instance_id", ("cloud-init", "query", "instance_id"))):
                        recorded = machine.receipt.get(field)
                        if recorded and machine.m(*prefix, *command).stdout.strip() != recorded:
                            raise Failure(f"guest {field} changed; refusing to delete a replacement instance")
                    machine.m("delete", "--purge", name, timeout=120)
                    if name in machine.host.instances():
                        raise Failure(f"Multipass still lists {name} after delete --purge")
                else:
                    state = "absent"
            with machine.step("ssh", "Remove only this instance's managed SSH files"):
                with lock(machine.base / "ssh.lock"):
                    setting.remove(machine.receipt.get("ssh_files"))
            with machine.step("archive", "Archive the old declaration and receipt"):
                safe_directory(archive_dir, create=True)
                if archive.exists() or archive.is_symlink():
                    raise Failure(f"retired state destination appeared during destroy: {archive}")
                save_json(machine.path / "retirement.json", {"schema": 1, "name": name, "uuid": identity,
                                                               "retired_at": time.time(),
                                                               "vm_state_before_apply": state})
                os.rename(machine.path, archive)
                held.path = archive / "lock"
                machine.relocate(archive)


def check(args, config, home, runner):
    name = value(args, config, "name", DEFAULTS["name"])
    validate_name(name)
    machine = Machine(home, name, runner)
    if not machine.declaration:
        raise Failure(f"no managed declaration for {name}")
    observed = machine.host.probe()
    if not observed or not observed["qualified"]:
        raise Failure("Multipass is missing or below the required version")
    machine.host.verify_service()
    instances = machine.host.instances()
    if name not in instances:
        raise Failure(f"managed instance {name} is missing")
    info = machine.host.info(name)
    print(json.dumps({"name": name, "state": instances[name].get("state"),
                      "image_hash": info.get("image_hash"),
                      "target_ref": machine.declaration["target_ref"],
                      "current_ref": machine.receipt.get("current_ref"),
                      "last_successful_ref": machine.receipt.get("last_successful_ref"),
                      "last_stage": machine.receipt.get("last_successful_stage")}, indent=2))
    require_running(name, instances[name].get("state"))
    marker = machine.m("exec", "--no-map-working-directory", name, "--", "sudo", "-n", "cat",
                       "/var/lib/dotfiles-multipass/instance.json").stdout
    if json.loads(marker).get("uuid") != machine.declaration["uuid"]:
        raise Failure("guest ownership marker does not match declaration")
    current = machine.m("exec", "--no-map-working-directory", name, "--", "git", "-C",
                        "/home/ubuntu/.dotfiles", "rev-parse", "HEAD").stdout.strip()
    if current != machine.receipt.get("current_ref"):
        raise Failure(f"guest HEAD {current} differs from recorded checkout")
    if args.runtime:
        machine.m("exec", "--no-map-working-directory", name, "--", "env", "-i",
                  "HOME=/home/ubuntu", "USER=ubuntu", "LOGNAME=ubuntu",
                  "PATH=/home/ubuntu/.local/bin:/usr/local/bin:/usr/bin:/bin",
                  "zsh", "-c", 'exec bash "$@"', "dotfiles-multipass-check",
                  "/home/ubuntu/.dotfiles/scripts/doctor.sh", "--runtime",
                  *[part for module in SERVER_MODULES for part in ("--only", module)],
                  timeout=1200, stream=True)
    if machine.receipt.get("last_successful_ref") != machine.declaration["target_ref"]:
        raise Failure("last successful provision does not match target ref")


def ssh(args, config, home, runner):
    name = value(args, config, "name", DEFAULTS["name"])
    validate_name(name)
    machine = Machine(home, name, runner)
    if not machine.declaration:
        raise Failure(f"no managed declaration for {name}")
    observed = machine.host.probe()
    if not observed:
        raise Failure("Multipass is missing")
    instances = machine.host.instances()
    require_running(name, instances.get(name, {}).get("state"))
    os.execvp("ssh", ["ssh", name])


def main():
    os.umask(0o077)
    args = parser().parse_args()
    home = Path(os.environ.get("HOME", ""))
    runner = Runner()

    def interrupted(signum, _frame):
        runner.stop()
        raise Failure(f"interrupted by signal {signum}; guest tasks may still be running", 128 + signum)

    for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, interrupted)
    try:
        require_default_layout(home)
        config_path = args.config or home / ".config/dotfiles-multipass/config.json"
        config = read_config(config_path)
        if args.action in ("create", "provision"):
            create_or_provision(args, config, home, runner)
        elif args.action == "destroy":
            destroy(args, config, home, runner)
        elif args.action == "check":
            check(args, config, home, runner)
        else:
            ssh(args, config, home, runner)

    except Exception as exc:
        if runner.last_log:
            runner.log = runner.last_log
            try:
                runner.save_output(failure_line(exc))
            except OSError:
                pass
        else:
            print("Log: none (read-only check or preflight; no persistent attempt started).", file=sys.stderr)
        raise


def failure_line(exc):
    location = getattr(exc, "progress_path", None)
    return f"FAIL [{location}]: {brief(exc)}" if location else f"FAIL: {brief(exc)}"


if __name__ == "__main__":
    try:
        main()
    except Failure as exc:
        if not getattr(exc, "reported", False):
            print(failure_line(exc), file=sys.stderr)
        raise SystemExit(exc.code)
    except Exception as exc:
        if not getattr(exc, "reported", False):
            print(failure_line(exc), file=sys.stderr)
        raise SystemExit(1)
