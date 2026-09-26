"""宿主机只读探测及限定范围的 Multipass 安装。"""

from contextlib import nullcontext
import hashlib
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import shutil
import time

from errors import CommandFailure


STANDARD_CLI = Path("/Library/Application Support/com.canonical.multipass/bin/multipass")
VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)")
SIZE = re.compile(r"^(\d+(?:\.\d+)?)\s*(B|K|KB|KiB|M|MB|MiB|G|GB|GiB)$", re.IGNORECASE)


def version_tuple(value):
    match = VERSION.match(value or "")
    if not match:
        raise ValueError(f"invalid Multipass version: {value!r}")
    return tuple(map(int, match.groups()))


def size_bytes(value):
    match = SIZE.fullmatch(str(value))
    if not match:
        raise ValueError(f"invalid Multipass size: {value!r}")
    exponent = {"b": 0, "k": 1, "kb": 1, "kib": 1, "m": 2, "mb": 2,
                "mib": 2, "g": 3, "gb": 3, "gib": 3}[match.group(2).lower()]
    return int(Decimal(match.group(1)) * 1024 ** exponent)


def json_command(run, argv, timeout=15):
    result = run(argv, timeout=timeout)
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid JSON from {' '.join(map(str, argv[:2]))}: {exc}") from exc
    if data.get("errors") and any(data["errors"]):
        raise ValueError(f"Multipass reported errors: {data['errors']}")
    return data


