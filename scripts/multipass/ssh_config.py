"""Preserve user SSH configuration while publishing instance-scoped entries."""

import fnmatch
import glob
import hashlib
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile


SENTINEL = "# dotfiles-multipass managed Include\n"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def quote(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def read_regular(path):
    if path.is_symlink():
        raise ValueError(f"refusing symbolic link: {path}")
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError(f"expected regular file: {path}")
    return path.read_bytes()


def atomic(path, content, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class SSHConfig:
    def __init__(self, home, name, uuid, public_path, proxy_source, multipass_path):
        self.home = home
        self.name = name
        self.uuid = uuid
        self.public_path = public_path
        self.proxy_source = proxy_source
        self.multipass_path = multipass_path
        self.root = home / ".ssh/config"
        self.base = home / ".ssh/dotfiles-multipass"
        self.aggregate = self.base / "config"
        self.host = self.base / "hosts" / f"{name}.conf"
        self.known = self.base / "known_hosts" / name
        self.proxy = home / ".local/share/dotfiles-multipass/ssh-proxy.py"
        self.alias = f"dotfiles-multipass-{uuid}"

    def _root_content(self, existing, aggregate_path=None):
        include = SENTINEL + f"Include {quote(aggregate_path or self.aggregate)}\n"
        source = existing.decode() if existing else ""
        if source.count(SENTINEL) > 1:
            raise ValueError("duplicate managed SSH Include entries")
        if SENTINEL in source:
            lines = source.splitlines(keepends=True)
            for index, line in enumerate(lines):
                if line == SENTINEL:
                    if index + 1 >= len(lines) or not lines[index + 1].lstrip().startswith("Include "):
                        raise ValueError("managed SSH Include marker was edited")
                    lines[index:index + 2] = [include]
                    return "".join(lines)
        return include + source

    def _host_content(self, proxy_path=None, known_path=None):
        proxy = shlex.join([sys.executable, str(proxy_path or self.proxy),
                            self.name, self.multipass_path])
        return (f"# dotfiles-multipass instance {self.uuid}\n"
                f"Host {self.name}\n"
                f"    HostName {self.name}\n"
                "    User ubuntu\n"
                "    Port 22\n"
                f"    IdentityFile {quote(self.public_path)}\n"
                "    IdentitiesOnly yes\n"
                f"    HostKeyAlias {self.alias}\n"
                f"    UserKnownHostsFile {quote(known_path or self.known)}\n"
                "    StrictHostKeyChecking yes\n"
                f"    ProxyCommand {proxy}\n")

    def _other_host_conflicts(self, source, seen=None):
        if seen is None:
            seen = set()
        for line in source.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            directive = parts[0].lower()
            if directive == "match":
                raise ValueError("SSH Match blocks require manual Include placement")
            if directive == "host":
                for pattern in parts[1:]:
                    if pattern != "*" and fnmatch.fnmatchcase(self.name, pattern.lstrip("!")):
                        raise ValueError(f"SSH alias {self.name} conflicts with Host {pattern}")
            if directive == "include":
                for pattern in parts[1:]:
                    expanded = os.path.expanduser(pattern.strip('"'))
                    if not os.path.isabs(expanded):
                        expanded = str(self.home / ".ssh" / expanded)
                    for entry in glob.glob(expanded):
                        path = Path(entry)
                        if path == self.aggregate or path in seen:
                            continue
                        seen.add(path)
                        nested = read_regular(path)
                        if nested is not None:
                            self._other_host_conflicts(nested.decode(), seen)

    def preflight(self, receipt=None):
        ssh_dir = self.home / ".ssh"
        if not ssh_dir.is_dir() or ssh_dir.is_symlink():
            raise ValueError(f"SSH directory must be an existing real directory: {ssh_dir}")
        existing = read_regular(self.root)
        if existing:
            self._other_host_conflicts(existing.decode())
        for path in (self.aggregate, self.host, self.known, self.proxy):
            current = read_regular(path)
            if current is not None and path == self.aggregate:
                host_files = sorted((self.base / "hosts").glob("*.conf"))
                for entry in host_files:
                    read_regular(entry)
                expected = ("".join(f"Include {quote(entry)}\n" for entry in host_files) +
                            "Host *\n").encode()
                if current != expected:
                    raise ValueError("managed SSH aggregate was changed outside this entrypoint")
            if current is not None and path == self.host:
                if not current.decode().startswith(f"# dotfiles-multipass instance {self.uuid}\n"):
                    raise ValueError(f"existing SSH host entry belongs to another instance: {path}")
            if current is not None and path != self.aggregate and receipt and str(path) in receipt:
                if sha256(current) != receipt[str(path)]:
                    raise ValueError(f"managed SSH file was changed outside this entrypoint: {path}")
        return existing

    def publish(self, host_key, receipt=None):
        existing = self.preflight(receipt)
        root_hash = sha256(existing) if existing is not None else None
        self.base.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.base / "hosts").mkdir(mode=0o700, exist_ok=True)
        (self.base / "known_hosts").mkdir(mode=0o700, exist_ok=True)
        self.proxy.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        host_bytes = self._host_content().encode()
        known_bytes = f"{self.alias} {host_key}\n".encode()
        old_known = read_regular(self.known)
        if old_known is not None and old_known != known_bytes:
            raise ValueError("SSH host key changed; inspect instance identity before changing trust")
        old_proxy = read_regular(self.proxy)
        proxy_bytes = self.proxy_source.read_bytes()
        if old_proxy is not None and old_proxy != proxy_bytes and receipt and \
                receipt.get(str(self.proxy)) != sha256(old_proxy):
            raise ValueError("managed SSH proxy was modified")
        temp_dir = Path(tempfile.mkdtemp(prefix=".ssh-check-", dir=self.base))
        try:
            candidate_host = temp_dir / "host.conf"
            candidate_aggregate = temp_dir / "aggregate.conf"
            candidate_root = temp_dir / "root.conf"
            candidate_host.write_bytes(self._host_content(known_path=self.known).encode())
            candidate_aggregate.write_text(f"Include {quote(candidate_host)}\nHost *\n")
            candidate_root.write_text(self._root_content(existing, candidate_aggregate))
            self._validate(candidate_root)
            if existing is not None:
                representatives = {"dotfiles-multipass-example.invalid"}
                for line in existing.decode().splitlines():
                    parts = line.split()
                    if parts and parts[0].lower() == "host":
                        representatives.update(token for token in parts[1:]
                                               if not any(char in token for char in "*!?"))
                for other in representatives:
                    if other == self.name:
                        continue
                    original = self._effective(self.root, other)
                    proposed = self._effective(candidate_root, other)
                    for field in ("user", "hostname", "port", "identityfile", "proxycommand",
                                  "stricthostkeychecking", "userknownhostsfile"):
                        if original.get(field) != proposed.get(field):
                            raise ValueError(f"managed Include would change SSH {field} for {other}")
        finally:
            shutil.rmtree(temp_dir)
        # Recheck immediately before writing; another writer must not be silently overwritten.
        now = read_regular(self.root)
        if (sha256(now) if now is not None else None) != root_hash:
            raise ValueError("SSH config changed concurrently")
        if existing is not None and SENTINEL.encode() not in existing:
            backup = self.root.with_name(f"config.dotfiles-multipass.{self.uuid}.bak")
            if backup.exists() or backup.is_symlink():
                raise ValueError(f"SSH backup path already exists: {backup}")
            atomic(backup, existing)
        atomic(self.proxy, proxy_bytes, 0o700)
        atomic(self.known, known_bytes)
        atomic(self.host, host_bytes)
        host_files = sorted((self.base / "hosts").glob("*.conf"))
        aggregate_bytes = ("".join(f"Include {quote(path)}\n" for path in host_files) +
                           "Host *\n").encode()
        atomic(self.aggregate, aggregate_bytes)
        root_bytes = self._root_content(existing).encode()
        if now != root_bytes:
            atomic(self.root, root_bytes)
        return {str(path): sha256(path.read_bytes()) for path in
                (self.root, self.aggregate, self.host, self.known, self.proxy)}

    def _validate(self, candidate):
        config = self._effective(candidate, self.name)
        if config.get("user") != "ubuntu" or config.get("stricthostkeychecking") != "true":
            raise ValueError("effective SSH config lost managed user or strict host checking")

    @staticmethod
    def _effective(path, name):
        result = subprocess.run(["ssh", "-G", "-F", str(path), name],
                                capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise ValueError(f"SSH config validation failed: {result.stderr.strip()}")
        return dict(line.split(" ", 1) for line in result.stdout.splitlines() if " " in line)
