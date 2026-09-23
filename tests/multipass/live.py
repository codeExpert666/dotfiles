"""Sequential native VM acceptance with ownership-checked cleanup."""

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
from runtime import lock
from ssh_config import atomic


REPO = Path(__file__).resolve().parents[2]
ENTRY = REPO / "scripts/multipass.sh"
HOME = Path.home()
STATE = HOME / ".local/state/dotfiles-multipass/instances"
SSH_BASE = HOME / ".ssh/dotfiles-multipass"
INCLUDE_MARKER = "# dotfiles-multipass managed Include\n"


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", required=True, help="remote full commit SHA")
    parser.add_argument("--ssh-public-key", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path)
    return parser.parse_args()


def run(argv, log=None, timeout=None):
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a") as output:
            output.write("$ " + " ".join(map(str, argv)) + "\n")
            output.flush()
            result = subprocess.run(argv, stdout=output, stderr=subprocess.STDOUT,
                                    timeout=timeout, text=True)
    else:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        detail = f"; see {log}" if log else f": {result.stderr[-500:]}"
        raise RuntimeError(f"command failed (exit {result.returncode}){detail}")
    return result.stdout if not log else ""


def instance_marker(name):
    output = run(["multipass", "exec", name, "--", "sudo", "-n", "cat",
                  "/var/lib/dotfiles-multipass/instance.json"])
    return json.loads(output)


def copy_evidence(name, report):
    state = STATE / name
    if state.is_dir():
        shutil.copytree(state, report / "state", dirs_exist_ok=True)
    for label, argv in (
        ("multipass-info", ["multipass", "info", name, "--format", "json"]),
        ("cloud-init", ["multipass", "exec", name, "--", "cloud-init", "status", "--format", "json"]),
        ("cloud-init-output", ["multipass", "exec", name, "--", "sudo", "-n", "cat",
                               "/var/log/cloud-init-output.log"]),
    ):
        try:
            suffix = "json" if label in ("multipass-info", "cloud-init") else "log"
            (report / f"{label}.{suffix}").write_text(run(argv))
        except Exception as exc:
            (report / f"{label}.error").write_text(str(exc) + "\n")
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
    state = STATE / name
    if not state.is_dir():
        return
    declaration = json.loads((state / "declaration.json").read_text())
    copy_evidence(name, report)
    listed = json.loads(run(["multipass", "list", "--format", "json"]))
    entry = next((entry for entry in listed.get("list", []) if entry["name"] == name), None)
    if entry:
        if entry.get("state") != "Running":
            run(["multipass", "start", name], report / "cleanup.log", timeout=600)
        marker = instance_marker(name)
        if marker != {"uuid": declaration["uuid"], "name": name}:
            raise RuntimeError(f"ownership marker mismatch; retained {name} for inspection")
        run(["multipass", "delete", "--purge", name], report / "cleanup.log", timeout=120)
    remove_ssh(name, declaration["uuid"], remove_include)
    shutil.rmtree(state)
    (report / "cleanup-ok.txt").write_text("owned instance and generated host entries removed\n")


def acceptance(name, image, ref, key, report):
    report.mkdir(parents=True)
    begin = time.time()
    create = ["bash", str(ENTRY), "create", "--apply", "--name", name,
              "--image", image, "--ref", ref, "--ssh-public-key", str(key)]
    run(create, report / "create.log", timeout=14400)
    run(["multipass", "exec", name, "--", "sudo", "cloud-init", "schema", "--system"],
        report / "schema.log", timeout=120)
    run(["bash", str(ENTRY), "check", "--name", name, "--runtime"],
        report / "check-runtime.log", timeout=1800)
    verify = ('test "$(id -un)" = ubuntu && test "$HOME" = /home/ubuntu '
              '&& test -d "$HOME/workspace" && test -d "$HOME/.dotfiles" '
              '&& test "$(getent passwd ubuntu | cut -d: -f7)" = "$(command -v zsh)" '
              '&& test "$(timedatectl show -p Timezone --value)" = Asia/Shanghai '
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
    for image, label in (("24.04", "2404"), ("26.04", "2604")):
        name = f"dotfiles-test-{label}-{stamp}"
        report = report_root / label
        print(f"LIVE: Ubuntu {image} as {name}; report {report}", file=sys.stderr)
        try:
            listed = json.loads(run(["multipass", "list", "--format", "json"]))
            if any(entry["name"] == name for entry in listed.get("list", [])):
                raise RuntimeError(f"test instance already exists: {name}")
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
        if failures:
            break
    summary = {"ref": args.ref, "images": ["24.04", "26.04"], "failures": failures}
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
