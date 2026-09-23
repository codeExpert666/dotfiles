"""Read-only host probes and narrowly scoped Multipass installation."""

import hashlib
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time


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
    def __init__(self, run, releases, cache_dir):
        self.run = run
        self.releases = releases
        self.cache_dir = cache_dir
        self.cli = None
        self.source = None

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
        except (ValueError, subprocess.CalledProcessError):
            # An old CLI may have no JSON mode. A broken daemon remains a hard error.
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
            self.verify_service()
            return observed
        if observed:
            if any(row.get("state") == "Running" for row in self.instances().values()):
                raise ValueError("stop existing running Multipass instances before upgrading")
            brew = shutil.which("brew")
            managed = False
            if brew:
                found = self.run([brew, "list", "--cask", "--versions", "multipass"],
                                 timeout=30, check=False)
                managed = found.returncode == 0 and bool(found.stdout.strip())
            if managed:
                self.run([brew, "upgrade", "--cask", "multipass"], timeout=1800, stream=True)
                self.source = "homebrew-cask"
            elif observed["source"] == "official-pkg":
                self.install_pkg()
            else:
                raise ValueError("old Multipass installation source is unknown; inspect it manually")
        else:
            receipt = self.run(["pkgutil", "--pkgs"], check=False)
            if "com.canonical.multipass" in receipt.stdout:
                raise ValueError("Multipass pkg receipts exist but its CLI is missing; repair PATH or installation")
            self.install_pkg()
        self.cli = None
        latest = self.probe()
        if not latest or not latest["qualified"]:
            raise ValueError("Multipass remains below the declared minimum after installation")
        self.wait_service()
        return latest

    def install_pkg(self):
        spec = self.releases["macos_pkg"]
        if version_tuple(spec["version"]) < version_tuple(self.releases["minimum"]):
            raise ValueError("declared installer is below the minimum Multipass version")
        if self.cache_dir.is_symlink():
            raise ValueError(f"installer cache directory is a symlink: {self.cache_dir}")
        self.cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        pkg = self.cache_dir / f"multipass-{spec['version']}.pkg"
        if pkg.is_symlink():
            raise ValueError(f"installer cache path is a symlink: {pkg}")
        if not pkg.exists() or self.sha256(pkg) != spec["sha256"]:
            temporary = self.cache_dir / f".{pkg.name}.{os.getpid()}"
            try:
                self.run(["curl", "--fail", "--location", "--silent", "--show-error",
                          "--max-time", "600", "--output", str(temporary), spec["url"]],
                         timeout=610, stream=True)
                if self.sha256(temporary) != spec["sha256"]:
                    raise ValueError("downloaded Multipass pkg SHA256 mismatch")
                os.replace(temporary, pkg)
            finally:
                temporary.unlink(missing_ok=True)
        self.run(["sudo", "-v"], timeout=120)
        self.run(["sudo", "installer", "-pkg", str(pkg), "-target", "/"],
                 timeout=1800, stream=True)
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
        while time.monotonic() < deadline:
            try:
                self.verify_service()
                return
            except (ValueError, subprocess.CalledProcessError) as exc:
                last = exc
                time.sleep(3)
        raise ValueError(f"Multipass daemon did not become ready: {last}")

    def verify_service(self):
        driver = self.run([self.cli, "get", "local.driver"]).stdout.strip()
        if driver != "qemu":
            raise ValueError(f"unsupported Multipass driver {driver!r}; expected qemu")
        self.instances()

    def instances(self):
        data = json_command(self.run, [self.cli, "list", "--format", "json"])
        return {entry["name"]: entry for entry in data.get("list", [])}

    def info(self, name):
        data = json_command(self.run, [self.cli, "info", name, "--format", "json"])
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

    def resources(self, name):
        result = {}
        for field in ("cpus", "memory", "disk"):
            result[field] = self.run([self.cli, "get", f"local.{name}.{field}"],
                                     timeout=15).stdout.strip()
        return result
