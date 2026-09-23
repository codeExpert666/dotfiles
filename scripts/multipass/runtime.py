#!/usr/bin/env python3
"""Create, reprovision, inspect and connect to a pinned Multipass dev machine."""

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
ENV = ROOT / "environments/multipass"
DEFAULTS = json.loads((ENV / "defaults.json").read_text())
RELEASES = json.loads((ENV / "host-releases.json").read_text())
TEMPLATE = ENV / "cloud-init.yaml.tmpl"
REF = re.compile(r"^[0-9a-f]{40}$")
NAME = re.compile(r"^[a-z][a-z0-9-]{0,61}[a-z0-9]$")
SIZE = re.compile(r"^([1-9][0-9]*)([GM])$")
KEY = re.compile(r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-[\w-]+) ([A-Za-z0-9+/]+={0,2})(?: .*)?$")
STAGES = ("host", "launch", "cloud-init", "guest", "ssh", "repository", "bootstrap-preview",
          "bootstrap", "finalize", "verified")
SERVER_MODULES = ("environment", "deployment", "dependencies", "zsh", "git", "lazygit",
                  "nvim", "starship", "atuin", "shuck", "vim", "state")


class Runner:
    def __init__(self):
        self.log = None
        self.active = None
        self.last_command = None
        self.last_code = None

    def __call__(self, argv, *, timeout=15, check=True, stream=False):
        argv = [str(part) for part in argv]
        self.last_command = argv[:3]
        self.last_code = None
        if stream:
            process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, bufsize=1, start_new_session=True)
            self.active = process
            output = deque(maxlen=200)

            def forward():
                with (open(self.log, "a") if self.log else open(os.devnull, "w")) as log:
                    for line in process.stdout:
                        output.append(line)
                        log.write(line)
                        log.flush()
                        print(line, end="", file=sys.stderr, flush=True)

            worker = threading.Thread(target=forward, daemon=True)
            worker.start()
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.stop()
                worker.join(timeout=5)
                self.last_code = 124
                raise CommandFailure(argv, 124, "command timed out")
            finally:
                self.active = None
            worker.join(timeout=5)
            result = subprocess.CompletedProcess(argv, code, "".join(output), "")
        else:
            try:
                result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                self.last_code = 124
                raise CommandFailure(argv, 124, "command timed out")
            if self.log:
                with open(self.log, "a") as log:
                    log.write(result.stdout)
                    log.write(result.stderr)
        self.last_code = result.returncode
        if check and result.returncode:
            raise CommandFailure(argv, result.returncode, result.stdout + result.stderr)
        return result

    def stop(self):
        if self.active and self.active.poll() is None:
            try:
                os.killpg(self.active.pid, signal.SIGTERM)
                self.active.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.active.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="action", required=True)
    for action in ("create", "provision", "check", "ssh"):
        sub = commands.add_parser(action)
        sub.add_argument("--name")
        sub.add_argument("--config", type=Path)
        if action in ("create", "provision"):
            modes = sub.add_mutually_exclusive_group()
            modes.add_argument("--dry-run", action="store_true")
            modes.add_argument("--apply", action="store_true")
            sub.add_argument("--ref")
            sub.add_argument("--ssh-public-key", type=Path)
            for option in ("image", "cpus", "memory", "disk", "repo-url"):
                sub.add_argument("--" + option)
        if action == "check":
            sub.add_argument("--runtime", action="store_true")
        if action == "create":
            sub.add_argument("--creation-record", type=Path,
                             help="write an exclusive name/UUID record before a fresh launch")
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
    # The caller keeps this record outside instance state, including after launch failure.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump({"name": declaration["name"], "uuid": declaration["uuid"]}, output)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())


def parse_json_output(output, label):
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


