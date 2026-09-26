#!/usr/bin/env -S python3 -B
"""仅复制到临时测试目录中执行的命令替身；禁用字节码以免污染待校验 HOME。"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

name = Path(sys.argv[0]).name
args = sys.argv[1:]
root = Path(os.environ['BOOTSTRAP_TEST_ROOT'])
configuration = root / 'fixture-env.json'
if configuration.is_file():
    os.environ.update(json.loads(configuration.read_text()))


def event(command, values):
    with (root / 'events').open('a') as stream:
        stream.write(json.dumps([command, *values]) + '\n')


def words(key):
    return set(filter(None, os.environ.get(key, '').split(',')))


def expire_sudo(stage):
    # 在明确的检查点使凭据失效，避免依赖真实的过期时间。
    if stage in words('BOOTSTRAP_TEST_SUDO_EXPIRE'):
        (root / 'sudo-valid').unlink(missing_ok=True)
        (root / 'sudo-expired').touch()
        event('sudo-expired', [stage])


def installed_commands(package, installer):
    commands = {'neovim': ['nvim'], 'git-delta': ['delta'], 'node': ['node', 'npm', 'npx'],
                'node@24': ['node', 'npm', 'npx'], 'python': ['python3'], 'ripgrep': ['rg'],
                'fd-find': ['fd'], 'build-essential': ['cc', 'make'], 'xz-utils': ['xz'],
                'ncurses-bin': ['infocmp', 'tic'], 'ncurses': ['infocmp', 'tic'],
                'ewhauser/tap/shuck-cli': ['shuck'], 'sevenzip': ['7zz'], '7zip': ['7zz'],
                'tree-sitter-cli': ['tree-sitter'], 'tree-sitter': [] if installer == 'brew' else ['tree-sitter'],
                'fontconfig': ['fc-list', 'fc-cache'], 'wl-clipboard': ['wl-copy', 'wl-paste']}.get(package, [package])
    for command in commands:
        if installer == 'apt' and command in words('BOOTSTRAP_TEST_APT_OLD'):
            continue
        (root / ('installed-' + command)).touch()
        target = root / 'bin' / command
        if not target.exists():
            if command == 'python3':
                # 所有替身均通过 env python3 启动，因此保留真实解释器。
                target.symlink_to(sys.executable)
            else:
                shutil.copyfile(root / 'fixture.py', target)
                target.chmod(0o755)


def brew_has(package):
    supplied = words('BOOTSTRAP_TEST_BREW_INSTALLED')
    return 'all' in supplied or package in supplied or (root / ('brew-' + package.replace('/', '_'))).exists()


def brew_mark(package):
    (root / ('brew-' + package.replace('/', '_'))).touch()
    installed_commands(package, 'brew')


def brew_trust_path(kind, package):
    kind = 'formula' if kind == 'brew' else kind
    return root / ('brew-trusted-' + kind + '-' + package.replace('/', '_'))


def brewfile_entries(file):
    # 仓库中的 Brewfile 仅使用静态声明及 trusted 布尔选项；替身遇到不支持的语法就报错，
    # 避免静默接受不完整的包清单。
    entries = []
    for line in Path(file).read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        match = re.fullmatch(r'\s*(tap|brew|cask) "([^"]+)"(?:,\s*trusted: (true|false))?(?:\s*#.*)?\s*', line)
        if not match:
            raise RuntimeError('unsupported Brewfile fixture syntax: ' + line)
        kind, package, trusted = match.groups()
        entries.append((kind, package, trusted == 'true'))
    return entries


if name == 'uname':
    print(os.environ.get('BOOTSTRAP_TEST_KERNEL', 'Linux') if args == ['-s'] else os.environ.get('BOOTSTRAP_TEST_ARCH', 'aarch64'))
elif name == 'sw_vers':
    print('26.0')
elif name == 'xcode-select':
    print('/Library/Developer/CommandLineTools')
elif name == 'dpkg-query':
    if args[-1] in words('BOOTSTRAP_TEST_PACKAGES') and not (root / ('apt-' + args[-1])).exists():
        sys.exit(1)
    print('install ok installed', end='')
elif name == 'sudo':
    event(name, args)
    if args == ['-v']:
        failure = os.environ.get('BOOTSTRAP_TEST_FAIL')
        if failure == 'sudo-auth' or (failure == 'sudo-renew' and (root / 'sudo-expired').exists()):
            print('fixture: sudo authentication denied', file=sys.stderr)
            sys.exit(49)
        (root / 'sudo-valid').touch()
    elif args[:1] == ['-n']:
        if not (root / 'sudo-valid').exists() and os.environ.get('BOOTSTRAP_TEST_SUDO_NOPASSWD') != '1':
            print('sudo: a password is required', file=sys.stderr)
            sys.exit(1)
        os.execvp(args[1], args[1:])
    else:
        raise RuntimeError('unexpected sudo invocation: ' + repr(args))
elif name == 'brew':
    event(name, args)
    if args[:2] in (['bundle', 'check'], ['bundle', 'install']):
        file = next(arg.removeprefix('--file=') for arg in args if arg.startswith('--file='))
        entries = brewfile_entries(file)
        if args[1] == 'check':
            sys.exit(0 if all(brew_has(package) for _, package, _ in entries) else 1)
        if os.environ.get('BOOTSTRAP_TEST_FAIL') == 'brew' or (
                os.environ.get('BOOTSTRAP_TEST_FAIL') == 'bundle-desktop' and Path(file).name == 'Brewfile.desktop'):
            sys.exit(42)
        for kind, package, trusted in entries:
            if trusted:
                brew_trust_path(kind, package).touch()
            if kind != 'tap' and '/' in package and not package.startswith('homebrew/'):
                tap = package.rsplit('/', 1)[0]
                if not (brew_trust_path(kind, package).exists() or brew_trust_path('tap', tap).exists()):
                    print(f'Error: Refusing to load {package} from untrusted tap {tap}.', file=sys.stderr)
                    sys.exit(1)
            if not brew_has(package):
                if kind == 'tap':
                    (root / ('brew-' + package.replace('/', '_'))).touch()
                else:
                    brew_mark(package)
            elif kind != 'tap' and '--no-upgrade' not in args:
                (root / 'brew-upgraded-by-bundle').touch()
                brew_mark(package)
    elif args[0] == 'list':
        if not brew_has(args[-1]):
            sys.exit(1)
        print(args[-1], '1.0')
    elif args[0] == 'upgrade':
        if os.environ.get('BOOTSTRAP_TEST_FAIL') == 'brew' or not brew_has(args[-1]):
            sys.exit(42)
        brew_mark(args[-1])
    else:
        raise RuntimeError('unexpected brew invocation: ' + repr(args))
elif name == 'apt-get':
    while args[:1] == ['-o']:
        assert args[1] in ('APT::Color=0', 'Dpkg::Use-Pty=0')
        args = args[2:]
    event(name, args)
    if os.environ.get('BOOTSTRAP_TEST_FAIL') == name:
        sys.exit(42)
    if args[0] == 'update':
        expire_sudo('apt-update')
    elif args[0] == 'install':
        for package in args[1:]:
            if package.startswith('-'):
                continue
            if package.endswith('.deb'):
                package = Path(package).stem
            (root / ('apt-' + package)).touch()
            installed_commands(package, 'apt')
elif name == 'doctor-fixture':
    event('doctor', args)
    sys.exit(41 if os.environ.get('BOOTSTRAP_TEST_FAIL') == 'doctor' else 0)
elif name == 'resources-fixture':
    event('resources', args)
    if os.environ.get('BOOTSTRAP_TEST_FAIL') == args[0]:
        sys.exit(43)
    if args[0] == 'install-go':
        installed_commands('go', 'release')
        installed_commands('gofmt', 'release')
    elif args[0] == 'install-jdk':
        home = Path(os.environ['HOME']) / '.local/share/dotfiles-bootstrap/jdk' / ('jdk-25-fixture-' + args[1])
        bin_directory = home / 'bin'
        bin_directory.mkdir(parents=True, exist_ok=True)
        for command in ('java', 'javac', 'jar', 'javadoc', 'jshell'):
            shutil.copyfile(root / 'fixture.py', bin_directory / command)
            (bin_directory / command).chmod(0o755)
        for command in ('java', 'javac'):
            (root / ('installed-' + command)).touch()
        current = home.parent / 'current'
        if current.is_symlink():
            current.unlink()
        elif current.exists():
            raise RuntimeError('fixture JDK current path conflict')
        current.symlink_to(home)
    elif args[0] == 'install-maven':
        home = Path(os.environ['HOME']) / '.local/share/dotfiles-bootstrap/maven' / ('3.9.99-' + args[1])
        binary = home / 'apache-maven-3.9.99/bin/mvn'
        binary.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / 'fixture.py', binary)
        binary.chmod(0o755)
        (root / 'installed-mvn').touch()
        link = Path(os.environ['HOME']) / '.local/bin/mvn'
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            raise RuntimeError('fixture Maven command path conflict')
        link.symlink_to(binary)
    elif args[0] == 'install':
        installed_commands(args[1], 'release')
        if args[1] == 'antidote':
            entry = Path(os.environ['HOME']) / '.local/share/antidote/antidote.zsh'
            entry.parent.mkdir(parents=True, exist_ok=True)
            entry.write_text('# Antidote 测试夹具。\n')
    elif args[0] == 'fetch':
        Path(args[3]).touch()
elif name == 'nvim' and '--headless' in args:
    event('nvim', args)
    if os.environ.get('BOOTSTRAP_TEST_NVM_PROGRESS'):
        print('@@DOTFILES/1 EVENT RUN: Neovim / Treesitter / install; timeout=600s', flush=True)
        print('@@DOTFILES/1 EVENT WAIT: Neovim / Treesitter / install; elapsed=30s; timeout=600s; active=bash (Compiling parser)',
              flush=True)
    if os.environ.get('BOOTSTRAP_TEST_FAIL') == 'nvim':
        sys.exit(44)
    if os.environ.get('BOOTSTRAP_TEST_FAIL') == 'signal':
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        (root / 'child-pid').write_text(str(child.pid))
        print('NATIVE CHECKPOINT', flush=True)
        (root / 'ready').touch()
        time.sleep(60)
    expire_sudo('nvim')
elif name == 'zsh' and any(arg.endswith('/bootstrap/zsh.zsh') for arg in args):
    event('zsh', args)
    sys.exit(45 if os.environ.get('BOOTSTRAP_TEST_FAIL') == 'zsh' else 0)
elif name == 'cc' and '-x' in args:
    event(name, args)
    if os.environ.get('BOOTSTRAP_TEST_CAPABILITY_FAIL') == 'compiler':
        sys.exit(46)
    output = Path(args[args.index('-o') + 1])
    code = 47 if os.environ.get('BOOTSTRAP_TEST_CAPABILITY_FAIL') == 'executable' else 0
    output.write_text(f'#!/bin/sh\nexit {code}\n')
    output.chmod(0o755)
elif name == 'go':
    # Go 只接受 version 子命令，不能让通用的 --version 替身掩盖探测错误。
    if args != ['version']:
        print('fixture: expected go version', file=sys.stderr)
        sys.exit(2)
    old = words('BOOTSTRAP_TEST_OLD') | words('BOOTSTRAP_TEST_APT_OLD')
    incompatible = name in words('BOOTSTRAP_TEST_STUCK') or (
        name in old and not (root / ('installed-' + name)).exists())
    print('go version go' + ('1.20.14' if incompatible else '1.21.0') + ' linux/arm64')
elif name == 'java':
    old = words('BOOTSTRAP_TEST_OLD') | words('BOOTSTRAP_TEST_APT_OLD')
    managed = '.local/share/dotfiles-bootstrap/jdk/' in str(Path(sys.argv[0]).resolve())
    incompatible = name in words('BOOTSTRAP_TEST_STUCK') or (
        name in old and not managed)
    version = '20.0.2' if incompatible else os.environ.get('BOOTSTRAP_TEST_JAVA_VERSION', '25')
    runtime = os.environ.get('BOOTSTRAP_TEST_JAVA_RUNTIME', str(Path(sys.argv[0]).resolve().parent.parent))
    if args == ['-XshowSettings:properties', '-version']:
        print('Property settings:\n    java.home = ' + runtime, file=sys.stderr)
        print('openjdk version "' + version + '" fixture', file=sys.stderr)
    elif '-cp' in args and args[-1] == 'BootstrapJavaProbe':
        print('java-bootstrap-ready')
    else:
        print('fixture: unexpected java invocation ' + repr(args), file=sys.stderr)
        sys.exit(2)
elif name == 'javac':
    old = words('BOOTSTRAP_TEST_OLD') | words('BOOTSTRAP_TEST_APT_OLD')
    managed = '.local/share/dotfiles-bootstrap/jdk/' in str(Path(sys.argv[0]).resolve())
    incompatible = name in words('BOOTSTRAP_TEST_STUCK') or (
        name in old and not managed)
    version = '20.0.2' if incompatible else os.environ.get(
        'BOOTSTRAP_TEST_JAVAC_VERSION', os.environ.get('BOOTSTRAP_TEST_JAVA_VERSION', '25'))
    if args == ['-version']:
        print('javac ' + version)
    elif '-d' in args:
        output = Path(args[args.index('-d') + 1]) / 'BootstrapJavaProbe.class'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b'fixture class')
    else:
        print('fixture: unexpected javac invocation ' + repr(args), file=sys.stderr)
        sys.exit(2)
elif name == 'mvn':
    if not os.environ.get('JAVA_HOME') or not (Path(os.environ['JAVA_HOME']) / 'bin/java').is_file():
        print('JAVA_HOME is invalid', file=sys.stderr)
        sys.exit(1)
    old = words('BOOTSTRAP_TEST_OLD') | words('BOOTSTRAP_TEST_APT_OLD')
    managed = '.local/share/dotfiles-bootstrap/maven/' in str(Path(sys.argv[0]).resolve())
    incompatible = name in words('BOOTSTRAP_TEST_STUCK') or (
        name in old and not managed)
    if args == ['--version']:
        version = '4.0.0-rc-5' if incompatible else (
            '3.9.99' if managed else os.environ.get('BOOTSTRAP_TEST_MAVEN_VERSION', '3.9.99'))
        print('Apache Maven ' + version + ' (fixture)')
        print('Java version: 25, vendor: Fixture, runtime: ' + str(Path(os.environ['JAVA_HOME']).resolve()))
    elif 'validate' in args:
        required = ('--offline', '--settings', '--global-settings', '--toolchains', '--global-toolchains', '--file')
        if os.environ.get('MAVEN_SKIP_RC') != '1' or not all(option in args for option in required) or not any(
                option.startswith('-Dmaven.repo.local=') for option in args):
            print('fixture: Maven probe isolation is incomplete', file=sys.stderr)
            sys.exit(2)
        for option in ('--toolchains', '--global-toolchains'):
            toolchains = Path(args[args.index(option) + 1])
            if not toolchains.is_file() or '<toolchains ' not in toolchains.read_text():
                print('fixture: Maven toolchains isolation is incomplete', file=sys.stderr)
                sys.exit(2)
        if os.environ.get('BOOTSTRAP_TEST_CAPABILITY_FAIL') == 'maven':
            sys.exit(48)
    else:
        print('fixture: unexpected mvn invocation ' + repr(args), file=sys.stderr)
        sys.exit(2)
elif '--version' in args:
    if name == 'npm' and os.environ.get('BOOTSTRAP_TEST_CAPABILITY_FAIL') == 'npm':
        sys.exit(48)
    old = words('BOOTSTRAP_TEST_OLD') | words('BOOTSTRAP_TEST_APT_OLD')
    if name in words('BOOTSTRAP_TEST_STUCK') or (name in old and not (root / ('installed-' + name)).exists()):
        print('version=0.0.1')
    elif name == 'nvim':
        print('NVIM v0.12.5\nLuaJIT 2.1')
    elif name == 'lazygit':
        print('commit=abc, version=0.65.1, os=linux, git version=2.43.0')
    elif name in ('git', 'stow'):
        os.execv(os.environ['BOOTSTRAP_TEST_REAL_' + name.upper()], [name, *args])
    else:
        print('99.99.99')
elif name in ('git', 'stow'):
    os.execv(os.environ['BOOTSTRAP_TEST_REAL_' + name.upper()], [name, *args])
elif name == 'curl':
    event(name, args)
    sys.exit(90)  # 意外的网络请求必须失败。
else:
    print('bootstrap fixture: unexpected invocation', name, args, file=sys.stderr)
    sys.exit(99)
