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
}
PROGRESS_INTERVAL = 30
CONNECT_RETRY_SECONDS = 120
CONNECT_RETRY_INTERVAL = 5
SERVER_MODULES = ("environment", "deployment", "dependencies", "zsh", "git", "lazygit",
                  "nvim", "starship", "atuin", "shuck", "vim", "state")


class Runner:
    def __init__(self):
        self.log = None
        self.active = None
        self.on_wait = None

    def __call__(self, argv, *, timeout=15, check=True, stream=False, heartbeat=None,
                 on_wait=None, show_output=True):
        argv = [str(part) for part in argv]
        if heartbeat is None and timeout >= 60:
            heartbeat = PROGRESS_INTERVAL
        on_wait = on_wait or self.on_wait
        if stream or heartbeat:
            # 长命令由读取线程持续写日志，主线程负责超时、进度提示和信号处理。
            process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, bufsize=1, start_new_session=True)
            self.active = process
            output = deque(maxlen=200) if stream else []
            last_output = [time.monotonic()]
            forwarding_errors = []

            def forward():
                try:
                    with process.stdout, (open(self.log, "a") if self.log else open(os.devnull, "w")) as log:
                        for line in process.stdout:
                            output.append(line)
                            log.write(line)
                            log.flush()
                            last_output[0] = time.monotonic()
                            if stream and show_output:
                                print(line, end="", file=sys.stderr, flush=True)
                except Exception as exc:
                    forwarding_errors.append(exc)

            worker = threading.Thread(target=forward, daemon=True)
            worker.start()
            started = time.monotonic()
            try:
                while True:
                    remaining = timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(argv, timeout)
                    try:
                        code = process.wait(timeout=min(remaining, heartbeat or remaining))
                        break
                    except subprocess.TimeoutExpired:
                        if heartbeat and on_wait and (not stream or not show_output or
                                                      time.monotonic() - last_output[0] >= heartbeat):
                            on_wait(time.monotonic() - started, timeout)
            except subprocess.TimeoutExpired:
                self.stop()
                worker.join(timeout=5)
                raise CommandFailure(argv, 124, "command timed out")
            except BaseException:
                self.stop()
                worker.join(timeout=5)
                raise
            finally:
                self.active = None
            worker.join(timeout=5)
            if worker.is_alive() or forwarding_errors:
                raise Failure(f"command output could not be saved to {self.log}: "
                              f"{forwarding_errors[0] if forwarding_errors else 'forwarder did not finish'}")
            result = subprocess.CompletedProcess(argv, code, "".join(output), "")
        else:
            try:
                result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                raise CommandFailure(argv, 124, "command timed out")
            if self.log:
                with open(self.log, "a") as log:
                    log.write(result.stdout)
                    log.write(result.stderr)
        if check and result.returncode:
            raise CommandFailure(argv, result.returncode, result.stdout + result.stderr)
        return result

    def stop(self):
        if self.active and self.active.poll() is None:
            # 长命令单独建立会话；停止整个进程组，避免留下安装器等子进程。
            try:
                os.killpg(self.active.pid, signal.SIGTERM)
                self.active.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.active.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


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
    identity = data.get("git_identity", {})
    if not isinstance(identity, dict) or set(identity) - {"name", "email"}:
        raise Failure("git_identity must contain only name and email", 2)
    if identity and (not identity.get("name") or not identity.get("email")):
        raise Failure("git_identity requires both name and email", 2)
    return data


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
                                               "network is unreachable")) or \
        "timed out" in output


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
        (held.path / "pid").unlink(missing_ok=True)
        held.path.rmdir()


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
    def attempt(self, action):
        row = {"id": f"{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:12]}",
               "action": action, "started": time.time(), "status": "running", "stages": []}
        self.receipt.setdefault("attempts", []).append(row)
        self.attempt_row = row
        save_json(self.receipt_file, self.receipt)
        try:
            yield
        except Exception as exc:
            row.update(status="failed", ended=time.time(), error=str(exc))
            if getattr(exc, "progress_path", None):
                row["failed_at"] = exc.progress_path
            save_json(self.receipt_file, self.receipt)
            raise
        else:
            row.update(status="ok", ended=time.time())
            save_json(self.receipt_file, self.receipt)
        finally:
            self.attempt_row = None

    def emit(self, line):
        print(line, file=sys.stderr, flush=True)
        if self.runner.log:
            with open(self.runner.log, "a") as log:
                log.write(line + "\n")

    @contextmanager
    def stage(self, label):
        if self.attempt_row is None:
            raise Failure("internal error: stage started outside an attempt")
        logs = self.path / "logs"
        safe_directory(logs, create=True)
        log_dir = logs / self.attempt_row["id"]
        safe_directory(log_dir, create=True)
        self.runner.log = log_dir / f"{label}.log"
        row = {"id": label, "started": time.time(), "status": "running",
               "log": str(self.runner.log), "steps": []}
        # stages 保存各阶段最新结果；attempts 另行保留每次执行的完整历史。
        self.receipt["stages"][label] = row
        self.attempt_row["stages"].append(row)
        self.current_stage = label
        previous_wait_callback = self.runner.on_wait
        self.runner.on_wait = self.wait_update
        started = time.monotonic()
        self.emit(f"STAGE RUN  [{label}] {STAGE_DESCRIPTIONS[label]}; log={self.runner.log}")
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
            self.emit(f"STAGE FAIL [{label}] step={row.get('current_step', 'none')}; "
                      f"elapsed={row['elapsed_seconds']}s; log={self.runner.log}")
            save_json(self.receipt_file, self.receipt)
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
            self.runner.log = None

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
                  (f"; timeout={timeout}s" if timeout is not None else ""))
        try:
            yield
        except Exception as exc:
            if not hasattr(exc, "progress_path"):
                exc.progress_path = name
            command = exc.argv[:3] if isinstance(exc, CommandFailure) else None
            exit_code = exc.returncode if isinstance(exc, CommandFailure) else None
            row.update(status="failed", ended=time.time(), elapsed_seconds=round(time.monotonic() - started, 1),
                       error=str(exc), command=command, exit_code=exit_code)
            self.emit(f"  STEP FAIL [{name}] {description}; elapsed={row['elapsed_seconds']}s")
            if stage:
                save_json(self.receipt_file, self.receipt)
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
        self.emit(f"  STEP WAIT [{name}] command still running; elapsed={int(elapsed)}s; timeout={timeout}s")

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

    def transfer_helper(self):
        self.helper = f"/tmp/dotfiles-multipass-{self.declaration['uuid']}.py"
        self.m("transfer", str(HERE / "guest.py"), f"{self.name}:{self.helper}", timeout=120)

    def cleanup_helper(self):
        if self.helper:
            self.m("exec", "--no-map-working-directory", self.name, "--", "rm", "-f",
                   self.helper, check=False)
            self.helper = None

    def cleanup_helper_on_exit(self):
        if not self.helper:
            return
        primary_error = sys.exc_info()[0] is not None
        label = self.current_stage or "guest-helper"
        self.emit(f"CLEANUP RUN  [{label}] Remove temporary guest helper")
        try:
            self.cleanup_helper()
        except Exception as exc:
            self.emit(f"CLEANUP FAIL [{label}] {exc}")
            if not primary_error:
                raise
        else:
            self.emit(f"CLEANUP OK   [{label}] Temporary guest helper removed")

    def verify_guest(self):
        probe = json.loads(self.guest("probe", root=True).stdout)
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
        self.m("exec", "--no-map-working-directory", self.name, "--", "sudo", "-n", "true")
        self.receipt.update(machine_id=probe["machine_id"], cloud_instance_id=probe["cloud_instance_id"],
                            guest=probe)
        info = self.host.info(self.name)
        if info.get("cpu_count") is not None and int(info["cpu_count"]) != declaration["cpus"]:
            raise Failure("guest CPU count differs from creation declaration")
        resources = self.host.resources(self.name)
        if int(resources["cpus"]) != declaration["cpus"] or \
                any(abs(size_bytes(resources[field]) - size_bytes(declaration[field])) > 1024 ** 2
                    for field in ("memory", "disk")):
            raise Failure(f"Multipass resources differ from creation declaration: {resources}")
        self.receipt["resources"] = resources
        self.receipt["image_hash"] = info.get("image_hash")
        save_json(self.receipt_file, self.receipt)
        return probe

    def cloud_wait(self, timeout=None):
        timeout = timeout or DEFAULTS["timeouts"]["cloud_init"]
        connect_deadline = time.monotonic() + min(CONNECT_RETRY_SECONDS, timeout)
        retries = 0
        while True:
            try:
                result = self.m("exec", "--no-map-working-directory", self.name, "--", "cloud-init",
                                "status", "--wait", "--format", "json", timeout=timeout,
                                heartbeat=PROGRESS_INTERVAL)
                break
            except CommandFailure as exc:
                remaining = connect_deadline - time.monotonic()
                if transient_guest_connection(exc):
                    if remaining > 0:
                        retries += 1
                        self.emit(f"  STEP RETRY [{self.current_stage or 'cloud-init'}/"
                                  f"{self.current_step or 'status'}] guest connection unavailable "
                                  f"({exc.output.strip()[-160:]}); attempt={retries}; "
                                  f"retrying in {min(CONNECT_RETRY_INTERVAL, remaining):.0f}s")
                        time.sleep(min(CONNECT_RETRY_INTERVAL, remaining))
                        continue
                    self.emit(f"  STEP WAIT [{self.current_stage or 'cloud-init'}/"
                              f"{self.current_step or 'status'}] guest connection did not recover "
                              f"within {min(CONNECT_RETRY_SECONDS, timeout)}s")
                else:
                    # 保留原始 cloud-init 错误；只在诊断命令可用时补充日志。
                    self.emit(f"  STEP DIAG [{self.current_stage or 'cloud-init'}/"
                              f"{self.current_step or 'status'}] collect guest cloud-init output if reachable")
                    try:
                        self.m("exec", "--no-map-working-directory", self.name, "--", "sudo", "-n",
                               "tail", "-n", "120", "/var/log/cloud-init-output.log", check=False)
                    except (CommandFailure, OSError):
                        pass
                raise
        status = parse_json_output(result.stdout, "cloud-init status")
        extended = status.get("extended_status", status.get("status"))
        if extended != "done" or status.get("errors"):
            raise Failure(f"cloud-init did not finish cleanly: {status}")
        self.receipt["cloud_init"] = status
        save_json(self.receipt_file, self.receipt)

    def management_snapshot(self):
        name = f"{self.current_stage or 'cloud-init'}/{self.current_step or 'restart'}"
        self.emit(f"  STEP DIAG [{name}] collect read-only Multipass state; each query has a 15s limit")
        for command in ("list", "info"):
            argv = (command, "--format", "json") if command == "list" else \
                (command, self.name, "--format", "json")
            try:
                result = self.m(*argv, timeout=15, check=False)
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

    def reboot_if_required(self, probe):
        if not probe["reboot_required"]:
            self.emit(f"  STEP SKIP [{self.current_stage or 'cloud-init'}/restart] "
                      "guest does not require a reboot")
            return probe
        before = probe["boot_id"]
        self.receipt["reboot_from"] = before
        save_json(self.receipt_file, self.receipt)
        limit = DEFAULTS["timeouts"]["reboot"]
        with self.step("restart", "Wait for Multipass restart to return", timeout=limit + 30):
            try:
                self.m("restart", "--timeout", str(limit), self.name, timeout=limit + 30,
                       stream=True, heartbeat=PROGRESS_INTERVAL, show_output=False)
            except CommandFailure:
                self.management_snapshot()
                raise
        with self.step("reconnect", "Verify guest boot ID and management connection", timeout=limit):
            deadline = time.monotonic() + limit
            last_report = time.monotonic()
            while time.monotonic() < deadline:
                try:
                    remaining = max(1, int(deadline - time.monotonic()))
                    self.cloud_wait(timeout=remaining)
                    # /tmp 中的助手可能在重启时被清除；验证新 boot ID 前重新传入。
                    self.transfer_helper()
                    after = self.verify_guest()
                    if after["boot_id"] != before:
                        if after["reboot_required"]:
                            raise Failure("guest still requires reboot after one automatic restart")
                        return after
                except CommandFailure as exc:
                    if not transient_guest_connection(exc):
                        raise
                now = time.monotonic()
                if now - last_report >= PROGRESS_INTERVAL:
                    self.wait_update(limit - (deadline - now), limit)
                    last_report = now
                time.sleep(min(CONNECT_RETRY_INTERVAL, max(0, deadline - now)))
            raise Failure("guest boot ID did not change or management channel did not recover")

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
                    self.emit("    CHILD BEGIN [bootstrap-preview] Guest bootstrap output follows unchanged")
                    try:
                        self.guest("bootstrap", "preview", timeout=1200, stream=True,
                                   heartbeat=PROGRESS_INTERVAL)
                    finally:
                        self.emit("    CHILD END   [bootstrap-preview] See guest bootstrap log for details")
            with self.stage("bootstrap"):
                limit = DEFAULTS["timeouts"]["bootstrap"]
                with self.step("child", "Run guest bootstrap --apply --profile server", timeout=limit):
                    self.emit("    CHILD BEGIN [bootstrap] Guest bootstrap output follows unchanged")
                    try:
                        self.guest("bootstrap", "apply", timeout=limit, stream=True,
                                   heartbeat=PROGRESS_INTERVAL)
                    finally:
                        self.emit("    CHILD END   [bootstrap] See guest bootstrap log for details")
            with self.stage("finalize"):
                with self.step("account", "Set guest login shell and optional Git identity", timeout=120):
                    self.guest("finalize", git_identity.get("name", ""), git_identity.get("email", ""),
                               root=True, timeout=120)
            with self.stage("verified"):
                with self.step("identity", "Recheck guest identity and Zsh login shell"):
                    final = self.verify_guest()
                    if not final["ubuntu_shell"].endswith("/zsh"):
                        raise Failure("ubuntu login shell was not changed to Zsh")
                with self.step("versions", "Collect installed tool and doctor results", timeout=180):
                    self.receipt["development"] = parse_json_output(
                        self.guest("versions", timeout=180, heartbeat=PROGRESS_INTERVAL).stdout,
                        "guest tool versions")
                self.receipt["last_successful_ref"] = ref
                self.receipt["git_identity_configured"] = bool(git_identity)
                self.receipt["guest"] = final
                save_json(self.receipt_file, self.receipt)
                with self.step("helper-cleanup", "Remove the temporary guest helper"):
                    self.cleanup_helper()
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
            if name in instances and instances[name].get("state") != "Running":
                raise Failure(f"instance {name} is {instances[name].get('state')}; use multipass start {name}")
            if args.action == "provision" and name not in instances:
                raise Failure(f"managed instance {name} is missing")
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
    if not args.apply:
        print("Preview complete; no files or instances changed. DEFER: image availability and guest checks.",
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
    with lock(machine.path / "lock"):
        # 其他调用可能在预检和获取锁之间完成，不能沿用过期的状态快照。
        if load_json(machine.declaration_file) != saved or \
                (load_json(machine.receipt_file) or {"schema": 1, "stages": {}}) != machine.receipt:
            raise Failure("instance state changed during preflight; rerun after inspecting the other run")
        machine.declaration = requested
        save_json(machine.declaration_file, requested)
        machine.receipt["target_ref"] = requested["target_ref"]
        save_json(machine.receipt_file, machine.receipt)
        if creation_record is not None:
            record_creation(creation_record, requested)
        with machine.attempt(args.action):
            with machine.stage("host"):
                with lock(machine.base / "host-install.lock"):
                    observed = machine.host.ensure(observed)
                with machine.step("instances", "Read current Multipass instances"):
                    existing = machine.host.instances()
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
                        with machine.step("status", "Wait for cloud-init to complete cleanly",
                                          timeout=DEFAULTS["timeouts"]["cloud_init"]):
                            machine.cloud_wait()
                        with machine.step("helper", "Transfer the temporary guest helper", timeout=120):
                            machine.transfer_helper()
                        try:
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
            machine.provision(requested["target_ref"], config.get("git_identity", {}))
    print(f"READY: {name}; SSH alias: ssh {name}", file=sys.stderr)


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
              "DEFER: guest identity check until --apply if the VM exists.", file=sys.stderr)
        return

    with lock(machine.path / "lock") as held:
        if load_json(machine.declaration_file) != saved or \
                (load_json(machine.receipt_file) or {"schema": 1, "stages": {}}) != machine.receipt:
            raise Failure("instance state changed during preflight; rerun after inspecting the other run")
        with preflight_progress("STAGE", "destroy", "Verify ownership and retire the managed instance"):
            with preflight_progress("STEP", "destroy/instance", "Verify and permanently remove the VM if present"):
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
            with preflight_progress("STEP", "destroy/ssh", "Remove only this instance's managed SSH files"):
                with lock(machine.base / "ssh.lock"):
                    setting.remove(machine.receipt.get("ssh_files"))
            with preflight_progress("STEP", "destroy/archive", "Archive the old declaration and receipt"):
                safe_directory(archive_dir, create=True)
                if archive.exists() or archive.is_symlink():
                    raise Failure(f"retired state destination appeared during destroy: {archive}")
                save_json(machine.path / "retirement.json", {"schema": 1, "name": name, "uuid": identity,
                                                               "retired_at": time.time(),
                                                               "vm_state_before_apply": state})
                os.rename(machine.path, archive)
                held.path = archive / "lock"
    print(f"RETIRED: {name}; archived state: {archive}", file=sys.stderr)


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
    if instances[name].get("state") != "Running":
        raise Failure(f"instance is stopped; use multipass start {name}")
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
    if name not in instances or instances[name].get("state") != "Running":
        raise Failure(f"instance is not running; use multipass start {name}")
    os.execvp("ssh", ["ssh", name])


def main():
    os.umask(0o077)
    args = parser().parse_args()
    home = Path(os.environ.get("HOME", ""))
    require_default_layout(home)
    config_path = args.config or home / ".config/dotfiles-multipass/config.json"
    config = read_config(config_path)
    runner = Runner()

    def interrupted(signum, _frame):
        runner.stop()
        raise Failure(f"interrupted by signal {signum}; guest tasks may still be running", 128 + signum)

    for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, interrupted)
    if args.action in ("create", "provision"):
        create_or_provision(args, config, home, runner)
    elif args.action == "destroy":
        destroy(args, config, home, runner)
    elif args.action == "check":
        check(args, config, home, runner)
    else:
        ssh(args, config, home, runner)


def failure_line(exc):
    location = getattr(exc, "progress_path", None)
    return f"FAIL [{location}]: {exc}" if location else f"FAIL: {exc}"


if __name__ == "__main__":
    try:
        main()
    except Failure as exc:
        print(failure_line(exc), file=sys.stderr)
        raise SystemExit(exc.code)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(failure_line(exc), file=sys.stderr)
        raise SystemExit(1)