class Host:
    def __init__(self, run, releases, cache_dir, progress=None, notice=None):
        self.run = run
        self.releases = releases
        self.cache_dir = cache_dir
        self.cli = None
        self.source = None
        self.progress = progress or (lambda *_args, **_kwargs: nullcontext())
        self.notice = notice or (lambda _message: None)

    def probe(self):
        on_path = shutil.which("multipass")
        if not on_path and STANDARD_CLI.exists():
            raise ValueError(f"Multipass exists at {STANDARD_CLI} but is missing from PATH")
        if not on_path:
            return None
        self.cli = os.path.realpath(on_path)
        try:
            data = json_command(self.run, [self.cli, "version", "--format", "json"])
            client = data.get("multipass")
            daemon = data.get("multipassd")
        except (ValueError, CommandFailure):
            # 旧版 CLI 可能不支持 JSON 格式；守护进程故障仍须作为错误上报。
            result = self.run([self.cli, "version"])
            match = re.search(r"multipass\s+(\S+).*?multipassd\s+(\S+)",
                              result.stdout, flags=re.DOTALL)
            if not match:
                raise ValueError("could not read both Multipass client and daemon versions")
            client, daemon = match.groups()
        if not client or not daemon:
            raise ValueError("Multipass client or daemon version missing; inspect the service")
        if client != daemon:
            raise ValueError(f"Multipass client/daemon mismatch: {client} / {daemon}")
        caskroom = any(Path(prefix, "Caskroom/multipass").is_dir()
                       for prefix in ("/opt/homebrew", "/usr/local"))
        self.source = ("homebrew-cask" if caskroom else "official-pkg") \
            if self.cli == str(STANDARD_CLI) else "unknown"
        return {"client": client, "daemon": daemon, "path": self.cli, "source": self.source,
                "qualified": version_tuple(client) >= version_tuple(self.releases["minimum"])}

    def ensure(self, observed):
        if observed and observed["qualified"]:
            with self.progress("service", "Verify the existing Multipass daemon and driver"):
                self.verify_service()
            return observed
        if observed:
            with self.progress("upgrade-safety", "Check running instances and installation source"):
                if any(row.get("state") == "Running" for row in self.instances().values()):
                    raise ValueError("stop existing running Multipass instances before upgrading")
                brew = shutil.which("brew")
                managed = False
                if brew:
                    found = self.run([brew, "list", "--cask", "--versions", "multipass"],
                                     timeout=30, check=False)
                    managed = found.returncode == 0 and bool(found.stdout.strip())
            if managed:
                # 只升级明确由 Homebrew 管理的安装，避免覆盖来源不明的版本。
                with self.progress("upgrade", "Upgrade Multipass through Homebrew", timeout=1800):
                    self.run([brew, "upgrade", "--cask", "multipass"], timeout=1800, stream=True)
                self.source = "homebrew-cask"
            elif observed["source"] == "official-pkg":
                self.install_pkg()
            else:
                with self.progress("source", "Identify the existing Multipass installation source"):
                    raise ValueError("old Multipass installation source is unknown; inspect it manually")
        else:
            with self.progress("receipts", "Check for an existing Multipass package receipt"):
                receipt = self.run(["pkgutil", "--pkgs"], check=False)
                if "com.canonical.multipass" in receipt.stdout:
                    raise ValueError("Multipass pkg receipts exist but its CLI is missing; repair PATH or installation")
            self.install_pkg()
        self.cli = None
        with self.progress("daemon", "Wait for Multipass daemon and qemu driver", timeout=120):
            return self.wait_service()

    def install_pkg(self):
        with self.progress("package", "Validate the official installer and cache"):
            spec = self.releases["macos_pkg"]
            if version_tuple(spec["version"]) < version_tuple(self.releases["minimum"]):
                raise ValueError("declared installer is below the minimum Multipass version")
            if self.cache_dir.is_symlink():
                raise ValueError(f"installer cache directory is a symlink: {self.cache_dir}")
            self.cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            pkg = self.cache_dir / f"multipass-{spec['version']}.pkg"
            if pkg.is_symlink():
                raise ValueError(f"installer cache path is a symlink: {pkg}")
            cached = pkg.exists() and self.sha256(pkg) == spec["sha256"]
        if not cached:
            # 下载先落到临时文件，校验固定摘要后才替换缓存文件。
            temporary = self.cache_dir / f".{pkg.name}.{os.getpid()}"
            try:
                with self.progress("download", "Download the pinned Multipass package", timeout=610):
                    self.run(["curl", "--fail", "--location", "--silent", "--show-error",
                              "--max-time", "600", "--output", str(temporary), spec["url"]],
                             timeout=610, stream=True)
                with self.progress("checksum", "Verify package SHA256 and publish the cache"):
                    if self.sha256(temporary) != spec["sha256"]:
                        raise ValueError("downloaded Multipass pkg SHA256 mismatch")
                    os.replace(temporary, pkg)
            finally:
                temporary.unlink(missing_ok=True)
        else:
            self.notice("  STEP SKIP [host/download] Reuse verified Multipass package cache")
        with self.progress("sudo", "Authorize the official package installer", timeout=120):
            self.run(["sudo", "-v"], timeout=120, interactive=True)
        with self.progress("installer", "Install the official Multipass package", timeout=1800):
            # 与认证共享终端会话以复用 sudo 凭据；凭据失效时立即报告失败。
            self.run(["sudo", "-n", "installer", "-pkg", str(pkg), "-target", "/"],
                     timeout=1800, stream=True, interactive=True)
        self.source = "official-pkg"

    @staticmethod
    def sha256(path):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def wait_service(self):
        deadline = time.monotonic() + 120
        last = None
        next_report = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                latest = self.probe()
                if not latest or not latest["qualified"]:
                    raise ValueError("Multipass remains missing or below the declared minimum after installation")
                self.verify_service()
                return latest
            except CommandFailure as exc:
                last = exc
                if time.monotonic() >= next_report:
                    self.notice("  STEP WAIT [host/daemon] Multipass daemon is still unavailable")
                    next_report = time.monotonic() + 30
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(3, remaining))
        raise ValueError(f"Multipass daemon did not become ready: {last}")

    def verify_service(self):
        driver = self.run([self.cli, "get", "local.driver"]).stdout.strip()
        if driver != "qemu":
            raise ValueError(f"unsupported Multipass driver {driver!r}; expected qemu")
        self.instances()

    def instances(self):
        data = json_command(self.run, [self.cli, "list", "--format", "json"])
        return {entry["name"]: entry for entry in data.get("list", [])}

    def info(self, name, timeout=15):
        data = json_command(self.run, [self.cli, "info", name, "--format", "json"], timeout=timeout)
        entry = data.get("info", {}).get(name)
        if isinstance(entry, list):
            entry = entry[0] if len(entry) == 1 else None
        if not isinstance(entry, dict):
            raise ValueError(f"Multipass info is missing {name}")
        return entry

    def image(self, release):
        data = json_command(self.run, [self.cli, "find", "--format", "json"], timeout=60)
        entry = data.get("images", {}).get(release)
        if not entry or entry.get("os") != "Ubuntu":
            raise ValueError(f"Ubuntu {release} release image is unavailable")
        return entry

    def resources(self, name, timeout=15, budget=None):
        result = {}
        for field in ("cpus", "memory", "disk"):
            result[field] = self.run([self.cli, "get", f"local.{name}.{field}"],
                                     timeout=budget.remaining(timeout) if budget else timeout).stdout.strip()
        return result
