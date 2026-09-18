#!/usr/bin/env python3
"""下载固定资源并准备用户工具、字体和 Neovim 插件；仅使用 Python 标准库。

工具归档先校验并解压到暂存目录，再发布安装目录并逐个更新入口链接。
目录和各链接分别发布；后续失败保留已发布内容，修复问题后可重试。
"""

import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import zipfile


HERE = Path(__file__).resolve().parent


# ===== 命令执行、路径检查与归档解压 =====

def run(args, *, env=None, cwd=None, timeout=600, input=None):
    result = subprocess.run(args, env=env, cwd=cwd, input=input, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{args[0]} exited {result.returncode}: {result.stderr[-6000:]}")
    return result.stdout


def real_directory(path):
    """逐级创建目录，拒绝已有路径中的符号链接。"""
    if path.parent != path:
        real_directory(path.parent)
    if path.is_symlink():
        raise RuntimeError(f"directory must not be a symbolic link: {path}")
    if path.exists():
        if not path.is_dir():
            raise RuntimeError(f"not a directory: {path}")
        return
    path.mkdir(mode=0o700)


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def within(path, root):
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def member_path(root, name):
    relative = PurePosixPath(name)
    if relative.is_absolute() or '..' in relative.parts:
        raise RuntimeError(f"unsafe archive path: {name}")
    target = root.joinpath(*relative.parts)
    if not within(target, root):
        raise RuntimeError(f"archive path escapes extraction directory: {name}")
    return target


def extract(archive, destination, kind, binary_name):
    """解压普通文件和目录；tar 链接必须指向本次解压目录内部。"""
    if kind == '7z':
        # 解压前检查条目路径并拒绝链接；解压后只允许普通文件和目录。
        # 字体筛选和许可文件保留由后续字体安装阶段完成。
        listing = run(['7zz', 'l', '-slt', '-ba', '--', str(archive)])
        for block in listing.strip().split('\n\n'):
            fields = dict(line.split(' = ', 1) for line in block.splitlines() if ' = ' in line)
            member_path(destination, fields['Path'])
            if any('Link' in key for key in fields) or 'l' in fields.get('Attributes', '').split(' ')[-1][:1]:
                raise RuntimeError('links are not supported in font archives')
        run(['7zz', 'x', '-y', '-o' + str(destination), '--', str(archive)], timeout=300)
        for path in destination.rglob('*'):
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise RuntimeError(f'unexpected extracted font entry: {path}')
    elif kind == 'gzip':
        with gzip.open(archive, 'rb') as src, (destination / binary_name).open('wb') as dst:
            shutil.copyfileobj(src, dst)
        (destination / binary_name).chmod(0o755)
    elif zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as source:
            for entry in source.infolist():
                path = member_path(destination, entry.filename)
                if stat.S_ISLNK(entry.external_attr >> 16):
                    raise RuntimeError(f"unexpected ZIP symlink: {entry.filename}")
                if entry.is_dir():
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with source.open(entry) as src, path.open('xb') as dst:
                        shutil.copyfileobj(src, dst)
                    path.chmod(0o755 if (entry.external_attr >> 16) & 0o111 else 0o644)
    else:
        links = []
        with tarfile.open(archive, 'r:*') as source:
            for entry in source:
                path = member_path(destination, entry.name)
                if entry.isdir():
                    path.mkdir(parents=True, exist_ok=True)
                elif entry.isfile():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with source.extractfile(entry) as src, path.open('xb') as dst:
                        shutil.copyfileobj(src, dst)
                    path.chmod(0o755 if entry.mode & 0o111 else 0o644)
                elif entry.issym() or entry.islnk():
                    links.append((path, entry))
                else:
                    raise RuntimeError(f"unexpected archive member: {entry.name}")
            # 最后处理链接，避免解压普通文件时经已有链接写入。
            for path, entry in links:
                target = path.parent / entry.linkname if entry.issym() else member_path(destination, entry.linkname)
                if PurePosixPath(entry.linkname).is_absolute() or not within(target, destination):
                    raise RuntimeError(f"unsafe archive link: {entry.name}")
                path.parent.mkdir(parents=True, exist_ok=True)
                if entry.issym():
                    path.symlink_to(entry.linkname)
                else:
                    shutil.copyfile(target, path)
                    shutil.copymode(target, path)


# ===== 固定资源与字体发布 =====

class Resources:
    def __init__(self, home=None, manifest=None):
        self.home = Path(home or os.environ['HOME']).resolve(strict=True)
        self.manifest = manifest if manifest is not None else json.loads((HERE / 'releases.json').read_text())
        self.root = self.home / '.local/share/dotfiles-bootstrap'
        self.cache = self.home / '.cache/dotfiles-bootstrap'

    def fetch(self, name, key):
        item = self.manifest[name]['assets'][key]
        sha = item['sha256']
        if not re.fullmatch(r'[a-f0-9]{64}', sha) or not item['url'].startswith('https://'):
            raise RuntimeError(f"invalid pinned resource: {name}/{key}")
        real_directory(self.cache)
        cached = self.cache / sha
        if cached.is_symlink() or (cached.exists() and not cached.is_file()):
            raise RuntimeError(f"invalid download cache entry: {cached}")
        if cached.exists() and digest(cached) == sha:
            return cached
        with tempfile.TemporaryDirectory(prefix='.download-', dir=self.cache) as temporary:
            path = Path(temporary) / 'asset'
            print(f"Downloading {name}: {item['url']}", file=sys.stderr, flush=True)
            run(['curl', '--fail', '--silent', '--show-error', '--location', '--proto', '=https',
                 '--proto-redir', '=https', '--retry', '2', '--connect-timeout', '15', '--max-time', '900',
                 '--output', str(path), item['url']], timeout=1000)
            if digest(path) != sha:
                raise RuntimeError(f"SHA256 mismatch for {name}; downloaded content was not installed")
            path.replace(cached)
        return cached

    def check_link(self, destination, target=None):
        if os.path.lexists(destination):
            if destination.is_symlink() and (within(destination, self.root) or
                                             (target is not None and destination.resolve() == target.resolve())):
                return
            raise RuntimeError(f"installation path needs manual review: {destination}; existing content kept")

    def link(self, destination, target):
        self.check_link(destination, target)
        if destination.is_symlink() and destination.resolve() == target.resolve():
            return
        real_directory(destination.parent)
        with tempfile.TemporaryDirectory(prefix='.link-', dir=destination.parent) as temporary:
            staged = Path(temporary) / 'entry'
            staged.symlink_to(target)
            self.check_link(destination, target)
            staged.replace(destination)

    @staticmethod
    def locate(root, suffix):
        matches = [path for path in root.rglob(PurePosixPath(suffix).name)
                   if path.as_posix().endswith('/' + suffix) and within(path, root)]
        if len(matches) != 1 or not matches[0].exists():
            raise RuntimeError(f"expected exactly one payload path {suffix}, found {len(matches)}")
        return matches[0]

    def install(self, name, key):
        item = self.manifest[name]
        for relative in item.get('links', {}):
            self.check_link(self.home / relative)
        real_directory(self.root / name)
        destination = self.root / name / (item['version'] + '-' + key)
        receipt = {'sha256': item['assets'][key]['sha256']}
        if destination.is_symlink():
            raise RuntimeError(f"invalid managed installation: {destination}")
        if destination.exists():
            if json.loads((destination / '.ready.json').read_text()) != receipt:
                raise RuntimeError(f"incomplete or changed managed installation: {destination}")
        else:
            archive = self.fetch(name, key)
            with tempfile.TemporaryDirectory(prefix='.install-', dir=destination.parent) as temporary:
                staged = Path(temporary) / 'payload'
                staged.mkdir()
                extract(archive, staged, item['kind'], name)
                for relative, suffix in item.get('links', {}).items():
                    path = self.locate(staged, suffix)
                    if relative.startswith('.local/bin/'):
                        path.chmod(path.stat().st_mode | 0o111)
                (staged / '.ready.json').write_text(json.dumps(receipt) + '\n')
                staged.rename(destination)
        for relative, suffix in item.get('links', {}).items():
            self.link(self.home / relative, self.locate(destination, suffix))
        print(f"Prepared {name} {item['version']}: {destination}")

    def font_families(self, platform):
        if platform == 'linux':
            output = run(['fc-list', '--format=%{family}\n'], timeout=30)
            return {part.strip() for line in output.splitlines() for part in line.split(',')}
        binary = shutil.which('ghostty')
        if not binary:
            binary = next((str(path) for path in [Path('/Applications/Ghostty.app/Contents/MacOS/ghostty'),
                          self.home / 'Applications/Ghostty.app/Contents/MacOS/ghostty'] if path.is_file()), None)
        if not binary:
            raise RuntimeError('Ghostty font enumerator unavailable')
        return {line.strip() for line in run([binary, '+list-fonts'], timeout=30).splitlines()}

    def font(self, name, platform):
        family = self.manifest[name]['family']
        if family in self.font_families(platform):
            print(f"Font family present: {family}")
            return
        base = self.home / ('.local/share/fonts' if platform == 'linux' else 'Library/Fonts')
        destination = base / 'dotfiles-bootstrap' / name
        real_directory(destination.parent)
        if platform == 'linux' and destination.is_dir() and not destination.is_symlink() and (destination / '.ready.json').is_file():
            receipt = json.loads((destination / '.ready.json').read_text())
            if receipt.get('family') == family:
                run(['fc-cache', '-f', str(destination)], timeout=120)
                if family in self.font_families(platform):
                    print(f"Font cache restored: {family}")
                    return
        if destination.exists() or destination.is_symlink():
            raise RuntimeError(f"font directory exists but family is unavailable: {destination}; inspect it and the font cache")
        archive = self.fetch(name, 'universal')
        with tempfile.TemporaryDirectory(prefix='.font-', dir=destination.parent) as temporary:
            unpacked = Path(temporary) / 'unpacked'
            unpacked.mkdir()
            extract(archive, unpacked, self.manifest[name]['kind'], name)
            selected = Path(temporary) / 'selected'
            selected.mkdir()
            count = 0
            for path in unpacked.rglob('*.ttf'):
                if family in ttf_families(path):
                    shutil.copyfile(path, selected / path.name)
                    (selected / path.name).chmod(0o644)
                    count += 1
            if not count:
                raise RuntimeError(f"archive does not contain expected font family: {family}")
            # 将上游许可文件与筛选出的字体一同发布，保留字体的授权说明。
            for path in unpacked.rglob('*'):
                if path.is_file() and any(word in path.name.lower() for word in ('license', 'ofl', 'copyright')):
                    shutil.copyfile(path, selected / path.name)
            (selected / '.ready.json').write_text(json.dumps({'family': family, 'version': self.manifest[name]['version']}) + '\n')
            selected.rename(destination)
        if platform == 'linux':
            run(['fc-cache', '-f', str(destination)], timeout=120)
        if family not in self.font_families(platform):
            raise RuntimeError(f"font files installed but {family} is not enumerated; refresh the desktop font service and rerun")
        print(f"Font family prepared: {family}")


def ttf_families(path):
    """直接读取 SFNT 字体族名，避免引入第三方字体解析器。"""
    data = path.read_bytes()
    tables = struct.unpack_from('>H', data, 4)[0]
    for i in range(tables):
        tag, _, offset, size = struct.unpack_from('>4sIII', data, 12 + i * 16)
        if tag != b'name':
            continue
        names = data[offset:offset + size]
        _, count, strings = struct.unpack_from('>HHH', names)
        families = set()
        for j in range(count):
            platform, encoding, _, ident, length, position = struct.unpack_from('>HHHHHH', names, 6 + j * 12)
            if ident not in (1, 16) or platform not in (0, 1, 3):
                continue
            raw = names[strings + position:strings + position + length]
            families.add(raw.decode('mac_roman' if platform == 1 else 'utf-16-be', errors='replace'))
        return families
    return set()


# ===== Neovim 配置暂存与管理器准备 =====

def checkout(repo, commit, destination):
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise RuntimeError(f"invalid locked commit: {repo}")
    real_directory(destination.parent)
    if destination.is_symlink():
        raise RuntimeError(f"plugin checkout must be a real directory: {destination}")
    if destination.exists():
        origin = run(['git', '-C', str(destination), 'remote', 'get-url', 'origin']).strip()
        if origin.removesuffix('.git') != ('https://github.com/' + repo):
            raise RuntimeError(f"unexpected plugin origin: {destination}: {origin}")
        if run(['git', '-C', str(destination), 'status', '--porcelain', '--untracked-files=no']).strip():
            raise RuntimeError(f"dirty plugin checkout: {destination}; local changes kept")
        if run(['git', '-C', str(destination), 'rev-parse', 'HEAD']).strip() == commit:
            return
        run(['git', '-C', str(destination), 'fetch', '--depth=1', 'origin', commit])
        run(['git', '-C', str(destination), 'checkout', '--detach', commit])
    else:
        with tempfile.TemporaryDirectory(prefix='.checkout-', dir=destination.parent) as temporary:
            staged = Path(temporary) / 'repo'
            run(['git', 'init', str(staged)])
            run(['git', '-C', str(staged), 'remote', 'add', 'origin', 'https://github.com/' + repo + '.git'])
            run(['git', '-C', str(staged), 'fetch', '--depth=1', 'origin', commit])
            run(['git', '-C', str(staged), 'checkout', '--detach', commit])
            staged.rename(destination)


def stage_nvim(resources, repo, destination):
    config = Path(repo) / 'nvim/.config/nvim'
    lock = json.loads((config / 'lazy-lock.json').read_text())
    data = resources.home / '.local/share/nvim/lazy'
    # 在执行或更新锁定插件前，先拒绝已跟踪文件有改动的检出目录。
    # 此检查不包含未跟踪文件。
    for name in lock:
        if not re.fullmatch(r'[\w.-]+', name):
            raise RuntimeError(f"invalid locked plugin name: {name}")
        path = data / name
        if path.is_symlink():
            raise RuntimeError(f"plugin checkout must be a real directory: {path}")
        if path.exists() and run(['git', '-C', str(path), 'status', '--porcelain', '--untracked-files=no']).strip():
            raise RuntimeError(f"dirty plugin checkout: {path}; local changes kept")
    for name, remote in [('lazy.nvim', 'folke/lazy.nvim'), ('LazyVim', 'LazyVim/LazyVim')]:
        checkout(remote, lock[name]['commit'], data / name)
    shutil.copytree(config, Path(destination) / 'nvim')


# ===== 子命令入口 =====

def main():
    resources = Resources()
    command, *args = sys.argv[1:]
    if command == 'install':
        resources.install(*args)
    elif command == 'fetch':
        source = resources.fetch(args[0], args[1])
        # 调用方提供私有临时目录内的目标路径。
        with Path(args[2]).open('xb') as dst, source.open('rb') as src:
            shutil.copyfileobj(src, dst)
    elif command == 'font':
        resources.font(*args)
    elif command == 'link-fd':
        resources.link(resources.home / '.local/bin/fd', Path(shutil.which('fdfind')))
    elif command == 'link-brew-node':
        for name in ('node', 'npm', 'npx'):
            resources.link(resources.home / '.local/bin' / name, Path('/opt/homebrew/opt/node@24/bin') / name)
    elif command == 'stage-nvim':
        stage_nvim(resources, *args)
    else:
        raise RuntimeError(f"unknown preparation command: {command}")


if __name__ == '__main__':
    for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda signum, frame: sys.exit(128 + signum))
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError, tarfile.TarError,
            zipfile.BadZipFile, struct.error) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        sys.exit(1)
