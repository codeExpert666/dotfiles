"""依次对真实虚拟机验收，并在确认归属后清理。"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/multipass"))
from runtime import load_json, lock, safe_directory
from ssh_config import atomic
from ssh_pty import verify as verify_ssh_pty


REPO = Path(__file__).resolve().parents[2]
ENTRY = REPO / "scripts/multipass.sh"
HOME = Path.home()
STATE = HOME / ".local/state/dotfiles-multipass/instances"
SSH_BASE = HOME / ".ssh/dotfiles-multipass"
INCLUDE_MARKER = "# dotfiles-multipass managed Include\n"


def arguments():
    parser = argparse.ArgumentParser(
        description="Sequential native VM acceptance with ownership-checked cleanup.")
    parser.add_argument("--ref", required=True, help="remote full commit SHA")
    parser.add_argument("--ssh-public-key", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--only-image", choices=("24.04", "26.04"),
                        help="retry one image after a sequential acceptance run")
    return parser.parse_args()


def run(argv, log=None, timeout=None, env=None):
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a") as output:
            output.write("$ " + " ".join(map(str, argv)) + "\n")
            output.flush()
            result = subprocess.run(argv, stdout=output, stderr=subprocess.STDOUT,
                                    timeout=timeout, text=True, env=env)
    else:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
    if result.returncode:
        detail = f"; see {log}" if log else f": {result.stderr[-500:]}"
        raise RuntimeError(f"command failed (exit {result.returncode}){detail}")
    return result.stdout if not log else ""


def instance_marker(name):
    output = run(["multipass", "exec", name, "--", "sudo", "-n", "cat",
                  "/var/lib/dotfiles-multipass/instance.json"], timeout=30)
    return json.loads(output)


def copy_evidence(name, report):
    state = STATE / name
    if state.is_dir():
        shutil.copytree(state, report / "state", dirs_exist_ok=True)
    current = None
    try:
        listing = run(["multipass", "list", "--format", "json"], timeout=15)
        (report / "multipass-list.json").write_text(listing)
        current = next((row.get("state") for row in json.loads(listing).get("list", [])
                        if row.get("name") == name), None)
    except Exception as exc:
        (report / "multipass-list.error").write_text(str(exc) + "\n")
    commands = [("multipass-info", ["multipass", "info", name, "--format", "json"])]
    if current == "Running":
        commands.extend((
            ("cloud-init", ["multipass", "exec", name, "--", "cloud-init", "status", "--format", "json"]),
            ("cloud-init-output", ["multipass", "exec", name, "--", "sudo", "-n", "cat",
                                   "/var/log/cloud-init-output.log"]),
        ))
    else:
        (report / "guest-collection-skipped.txt").write_text(
            f"instance state={current or 'unavailable'}; guest commands could start or alter it\n")
    for label, argv in commands:
        try:
            suffix = "json" if label in ("multipass-info", "cloud-init") else "log"
            (report / f"{label}.{suffix}").write_text(run(argv, timeout=30))
        except Exception as exc:
            (report / f"{label}.error").write_text(str(exc) + "\n")
    if current == "Running":
        try:
            run(["multipass", "transfer", "--recursive",
                 f"{name}:/home/ubuntu/.local/state/dotfiles-bootstrap",
                 str(report / "guest-bootstrap")], report / "collect-bootstrap.log", timeout=120)
        except Exception as exc:
            (report / "guest-bootstrap.error").write_text(str(exc) + "\n")


def remove_ssh(name, uuid, remove_include):
    with lock(STATE.parent / "ssh.lock"):
        _remove_ssh_locked(name, uuid, remove_include)


def _remove_ssh_locked(name, uuid, remove_include):
    host = SSH_BASE / "hosts" / f"{name}.conf"
    known = SSH_BASE / "known_hosts" / name
    # 现存的 SSH 信任文件须匹配本次实例身份，才能移除受管入口。
    if host.exists():
        if not host.is_file() or host.is_symlink() or not host.read_text().startswith(
                f"# dotfiles-multipass instance {uuid}\n"):
            raise RuntimeError(f"managed SSH host entry changed; retaining {host}")
    if known.exists():
        if not known.is_file() or known.is_symlink() or not known.read_text().startswith(
                f"dotfiles-multipass-{uuid} "):
            raise RuntimeError(f"known_hosts entry changed; retaining {known}")
    if host.exists():
        host.unlink()
    if known.exists():
        known.unlink()
    remaining = sorted((SSH_BASE / "hosts").glob("*.conf"))
    aggregate = SSH_BASE / "config"
    if remaining:
        content = "".join(f'Include "{path}"\n' for path in remaining) + "Host *\n"
        atomic(aggregate, content.encode())
    elif aggregate.exists():
        aggregate.unlink()
        root = HOME / ".ssh/config"
        if remove_include and root.is_file() and not root.is_symlink():
            text = root.read_text()
            lines = text.splitlines(keepends=True)
            if len(lines) >= 2 and lines[0] == INCLUDE_MARKER and \
                    lines[1] == f'Include "{aggregate}"\n':
                if root.read_text() != text:
                    raise RuntimeError("SSH config changed during cleanup")
                atomic(root, "".join(lines[2:]).encode())
                backup = root.with_name(f"config.dotfiles-multipass.{uuid}.bak")
                if backup.is_file() and not backup.is_symlink() and backup.read_bytes() == root.read_bytes():
                    backup.unlink()
        proxy = HOME / ".local/share/dotfiles-multipass/ssh-proxy.py"
        source = REPO / "scripts/multipass/ssh_proxy.py"
        if proxy.is_file() and not proxy.is_symlink() and \
                hashlib.sha256(proxy.read_bytes()).digest() == hashlib.sha256(source.read_bytes()).digest():
            proxy.unlink()


def cleanup(name, report, remove_include):
    # 创建记录位于报告目录；没有它就不能把同名实例认作本次验收的资源。
    registration = load_json(report / "creation.json")
    if registration is None:
        return
    if not isinstance(registration, dict) or registration.get("name") != name or not registration.get("uuid"):
        raise RuntimeError(f"invalid creation record; retaining {name}")
    state = STATE / name
    safe_directory(state)
    if not state.exists():
        raise RuntimeError(f"registered instance state is missing; retaining {name} for inspection")
    with lock(state / "lock"):
        declaration = load_json(state / "declaration.json")
        if not declaration or registration != {"uuid": declaration["uuid"], "name": declaration["name"]}:
            raise RuntimeError(f"creation record does not match instance state; retaining {name}")
        # 删除前留存状态和诊断，并用来宾机标记再次确认虚拟机归属。
        copy_evidence(name, report)
        listed = json.loads(run(["multipass", "list", "--format", "json"]))
        entry = next((entry for entry in listed.get("list", []) if entry["name"] == name), None)
        if entry:
            if entry.get("state") != "Running":
                raise RuntimeError(f"registered instance {name} is {entry.get('state')}; "
                                   "retaining it because cleanup cannot verify guest ownership")
            if instance_marker(name) != registration:
                raise RuntimeError(f"ownership marker mismatch; retained {name} for inspection")
            receipt = load_json(state / "receipt.json") or {}
            for field, command in (("machine_id", ["cat", "/etc/machine-id"]),
                                   ("cloud_instance_id", ["cloud-init", "query", "instance_id"])):
                if receipt.get(field) and run(["multipass", "exec", name, "--", *command],
                                              timeout=30).strip() != receipt[field]:
                    raise RuntimeError(f"{field} mismatch; retained {name} for inspection")
            run(["multipass", "delete", "--purge", name], report / "cleanup.log", timeout=120)
            after = json.loads(run(["multipass", "list", "--format", "json"], timeout=15))
            if any(row.get("name") == name for row in after.get("list", [])):
                raise RuntimeError(f"{name} still exists after delete; retaining host state")
        remove_ssh(name, registration["uuid"], remove_include)
        for child in state.iterdir():
            if child.name != "lock":
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
    # 释放锁后仅删除空目录，不递归清理可能由其他调用新建的内容。
    state.rmdir()
    (report / "cleanup-ok.txt").write_text("owned instance and generated host entries removed\n")


def interrupt_after_first_stop(name, report):
    """Limit the native interruption to this test instance's first successful stop."""
    real_cli = shutil.which("multipass")
    if not real_cli:
        raise RuntimeError("multipass CLI is missing")
    wrapper = report / "multipass"
    wrapper.write_text(
        "#!/usr/bin/env python3\n"
        "import os, signal, subprocess, sys, time\n"
        f"real_cli = {str(Path(real_cli).resolve())!r}\n"
        f"target = {name!r}\n"
        "if len(sys.argv) == 3 and sys.argv[1:] == ['stop', target]:\n"
        "    result = subprocess.run([real_cli, *sys.argv[1:]])\n"
        "    if result.returncode == 0:\n"
        "        os.kill(os.getppid(), signal.SIGTERM)\n"
        "        time.sleep(3)\n"
        "    sys.exit(result.returncode)\n"
        "os.execv(real_cli, [real_cli, *sys.argv[1:]])\n")
    wrapper.chmod(0o700)
    return {**os.environ, "PATH": str(report) + os.pathsep + os.environ["PATH"]}


