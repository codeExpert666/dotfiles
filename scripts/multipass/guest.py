#!/usr/bin/env python3
"""Temporary guest-side helper, transferred and invoked through Multipass exec."""

import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tempfile
import time


HOME = Path("/home/ubuntu")
REPO = HOME / ".dotfiles"
WORKSPACE = HOME / "workspace"
MARKER = Path("/var/lib/dotfiles-multipass/instance.json")
REQUIRED = ("scripts/bootstrap.sh", "scripts/deploy.sh", "scripts/doctor.sh")


def command(argv, *, cwd=None, capture=False, env=None):
    try:
        result = subprocess.run(argv, cwd=cwd, env=env, text=True,
                                stdout=subprocess.PIPE if capture else None,
                                check=True)
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"{argv[0]} failed (exit {exc.returncode})") from exc
    return result.stdout.strip() if capture else None


def clean_env():
    return {"HOME": str(HOME), "USER": "ubuntu", "LOGNAME": "ubuntu",
            "PATH": f"{HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "GIT_TERMINAL_PROMPT": "0"}


def require_ubuntu():
    user = pwd.getpwnam("ubuntu")
    if os.geteuid() != user.pw_uid or HOME.stat().st_uid != user.pw_uid:
        raise ValueError("guest operation must run as the ubuntu user with its own HOME")


def ensure_owned_directory(path, *, create=False):
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ValueError(f"expected a real directory: {path}")
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    if path.exists() and path.stat().st_uid != pwd.getpwnam("ubuntu").pw_uid:
        raise ValueError(f"directory belongs to another user: {path}")


def git(*argv, cwd=None, capture=False):
    return command(["git", *argv], cwd=cwd or REPO, capture=capture, env=clean_env())


def verify_checkout(ref):
    kind = git("cat-file", "-t", ref, capture=True)
    if kind != "commit":
        raise ValueError(f"requested object is {kind}, not a commit")
    actual = git("rev-parse", "HEAD", capture=True)
    if actual != ref:
        raise ValueError(f"checkout mismatch: {actual} != {ref}")
    for relative in REQUIRED:
        if not (REPO / relative).is_file():
            raise ValueError(f"required repository entry missing: {relative}")
    return actual


def repository(url, ref):
    require_ubuntu()
    ensure_owned_directory(WORKSPACE, create=True)
    if REPO.is_symlink():
        raise ValueError("dotfiles repository must not be a symlink")
    if not REPO.exists():
        staging = Path(tempfile.mkdtemp(prefix=".dotfiles-fetch-", dir=HOME))
        try:
            checkout = staging / "repo"
            command(["git", "clone", "--origin", "origin", url, str(checkout)],
                    env=clean_env())
            kind = git("cat-file", "-t", ref, cwd=checkout, capture=True)
            if kind != "commit":
                raise ValueError("remote object is not a commit")
            git("checkout", "--detach", ref, cwd=checkout)
            if git("rev-parse", "HEAD", cwd=checkout, capture=True) != ref:
                raise ValueError("detached checkout does not match requested commit")
            for relative in REQUIRED:
                if not (checkout / relative).is_file():
                    raise ValueError(f"required repository entry missing: {relative}")
            if REPO.exists() or REPO.is_symlink():
                raise ValueError("dotfiles target appeared while cloning")
            checkout.rename(REPO)
        finally:
            shutil.rmtree(staging)
    else:
        ensure_owned_directory(REPO)
        if not (REPO / ".git").is_dir():
            raise ValueError("existing dotfiles path is not a normal Git checkout")
        origin = git("remote", "get-url", "origin", capture=True)
        if origin != url:
            raise ValueError(f"existing dotfiles origin differs: {origin}")
        dirty = git("status", "--porcelain", "--untracked-files=all", capture=True)
        if dirty:
            raise ValueError(f"dotfiles checkout contains user changes:\n{dirty}")
        if git("rev-parse", "HEAD", capture=True) != ref:
            git("fetch", "origin")
            kind = git("cat-file", "-t", ref, capture=True)
            if kind != "commit":
                raise ValueError("requested commit is unavailable from origin")
            git("checkout", "--detach", ref)
    actual = verify_checkout(ref)
    print(json.dumps({"current_ref": actual, "workspace": str(WORKSPACE)}))


def bootstrap(mode):
    require_ubuntu()
    ensure_owned_directory(REPO)
    if mode == "apply":
        lock = HOME / ".local/state/dotfiles-bootstrap/lock/pid"
        if lock.exists():
            try:
                pid = int(lock.read_text().strip())
                os.kill(pid, 0)
            except (OSError, ValueError):
                raise ValueError(f"abandoned guest bootstrap lock needs inspection: {lock}")
            raise ValueError(f"guest bootstrap is still running as PID {pid}")
    command(["bash", "scripts/bootstrap.sh", "--dry-run" if mode == "preview" else "--apply",
             "--profile", "server"], cwd=REPO, env=clean_env())


def packages_ready():
    if os.geteuid() != 0:
        raise ValueError("package readiness probe requires root")
    deadline = time.monotonic() + 600
    locks = ["/var/lib/dpkg/lock", "/var/lib/dpkg/lock-frontend", "/var/lib/apt/lists/lock"]
    while shutil.which("fuser") and time.monotonic() < deadline:
        held = subprocess.run(["fuser", *locks], capture_output=True).returncode == 0
        if not held:
            break
        time.sleep(5)
    else:
        if shutil.which("fuser"):
            raise ValueError("apt/dpkg locks remained held for ten minutes")
    audit = command(["dpkg", "--audit"], capture=True)
    if audit:
        raise ValueError(f"unfinished dpkg configuration:\n{audit}")


def probe():
    if os.geteuid() != 0:
        raise ValueError("guest identity probe requires root")
    marker = json.loads(MARKER.read_text()) if MARKER.is_file() else None
    release = {}
    for line in Path("/etc/os-release").read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            release[key] = value.strip('"')
    result = {
        "marker": marker,
        "machine_id": Path("/etc/machine-id").read_text().strip(),
        "cloud_instance_id": command(["cloud-init", "query", "instance_id"], capture=True),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "reboot_required": Path("/var/run/reboot-required").exists(),
        "os_id": release.get("ID"), "os_version": release.get("VERSION_ID"),
        "arch": command(["uname", "-m"], capture=True),
        "kernel": command(["uname", "-r"], capture=True),
        "ubuntu_home": str(pwd.getpwnam("ubuntu").pw_dir),
        "ubuntu_shell": str(pwd.getpwnam("ubuntu").pw_shell),
    }
    print(json.dumps(result))


def finalize(name, email):
    if os.geteuid() != 0:
        raise ValueError("login shell configuration requires root")
    zsh = shutil.which("zsh")
    if not zsh:
        raise ValueError("bootstrap did not install Zsh")
    if pwd.getpwnam("ubuntu").pw_shell != zsh:
        command(["usermod", "-s", zsh, "ubuntu"])
    if name or email:
        if not name or not email:
            raise ValueError("both Git name and email are required")
        config = HOME / ".config/git/config"
        if config.is_symlink() or not config.is_file():
            raise ValueError("deployed personal Git config is missing or not a regular file")
        if "config.shared" not in config.read_text():
            raise ValueError("Git config is missing its shared include")
        for field, value in (("user.name", name), ("user.email", email)):
            command(["sudo", "-n", "-u", "ubuntu", "env", "-i", *[f"{k}={v}" for k, v in clean_env().items()],
                     "git", "config", "--file", str(config), field, value])


def runtime():
    require_ubuntu()
    command(["bash", "scripts/doctor.sh", "--runtime",
             *[part for module in ("environment", "deployment", "dependencies", "zsh", "git",
                                   "lazygit", "nvim", "starship", "atuin", "shuck", "vim", "state")
             for part in ("--only", module)]], cwd=REPO, env=clean_env())


def versions():
    require_ubuntu()
    specs = (("git", "git", "--version"), ("zsh", "zsh", "--version"),
             ("nvim", "nvim", "--version"), ("node", "node", "--version"),
             ("npm", "npm", "--version"), ("go", "go", "version"),
             ("java", "java", "-version"), ("javac", "javac", "-version"),
             ("maven", "mvn", "-version"))
    found = {}
    for label, *argv in specs:
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=15,
                                    env=clean_env(), cwd=HOME)
            found[label] = (result.stdout or result.stderr).splitlines()[:3]
            if result.returncode:
                found[label].append(f"exit {result.returncode}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            found[label] = [f"unavailable: {exc}"]
    logs = sorted((HOME / ".local/state/dotfiles-bootstrap").glob("run.*"),
                  key=lambda path: path.stat().st_mtime, reverse=True)
    print(json.dumps({"tools": found, "bootstrap_log": str(logs[0]) if logs else None,
                      "current_ref": git("rev-parse", "HEAD", capture=True)}))


def main():
    action, *args = sys.argv[1:]
    if action == "probe" and not args:
        probe()
    elif action == "packages-ready" and not args:
        packages_ready()
    elif action == "repository" and len(args) == 2:
        repository(*args)
    elif action == "bootstrap" and len(args) == 1 and args[0] in ("preview", "apply"):
        bootstrap(args[0])
    elif action == "finalize" and len(args) == 2:
        finalize(*args)
    elif action == "runtime" and not args:
        runtime()
    elif action == "versions" and not args:
        versions()
    else:
        raise ValueError("invalid guest helper action")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"dotfiles-multipass guest: {exc}", file=sys.stderr)
        raise SystemExit(1)