@contextmanager
def lock(path):
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        owner = path / "pid"
        pid = owner.read_text().strip() if owner.is_file() else "unknown"
        raise Failure(f"another run or abandoned lock exists: {path} (PID {pid})")
    try:
        (path / "pid").write_text(f"{os.getpid()}\n")
        yield
    finally:
        (path / "pid").unlink(missing_ok=True)
        path.rmdir()


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
        self.host = Host(runner, RELEASES, home / ".cache/dotfiles-multipass")
        self.helper = None

    def refuse_missing_guest(self, instances):
        recorded = (self.receipt.get("machine_id") or self.receipt.get("cloud_instance_id") or
                    self.receipt.get("ssh_files") or self.receipt.get("last_successful_ref") or
                    any(self.receipt["stages"].get(stage, {}).get("status") == "ok"
                        for stage in ("launch", "cloud-init")))
        if self.name not in instances and recorded:
            raise Failure(f"previously created instance {self.name} is missing; its identity and SSH trust "
                          f"are retained in {self.path}. Use a different --name for a new instance; "
                          "automatic same-name rebuilding is not supported")

    @contextmanager
    def stage(self, label):
        self.path.joinpath("logs").mkdir(mode=0o700, exist_ok=True)
        self.runner.log = self.path / "logs" / f"{label}.log"
        self.receipt["stages"][label] = {"started": time.time(), "status": "running",
                                           "log": str(self.runner.log)}
        save_json(self.receipt_file, self.receipt)
        print(f"RUN: {label}", file=sys.stderr)
        try:
            yield
        except Exception as exc:
            row = self.receipt["stages"][label]
            row.update(status="failed", ended=time.time(), error=str(exc),
                       command=self.runner.last_command, exit_code=self.runner.last_code)
            save_json(self.receipt_file, self.receipt)
            raise
        else:
            self.receipt["stages"][label].update(status="ok", ended=time.time())
            self.receipt["last_successful_stage"] = label
            save_json(self.receipt_file, self.receipt)
            print(f"READY: {label}", file=sys.stderr)
        finally:
            self.runner.log = None

    def m(self, *args, timeout=15, stream=False, check=True):
        return self.runner([self.host.cli, *args], timeout=timeout, stream=stream, check=check)

    def guest(self, action, *args, root=False, timeout=15, stream=False):
        if not self.helper:
            raise Failure("guest helper was not transferred")
        prefix = ["sudo", "-n"] if root else ["env", "-i", "HOME=/home/ubuntu",
                                             "USER=ubuntu", "LOGNAME=ubuntu", "PATH=/usr/local/bin:/usr/bin:/bin"]
        return self.m("exec", "--no-map-working-directory", self.name, "--", *prefix,
                      "python3", self.helper, action, *args, timeout=timeout, stream=stream)

    def transfer_helper(self):
        self.helper = f"/tmp/dotfiles-multipass-{self.declaration['uuid']}.py"
        self.m("transfer", str(HERE / "guest.py"), f"{self.name}:{self.helper}", timeout=120)

    def cleanup_helper(self):
        if self.helper:
            self.m("exec", "--no-map-working-directory", self.name, "--", "rm", "-f",
                   self.helper, check=False)
            self.helper = None

    def verify_guest(self):
        probe = json.loads(self.guest("probe", root=True).stdout)
        declaration = self.declaration
        marker = probe.get("marker") or {}
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

    def cloud_wait(self):
        try:
            result = self.m("exec", "--no-map-working-directory", self.name, "--", "cloud-init",
                            "status", "--wait", "--format", "json",
                            timeout=DEFAULTS["timeouts"]["cloud_init"])
        except CommandFailure:
            self.m("exec", "--no-map-working-directory", self.name, "--", "sudo", "-n",
                   "tail", "-n", "120", "/var/log/cloud-init-output.log", check=False)
            raise
        status = parse_json_output(result.stdout, "cloud-init status")
        extended = status.get("extended_status", status.get("status"))
        if extended != "done" or status.get("errors"):
            raise Failure(f"cloud-init did not finish cleanly: {status}")
        self.receipt["cloud_init"] = status
        save_json(self.receipt_file, self.receipt)

    def reboot_if_required(self, probe):
        if not probe["reboot_required"]:
            return probe
        before = probe["boot_id"]
        self.receipt["reboot_from"] = before
        save_json(self.receipt_file, self.receipt)
        self.m("restart", "--timeout", str(DEFAULTS["timeouts"]["reboot"]), self.name,
               timeout=DEFAULTS["timeouts"]["reboot"] + 30, stream=True)
        deadline = time.monotonic() + DEFAULTS["timeouts"]["reboot"]
        while time.monotonic() < deadline:
            try:
                self.cloud_wait()
                # /tmp 中的助手可能在重启时被清除；验证新 boot ID 前重新传入。
                self.transfer_helper()
                after = self.verify_guest()
                if after["boot_id"] != before:
                    if after["reboot_required"]:
                        raise Failure("guest still requires reboot after one automatic restart")
                    return after
            except (CommandFailure, subprocess.SubprocessError):
                time.sleep(5)
        raise Failure("guest boot ID did not change or management channel did not recover")

    def ssh_config(self, host_info):
        declaration = self.declaration
        setting = SSHConfig(self.home, self.name, declaration["uuid"],
                            Path(declaration["ssh_public_key"]), HERE / "ssh_proxy.py",
                            self.host.cli)
        host_key_output = self.m("exec", "--no-map-working-directory", self.name, "--", "cat",
                                 "/etc/ssh/ssh_host_ed25519_key.pub").stdout.strip()
        if not KEY.fullmatch(host_key_output):
            raise Failure("guest Ed25519 host public key is invalid")
        host_key = " ".join(host_key_output.split()[:2])
        previous = self.receipt.get("ssh_files", {})
        hashes = setting.publish(host_key, previous)
        self.receipt["ssh_files"] = hashes
        self.receipt["host_key_sha256"] = hashlib.sha256(host_key.encode()).hexdigest()
        fingerprint = self.runner(["ssh-keygen", "-lf", str(setting.known), "-E", "sha256"]).stdout
        self.receipt["host_key_fingerprint"] = next(
            (part for part in fingerprint.split() if part.startswith("SHA256:")), None)
        save_json(self.receipt_file, self.receipt)
        result = self.runner(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", self.name,
                              "sh", "-c", "'test \"$(id -un)\" = ubuntu && test \"$HOME\" = /home/ubuntu'"],
                             timeout=45)
        if result.returncode:
            raise Failure("ordinary SSH did not authenticate as ubuntu")
        self.receipt["image_hash"] = host_info.get("image_hash")
        save_json(self.receipt_file, self.receipt)

    def provision(self, ref, git_identity):
        self.transfer_helper()
        try:
            with self.stage("guest"):
                probe = self.verify_guest()
                self.guest("bootstrap-idle")
                self.guest("packages-ready", root=True, timeout=630)
            with self.stage("ssh"):
                with lock(self.base / "ssh.lock"):
                    info = self.host.info(self.name)
                    self.ssh_config(info)
            with self.stage("repository"):
                self.guest("repository", self.declaration["repo_url"], ref,
                           timeout=1200, stream=True)
                self.receipt["current_ref"] = ref
                save_json(self.receipt_file, self.receipt)
            with self.stage("bootstrap-preview"):
                self.guest("bootstrap", "preview", timeout=1200, stream=True)
            with self.stage("bootstrap"):
                self.guest("bootstrap", "apply", timeout=DEFAULTS["timeouts"]["bootstrap"],
                           stream=True)
            with self.stage("finalize"):
                self.guest("finalize", git_identity.get("name", ""), git_identity.get("email", ""),
                           root=True, timeout=120)
            with self.stage("verified"):
                final = self.verify_guest()
                if not final["ubuntu_shell"].endswith("/zsh"):
                    raise Failure("ubuntu login shell was not changed to Zsh")
                self.receipt["last_successful_ref"] = ref
                self.receipt["git_identity_configured"] = bool(git_identity)
                self.receipt["guest"] = final
                self.receipt["development"] = parse_json_output(
                    self.guest("versions", timeout=180).stdout, "guest tool versions")
                save_json(self.receipt_file, self.receipt)
        finally:
            self.cleanup_helper()


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
    host_preflight()
    name = value(args, config, "name", DEFAULTS["name"])
    validate_name(name)
    machine = Machine(home, name, runner)
    saved = machine.declaration
    creation_record = getattr(args, "creation_record", None)
    if creation_record is not None:
        if machine.path.exists() or machine.path.is_symlink():
            raise Failure("--creation-record requires a fresh instance name without saved state")
        if not creation_record.is_absolute() or not creation_record.parent.is_dir() or \
                creation_record.exists() or creation_record.is_symlink():
            raise Failure("--creation-record must be a new absolute file in an existing directory", 2)
    if args.action == "provision" and not saved:
        raise Failure(f"no managed declaration for {name}")
    key_path = value(args, config, "ssh_public_key", saved["ssh_public_key"] if saved else None)
    canonical, fingerprint = public_key(Path(key_path) if key_path else None, runner)
    requested = declaration_from(args, config, saved, canonical, fingerprint)
    if args.action == "provision" and args.ref:
        requested["target_ref"] = args.ref
        validate_declaration(requested)
    require_agent(canonical, runner)
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
    if not observed:
        print("DEFER: Multipass installation, daemon, image and instance checks until --apply", file=sys.stderr)
    else:
        if not observed["qualified"]:
            print(f"PLAN: upgrade Multipass {observed['client']} to at least {RELEASES['minimum']}",
                  file=sys.stderr)
        else:
            machine.host.verify_service()
            print(f"REUSE: Multipass {observed['client']}", file=sys.stderr)
    print(f"PLAN: {args.action} {name}, Ubuntu {requested['image']}, {requested['cpus']} CPU, "
          f"{requested['memory']} RAM, {requested['disk']} disk, ref {requested['target_ref']}",
          file=sys.stderr)
    if not args.apply:
        print("Preview complete; no files or instances changed. DEFER: image availability and guest checks.",
              file=sys.stderr)
        return
    free = shutil.disk_usage(home).free
    minimum = int(SIZE.fullmatch(requested["disk"]).group(1)) * (1024 ** 3 if requested["disk"].endswith("G") else 1024 ** 2)
    if free < minimum + 5 * 1024 ** 3:
        raise Failure("host disk free space is below requested guest disk plus 5 GiB")
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
        # A concurrent invocation may have finished between the preview checks and this lock.
        if load_json(machine.declaration_file) != saved or \
                (load_json(machine.receipt_file) or {"schema": 1, "stages": {}}) != machine.receipt:
            raise Failure("instance state changed during preflight; rerun after inspecting the other run")
        machine.declaration = requested
        save_json(machine.declaration_file, requested)
        machine.receipt["target_ref"] = requested["target_ref"]
        save_json(machine.receipt_file, machine.receipt)
        if creation_record is not None:
            record_creation(creation_record, requested)
        with machine.stage("host"):
            with lock(machine.base / "host-install.lock"):
                observed = machine.host.ensure(observed)
            machine.receipt["host"] = {"macos": platform.mac_ver()[0],
                                       "multipass": observed, "source": machine.host.source,
                                       "runtime_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
            save_json(machine.receipt_file, machine.receipt)
        existing = machine.host.instances()
        if args.action == "create" and name not in existing:
            machine.refuse_missing_guest(existing)
            with machine.stage("launch"):
                machine.host.image(requested["image"])
                cloud = machine.path / "user-data.yaml"
                atomic(cloud, render_cloud(requested, canonical).encode())
                machine.receipt["instances_before_launch"] = sorted(existing)
                save_json(machine.receipt_file, machine.receipt)
                machine.m("launch", requested["image"], "--name", name,
                          "--cpus", str(requested["cpus"]), "--memory", requested["memory"],
                          "--disk", requested["disk"], "--cloud-init", str(cloud),
                          "--timeout", str(DEFAULTS["timeouts"]["cloud_init"]),
                          timeout=DEFAULTS["timeouts"]["cloud_init"] + 60, stream=True)
        if args.action == "create" and machine.receipt["stages"].get("cloud-init", {}).get("status") != "ok":
            with machine.stage("cloud-init"):
                machine.cloud_wait()
                machine.transfer_helper()
                try:
                    probe = machine.verify_guest()
                    machine.reboot_if_required(probe)
                finally:
                    machine.cleanup_helper()
        machine.transfer_helper()
        try:
            machine.verify_guest()
        finally:
            machine.cleanup_helper()
        machine.provision(requested["target_ref"], config.get("git_identity", {}))
    print(f"READY: {name}; SSH alias: ssh {name}", file=sys.stderr)


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
    elif args.action == "check":
        check(args, config, home, runner)
    else:
        ssh(args, config, home, runner)


if __name__ == "__main__":
    try:
        main()
    except Failure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(exc.code)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