def assert_first_boot(name, report, rebooted):
    state = STATE / name
    declaration = load_json(state / "declaration.json")
    receipt = load_json(state / "receipt.json")
    operation = receipt.get("reboot_operation") if isinstance(receipt, dict) else None
    if not declaration or not receipt or receipt.get("stages", {}).get("cloud-init", {}).get("status") != "ok":
        raise RuntimeError(f"first boot was not verified for {name}")
    if rebooted:
        if not operation or operation.get("method") != "stop-start" or operation.get("phase") != "verified" or \
                receipt.get("reboot_from") == receipt.get("reboot_to") or not receipt.get("reboot_to"):
            raise RuntimeError(f"first stop/start reboot was not verified for {name}")
        kinds = [event["kind"] for event in operation["events"]]
        if not any(kind in kinds for kind in ("stop-observed", "stop-reconciled")) or \
                not all(kind in kinds for kind in ("start-observed", "verified")):
            raise RuntimeError(f"stop/start state observations are incomplete for {name}")
        if "STEP RUN  [cloud-init/stop]" in (report / "create-resumed.log").read_text():
            raise RuntimeError(f"resumed create repeated the completed stop for {name}")
    elif operation or receipt.get("reboot_from") or receipt.get("reboot_to"):
        raise RuntimeError(f"unexpected reboot operation without controlled interruption for {name}")
    listed = json.loads(run(["multipass", "list", "--format", "json"], timeout=15))
    entry = next((row for row in listed.get("list", []) if row.get("name") == name), None)
    if not entry or entry.get("state") != "Running":
        raise RuntimeError(f"{name} is not Running after first reboot")
    marker = instance_marker(name)
    machine_id = run(["multipass", "exec", name, "--", "cat", "/etc/machine-id"], timeout=30).strip()
    cloud_id = run(["multipass", "exec", name, "--", "cloud-init", "query", "instance_id"],
                   timeout=30).strip()
    boot_id = run(["multipass", "exec", name, "--", "cat", "/proc/sys/kernel/random/boot_id"],
                  timeout=30).strip()
    cloud = json.loads(run(["multipass", "exec", name, "--", "cloud-init", "status", "--format", "json"],
                           timeout=30))
    required = run(["multipass", "exec", name, "--", "sh", "-c",
                    "test -e /var/run/reboot-required && echo true || echo false"], timeout=30).strip()
    assertions = {
        "state_running": entry["state"] == "Running",
        "management_exec": True,
        "marker_matches": marker == {"name": name, "uuid": declaration["uuid"]},
        "machine_id_matches": machine_id == receipt["machine_id"],
        "cloud_instance_id_matches": cloud_id == receipt["cloud_instance_id"],
        "cloud_init_clean": cloud.get("extended_status", cloud.get("status")) == "done" and not cloud.get("errors"),
        "reboot_required_cleared": required == "false",
    }
    if rebooted:
        assertions.update(
            boot_id_changed=boot_id == receipt["reboot_to"] and boot_id != receipt["reboot_from"],
            resume_attempt_recorded=[row.get("status") for row in receipt.get("attempts", [])][:2] == ["failed", "ok"])
    else:
        assertions["saved_boot_id_matches"] = bool(boot_id) and boot_id == receipt.get("guest", {}).get("boot_id")
    filename = "first-reboot-assertions.json" if rebooted else "first-boot-assertions.json"
    (report / filename).write_text(json.dumps(assertions, indent=2) + "\n")
    if not all(assertions.values()):
        raise RuntimeError(f"first boot assertions failed for {name}: {assertions}")


def acceptance(name, image, ref, key, report):
    report.mkdir(parents=True)
    begin = time.time()
    create = ["bash", str(ENTRY), "create", "--apply", "--name", name,
              "--image", image, "--ref", ref, "--ssh-public-key", str(key),
              "--creation-record", str(report / "creation.json")]
    first_error = None
    try:
        run(create, report / "create-interrupted.log", timeout=14400,
            env=interrupt_after_first_stop(name, report))
    except RuntimeError as exc:
        first_error = exc
    receipt = load_json(STATE / name / "receipt.json")
    operation = receipt.get("reboot_operation") if isinstance(receipt, dict) else None
    if first_error:
        if not operation or operation.get("phase") not in ("stop_requested", "stopped"):
            raise RuntimeError(f"first create failed before the controlled stop: {first_error}") from first_error
        # A direct signal yields -15; the public Bash wrapper may report 128 + SIGTERM.
        if not any(code in str(first_error) for code in ("exit -15)", "exit 143)")):
            raise RuntimeError(f"controlled interruption ended unexpectedly: {first_error}")
        listed = json.loads(run(["multipass", "list", "--format", "json"], timeout=15))
        entry = next((row for row in listed.get("list", []) if row.get("name") == name), None)
        if not entry or entry.get("state") != "Stopped":
            raise RuntimeError(f"interrupted {name} is not Stopped; inspect before continuation")
        (report / "interrupted-operation.json").write_text(json.dumps(operation, indent=2) + "\n")
        run(create[:-2], report / "create-resumed.log", timeout=14400)
    assert_first_boot(name, report, rebooted=first_error is not None)
    coverage = {"status": "passed" if first_error else "skipped",
                "reason": "controlled stop and resume verified" if first_error else
                          "guest did not require a reboot; interruption recovery was not exercised"}
    (report / "reboot-coverage.json").write_text(json.dumps(coverage, indent=2) + "\n")
    if not first_error:
        print(f"SKIP: {name} first reboot interruption; {coverage['reason']}", file=sys.stderr)
    run(["multipass", "exec", name, "--", "sudo", "cloud-init", "schema", "--system"],
        report / "schema.log", timeout=120)
    run(["multipass", "exec", name, "--", "sh", "-c",
         'test "$LANG" = C.UTF-8 && test "$(locale charmap)" = UTF-8'],
        report / "locale.log", timeout=120)
    run(["bash", str(ENTRY), "check", "--name", name, "--runtime"],
        report / "check-runtime.log", timeout=1800)
    # Explicit name, real ubuntu HOME, no TERM/TERMINFO overrides or Ghostty
    # shell integration: these entries must have been prepared by bootstrap.
    run(["/usr/bin/ssh", "-o", "BatchMode=yes", name,
         'test "$(id -un)" = ubuntu && test "$HOME" = /home/ubuntu && '
         'env -u TERM -u TERMINFO -u TERMINFO_DIRS infocmp -x xterm-ghostty && '
         'env -u TERMINFO -u TERMINFO_DIRS TERM=xterm-ghostty tput cols'],
        report / "terminfo.log", timeout=30)
    verify_ssh_pty(name, report)
    verify = ('test "$(id -un)" = ubuntu && test "$HOME" = /home/ubuntu '
              '&& test -d "$HOME/workspace" && test -d "$HOME/.dotfiles" '
              '&& test "$(getent passwd ubuntu | cut -d: -f7)" = "$(command -v zsh)" '
              '&& test "$(timedatectl show -p Timezone --value)" = Asia/Shanghai '
              '&& test -n "$JAVA_HOME" && test -x "$JAVA_HOME/bin/java" '
              '&& test "$(command -v java)" = "$JAVA_HOME/bin/java" '
              '&& command -v nvim zsh git node npm go java javac mvn')
    run(["ssh", "-o", "BatchMode=yes", name, "bash -lc " + shlex.quote(verify)],
        report / "ssh.log", timeout=120)
    run(["ssh", "-o", "BatchMode=yes", name,
         "printf sentinel > /home/ubuntu/workspace/dotfiles-multipass-sentinel"],
        report / "sentinel.log", timeout=120)
    run(["bash", str(ENTRY), "provision", "--apply", "--name", name],
        report / "reprovision.log", timeout=14400)
    run(["ssh", "-o", "BatchMode=yes", name,
         'test "$(cat /home/ubuntu/workspace/dotfiles-multipass-sentinel)" = sentinel'],
        report / "sentinel-after.log", timeout=120)
    root = HOME / ".ssh/config"
    if root.read_text().count(INCLUDE_MARKER) != 1:
        raise RuntimeError("managed SSH Include was duplicated on provision")
    (report / "info-before-restart.json").write_text(run(
        ["multipass", "info", name, "--format", "json"]))
    run(["multipass", "stop", name], report / "restart.log", timeout=120)
    run(["multipass", "start", name], report / "restart.log", timeout=600)
    (report / "info-after-restart.json").write_text(run(
        ["multipass", "info", name, "--format", "json"]))
    run(["ssh", "-o", "BatchMode=yes", name, "bash -lc " + shlex.quote(verify)],
        report / "ssh-after-restart.log", timeout=120)
    (report / "duration.txt").write_text(f"{time.time() - begin:.1f} seconds\n")


def main():
    args = arguments()
    if len(args.ref) != 40 or any(char not in "0123456789abcdef" for char in args.ref):
        raise ValueError("--ref must be a complete lowercase commit SHA")
    key = args.ssh_public_key.expanduser().resolve(strict=True)
    stamp = time.strftime("%Y%m%d%H%M%S")
    report_root = args.report_dir or HOME / ".local/state/dotfiles-multipass/reports" / stamp
    report_root = report_root.expanduser().absolute()
    if report_root.exists():
        raise ValueError(f"report directory already exists: {report_root}")
    report_root.mkdir(parents=True, mode=0o700)
    root_config = HOME / ".ssh/config"
    original_include = root_config.is_file() and INCLUDE_MARKER in root_config.read_text()
    def interrupted(signum, _frame):
        raise KeyboardInterrupt(f"signal {signum}")
    for signum in (signal.SIGHUP, signal.SIGTERM):
        signal.signal(signum, interrupted)
    failures = []
    runtime_hash = hashlib.sha256((REPO / "scripts/multipass/runtime.py").read_bytes()).hexdigest()
    attempted = []
    coverage = {}
    # 默认按两个镜像顺序验收；可单独重试失败镜像。每轮仅按本轮创建记录清理。
    images = [("24.04", "2404"), ("26.04", "2604")]
    if getattr(args, "only_image", None):
        images = [entry for entry in images if entry[0] == args.only_image]
    for image, label in images:
        attempted.append(image)
        name = f"dotfiles-test-{label}-{stamp}"
        report = report_root / label
        print(f"LIVE: Ubuntu {image} as {name}; report {report}", file=sys.stderr)
        try:
            listed = json.loads(run(["multipass", "list", "--format", "json"]))
            if any(entry["name"] == name for entry in listed.get("list", [])):
                raise RuntimeError(f"test instance already exists: {name}")
            state = STATE / name
            if state.exists() or state.is_symlink():
                raise RuntimeError(f"test instance state already exists: {state}")
            acceptance(name, image, args.ref, key, report)
        except BaseException as exc:
            failures.append(f"{image}: {exc}")
            report.mkdir(parents=True, exist_ok=True)
            (report / "failure.txt").write_text(str(exc) + "\n")
        finally:
            try:
                cleanup(name, report, not original_include)
            except Exception as exc:
                failures.append(f"{image} cleanup: {exc}")
        coverage[image] = load_json(report / "reboot-coverage.json") or {"status": "not_reached"}
        if failures:
            break
    if hashlib.sha256((REPO / "scripts/multipass/runtime.py").read_bytes()).hexdigest() != runtime_hash:
        failures.append("host runtime changed during acceptance; rerun against one fixed version")
    summary = {"ref": args.ref, "runtime_sha256": runtime_hash, "images": attempted,
               "reboot_coverage": coverage, "failures": failures}
    (report_root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"Live acceptance report: {report_root}", file=sys.stderr)
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    return bool(failures)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
