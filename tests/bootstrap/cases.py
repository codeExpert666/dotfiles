#!/usr/bin/env python3
"""bootstrap 的离线行为测试：安装编排、应用准备与资源发布。"""
import errno
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
import re
import select
import shlex
from pathlib import Path
import shutil
import signal
import socket
import ssl
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'support'))
from harness import REPO, Process, cleanup_on_exit, run, running, snapshot, terminal
BASH = os.environ.get('DOTFILES_TEST_BASH') or shutil.which('bash')
REAL_TOOLS = {name: shutil.which(name) for name in ('git', 'stow', 'rm', 'rmdir', 'mktemp', 'zsh', 'nvim', 'starship', 'cc', 'npm')}
spec = importlib.util.spec_from_file_location('bootstrap_resources', REPO / 'scripts/bootstrap/resources.py')
resources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resources)


def zsh_fixture(home, root, env):
    manager = home / '.local/share/antidote'
    manager.mkdir(parents=True)
    # 模拟 Antidote 在另一个真实 Zsh 中读取 here-doc 的调用方式。
    # read -d 会访问控制终端；仅用 cat 替身或无终端测试不能复现 SIGTTOU。
    cli = manager / 'cli.zsh'
    cli.write_text("read -rd '' initialization <<'EOS' || true\nfixture initialization\nEOS\n"
                   'case $1 in\nbundle) cat "$TEST_BUNDLE" ;;\n'
                   'path) print -r -- "$ANTIDOTE_HOME/$2" ;;\nesac\n')
    (manager / 'antidote.zsh').write_text('antidote() { zsh -d -f "$TEST_ANTIDOTE_CLI" "$@" }\n')
    cache = home / '.cache/antidote'
    for name in ('zsh-autosuggestions', 'zsh-syntax-highlighting'):
        plugin = cache / 'zsh-users' / name
        plugin.mkdir(parents=True)
        (plugin / (name + '.zsh')).write_text('typeset -g prepared=yes\n')
    manifest = root / 'plugins.txt'
    manifest.write_text('zsh-users/zsh-autosuggestions\nzsh-users/zsh-syntax-highlighting kind:clone\n')
    os.utime(manifest, (1, 1))
    bundle = root / 'bundle.zsh'
    bundle.write_text('source "$ANTIDOTE_HOME/zsh-users/zsh-autosuggestions/zsh-autosuggestions.zsh"\n')
    env.update(TEST_BUNDLE=str(bundle), TEST_ANTIDOTE_CLI=str(cli))
    return manifest, bundle, cache


class Orchestration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.suite = tempfile.TemporaryDirectory(prefix='dotfiles-bootstrap-tests-')
        cls.addClassCleanup(cls.suite.cleanup)
        cls.base = Path(cls.suite.name).resolve()
        cls.repo = cls.base / 'repo with spaces'
        def ignore_checkout_files(directory, names):
            # 根目录的代理挂载不属于夹具；Stow 包内的 .agents 是受管源文件。
            ignored = {'.git', '__pycache__'}
            if Path(directory) == REPO:
                ignored.update({'.agents', '.codex'})
            return ignored.intersection(names)

        shutil.copytree(REPO, cls.repo, symlinks=True, ignore=ignore_checkout_files)
        helper = (REPO / 'tests/bootstrap/mock.py').read_text()
        (cls.repo / 'scripts/bootstrap/resources.py').write_text(helper.replace("name = Path(sys.argv[0]).name", "name = 'resources-fixture'"))
        (cls.repo / 'scripts/doctor.sh').write_text('#!/usr/bin/env bash\nexec python3 -B "${BASH_SOURCE[0]%/*}/doctor-fixture" "$@"\n')
        (cls.repo / 'scripts/doctor-fixture').write_text(helper)
        # 仅替换夹具中的系统元数据，平台选择仍执行实际逻辑。
        common = cls.repo / 'scripts/bootstrap/common.bash'
        common.write_text(common.read_text().replace('done < /etc/os-release',
                                                    'done < "$BOOTSTRAP_TEST_ROOT/os-release"'))
        # 只在隔离副本中改写固定安装前缀；macOS 上仅控制 PATH
        # 无法阻止真实 Homebrew 优先执行。
        for relative in ('scripts/bootstrap/common.bash', 'scripts/bootstrap/macos/install.bash'):
            path = cls.repo / relative
            text = path.read_text()
            assert '/opt/homebrew' in text, relative
            path.write_text(text.replace('/opt/homebrew', '${BOOTSTRAP_TEST_BREW_PREFIX}'))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='case-', dir=self.base)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / 'home with spaces'
        self.home.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.scratch = self.root / 'tmp'
        self.scratch.mkdir()
        source = REPO / 'tests/bootstrap/mock.py'
        helper = source.read_text().replace("root = Path(os.environ['BOOTSTRAP_TEST_ROOT'])",
                                          f"root = Path({str(self.root)!r})")
        (self.root / 'fixture.py').write_text(helper)
        (self.root / 'os-release').write_text('ID=ubuntu\nVERSION_ID=24.04\n')
        commands = {line.split('\t')[0] for line in (REPO / 'scripts/bootstrap/requirements.tsv').read_text().splitlines() if line and not line.startswith('#')}
        commands.discard('python3')
        commands |= {'sudo', 'apt-get', 'dpkg-query', 'brew', 'sw_vers', 'xcode-select', 'uname', 'ghostty', '7zz'}
        for name in commands:
            shutil.copyfile(self.root / 'fixture.py', self.bin / name)
            (self.bin / name).chmod(0o755)
        (self.bin / 'python3').symlink_to(sys.executable)
        # PATH 中的同名 Bash 故意失败，确保执行时沿用测试入口选定的解释器。
        (self.bin / 'bash').write_text('#!/bin/sh\nexit 91\n')
        (self.bin / 'bash').chmod(0o755)
        self.env = {'HOME': str(self.home), 'PATH': str(self.bin) + ':/usr/bin:/bin', 'LC_ALL': 'C.UTF-8',
                    'GIT_CONFIG_NOSYSTEM': '1',
                    'BOOTSTRAP_TEST_ROOT': str(self.root), 'BOOTSTRAP_TEST_BREW_PREFIX': str(self.root / 'homebrew'),
                    **{'BOOTSTRAP_TEST_REAL_' + name.upper(): path for name, path in REAL_TOOLS.items() if path}, 'TERM': 'xterm-256color', 'TMPDIR': str(self.scratch)}

    def prepare_environment(self):
        # 查询命令会使用 env -i 清空环境；替身从私有文件读取测试控制项，
        # 文件路径在复制替身时固定到脚本中。
        (self.root / 'fixture-env.json').write_text(json.dumps(
            {key: value for key, value in self.env.items() if key.startswith('BOOTSTRAP_TEST_')}))

    def invoke(self, *args, success=0):
        self.prepare_environment()
        result = run([BASH, str(self.repo / 'scripts/bootstrap.sh'), *args], env=self.env,
                     cwd=self.root, timeout=60)
        self.assertEqual(result.returncode, success, result.stdout + result.stderr)
        return result

    def mock_shell(self, name, body):
        path = self.bin / name
        if path.is_symlink():
            path.unlink()
        path.write_text('#!/bin/sh\n' + body)
        path.chmod(0o755)
        return path

    def start(self, *args):
        self.prepare_environment()
        # 将输出直接写入日志文件，避免测试等待应用检查点时，
        # 安装进程因管道无人读取而阻塞。
        with (self.root / 'live.log').open('w') as output:
            process = Process([BASH, str(self.repo / 'scripts/bootstrap.sh'), *args],
                              env=self.env, cwd=self.root, stdout=output, stderr=output)
        self.addCleanup(lambda: print('Live command output:\n' + (self.root / 'live.log').read_text()[-8000:]))
        self.addCleanup(process.close)
        return process

    def checkpoint(self, process, name='ready'):
        deadline = time.monotonic() + 30
        while not (self.root / name).exists():
            process.groups()
            self.assertIsNone(process.poll(), (self.root / 'live.log').read_text())
            self.assertLess(time.monotonic(), deadline, (self.root / 'live.log').read_text())
            time.sleep(0.02)

    def events(self):
        path = self.root / 'events'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def macos(self, **overrides):
        self.env.update(BOOTSTRAP_TEST_KERNEL='Darwin', BOOTSTRAP_TEST_ARCH='arm64', **overrides)

    def hide_tools(self, *names):
        # 将 PATH 限制到夹具目录，避免宿主命令使缺失依赖探测意外通过。
        for directory in ('/usr/bin', '/bin'):
            for path in Path(directory).iterdir():
                if path.name not in names and not (self.bin / path.name).exists() and path.is_file():
                    (self.bin / path.name).symlink_to(path)
        for name in names:
            (self.bin / name).unlink(missing_ok=True)
        self.env['PATH'] = str(self.bin)

    def brew_installs(self, events=None):
        return [event for event in (self.events() if events is None else events)
                if event[:3] == ['brew', 'bundle', 'install']]

    def test_help_and_invalid_arguments_are_read_only(self):
        before = snapshot(self.home)
        self.assertIn('Usage:', self.invoke('--help').stdout)
        for args in [[], ['--apply'], ['--profile', 'bad'], ['--profile', 'server', '--apply', '--dry-run'],
                     ['--profile', 'server', '--profile', 'desktop']]:
            self.invoke(*args, success=2)
        self.assertEqual(before, snapshot(self.home))
        self.assertEqual(self.events(), [])

    def test_help_needs_only_bash(self):
        self.env.update(PATH='', HOME='/missing-bootstrap-home')
        self.assertIn('Usage:', self.invoke('--help').stdout)

    def test_dry_run_does_not_install_or_create_home_state(self):
        self.env['BOOTSTRAP_TEST_OLD'] = 'nvim,shfmt,atuin'
        before = snapshot(self.home)
        output = self.invoke('--profile', 'server')
        self.assertEqual(output.stdout, '', 'preview reports must use stderr')
        self.assertIn('PLAN: nvim', output.stderr)
        self.assertIn('dry run completed', output.stderr)
        self.assertEqual(snapshot(self.home), before)
        self.assertEqual(self.events(), [])

    def test_missing_deploy_dependencies_defer_preview(self):
        self.env['BOOTSTRAP_TEST_OLD'] = 'git,stow'
        output = self.invoke('--profile', 'server')
        self.assertIn('DEFER: deployment preview', output.stderr)
        self.assertEqual(self.events(), [])

    def test_go_preview_checks_version_and_reports_missing_runtime(self):
        before = snapshot(self.home)
        output = self.invoke('--profile', 'server').stderr
        self.assertIn('FOUND: go (', output)
        self.assertNotIn('PLAN: go >=', output)
        self.hide_tools('go')
        output = self.invoke('--profile', 'server').stderr
        self.assertIn('PLAN: go >= 1.21.0', output)
        self.assertIn('latest stable Go from go.dev (resolved only during --apply)', output)
        self.assertEqual(snapshot(self.home), before)
        self.assertEqual(self.events(), [])

    def test_incompatible_go_stops_before_deployment(self):
        self.env.update(BOOTSTRAP_TEST_STUCK='go', BOOTSTRAP_TEST_BREW_INSTALLED='all')
        for kernel, arch in (('Linux', 'aarch64'), ('Darwin', 'arm64')):
            with self.subTest(kernel=kernel):
                self.env.update(BOOTSTRAP_TEST_KERNEL=kernel, BOOTSTRAP_TEST_ARCH=arch)
                before = len(self.events())
                output = self.invoke('--apply', '--profile', 'server', success=1).stderr
                self.assertIn('go >= 1.21.0 is still unavailable', output)
                self.assertIn('FAILED phase: software installation', output)
                self.assertFalse((self.home / '.config/nvim/init.lua').exists())
                attempts = self.events()[before:]
                self.assertTrue(any(event[:2] == ['resources', 'install-go'] for event in attempts))
                self.assertFalse(any(event[0] in ('nvim', 'doctor') or
                                     (event[0] == 'resources' and event[1] != 'install-go') for event in attempts))
                self.assertFalse((self.home / '.local/state/dotfiles-bootstrap/lock').exists())

    def test_go_resolution_failure_stops_before_deployment_and_retries(self):
        self.hide_tools('go')
        self.env['BOOTSTRAP_TEST_FAIL'] = 'install-go'
        output = self.invoke('--apply', '--profile', 'server', success=1).stderr
        self.assertIn('install latest stable Go failed', output)
        self.assertIn('FAILED phase: software installation', output)
        self.assertFalse((self.home / '.config/nvim/init.lua').exists())
        self.assertFalse(any(event[0] in ('nvim', 'doctor') for event in self.events()))
        self.assertFalse((self.home / '.local/state/dotfiles-bootstrap/lock').exists())
        self.env.pop('BOOTSTRAP_TEST_FAIL')
        self.invoke('--apply', '--profile', 'server')
        self.assertTrue((self.bin / 'go').exists())
        self.assertTrue(any(event[0] == 'nvim' for event in self.events()))

    def test_old_jdk_and_other_maven_series_install_before_neovim_and_reuse_offline(self):
        self.env['BOOTSTRAP_TEST_OLD'] = 'java,mvn'
        user_bin = self.home / '.local/bin'
        user_bin.mkdir(parents=True)
        for name in ('java', 'javac'):
            shutil.copyfile(self.root / 'fixture.py', user_bin / name)
            (user_bin / name).chmod(0o755)
        user_toolchains = self.home / '.m2/toolchains.xml'
        user_toolchains.parent.mkdir()
        user_toolchains.write_text('<malformed personal toolchains>\n')
        output = self.invoke('--apply', '--profile', 'server').stderr
        self.assertIn('READY: Java compilation/execution and offline Maven validation', output)
        events = self.events()
        jdk = ['resources', 'install-jdk', 'linux-arm64']
        maven = ['resources', 'install-maven', 'linux-arm64']
        self.assertLess(events.index(jdk), events.index(maven))
        self.assertLess(events.index(maven), next(index for index, event in enumerate(events) if event[0] == 'nvim'))
        current = self.home / '.local/share/dotfiles-bootstrap/jdk/current'
        self.assertTrue((current / 'bin/java').is_file())
        self.assertTrue((current / 'bin/javac').is_file())
        self.assertTrue((current / 'bin/jar').is_file(), 'the complete JDK bin directory must remain available')
        self.assertEqual(user_toolchains.read_text(), '<malformed personal toolchains>\n')
        shell = run([REAL_TOOLS['zsh'], '-d', '-c',
                     'print -r -- "$JAVA_HOME"; print -r -- "$commands[java]"; '
                     'print -r -- "$commands[javac]"; print -r -- "$commands[jar]"'], env=self.env, check=True)
        self.assertEqual(shell.stdout.splitlines(), [str(current), str(current / 'bin/java'),
                                                     str(current / 'bin/javac'), str(current / 'bin/jar')])
        login = run([REAL_TOOLS['zsh'], '-d', '-l', '-c', 'print -r -- "$commands[mvn]"'],
                    env=self.env, check=True)
        self.assertEqual(login.stdout.strip(), str(self.home / '.local/bin/mvn'))
        before = len(events)
        self.invoke('--apply', '--profile', 'server')
        self.assertFalse(any(event[:2] in (['resources', 'install-jdk'], ['resources', 'install-maven'])
                             for event in self.events()[before:]))

    def test_stale_java_home_is_replaced_by_managed_jdk(self):
        bad = self.root / 'old-java'
        (bad / 'bin').mkdir(parents=True)
        for name in ('java', 'javac'):
            shutil.copyfile(self.root / 'fixture.py', bad / 'bin' / name)
            (bad / 'bin' / name).chmod(0o755)
        self.env.update(JAVA_HOME=str(bad), BOOTSTRAP_TEST_OLD='java')
        result = self.invoke('--apply', '--profile', 'server')
        self.assertIn('READY: Java compilation/execution', result.stderr)
        self.assertTrue(any(event[:2] == ['resources', 'install-jdk'] for event in self.events()))

    def test_java_home_with_only_java_is_treated_as_incomplete_jre(self):
        jre = self.root / 'jre-only'
        (jre / 'bin').mkdir(parents=True)
        shutil.copyfile(self.root / 'fixture.py', jre / 'bin/java')
        (jre / 'bin/java').chmod(0o755)
        self.env['JAVA_HOME'] = str(jre)
        result = self.invoke('--apply', '--profile', 'server')
        self.assertIn('READY: Java compilation/execution', result.stderr)
        self.assertTrue(any(event[:2] == ['resources', 'install-jdk'] for event in self.events()))

    def test_invalid_java_home_is_explained_in_offline_preview(self):
        bad = self.root / 'invalid-java'
        (bad / 'bin').mkdir(parents=True)
        (bad / 'bin/java').write_text('#!/bin/sh\nexit 0\n')
        self.env['JAVA_HOME'] = str(bad)
        output = self.invoke('--profile', 'server').stderr
        self.assertIn('JAVA_HOME does not contain an executable bin/java', output)
        self.assertEqual(self.events(), [])

    def test_maven_runtime_path_with_comma_is_reused(self):
        home = self.root / 'jdk,with-comma'
        (home / 'bin').mkdir(parents=True)
        for name in ('java', 'javac'):
            shutil.copyfile(self.root / 'fixture.py', home / 'bin' / name)
            (home / 'bin' / name).chmod(0o755)
        self.env['JAVA_HOME'] = str(home)
        output = self.invoke('--profile', 'server').stderr
        self.assertIn('FOUND: mvn', output)
        self.assertNotIn('PLAN: mvn >=', output)
        self.assertEqual(self.events(), [])

    def test_java_four_component_version_and_matching_javac_are_required(self):
        self.env.update(BOOTSTRAP_TEST_JAVA_VERSION='25.0.4.1', BOOTSTRAP_TEST_JAVAC_VERSION='25.0.4.1')
        output = self.invoke('--profile', 'server').stderr
        self.assertIn('FOUND: java', output)
        self.assertIn('FOUND: javac', output)
        self.env['BOOTSTRAP_TEST_JAVAC_VERSION'] = '25.0.4'
        output = self.invoke('--profile', 'server').stderr
        self.assertIn('PLAN: java >= 21.0.0', output)
        self.assertIn('java and javac versions disagree', output)
        self.assertEqual(self.events(), [])

    def test_complete_jdk_21_and_24_are_reused_without_jdk_publication(self):
        for version in ('21', '24.0.2'):
            with self.subTest(version=version):
                jdk = self.root / ('compatible-jdk-' + version)
                (jdk / 'bin').mkdir(parents=True)
                for name in ('java', 'javac'):
                    shutil.copyfile(self.root / 'fixture.py', jdk / 'bin' / name)
                    (jdk / 'bin' / name).chmod(0o755)
                self.env.update(JAVA_HOME=str(jdk), BOOTSTRAP_TEST_JAVA_VERSION=version,
                                BOOTSTRAP_TEST_JAVAC_VERSION=version)
                before = len(self.events())
                output = self.invoke('--apply', '--profile', 'server').stderr
                self.assertIn('READY: Java compilation/execution', output)
                self.assertFalse(any(event[:2] == ['resources', 'install-jdk']
                                     for event in self.events()[before:]))
                self.assertFalse((self.home / '.local/share/dotfiles-bootstrap/jdk/current').exists())

    def test_maven_3_9_prerelease_is_replaced_by_stable_series(self):
        jdk = self.root / 'external-jdk'
        (jdk / 'bin').mkdir(parents=True)
        for name in ('java', 'javac'):
            shutil.copyfile(self.root / 'fixture.py', jdk / 'bin' / name)
            (jdk / 'bin' / name).chmod(0o755)
        self.env['JAVA_HOME'] = str(jdk)
        self.env['BOOTSTRAP_TEST_MAVEN_VERSION'] = '3.9.99-rc-1'
        self.invoke('--apply', '--profile', 'server')
        self.assertTrue(any(event[:2] == ['resources', 'install-maven'] for event in self.events()))

    def test_jdk_resolution_failure_stops_before_deployment_and_retries(self):
        self.env.update(BOOTSTRAP_TEST_OLD='java', BOOTSTRAP_TEST_FAIL='install-jdk')
        output = self.invoke('--apply', '--profile', 'server', success=1).stderr
        self.assertIn('install latest stable JDK 25 failed', output)
        self.assertIn('FAILED phase: software installation', output)
        self.assertFalse((self.home / '.config/nvim/init.lua').exists())
        self.assertFalse(any(event[0] in ('nvim', 'doctor') for event in self.events()))
        self.env.pop('BOOTSTRAP_TEST_FAIL')
        self.invoke('--apply', '--profile', 'server')
        self.assertTrue((self.home / '.local/share/dotfiles-bootstrap/jdk/current/bin/javac').is_file())

    def test_xdg_override_rejected_before_install(self):
        self.env['XDG_CONFIG_HOME'] = str(self.root / 'elsewhere')
        self.invoke('--apply', '--profile', 'server', success=1)
        self.assertEqual(self.events(), [])

    def test_relative_xdg_alias_rejected_before_install(self):
        (self.home / '.config').mkdir()
        self.env['XDG_CONFIG_HOME'] = 'home with spaces/.config'
        self.invoke('--apply', '--profile', 'server', success=1)
        self.assertEqual(self.events(), [])

    def test_git_repository_context_is_rejected_before_any_preparation(self):
        other = self.root / 'other-repository'
        plugin = self.home / '.local/share/nvim/lazy/sample'
        for repository in (other, plugin):
            repository.mkdir(parents=True)
            run([REAL_TOOLS['git'], 'init', '-q', str(repository)], env=self.env, check=True)
            (repository / 'file').write_text('original\n')
            run([REAL_TOOLS['git'], '-C', str(repository), 'add', 'file'], env=self.env, check=True)
            run([REAL_TOOLS['git'], '-C', str(repository), '-c', 'user.name=Test',
                 '-c', 'user.email=test@example.invalid', '-c', 'commit.gpgsign=false',
                 'commit', '-qm', 'fixture'], env=self.env, check=True)
        (plugin / 'file').write_text('keep local plugin edits\n')
        overrides = {
            'GIT_DIR': str(other / '.git'), 'GIT_WORK_TREE': str(other),
            'GIT_COMMON_DIR': str(other / '.git'), 'GIT_INDEX_FILE': str(other / '.git/index'),
            'GIT_OBJECT_DIRECTORY': str(other / '.git/objects'),
            'GIT_ALTERNATE_OBJECT_DIRECTORIES': str(other / '.git/objects'),
            'GIT_SHALLOW_FILE': str(other / '.git/shallow'), 'GIT_GRAFT_FILE': str(other / '.git/info/grafts'),
            'GIT_NAMESPACE': 'other', 'GIT_IMPLICIT_WORK_TREE': '0', 'GIT_PREFIX': 'nested/',
        }
        before = snapshot(self.home), snapshot(other), snapshot(self.repo)
        for name, selected in overrides.items():
            for value in (selected, ''):
                for mode in ('--dry-run', '--apply'):
                    with self.subTest(variable=name, value=value, mode=mode):
                        self.env[name] = value
                        try:
                            result = self.invoke(mode, '--profile', 'server', success=1)
                        finally:
                            self.env.pop(name)
                        self.assertIn(name + ' must be unset', result.stderr)
                        self.assertIn('FAILED phase: preflight', result.stderr)
                        self.assertEqual(result.stdout, '')
                        self.assertNotIn('Bootstrap complete.', result.stderr)
                        self.assertEqual(self.events(), [])
                        self.assertEqual((snapshot(self.home), snapshot(other), snapshot(self.repo)), before)
                        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_git_user_configuration_remains_supported(self):
        config = self.root / 'user.gitconfig'
        config.write_text('[user]\n\tname = Bootstrap Test\n')
        self.env.update(GIT_CONFIG_GLOBAL=str(config), GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_COUNT='1',
                        GIT_CONFIG_KEY_0='http.sslVerify', GIT_CONFIG_VALUE_0='true')
        before = snapshot(self.home), config.read_bytes(), config.stat().st_mtime_ns
        result = self.invoke('--profile', 'server')
        self.assertIn('dry run completed', result.stderr)
        self.assertEqual((snapshot(self.home), config.read_bytes(), config.stat().st_mtime_ns), before)
        self.assertEqual(self.events(), [])

    def test_unsupported_architecture_rejected(self):
        self.env['BOOTSTRAP_TEST_ARCH'] = 'riscv64'
        self.invoke('--profile', 'server', success=1)
        self.assertEqual(self.events(), [])

    def test_full_apply_deploys_and_rerun_reuses_tools(self):
        self.env['BOOTSTRAP_TEST_OLD'] = 'shfmt'
        original = snapshot(self.repo / 'nvim')
        self.invoke('--apply', '--profile', 'server')
        self.assertTrue((self.home / '.config/nvim/init.lua').is_symlink())
        self.assertIn(['apt-get', 'install', '-y', '--no-install-recommends', 'shfmt'], self.events())
        self.assertTrue(any(e[0] == 'doctor' and '--runtime' in e for e in self.events()))
        self.assertFalse(any(e[:2] == ['resources', 'font'] for e in self.events()))
        self.assertFalse(any(e[:2] == ['resources', 'install-go'] for e in self.events()))
        self.assertEqual(original, snapshot(self.repo / 'nvim'))
        first = len(self.events())
        self.invoke('--apply', '--profile', 'server')
        self.assertFalse(any(e[0] in ('sudo', 'apt-get', 'brew') for e in self.events()[first:]))
        self.assertFalse((self.home / '.local/state/dotfiles-bootstrap/lock').exists())

    def test_sudo_credentials_are_refreshed_after_expiry(self):
        self.env.update(BOOTSTRAP_TEST_OLD='shfmt', BOOTSTRAP_TEST_SUDO_EXPIRE='apt-update,nvim')
        self.hide_tools('fc-list', 'fc-cache', 'wl-copy', 'wl-paste', 'xclip', '7zz')
        result = self.invoke('--apply', '--profile', 'desktop')
        events = self.events()
        for checkpoint, package in (('apt-update', 'shfmt'), ('nvim', 'fontconfig')):
            with self.subTest(checkpoint=checkpoint):
                expired = events.index(['sudo-expired', checkpoint])
                renewed = events.index(['sudo', '-v'], expired + 1)
                installed = next(index for index, event in enumerate(events)
                                 if event[:2] == ['apt-get', 'install'] and package in event)
                self.assertLess(renewed, installed)
        self.assertIn('Bootstrap complete.', result.stderr)
        self.assertFalse((self.home / '.local/state/dotfiles-bootstrap/lock').exists())
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_passwordless_sudo_commands_do_not_require_validate(self):
        self.env.update(BOOTSTRAP_TEST_OLD='shfmt', BOOTSTRAP_TEST_SUDO_NOPASSWD='1')
        self.invoke('--apply', '--profile', 'server')
        events = self.events()
        self.assertIn(['sudo', '-n', 'true'], events)
        self.assertNotIn(['sudo', '-v'], events)
        self.assertTrue(any(event[:2] == ['apt-get', 'install'] for event in events))

    def test_sudo_reauthentication_failure_keeps_completed_steps_and_retries(self):
        self.env.update(BOOTSTRAP_TEST_OLD='shfmt', BOOTSTRAP_TEST_SUDO_EXPIRE='nvim',
                        BOOTSTRAP_TEST_FAIL='sudo-renew')
        self.hide_tools('fc-list', 'fc-cache', 'wl-copy', 'wl-paste', 'xclip', '7zz')
        result = self.invoke('--apply', '--profile', 'desktop', success=1)
        events = self.events()
        expired = events.index(['sudo-expired', 'nvim'])
        self.assertIn(['sudo', '-v'], events[expired + 1:])
        self.assertIn('sudo authentication failed', result.stderr)
        self.assertIn('FAILED phase: terminal resources', result.stderr)
        self.assertNotIn('Bootstrap complete.', result.stderr)
        self.assertTrue((self.root / 'installed-shfmt').exists())
        self.assertTrue((self.home / '.config/nvim/init.lua').is_symlink())
        self.assertTrue(any(event[0] == 'nvim' for event in events))
        self.assertFalse(any(event[0] == 'apt-get' or event[:2] == ['resources', 'font'] or
                             event[0] == 'doctor' for event in events[expired + 1:]))
        self.assertFalse((self.home / '.local/state/dotfiles-bootstrap/lock').exists())
        self.assertEqual(list(self.scratch.iterdir()), [])
        self.env.pop('BOOTSTRAP_TEST_FAIL')
        self.invoke('--apply', '--profile', 'desktop')
        retry = self.events()[len(events):]
        self.assertTrue(any(event[:2] == ['apt-get', 'install'] and 'fontconfig' in event for event in retry))
        self.assertFalse(any(event[:2] == ['apt-get', 'install'] and 'shfmt' in event for event in retry))
        self.assertIn(['doctor', '--runtime'], retry)

    def test_sudo_authentication_failure_stops_before_system_installation(self):
        self.env.update(BOOTSTRAP_TEST_OLD='shfmt', BOOTSTRAP_TEST_FAIL='sudo-auth')
        result = self.invoke('--apply', '--profile', 'server', success=1)
        self.assertIn(['sudo', '-v'], self.events())
        self.assertIn('sudo authentication failed', result.stderr)
        self.assertIn('FAILED phase: software installation', result.stderr)
        self.assertNotIn('Bootstrap complete.', result.stderr)
        self.assertFalse(any(event[0] in ('apt-get', 'resources', 'zsh', 'nvim', 'doctor') for event in self.events()))
        self.assertFalse((self.home / '.config/nvim/init.lua').exists())
        self.assertFalse((self.home / '.local/state/dotfiles-bootstrap/lock').exists())
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_deployment_conflict_stops_before_plugin_preparation(self):
        (self.home / '.gitconfig').write_text('[user]\n name = preserve\n')
        self.invoke('--apply', '--profile', 'server', success=1)
        self.assertEqual((self.home / '.gitconfig').read_text(), '[user]\n name = preserve\n')
        self.assertFalse(any(e[0] in ('resources', 'zsh', 'nvim', 'doctor') for e in self.events()))

    def test_failed_install_is_reported_and_retry_succeeds(self):
        self.env.update(BOOTSTRAP_TEST_OLD='nvim', BOOTSTRAP_TEST_FAIL='install')
        self.invoke('--apply', '--profile', 'server', success=1)
        self.assertFalse((self.home / '.config/nvim/init.lua').exists())
        self.env.pop('BOOTSTRAP_TEST_FAIL')
        self.invoke('--apply', '--profile', 'server')

    def test_plugin_and_doctor_failures_are_not_success(self):
        stages = [('zsh', 'Zsh plugins'), ('nvim', 'Neovim plugins'), ('doctor', 'verification')]
        for index, (failure, phase) in enumerate(stages):
            with self.subTest(failure=failure):
                self.env['BOOTSTRAP_TEST_FAIL'] = failure
                before = len(self.events())
                result = self.invoke('--apply', '--profile', 'server', success=1)
                events = self.events()[before:]
                self.assertTrue(any(event[0] == failure for event in events), events)
                self.assertIn('FAILED phase: ' + phase + ' (exit 1)', result.stderr)
                self.assertNotIn('Bootstrap complete.', result.stderr)
                later = {name for name, _ in stages[index + 1:]}
                self.assertFalse(any(event[0] in later for event in events), events)

    def test_desktop_selects_fonts_and_ghostty(self):
        self.env['BOOTSTRAP_TEST_OLD'] = 'ghostty'
        self.invoke('--apply', '--profile', 'desktop')
        self.assertTrue(any(e[:3] == ['resources', 'fetch', 'ghostty'] for e in self.events()))
        self.assertIn(['resources', 'font', 'iosevka', 'linux'], self.events())
        self.assertIn(['resources', 'font', 'sarasa', 'linux'], self.events())
        self.assertIn(['doctor', '--runtime'], self.events())

    def test_macos_platform_preview(self):
        self.macos(BOOTSTRAP_TEST_OLD='nvim')
        before = snapshot(self.home)
        output = self.invoke('--profile', 'desktop').stderr
        self.assertIn('brew "neovim"', output)
        self.assertIn('Brewfile.desktop', output)
        self.assertIn('PLAN: nvim', output)
        self.assertEqual(before, snapshot(self.home))
        self.assertEqual(self.events(), [])

    def test_server_previews_exclude_desktop_manifests(self):
        ubuntu = self.invoke('--profile', 'server').stderr
        self.assertIn('ubuntu/packages.bash', ubuntu)
        self.assertNotIn('packages.desktop.bash', ubuntu)
        self.assertNotIn('fontconfig', ubuntu)
        self.macos()
        macos = self.invoke('--profile', 'server').stderr
        self.assertIn('macos/Brewfile', macos)
        self.assertNotIn('Brewfile.desktop', macos)
        self.assertNotIn('cask "ghostty"', macos)
        self.assertEqual(self.events(), [])

    def test_missing_requirements_stops_before_install(self):
        self.macos()
        requirements = self.repo / 'scripts/bootstrap/requirements.tsv'
        saved = requirements.read_bytes()
        try:
            requirements.unlink()
            output = self.invoke('--apply', '--profile', 'server', success=1).stderr
            self.assertIn('requirements.tsv is missing', output)
            self.assertEqual(self.events(), [])
        finally:
            requirements.write_bytes(saved)

    def test_macos_installs_brew_managed_packages_and_reuses_on_retry(self):
        # PATH 中的命令已满足要求，但模拟的 Homebrew 尚未登记任何包。
        self.macos()
        self.invoke('--apply', '--profile', 'server')
        installs = self.brew_installs()
        self.assertEqual(len(installs), 1)
        self.assertIn('--no-upgrade', installs[0])
        self.assertEqual(installs[0][-1], '--file=' + str(self.repo / 'scripts/bootstrap/macos/Brewfile'))
        self.assertEqual({path.name for path in self.root.glob('brew-trusted-*')},
                         {'brew-trusted-formula-ewhauser_tap_shuck-cli'})
        self.assertFalse(any(event[:2] == ['brew', 'upgrade'] for event in self.events()))
        self.assertFalse(any(event[:2] == ['resources', 'install-go'] for event in self.events()))
        self.assertFalse(any(event[0] == 'apt-get' or event[:2] == ['resources', 'font'] for event in self.events()))
        before = len(self.events())
        self.invoke('--apply', '--profile', 'server')
        self.assertEqual(self.brew_installs(self.events()[before:]), [])
        self.assertFalse(any(event[:2] == ['brew', 'upgrade'] for event in self.events()[before:]))

    def test_macos_untrusted_formula_stops_before_deployment_and_retries(self):
        self.macos()
        self.hide_tools('shuck')
        manifest = self.repo / 'scripts/bootstrap/macos/Brewfile'
        saved = manifest.read_text()
        try:
            manifest.write_text(saved.replace('brew "ewhauser/tap/shuck-cli", trusted: true',
                                              'brew "ewhauser/tap/shuck-cli"'))
            output = self.invoke('--apply', '--profile', 'server', success=1).stderr
            self.assertIn('untrusted tap ewhauser/tap', output)
            self.assertTrue((self.root / 'brew-git').exists())
            self.assertFalse((self.root / 'brew-ewhauser_tap_shuck-cli').exists())
            self.assertFalse((self.home / '.config/nvim/init.lua').exists())
            self.assertFalse(any(event[0] in ('resources', 'nvim', 'doctor') for event in self.events()))
            self.assertFalse((self.home / '.local/state/dotfiles-bootstrap/lock').exists())
        finally:
            manifest.write_text(saved)
        self.invoke('--apply', '--profile', 'server')
        self.assertTrue((self.root / 'brew-ewhauser_tap_shuck-cli').exists())
        self.assertTrue((self.bin / 'shuck').exists())
        self.assertTrue(any(event[:2] == ['doctor', '--runtime'] for event in self.events()))

    def test_macos_installs_tree_sitter_cli_when_only_library_is_installed(self):
        self.macos(BOOTSTRAP_TEST_BREW_INSTALLED='tree-sitter')
        self.hide_tools('tree-sitter')
        self.invoke('--apply', '--profile', 'server')
        self.assertTrue((self.root / 'brew-tree-sitter-cli').exists())
        self.assertTrue((self.bin / 'tree-sitter').exists())
        self.assertFalse(any(event[:2] == ['brew', 'upgrade'] for event in self.events()))

    def test_macos_installs_missing_go_before_neovim_and_reuses_on_retry(self):
        self.macos()
        self.hide_tools('go')
        self.invoke('--apply', '--profile', 'server')
        events = self.events()
        self.assertFalse((self.root / 'brew-go').exists())
        self.assertTrue((self.bin / 'go').exists())
        self.assertLess(events.index(['resources', 'install-go', 'macos-arm64']),
                        next(index for index, event in enumerate(events) if event[0] == 'nvim'))
        before = len(events)
        self.invoke('--apply', '--profile', 'server')
        self.assertEqual(self.brew_installs(self.events()[before:]), [])
        self.assertFalse(any(event[:2] == ['brew', 'upgrade'] for event in self.events()[before:]))
        self.assertFalse(any(event[:2] == ['resources', 'install-go'] for event in self.events()[before:]))

    def test_macos_upgrades_incompatible_go_before_neovim(self):
        self.macos(BOOTSTRAP_TEST_BREW_INSTALLED='all', BOOTSTRAP_TEST_OLD='go')
        self.invoke('--apply', '--profile', 'server')
        events = self.events()
        self.assertEqual(self.brew_installs(), [])
        self.assertFalse(any(event[:2] == ['brew', 'upgrade'] for event in events))
        self.assertLess(events.index(['resources', 'install-go', 'macos-arm64']),
                        next(index for index, event in enumerate(events) if event[0] == 'nvim'))

    def test_macos_upgrades_incompatible_tree_sitter_cli(self):
        self.macos(BOOTSTRAP_TEST_BREW_INSTALLED='all', BOOTSTRAP_TEST_OLD='tree-sitter')
        self.invoke('--apply', '--profile', 'server')
        self.assertEqual(self.brew_installs(), [])
        self.assertEqual([event for event in self.events() if event[:2] == ['brew', 'upgrade']],
                         [['brew', 'upgrade', '--formula', 'tree-sitter-cli']])

    def test_macos_desktop_adds_manifest_before_fonts(self):
        self.macos()
        self.invoke('--apply', '--profile', 'desktop')
        events = self.events()
        installs = self.brew_installs(events)
        self.assertEqual([Path(event[-1].removeprefix('--file=')).name for event in installs],
                         ['Brewfile', 'Brewfile.desktop'])
        self.assertTrue(all('--no-upgrade' in event for event in installs))
        self.assertLess(events.index(installs[1]), events.index(['resources', 'font', 'iosevka', 'macos']))
        self.assertIn(['resources', 'font', 'sarasa', 'macos'], events)
        self.assertFalse(any(event[0] == 'apt-get' for event in events))

    def test_macos_only_upgrades_incompatible_installed_formula(self):
        self.macos(BOOTSTRAP_TEST_BREW_INSTALLED='all', BOOTSTRAP_TEST_OLD='nvim')
        self.invoke('--apply', '--profile', 'server')
        self.assertEqual(self.brew_installs(), [])
        self.assertEqual([event for event in self.events() if event[:2] == ['brew', 'upgrade']],
                         [['brew', 'upgrade', '--formula', 'neovim']])

    def test_macos_bundle_leaves_installed_formula_for_targeted_upgrade(self):
        self.macos(BOOTSTRAP_TEST_BREW_INSTALLED='neovim', BOOTSTRAP_TEST_OLD='nvim')
        self.invoke('--apply', '--profile', 'server')
        # bundle 保留已安装的旧版本，由显式升级修复不兼容的包。
        self.assertEqual(len(self.brew_installs()), 1)
        self.assertIn(['brew', 'upgrade', '--formula', 'neovim'], self.events())
        self.assertFalse((self.root / 'brew-upgraded-by-bundle').exists())

    def test_macos_upgrades_incompatible_cask(self):
        self.macos(BOOTSTRAP_TEST_BREW_INSTALLED='all', BOOTSTRAP_TEST_OLD='ghostty')
        self.invoke('--apply', '--profile', 'desktop')
        self.assertEqual([event for event in self.events() if event[:2] == ['brew', 'upgrade']],
                         [['brew', 'upgrade', '--cask', 'ghostty']])

    def test_macos_bundle_failure_stops_before_deployment_and_retry_succeeds(self):
        self.macos(BOOTSTRAP_TEST_FAIL='brew')
        output = self.invoke('--apply', '--profile', 'server', success=1).stderr
        self.assertIn('install Homebrew manifest', output)
        self.assertFalse((self.home / '.config/nvim/init.lua').exists())
        self.assertFalse(any(event[0] in ('resources', 'nvim', 'doctor') for event in self.events()))
        self.assertFalse((self.home / '.local/state/dotfiles-bootstrap/lock').exists())
        self.env.pop('BOOTSTRAP_TEST_FAIL')
        self.invoke('--apply', '--profile', 'server')

    def test_macos_desktop_failure_preserves_base_and_retries(self):
        self.macos(BOOTSTRAP_TEST_FAIL='bundle-desktop')
        self.invoke('--apply', '--profile', 'desktop', success=1)
        self.assertTrue((self.home / '.config/nvim/init.lua').is_symlink())
        self.assertFalse(any(event[:2] == ['resources', 'font'] or event[0] == 'doctor' for event in self.events()))
        before = len(self.events())
        self.env.pop('BOOTSTRAP_TEST_FAIL')
        self.invoke('--apply', '--profile', 'desktop')
        installs = self.brew_installs(self.events()[before:])
        self.assertEqual(len(installs), 1)
        self.assertTrue(installs[0][-1].endswith('/Brewfile.desktop'))

    def test_macos_rechecks_command_after_upgrade(self):
        self.macos(BOOTSTRAP_TEST_BREW_INSTALLED='all', BOOTSTRAP_TEST_STUCK='nvim')
        output = self.invoke('--apply', '--profile', 'server', success=1).stderr
        self.assertIn(['brew', 'upgrade', '--formula', 'neovim'], self.events())
        self.assertIn('PATH', output)
        self.assertFalse((self.home / '.config/nvim/init.lua').exists())

    def test_ubuntu_desktop_packages_install_in_desktop_stage(self):
        self.hide_tools('fc-list', 'fc-cache', 'wl-copy', 'wl-paste', 'xclip', '7zz')
        self.invoke('--apply', '--profile', 'desktop')
        events = self.events()
        install = ['apt-get', 'install', '-y', '--no-install-recommends',
                   'fontconfig', 'wl-clipboard', 'xclip', '7zip']
        self.assertIn(install, events)
        self.assertLess(next(index for index, event in enumerate(events) if event[0] == 'nvim'), events.index(install))
        self.assertLess(events.index(install), events.index(['resources', 'font', 'iosevka', 'linux']))

    def test_ubuntu_installs_missing_go_before_neovim_and_reuses_on_retry(self):
        self.hide_tools('go')
        self.invoke('--apply', '--profile', 'server')
        events = self.events()
        install = ['resources', 'install-go', 'linux-arm64']
        self.assertTrue((self.bin / 'go').exists())
        self.assertFalse(any(event[0] == 'apt-get' for event in events))
        self.assertLess(events.index(install),
                        next(index for index, event in enumerate(events) if event[0] == 'nvim'))
        before = len(events)
        self.invoke('--apply', '--profile', 'server')
        self.assertFalse(any(event[0] in ('sudo', 'apt-get') for event in self.events()[before:]))
        self.assertFalse(any(event[:2] == ['resources', 'install-go'] for event in self.events()[before:]))

    def test_ubuntu_upgrades_incompatible_go_before_neovim(self):
        self.env['BOOTSTRAP_TEST_OLD'] = 'go'
        self.invoke('--apply', '--profile', 'server')
        events = self.events()
        self.assertFalse(any(event[0] == 'apt-get' for event in events))
        self.assertLess(events.index(['resources', 'install-go', 'linux-arm64']),
                        next(index for index, event in enumerate(events) if event[0] == 'nvim'))

    def test_ubuntu_server_does_not_install_missing_desktop_tools(self):
        self.hide_tools('fc-list', 'fc-cache', 'wl-copy', 'wl-paste', 'xclip', '7zz', 'ghostty')
        self.invoke('--apply', '--profile', 'server')
        self.assertFalse(any(event[0] in ('apt-get', 'brew') for event in self.events()))
        self.assertFalse(any(event[:2] in (['resources', 'font'], ['resources', 'fetch']) for event in self.events()))

    def test_ubuntu_26_04_uses_apt_for_ghostty(self):
        (self.root / 'os-release').write_text('ID=ubuntu\nVERSION_ID=26.04\n')
        self.env['BOOTSTRAP_TEST_OLD'] = 'ghostty'
        output = self.invoke('--profile', 'desktop').stderr
        self.assertIn('apt: fontconfig wl-clipboard xclip 7zip ghostty', output)
        self.assertNotIn('community deb', output)
        self.assertEqual(self.events(), [])
        self.invoke('--apply', '--profile', 'desktop')
        self.assertIn(['apt-get', 'install', '-y', '--no-install-recommends', 'ghostty'], self.events())
        self.assertFalse(any(event[:3] == ['resources', 'fetch', 'ghostty'] for event in self.events()))

    def test_ubuntu_apt_fallback_and_multi_command_release(self):
        self.env.update(BOOTSTRAP_TEST_APT_OLD='delta', BOOTSTRAP_TEST_OLD='node')
        self.invoke('--apply', '--profile', 'server')
        events = self.events()
        apt = ['apt-get', 'install', '-y', '--no-install-recommends', 'git-delta']
        fallback = ['resources', 'install', 'delta', 'linux-arm64']
        self.assertLess(events.index(apt), events.index(fallback))
        self.assertIn(['resources', 'install', 'node', 'linux-arm64'], events)
        before = len(events)
        self.invoke('--apply', '--profile', 'server')
        self.assertFalse(any(event[0] == 'apt-get' or event[:2] == ['resources', 'install']
                             for event in self.events()[before:]))

    def test_ubuntu_installs_packages_without_commands(self):
        self.env['BOOTSTRAP_TEST_PACKAGES'] = 'ca-certificates'
        self.invoke('--apply', '--profile', 'server')
        self.assertIn(['apt-get', 'install', '-y', '--no-install-recommends',
                       'ca-certificates'], self.events())
        before = len(self.events())
        self.invoke('--apply', '--profile', 'server')
        self.assertFalse(any(event[0] == 'apt-get' for event in self.events()[before:]))

    def test_ubuntu_base_does_not_request_nonexistent_lesspipe_package(self):
        for release in ('24.04', '26.04'):
            (self.root / 'os-release').write_text(f'ID=ubuntu\nVERSION_ID={release}\n')
            output = self.invoke('--profile', 'server').stderr
            self.assertNotIn('lesspipe', output)

    def test_ubuntu_node_prepares_missing_npm(self):
        self.hide_tools('npm')
        self.invoke('--apply', '--profile', 'server')
        self.assertIn(['resources', 'install', 'node', 'linux-arm64'], self.events())
        self.assertTrue((self.bin / 'npm').is_file())

    def test_ubuntu_installs_missing_release_tool_and_reuses_it(self):
        self.hide_tools('fzf')
        self.invoke('--apply', '--profile', 'server')
        self.assertIn(['resources', 'install', 'fzf', 'linux-arm64'], self.events())
        self.assertTrue((self.bin / 'fzf').is_file())
        before = len(self.events())
        self.invoke('--apply', '--profile', 'server')
        self.assertNotIn(['resources', 'install', 'fzf', 'linux-arm64'], self.events()[before:])

    def test_ubuntu_rechecks_commands_before_deployment(self):
        self.env['BOOTSTRAP_TEST_STUCK'] = 'shfmt'
        output = self.invoke('--apply', '--profile', 'server', success=1).stderr
        self.assertIn(['apt-get', 'install', '-y', '--no-install-recommends', 'shfmt'], self.events())
        self.assertIn('shfmt >= 3.8.0 is still unavailable', output)
        self.assertFalse((self.home / '.config/nvim/init.lua').exists())
        self.assertFalse(any(event[0] in ('resources', 'nvim', 'doctor') for event in self.events()))

    def test_existing_lock_refuses_second_apply(self):
        lock = self.home / '.local/state/dotfiles-bootstrap/lock'
        lock.mkdir(parents=True)
        (lock / 'pid').write_text('12345\n')
        self.invoke('--apply', '--profile', 'server', success=1)
        self.assertEqual((lock / 'pid').read_text(), '12345\n')
        self.assertEqual(self.events(), [])

    def test_term_interrupt_stops_children_and_releases_lock(self):
        self.env['BOOTSTRAP_TEST_FAIL'] = 'signal'
        process = self.start('--apply', '--profile', 'server')
        self.checkpoint(process)
        process.send_signal(signal.SIGTERM)
        self.assertEqual(process.wait(timeout=10), 143, (self.root / 'live.log').read_text())
        self.assertFalse((self.home / '.local/state/dotfiles-bootstrap/lock').exists())
        self.assertEqual(list(self.scratch.iterdir()), [])
        pid = int((self.root / 'child-pid').read_text())
        self.assertFalse(running(pid))

    def test_log_is_visible_before_long_command_finishes(self):
        self.env['BOOTSTRAP_TEST_FAIL'] = 'signal'
        process = self.start('--apply', '--profile', 'server')
        self.checkpoint(process)
        deadline = time.monotonic() + 3
        log = next((self.home / '.local/state/dotfiles-bootstrap').glob('run.*'))
        while 'NATIVE CHECKPOINT' not in log.read_text():
            self.assertLess(time.monotonic(), deadline, log.read_text())
            time.sleep(0.02)
        self.assertIn('NATIVE CHECKPOINT', (self.root / 'live.log').read_text())
        self.assertIsNone(process.poll())
        process.send_signal(signal.SIGTERM)
        self.assertEqual(process.wait(timeout=10), 143)

    def test_neovim_child_progress_reaches_terminal_and_persistent_log(self):
        self.env['BOOTSTRAP_TEST_NVM_PROGRESS'] = '1'
        output = self.invoke('--apply', '--profile', 'server').stderr
        line = ('WAIT: Neovim / Treesitter / install; elapsed=30s; timeout=600s; '
                'active=bash (Compiling parser)')
        self.assertIn(line, output)
        log = next((self.home / '.local/state/dotfiles-bootstrap').glob('run.*'))
        self.assertIn(line, log.read_text())

    def test_cleanup_failure_never_reports_success_or_masks_primary_failure(self):
        self.mock_shell('rmdir', 'case "$*" in */lock) exit 72;; esac\nexec '
                        + shlex.quote(REAL_TOOLS['rmdir']) + ' "$@"\n')
        for failure in ('', 'nvim', 'signal'):
            with self.subTest(failure=failure):
                self.env['BOOTSTRAP_TEST_FAIL'] = failure
                if failure == 'signal':
                    (self.root / 'ready').unlink(missing_ok=True)
                    process = self.start('--apply', '--profile', 'server')
                    self.checkpoint(process)
                    process.send_signal(signal.SIGTERM)
                    self.assertEqual(process.wait(timeout=10), 143)
                    output = (self.root / 'live.log').read_text()
                else:
                    output = self.invoke('--apply', '--profile', 'server', success=1).stderr
                self.assertIn('FAIL cleanup: could not release lock:', output)
                self.assertNotIn('Bootstrap complete.', output)
                self.assertIn('FAILED phase: ' + ('cleanup' if not failure else 'Neovim plugins'), output)
                self.assertEqual(list(self.scratch.iterdir()), [])
                lock = self.home / '.local/state/dotfiles-bootstrap/lock'
                self.assertTrue(lock.is_dir())
                lock.rmdir()  # 只删除本夹具故意保留的空锁目录。

    def test_initialization_group_signal_preserves_allocated_path_for_cleanup(self):
        helper = self.bin / 'mktemp'
        helper.write_text('#!' + sys.executable + ' -B\n' +
            'import os, pathlib, subprocess, sys, time\n' +
            f'root = pathlib.Path({str(self.root)!r})\n' +
            f'path = subprocess.check_output([{REAL_TOOLS["mktemp"]!r}, *sys.argv[1:]], text=True).strip()\n' +
            '(root / "allocated").write_text(path)\n(root / "ready").touch()\n' +
            'end = time.monotonic() + 10\n' +
            'while not (root / "release").exists() and time.monotonic() < end: time.sleep(.02)\n' +
            'print(path, flush=True)\n')
        helper.chmod(0o755)
        before = snapshot(self.home)
        process = self.start('--apply', '--profile', 'server')
        self.checkpoint(process)
        allocated = Path((self.root / 'allocated').read_text())
        self.assertTrue(allocated.is_dir())
        os.killpg(process.pid, signal.SIGTERM)
        (self.root / 'release').touch()
        self.assertEqual(process.wait(timeout=10), 143, (self.root / 'live.log').read_text())
        self.assertFalse(allocated.exists())
        self.assertEqual(snapshot(self.home), before)
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_home_alias_and_xdg_inputs_agree_across_entries(self):
        alias = self.root / 'home-alias'
        alias.symlink_to(self.home, target_is_directory=True)
        self.env.update(HOME=str(alias), XDG_CONFIG_HOME=str(alias / '.config'))
        before = snapshot(self.home)
        self.invoke('--profile', 'server')
        # 调用真实 doctor 入口，验证其路径处理行为。
        for script, args in ((self.repo / 'scripts/deploy.sh', ['--dry-run']),
                             (REPO / 'scripts/doctor.sh', ['--only', 'environment'])):
            result = run([BASH, str(script), *args], env=self.env, cwd=self.root,
                         timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(snapshot(self.home), before)
        self.assertEqual(list(self.scratch.iterdir()), [])
        # 部署创建目录后，相同的 HOME 与 XDG 路径写法仍应有效。
        self.invoke('--apply', '--profile', 'server')
        self.invoke('--profile', 'server')

    @unittest.skipUnless(REAL_TOOLS['nvim'] and REAL_TOOLS['starship'], 'native Neovim and Starship are required')
    def test_native_version_queries_leave_home_and_source_unchanged(self):
        for name in ('nvim', 'starship'):
            self.mock_shell(name, 'exec ' + shlex.quote(REAL_TOOLS[name]) + ' "$@"\n')
        before, source = snapshot(self.home), snapshot(self.repo)
        output = self.invoke('--profile', 'server').stderr
        self.assertIn('FOUND: nvim', output)
        self.assertIn('FOUND: starship', output)
        self.assertEqual(snapshot(self.home), before)
        self.assertEqual(snapshot(self.repo), source)
        self.assertEqual(list(self.scratch.iterdir()), [])
        self.assertEqual(self.events(), [])

    def test_version_query_timeout_cleans_private_state_and_descendants(self):
        marker = self.root / 'query-child'
        self.mock_shell('starship', 'trap "" TERM\nsleep 60 &\nprintf "%s\\n" "$!" > '
                        + shlex.quote(str(marker)) + '\nwait\n')
        before = snapshot(self.home)
        started = time.monotonic()
        output = self.invoke('--profile', 'server').stderr
        self.assertLess(time.monotonic() - started, 15)
        self.assertIn('PLAN: starship', output)
        self.assertTrue(marker.exists())
        self.assertFalse(running(int(marker.read_text())))
        self.assertEqual(snapshot(self.home), before)
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_build_capability_failure_stops_before_plugins(self):
        for failure in ('npm', 'compiler', 'executable'):
            with self.subTest(failure=failure):
                self.env['BOOTSTRAP_TEST_CAPABILITY_FAIL'] = failure
                output = self.invoke('--apply', '--profile', 'server', success=1).stderr
                self.assertIn('required build capability failed: ' + failure, output)
                self.assertFalse(any(event[0] in ('zsh', 'nvim', 'doctor') for event in self.events()))
                self.assertFalse((self.home / '.config/nvim/init.lua').exists())
                self.assertEqual(list(self.scratch.iterdir()), [])

    def test_logging_failure_is_not_command_success(self):
        self.mock_shell('tee', 'cat >/dev/null\nexit 73\n')
        output = self.invoke('--apply', '--profile', 'server', success=1).stderr
        self.assertIn('FAIL logging:', output)
        self.assertIn('deployment preflight failed (exit 73)', output)
        self.assertNotIn('Bootstrap complete.', output)
        self.assertEqual(list(self.scratch.iterdir()), [])
        # 部署和 tee 同时失败时，保留部署的退出码。
        (self.home / '.gitconfig').write_text('# 与部署冲突的个人配置入口。\n')
        output = self.invoke('--apply', '--profile', 'server', success=1).stderr
        self.assertIn('deployment preflight failed (exit 1)', output)
        self.assertNotIn('Bootstrap complete.', output)
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_fixed_homebrew_prefix_still_hits_only_fixture_commands(self):
        self.macos(BOOTSTRAP_TEST_OLD='nvim', BOOTSTRAP_TEST_BREW_INSTALLED='neovim')
        fixed = self.root / 'homebrew/bin'
        fixed.mkdir(parents=True)
        (fixed / 'brew').symlink_to(self.bin / 'brew')
        # 验证固定前缀的优先级：PATH 中的候选命令故意失败，确保调用未落到此处。
        alternate = self.root / 'alternate'
        alternate.mkdir()
        (alternate / 'brew').write_text('#!/bin/sh\nexit 92\n')
        (alternate / 'brew').chmod(0o755)
        self.env['PATH'] = str(alternate) + ':' + self.env['PATH']
        self.invoke('--apply', '--profile', 'server')
        self.assertTrue(self.brew_installs())
        self.assertIn(['brew', 'upgrade', '--formula', 'neovim'], self.events())

    @unittest.skipUnless(REAL_TOOLS['zsh'], 'native Zsh is required')
    def test_zsh_preparation_from_terminal_keeps_foreground_session(self):
        _, bundle, cache = zsh_fixture(self.home, self.root, self.env)
        self.mock_shell('zsh', 'exec ' + shlex.quote(REAL_TOOLS['zsh']) + ' "$@"\n')
        self.prepare_environment()
        output = terminal([BASH, '-c', 'set -e\n"$@"\nstty -g </dev/tty >/dev/null\n'
                           'printf "Foreground terminal is still attached.\\n"\n',
                           'bootstrap-terminal', BASH, str(self.repo / 'scripts/bootstrap.sh'),
                           '--apply', '--profile', 'server'], env=self.env, cwd=self.root, timeout=60).decode()
        self.assertIn('READY: Zsh plugin preparation', output)
        self.assertIn('Bootstrap complete.', output)
        self.assertIn('Foreground terminal is still attached.', output)
        self.assertEqual((cache / 'zsh_plugins.zsh').read_bytes(), bundle.read_bytes())
        self.assertEqual(list(cache.glob('.bootstrap.*')), [])
        self.assertFalse((self.home / '.local/state/dotfiles-bootstrap/lock').exists())
        self.assertEqual(list(self.scratch.iterdir()), [])


class Execution(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='bootstrap-execution-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.helper = REPO / 'scripts/bootstrap/without_tty.py'
        self.env = {'HOME': str(self.root), 'PATH': os.environ['PATH'], 'LC_ALL': 'C',
                    'TMPDIR': str(self.root)}

    def start(self, seconds):
        task = self.root / 'task.py'
        task.write_text('import errno, json, os, pathlib, signal, subprocess, sys, time\n'
                        'try:\n    os.open("/dev/tty", os.O_RDWR)\n'
                        'except OSError as error:\n    assert error.errno == errno.ENXIO\n'
                        'else:\n    raise RuntimeError("task still has a controlling terminal")\n'
                        'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                        'child = subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(60)"])\n'
                        'pathlib.Path("ready").write_text(json.dumps(dict(parent=os.getpid(), child=child.pid, '
                        'session=os.getsid(0), group=os.getpgrp())))\n'
                        'time.sleep(60)\n')
        # 调用生产执行器，测试任务和日志管道在超时/终端 Ctrl-C 后的实际清理。
        driver = '''set -e
source "$1"
scratch="$2" log_file="$2/task.log" active_pid='' timer_pid=''
registering=no interrupted_status=0
shift 2
trap 'stop_children' EXIT
trap 'interrupt_bootstrap 130' INT
trap 'interrupt_bootstrap 143' TERM
execute "$@"
'''
        process = Process([BASH, '-c', driver, 'bootstrap-execution',
                           str(REPO / 'scripts/bootstrap/common.bash'), str(self.root), str(seconds), 'log',
                           sys.executable, '-B', str(self.helper), sys.executable, '-B', str(task)],
                          terminal=True, env=self.env, cwd=self.root)
        self.addCleanup(process.close)
        return process

    def assert_cleaned(self, process):
        data = json.loads((self.root / 'ready').read_text())
        self.assertEqual(data['session'], process.pid, 'task escaped the original session')
        self.assertNotEqual(data['group'], process.pid, 'task lost its separate process group')
        for key in ('parent', 'child'):
            self.assertFalse(running(data[key]), f'{key} survived command cleanup')
        self.assertEqual(process.groups(), set(), 'task or watchdog descendants survived cleanup')

    def wait_terminal(self, process):
        # macOS 会在会话退出时等待终端输出排空（包括 Ctrl-C 的回显）。
        # 必须持续读取 PTY，不能像普通文件日志那样只 wait。
        deadline = time.monotonic() + 10
        output = bytearray()
        while process.poll() is None:
            self.assertLess(time.monotonic(), deadline, repr(bytes(output)))
            if select.select([process.master], [], [], .05)[0]:
                try:
                    block = os.read(process.master, 65536)
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                    break
                if not block:
                    break
                output.extend(block)
        return process.wait(timeout=max(0, deadline - time.monotonic()))

    def test_detached_task_timeout_stops_descendants(self):
        process = self.start(2)
        self.assertEqual(self.wait_terminal(process), 124)
        self.assertTrue((self.root / 'timed-out').exists())
        self.assert_cleaned(process)

    def test_terminal_ctrl_c_stops_detached_task_and_descendants(self):
        process = self.start(60)
        deadline = time.monotonic() + 5
        while not (self.root / 'ready').exists():
            self.assertIsNone(process.poll())
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.02)
        os.write(process.master, b'\x03')
        self.assertEqual(self.wait_terminal(process), 130)
        self.assertFalse((self.root / 'timed-out').exists())
        self.assert_cleaned(process)

    def test_command_without_terminal_preserves_exit_status(self):
        result = run([sys.executable, '-B', str(self.helper), sys.executable, '-B', '-c',
                      'import sys; print("command completed"); sys.exit(37)'], env=self.env, cwd=self.root)
        self.assertEqual(result.returncode, 37, result.stderr)
        self.assertEqual(result.stdout, 'command completed\n')

    def test_terminal_session_leader_is_rejected(self):
        marker = self.root / 'should-not-run'
        with self.assertRaises(subprocess.CalledProcessError) as failure:
            terminal([sys.executable, '-B', str(self.helper), sys.executable, '-B', '-c',
                      f'from pathlib import Path; Path({str(marker)!r}).touch()'],
                     env=self.env, cwd=self.root, timeout=5)
        self.assertEqual(failure.exception.returncode, 1)
        self.assertFalse(marker.exists())


class Preparation(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='bootstrap-native-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.env = {'HOME': str(self.home), 'PATH': os.environ['PATH'], 'LC_ALL': 'C',
                    'TMPDIR': str(self.root), 'NVIM_LOG_FILE': str(self.root / 'nvim.log')}
        for kind, relative in (('CONFIG', '.config'), ('DATA', '.local/share'), ('STATE', '.local/state'), ('CACHE', '.cache')):
            self.env['XDG_' + kind + '_HOME'] = str(self.home / relative)

    def test_bootstrap_fixtures_resolve_symlinked_tmpdir(self):
        physical = self.root / 'physical tmp'
        physical.mkdir()
        alias = self.root / 'tmp alias'
        alias.symlink_to(physical, target_is_directory=True)
        # 只运行一个用例来验证真实入口初始化和 Brewfile 的精确路径断言，
        # 避免递归执行当前用例或整套测试。
        result = run([BASH, str(REPO / 'tests/bootstrap.sh'),
                      'Orchestration.test_macos_installs_brew_managed_packages_and_reuses_on_retry'],
                     env=dict(os.environ, TMPDIR=str(alias)), cwd=self.root, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(list(physical.iterdir()), [], 'nested driver did not clean its fixtures')

    def prepare_zsh(self, manifest):
        return run([REAL_TOOLS['zsh'], '-d', '-f', str(REPO / 'scripts/bootstrap/zsh.zsh'), str(manifest)],
                   env=self.env, cwd=self.root, timeout=10)

    @unittest.skipUnless(REAL_TOOLS['zsh'], 'native Zsh is required')
    def test_zsh_cache_publication_and_reuse(self):
        manifest, bundle, cache = zsh_fixture(self.home, self.root, self.env)
        result = self.prepare_zsh(manifest)
        self.assertEqual(result.returncode, 0, result.stderr)
        generated = cache / 'zsh_plugins.zsh'
        self.assertEqual(generated.read_bytes(), bundle.read_bytes())
        before = (generated.stat().st_ino, generated.stat().st_mtime_ns, generated.read_bytes())
        result = self.prepare_zsh(manifest)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('cache is current', result.stdout)
        self.assertEqual((generated.stat().st_ino, generated.stat().st_mtime_ns, generated.read_bytes()), before)
        self.assertEqual(list(cache.glob('.bootstrap.*')), [])

    @unittest.skipUnless(REAL_TOOLS['zsh'], 'native Zsh is required')
    def test_invalid_zsh_bundle_keeps_existing_cache(self):
        manifest, bundle, cache = zsh_fixture(self.home, self.root, self.env)
        generated = cache / 'zsh_plugins.zsh'
        generated.write_text('# 保留已有缓存。\n')
        before = (generated.stat().st_ino, generated.stat().st_mtime_ns, generated.read_bytes())
        bundle.write_text('if then\n')
        result = self.prepare_zsh(manifest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('parse error', result.stderr)
        self.assertEqual((generated.stat().st_ino, generated.stat().st_mtime_ns, generated.read_bytes()), before)
        self.assertEqual(list(cache.glob('.bootstrap.*')), [])

    @unittest.skipUnless(os.environ.get('DOTFILES_TEST_PREPARED_HOME') and REAL_TOOLS['nvim'],
                         'set DOTFILES_TEST_PREPARED_HOME to a prepared baseline for cached Neovim integration')
    def test_cached_neovim_preparation_uses_real_plugin_apis(self):
        original = Path(os.environ['DOTFILES_TEST_PREPARED_HOME']) / '.local/share/nvim'
        self.assertTrue(original.is_dir(), original)
        before = snapshot(original)
        self.addCleanup(lambda: self.assertEqual(snapshot(original), before,
                                                'cached integration changed its source baseline'))
        data = self.home / '.local/share/nvim'
        data.mkdir(parents=True)
        for name in ('lazy', 'mason', 'site'):
            shutil.copytree(original / name, data / name, symlinks=True)
        config = self.home / '.config/nvim'
        shutil.copytree(REPO / 'nvim/.config/nvim', config)
        lock = (config / 'lazy-lock.json').read_bytes()
        # 缓存基线必须完整；在外部命令边界拒绝下载，避免测试意外访问网络。
        bindir = self.root / 'bin'
        bindir.mkdir()
        for name in ('curl', 'wget'):
            (bindir / name).write_text('#!/bin/sh\nprintf "unexpected network request\\n" >&2\nexit 90\n')
        (bindir / 'git').write_text('#!/bin/sh\nfor arg do\ncase "$arg" in fetch|clone|pull|ls-remote) exit 90;; esac\ndone\nexec '
                                    + shlex.quote(REAL_TOOLS['git']) + ' "$@"\n')
        for command in bindir.iterdir():
            command.chmod(0o755)
        self.env['PATH'] = str(bindir) + ':' + str(data / 'mason/bin') + ':' + self.env['PATH']
        entrypoint = str(REPO / 'scripts/bootstrap/nvim.lua')
        command = [REAL_TOOLS['nvim'], '--headless', '-u', 'NONE', '-n', '-i', 'NONE', '-l']
        cached_site = self.root / 'cached-site'
        (data / 'site').rename(cached_site)
        for scenario, arguments in (
                ('first-install', [str(REPO / 'tests/bootstrap/nvim-first-install.lua'), str(cached_site), entrypoint]),
                ('retry', [str(REPO / 'tests/bootstrap/nvim-cached-offline.lua'), entrypoint])):
            with self.subTest(scenario=scenario):
                child = run(command + arguments, env=self.env, cwd=self.root, timeout=90)
                stdout, stderr = child.stdout, child.stderr
                self.assertEqual(child.returncode, 0, stdout + stderr)
                if scenario == 'first-install':
                    self.assertIn('FIXTURE: published parsers during first installation', stdout)
                for stage in ('all plugin checkouts match', 'Mason', 'Treesitter parsers installed', 'completion resources'):
                    self.assertIn('READY: ' + stage if stage != 'Treesitter parsers installed' else stage, stdout)
                for phase in ('install', 'update', 'verify'):
                    self.assertIn('RUN: Neovim / Treesitter / ' + phase, stdout)
                    self.assertIn('READY: Neovim / Treesitter / ' + phase, stdout)
                self.assertIn('PARSER: Neovim / Treesitter / verify / bash; action=load', stdout)
                self.assertNotIn('DOWNLOAD:', stdout)
                self.assertEqual((config / 'lazy-lock.json').read_bytes(), lock)

        # 旧 parser.so 仍在时使修订记录过期；真实 update 必须尝试工作，并转发下载器原始错误。
        with self.subTest(scenario='stale-parser-update-diagnostic'):
            parser = data / 'site/parser/bash.so'
            before_parser = (parser.stat().st_size, parser.stat().st_mtime_ns)
            revision = data / 'site/parser-info/bash.revision'
            original_revision = revision.read_bytes()
            revision.write_text('stale-revision')
            try:
                child = run(command + [str(REPO / 'tests/bootstrap/nvim-cached-offline.lua'), entrypoint],
                            env=self.env, cwd=self.root, timeout=90)
                self.assertEqual(child.returncode, 1, child.stdout + child.stderr)
                self.assertIn('READY: Neovim / Treesitter / install', child.stdout)
                self.assertIn('PARSER: Neovim / Treesitter / update / bash;', child.stdout)
                self.assertIn('PARSER FAIL: Neovim / Treesitter / update / bash;', child.stdout)
                self.assertIn('Error during download: unexpected network request', child.stdout)
                self.assertIn('DOWNLOAD: Neovim / Treesitter / update / bash; request=1; event=start;', child.stdout)
                self.assertRegex(child.stdout, r'event=finish;[^\n]*exit=90;[^\n]*http=unavailable;')
                self.assertNotIn('READY: Neovim / Treesitter / update', child.stdout)
                self.assertEqual((parser.stat().st_size, parser.stat().st_mtime_ns), before_parser)
                self.assertEqual((config / 'lazy-lock.json').read_bytes(), lock)
            finally:
                revision.write_bytes(original_revision)

        # 保留查询和修订记录，让插件仍将 bash 视为已安装；真实加载必须报告缺少解析器。
        with self.subTest(scenario='missing-parser-diagnostic'):
            (data / 'site/parser/bash.so').unlink()
            child = run(command + [str(REPO / 'tests/bootstrap/nvim-cached-offline.lua'), entrypoint],
                        env=self.env, cwd=self.root, timeout=90)
            self.assertEqual(child.returncode, 1, child.stdout + child.stderr)
            self.assertIn('Treesitter parser cannot be loaded: bash: No parser for language "bash"', child.stderr)
            self.assertNotIn('READY: configured Treesitter', child.stdout)
            self.assertNotIn('READY: completion resources', child.stdout)
            self.assertEqual((config / 'lazy-lock.json').read_bytes(), lock)


@unittest.skipUnless(REAL_TOOLS['nvim'], 'native Neovim is required for production Lua progress tests')
class NeovimProgress(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='bootstrap-nvim-progress-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        config = self.root / '.config/nvim'
        config.mkdir(parents=True)
        (config / 'init.lua').write_text('require("lazy").setup({ spec = {} })\n')
        (config / 'lazy-lock.json').write_text('{}\n')
        self.env = dict(os.environ, HOME=str(self.root), XDG_CONFIG_HOME=str(self.root / '.config'),
                        XDG_DATA_HOME=str(self.root / '.local/share'), XDG_STATE_HOME=str(self.root / '.local/state'),
                        XDG_CACHE_HOME=str(self.root / '.cache'), NVIM_LOG_FILE=str(self.root / 'nvim.log'))

    def execute(self, scenario, waiting_for=None, persist=False):
        marker = self.root / (scenario + '.release')
        command = [REAL_TOOLS['nvim'], '--headless', '-u', 'NONE', '-n', '-i', 'NONE', '-l',
                   str(REPO / 'tests/bootstrap/nvim-progress.lua'), str(REPO / 'scripts/bootstrap/nvim.lua'),
                   scenario, str(marker)]
        if persist:
            self.log_path = self.root / 'run.local-http'
            self.env['DOTFILES_TEST_LOG_PATH'] = str(self.log_path)
            command = [BASH, '-c', '"$@" 2>&1 | tee -a "$DOTFILES_TEST_LOG_PATH"; exit "${PIPESTATUS[0]}"',
                       'bootstrap-log-fixture', *command]
        process = subprocess.Popen(command, env=self.env, cwd=self.root, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True)

        def stop_process_group():
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        # 保留退出后的子进程供断言检查；即使用例失败，也只清理本次创建的进程组。
        self.addCleanup(stop_process_group)
        prefix = b''
        try:
            if waiting_for:
                deadline = time.monotonic() + 8
                expected = waiting_for.encode()
                while expected not in prefix:
                    self.assertLess(time.monotonic(), deadline, prefix.decode(errors='replace'))
                    readable, _, _ = select.select([process.stdout], [], [], 0.1)
                    if readable:
                        chunk = os.read(process.stdout.fileno(), 4096)
                        self.assertTrue(chunk, prefix.decode(errors='replace'))
                        prefix += chunk
                    self.assertIsNone(process.poll(), prefix.decode(errors='replace'))
                if waiting_for.startswith('WAIT: '):
                    ready = waiting_for.replace('WAIT: ', 'READY: ', 1).rstrip(';')
                    self.assertNotIn(ready, prefix.decode(errors='replace'))
                    marker.touch()
                else:
                    self.assertIsNone(process.poll(), prefix.decode(errors='replace'))
            output, _ = process.communicate(timeout=8)
        finally:
            if process.poll() is None:
                stop_process_group()
                process.communicate()
        text = (prefix + output).decode(errors='replace')
        timers = json.loads((self.root / (scenario + '.release.timers')).read_text())
        self.assertEqual(timers['started'], timers['stopped'], text)
        self.assertEqual(timers['started'], timers['closed'], text)
        self.assertTrue(timers['notify_restored'], text)
        self.assertTrue(timers['system_restored'], text)
        return process.returncode, text, timers

    def local_http(self, scenario):
        requests = {}
        lock = threading.Lock()
        release = threading.Event()
        seen = self.root / 'http-seen'
        seen.mkdir(exist_ok=True)
        for name in ('bash', 'go'):
            (seen / name).unlink(missing_ok=True)

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                with lock:
                    requests[self.path] = requests.get(self.path, 0) + 1
                    attempt = requests[self.path]
                for name in ('bash', 'go'):
                    if self.path.startswith('/repo/' + name + '/'):
                        (seen / name).touch()
                if scenario == 'curl_cancel' or scenario.startswith('curl_real_'):
                    release.wait(5)
                if scenario == 'curl_http_failure' or (scenario == 'curl_parallel' and
                                                       self.path.startswith('/repo/go/')):
                    status, body = 404, b'missing parser'
                elif self.path.startswith('/repo/bash/') and scenario in ('curl_retry', 'curl_parallel'):
                    status, body = (503, b'temporary outage') if attempt == 1 else (302, b'')
                else:
                    status, body = 200, b'parser archive'
                self.send_response(status)
                if status == 302:
                    self.send_header('Location', '/payload/bash?access_token=redirect-secret')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(release.set)
        self.local_download_env(f'http://user:password@127.0.0.1:{server.server_port}')
        return requests

    def local_download_env(self, base):
        # 本地故障注入始终直连回环地址，不继承使用者的网络代理。
        for name in ('http_proxy', 'https_proxy', 'all_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
            self.env.pop(name, None)
        self.env.update(no_proxy='127.0.0.1,localhost,::1', NO_PROXY='127.0.0.1,localhost,::1')
        self.env['DOTFILES_TEST_CURL_BASE'] = base

    def local_tls(self, scenario):
        # 临时自签证书由 curl 显式信任；保持真实 TLS 验证，不使用 --insecure。
        cert, key, config = (self.root / name for name in ('tls.pem', 'tls.key', 'tls.cnf'))
        config.write_text('[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=ext\n'
                          '[dn]\nCN=localhost\n[ext]\nsubjectAltName=IP:127.0.0.1\n'
                          'basicConstraints=critical,CA:TRUE\n')
        run([shutil.which('openssl'), 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
             '-days', '1', '-keyout', str(key), '-out', str(cert), '-config', str(config)],
            env=self.env, cwd=self.root, timeout=10, check=True)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        evidence = {'connections': [], 'requests': [], 'errors': []}
        lock = threading.Lock()
        release = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                evidence['requests'].append(self.path)
                body = b'parser archive'
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body[:1])
                self.wfile.flush()
                started = time.monotonic()
                if scenario == 'curl_tls_body':
                    release.wait(1.0)  # 已完成 TLS、响应头及首字节，剩余响应体超过 0.3 秒连接上限。
                self.wfile.write(body[1:])
                evidence['body_elapsed'] = time.monotonic() - started

            def log_message(self, *_):
                pass

        class Server(ThreadingHTTPServer):
            def finish_request(self, request, client_address):
                connection = {'started': time.monotonic()}
                with lock:
                    evidence['connections'].append(connection)
                    attempt = len(evidence['connections'])
                # 安全护栏长于 curl 的测试连接上限；触发此护栏会记录错误并令测试失败。
                request.settimeout(5)
                try:
                    if scenario == 'curl_tls_failure' or (scenario == 'curl_tls_retry' and attempt == 1):
                        hello = request.recv(5, socket.MSG_PEEK)
                        connection['client_hello'] = hello[:2] == b'\x16\x03'
                        # 接收 ClientHello 后不回任何 TLS 数据；等待 curl 自行超时并关闭连接。
                        while request.recv(4096):
                            pass
                        connection['client_closed'] = True
                        connection['elapsed'] = time.monotonic() - connection['started']
                    else:
                        with context.wrap_socket(request, server_side=True) as tls:
                            connection['tls_established'] = True
                            Handler(tls, client_address, self)
                except OSError as error:
                    evidence['errors'].append(str(error))

        server = Server(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(release.set)
        self.local_download_env(f'https://127.0.0.1:{server.server_port}')
        self.env['CURL_CA_BUNDLE'] = str(cert)
        return evidence

    def assert_download_command(self, timers, output, name='bash'):
        destination = str(self.root / '.cache/nvim' / ('tree-sitter-' + name + '.tar.gz'))
        commands = [cmd for cmd in timers['system_commands'] if destination in cmd]
        self.assertEqual(len(commands), 1, output)
        command = commands[0]
        base = self.env.get('DOTFILES_TEST_CURL_BASE', 'http://127.0.0.1:1')
        self.assertEqual(command[:-2], [
            'curl', '--no-progress-meter', '--fail', '--show-error', '--retry', '7', '-L',
            base + '/repo/' + name + '/archive/test-revision.tar.gz', '--output', destination,
            '--connect-timeout', '20'], output)
        self.assertEqual(command[-2], '--write-out', output)
        self.assertRegex(output, r'event=start;[^\n]*connect_timeout=20s')

    def assert_tls_stalled(self, evidence, count):
        self.assertEqual(evidence['errors'], [], evidence)
        stalled = [item for item in evidence['connections'] if 'client_hello' in item]
        self.assertEqual(len(stalled), count, evidence)
        for connection in stalled:
            self.assertTrue(connection['client_hello'], evidence)
            self.assertTrue(connection.get('client_closed'), evidence)
            self.assertGreaterEqual(connection['elapsed'], 0.2, evidence)
            self.assertLess(connection['elapsed'], 3, evidence)

    @unittest.skipUnless(shutil.which('curl'), 'real curl is required for local HTTP download tests')
    def test_local_http_download_ignores_inherited_proxy(self):
        proxy_requests = self.local_http('proxy')
        proxy = self.env['DOTFILES_TEST_CURL_BASE']
        self.env.update({name: proxy for name in ('http_proxy', 'https_proxy', 'all_proxy',
                                                  'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY')})
        self.env.update(no_proxy='', NO_PROXY='')
        requests = self.local_http('curl_ok')
        code, output, _ = self.execute('curl_ok')
        self.assertEqual(code, 0, output)
        self.assertEqual(requests.get('/repo/bash/archive/test-revision.tar.gz', 0), 1, output)
        self.assertEqual(proxy_requests, {}, 'local HTTP fixture used an inherited proxy')

    @unittest.skipUnless(os.environ.get('DOTFILES_TEST_PREPARED_HOME') and shutil.which('curl'),
                         'set DOTFILES_TEST_PREPARED_HOME for real Treesitter cancellation tests')
    def test_real_treesitter_reaps_downloads_after_timeout_and_exception(self):
        source = Path(os.environ['DOTFILES_TEST_PREPARED_HOME']) / '.local/share/nvim/lazy/nvim-treesitter'
        locked = json.loads((REPO / 'nvim/.config/nvim/lazy-lock.json').read_text())['nvim-treesitter']['commit']
        actual = run([REAL_TOOLS['git'], '-C', str(source), 'rev-parse', 'HEAD'], check=True).stdout.strip()
        self.assertEqual(actual, locked, 'real installer fixture must match the locked plugin')
        plugin = self.root / 'real-treesitter'
        shutil.copytree(source, plugin, symlinks=True)
        self.env['DOTFILES_TEST_TREESITTER'] = str(plugin)
        for scenario, reason in (('curl_real_cancel', 'Treesitter install timed out'),
                                 ('curl_real_error', 'fixture waiter failed with active downloads'),
                                 ('curl_real_cleanup_failure', 'fixture waiter failed with active downloads')):
            with self.subTest(scenario=scenario):
                requests = self.local_http(scenario)
                code, output, timers = self.execute(scenario)
                self.assertEqual(code, 1, output)
                self.assertIn(reason, output)
                self.assertEqual(output.count('event=cancel;'), 2, output)
                self.assertNotIn('event=finish;', output)
                self.assertNotIn('READY: Neovim / Treesitter / install;', output)
                self.assertEqual(timers['real_curl_exits'], 2, output)
                for name in ('bash', 'go'):
                    self.assert_download_command(timers, output, name)
                failure = output.index('FAIL: Neovim preparation:')
                self.assertNotIn('DOWNLOAD:', output[failure:])
                self.assertIn(reason, output[failure:])
                if scenario == 'curl_real_cleanup_failure':
                    self.assertIn('FAIL: Neovim / Treesitter / download cleanup;', output)
                    self.assertIn('fixture kill diagnostic', output)
                    self.assertNotIn('fixture kill diagnostic', output[failure:])
                for name in ('bash', 'go'):
                    self.assertEqual(requests.get('/repo/' + name + '/archive/test-revision.tar.gz', 0), 1)
                    pid = int((self.root / (scenario + '.release.' + name + '.pid')).read_text())
                    with self.assertRaises(ProcessLookupError, msg=output):
                        os.kill(pid, 0)

    @unittest.skipUnless(shutil.which('curl'), 'real curl is required for local HTTP download tests')
    def test_real_curl_direct_success_reports_final_transfer(self):
        requests = self.local_http('curl_ok')
        code, output, timers = self.execute('curl_ok')
        self.assertEqual(code, 0, output)
        self.assertEqual(requests['/repo/bash/archive/test-revision.tar.gz'], 1)
        self.assertIn('event=finish;', output)
        self.assertIn('exit=0; retry_notices=0;', output)
        self.assertIn('http=200; bytes=14;', output)
        self.assertNotIn('event=retry;', output)
        self.assertNotIn('PARSER FAIL:', output)
        self.assertEqual(timers['curl_callbacks'], 1)
        self.assert_download_command(timers, output)

    @unittest.skipUnless(shutil.which('curl') and shutil.which('openssl'),
                         'real curl and openssl are required for local TLS timeout tests')
    def test_real_curl_tls_connect_timeout_retries_and_succeeds(self):
        evidence = self.local_tls('curl_tls_retry')
        code, output, timers = self.execute('curl_tls_retry', 'event=retry;', persist=True)
        self.assertEqual(code, 0, output)
        self.assert_download_command(timers, output)
        self.assert_tls_stalled(evidence, 1)
        self.assertEqual(len(evidence['connections']), 2, evidence)
        self.assertTrue(evidence['connections'][1]['tls_established'], evidence)
        self.assertEqual(evidence['requests'], ['/repo/bash/archive/test-revision.tar.gz'])
        self.assertRegex(output, r'event=stderr;[^\n]*curl: \(28\)')
        self.assertRegex(output, r'event=retry;[^\n]*[Tt]imeout[^\n]*\b1 seconds?\b')
        self.assertRegex(output, r'event=finish;[^\n]*exit=0; retry_notices=1;[^\n]*http=200;')
        self.assertLess(output.index('curl: (28)'), output.index('event=retry;'))
        self.assertLess(output.index('event=retry;'), output.index('event=finish;'))
        self.assertIn('curl: (28)', timers['curl_results']['bash']['stderr'])
        self.assertIn('curl: (28)', self.log_path.read_text())
        self.assertIn('event=retry;', self.log_path.read_text())
        self.assertIn('READY: configured Treesitter parsers installed and loadable', output)
        self.assertNotIn('PARSER FAIL:', output)
        self.assertEqual(timers['curl_callbacks'], 1)
        self.assertEqual((self.root / '.cache/nvim/tree-sitter-bash.tar.gz').read_bytes(), b'parser archive')

    @unittest.skipUnless(shutil.which('curl') and shutil.which('openssl'),
                         'real curl and openssl are required for local TLS timeout tests')
    def test_real_curl_tls_connect_timeout_exhaustion_fails(self):
        evidence = self.local_tls('curl_tls_failure')
        code, output, timers = self.execute('curl_tls_failure')
        self.assertEqual(code, 1, output)
        self.assert_download_command(timers, output)
        self.assert_tls_stalled(evidence, 2)
        self.assertEqual(len(evidence['connections']), 2, evidence)
        self.assertEqual(evidence['requests'], [])
        self.assertEqual(len(re.findall(r'event=stderr;[^\n]*curl: \(28\)', output)), 2, output)
        self.assertRegex(output, r'event=finish;[^\n]*exit=28; retry_notices=1;')
        self.assertIn('PARSER FAIL: Neovim / Treesitter / install / bash;', output)
        self.assertIn('curl: (28)', output[output.index('FAIL: Neovim preparation:'):])
        self.assertNotIn('READY: Neovim / Treesitter / install;', output)
        self.assertNotIn('READY: configured Treesitter', output)
        self.assertNotIn('READY: completion resources', output)
        self.assertNotIn('event=cancel;', output)
        self.assertEqual(timers['curl_callbacks'], 1)
        self.assertEqual(timers['curl_results']['bash']['code'], 28)

    @unittest.skipUnless(shutil.which('curl') and shutil.which('openssl'),
                         'real curl and openssl are required for local TLS timeout tests')
    def test_real_curl_body_can_outlast_connect_timeout(self):
        evidence = self.local_tls('curl_tls_body')
        code, output, timers = self.execute('curl_tls_body')
        self.assertEqual(code, 0, output)
        self.assert_download_command(timers, output)
        self.assertEqual(evidence['errors'], [], evidence)
        self.assertEqual(len(evidence['connections']), 1, evidence)
        self.assertTrue(evidence['connections'][0]['tls_established'], evidence)
        args = timers['executed_curl']
        limit = float(args[args.index('--connect-timeout') + 1])
        self.assertEqual(limit, 0.3)
        self.assertGreater(evidence['body_elapsed'], limit * 2, evidence)
        self.assertEqual((self.root / '.cache/nvim/tree-sitter-bash.tar.gz').read_bytes(), b'parser archive')
        finish = next(line for line in output.splitlines() if 'event=finish;' in line)
        self.assertGreater(float(re.search(r'curl_total=([\d.]+)', finish).group(1)), limit)
        self.assertIn('exit=0; retry_notices=0;', finish)
        self.assertNotIn('event=retry;', output)
        self.assertNotIn('event=stderr;', output)
        self.assertIn('READY: configured Treesitter parsers installed and loadable', output)

    @unittest.skipUnless(shutil.which('curl'), 'real curl is required for local HTTP download tests')
    def test_real_curl_retry_succeeds_with_live_reason_and_redirect_summary(self):
        requests = self.local_http('curl_retry')
        code, output, timers = self.execute('curl_retry', 'event=retry', persist=True)
        self.assertEqual(code, 0, output)
        self.assertEqual(requests['/repo/bash/archive/test-revision.tar.gz'], 2)
        self.assertIn('event=retry;', output)
        self.assertLess(output.index('event=retry;'), output.index('event=finish;'))
        self.assertIn('http=200; bytes=14;', output)
        self.assertIn('final_url=http://127.0.0.1:', output)
        self.assertIn('/payload/bash?<redacted>', output)
        self.assertIn('retry_notices=1;', output)
        self.assertNotIn('PARSER FAIL:', output)
        self.assertNotIn('password', output)
        self.assertNotIn('redirect-secret', output)
        self.assertEqual(timers['curl_callbacks'], 1)
        self.assertIn('DOWNLOAD: Neovim / Treesitter / install / bash;', self.log_path.read_text())
        self.assertIn('event=retry;', self.log_path.read_text())
        finish = next(line for line in output.splitlines() if 'event=finish;' in line)
        wall = float(re.search(r'wall_elapsed=([\d.]+)s', finish).group(1))
        curl_total = float(re.search(r'curl_total=([\d.]+)', finish).group(1))
        self.assertGreaterEqual(wall, 0.8)
        self.assertLess(curl_total, wall)

    @unittest.skipUnless(shutil.which('curl'), 'real curl is required for local HTTP download tests')
    def test_real_curl_nonretryable_http_error_fails_with_original_reason(self):
        requests = self.local_http('curl_http_failure')
        code, output, timers = self.execute('curl_http_failure')
        self.assertEqual(code, 1, output)
        self.assertEqual(requests['/repo/bash/archive/test-revision.tar.gz'], 1)
        self.assertRegex(output, r'event=finish;[^\n]*exit=22;')
        self.assertIn('http=404;', output)
        self.assertIn('curl: (22) The requested URL returned error: 404', output)
        self.assertIn('PARSER FAIL: Neovim / Treesitter / install / bash;', output)
        self.assertNotIn('READY: Neovim / Treesitter / install;', output)
        self.assertEqual(timers['curl_callbacks'], 1)

    @unittest.skipUnless(shutil.which('curl'), 'real curl is required for local HTTP download tests')
    def test_real_curl_slow_response_reports_wait_and_kills_cancelled_request(self):
        self.local_http('curl_cancel')
        code, output, timers = self.execute('curl_cancel', 'WAIT: Neovim / Treesitter / install;')
        self.assertEqual(code, 1, output)
        self.assertIn('event=cancel;', output)
        self.assertNotIn('event=finish;', output)
        self.assertNotIn('READY: Neovim / Treesitter / install;', output)
        self.assertEqual(timers['task_closed'], 1)
        self.assertNotIn('curl_callbacks', timers)
        pid = int((self.root / 'curl_cancel.release.bash.pid').read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    @unittest.skipUnless(shutil.which('curl'), 'real curl is required for local HTTP download tests')
    def test_real_curl_parallel_requests_keep_parser_and_error_attribution(self):
        requests = self.local_http('curl_parallel')
        code, output, timers = self.execute('curl_parallel', 'event=retry')
        self.assertEqual(code, 1, output)
        self.assertEqual(requests['/repo/bash/archive/test-revision.tar.gz'], 2)
        self.assertEqual(requests['/repo/go/archive/test-revision.tar.gz'], 1)
        starts = re.findall(r'DOWNLOAD: Neovim / Treesitter / install / (\w+); request=(\d+); event=start;', output)
        self.assertEqual(len(starts), 2, output)
        self.assertEqual({name for name, _ in starts}, {'bash', 'go'})
        self.assertEqual(len({request for _, request in starts}), 2)
        self.assertRegex(output, r'DOWNLOAD: Neovim / Treesitter / install / bash; request=\d+; event=finish;[^\n]*http=200;')
        self.assertRegex(output, r'DOWNLOAD: Neovim / Treesitter / install / go; request=\d+; event=finish;[^\n]*http=404;')
        self.assertRegex(output, r'DOWNLOAD: Neovim / Treesitter / install / bash; request=\d+; event=stderr;[^\n]*error: 503')
        self.assertRegex(output, r'DOWNLOAD: Neovim / Treesitter / install / go; request=\d+; event=stderr;[^\n]*error: 404')
        self.assertRegex(output, r'downloads=bash \(request=\d+ retry \(Warning: Problem : HTTP error')
        self.assertIn('PARSER FAIL: Neovim / Treesitter / install / go;', output)
        self.assertNotIn('PARSER FAIL: Neovim / Treesitter / install / bash;', output)
        self.assertEqual(timers['curl_callbacks'], 2)

    def test_missing_curl_writeout_fields_keep_success_and_redact_error(self):
        bindir = self.root / 'bin'
        bindir.mkdir()
        curl = bindir / 'curl'
        curl.write_text('#!/bin/sh\nif [ "$1" = --version ]; then echo "curl 7.88.0"; exit 0; fi\n'
                        'printf "Warning: reconnecting https://user:password@example.test/token/path-secret?token=private\\n" >&2\n'
                        'exit 0\n')
        curl.chmod(0o755)
        self.env['PATH'] = str(bindir) + ':' + self.env['PATH']
        code, output, timers = self.execute('curl_no_stats')
        self.assertEqual(code, 0, output)
        self.assertIn('http=unavailable; bytes=unavailable; final_url=unavailable;', output)
        self.assertIn('retries=unavailable', output)
        self.assertNotIn('password', output)
        self.assertNotIn('private', output)
        self.assertNotIn('path-secret', output)
        self.assertEqual(timers['curl_callbacks'], 1)
        self.assertTrue(timers['non_target_original'])
        self.assertTrue(timers['unrelated_curl_original'])
        self.assert_download_command(timers, output)
        self.assertEqual(len(timers['system_commands']), 4)
        self.assertIn(['curl', '--version'], timers['system_commands'])
        self.assertIn(['sh', '-c', 'printf unchanged'], timers['system_commands'])
        self.assertIn(['curl', '--silent', '--fail', '--show-error', '--retry', '7', '-L',
                       'http://example.invalid/unrelated', '--output',
                       str(self.root / '.cache/nvim/unrelated.tar.gz')], timers['system_commands'])

    def test_cache_reuse_and_distinct_treesitter_phases(self):
        code, output, _ = self.execute('reuse')
        self.assertEqual(code, 0, output)
        for phase in ('install', 'update', 'verify'):
            self.assertIn('RUN: Neovim / Treesitter / ' + phase, output)
            self.assertIn('READY: Neovim / Treesitter / ' + phase, output)
        self.assertNotIn('PARSER: Neovim / Treesitter / install /', output)
        self.assertNotIn('PARSER: Neovim / Treesitter / update /', output)
        self.assertNotIn('DOWNLOAD:', output)
        self.assertIn('PARSER: Neovim / Treesitter / verify / bash; action=load', output)

    def test_slow_install_and_existing_parser_update_report_real_task_events(self):
        for scenario, phase in (('install_wait', 'install'), ('update_wait', 'update')):
            with self.subTest(scenario=scenario):
                code, output, _ = self.execute(scenario, 'WAIT: Neovim / Treesitter / ' + phase + ';')
                self.assertEqual(code, 0, output)
                self.assertIn('timeout=600s; active=bash (Downloading tree-sitter-bash...)', output)
                self.assertIn('PARSER: Neovim / Treesitter / ' + phase + ' / bash;', output)
                self.assertIn('action=Compiling parser', output)
                self.assertIn('action=Installing parser', output)
                self.assertLess(output.index('WAIT: Neovim / Treesitter / ' + phase),
                                output.index('READY: Neovim / Treesitter / ' + phase))
                self.assertNotIn('WAIT:', output[output.index('READY: Neovim / Treesitter / ' + phase):])

    def test_registry_mason_and_completion_waits_identify_their_own_action(self):
        for scenario, label, timeout in (('registry_wait', 'Mason / registry refresh', '300s'),
                                         ('mason_wait', 'Mason / install / taplo', '600s'),
                                         ('completion_wait', 'completion / resources', '300s')):
            with self.subTest(scenario=scenario):
                code, output, _ = self.execute(scenario, 'WAIT: Neovim / ' + label + ';')
                self.assertEqual(code, 0, output)
                self.assertIn('WAIT: Neovim / ' + label + '; elapsed=', output)
                self.assertIn('timeout=' + timeout, output)
                self.assertIn('READY: Neovim / ' + label, output)

    def test_later_plugin_error_notifications_fail_preparation(self):
        for scenario, reason in (
                ('mason_notify_error', 'fixture config failed: mason.nvim'),
                ('treesitter_notify_error', 'fixture config failed: nvim-treesitter'),
                ('completion_notify_error', 'fixture config failed: blink.cmp'),
                ('completion_async_notify_error', 'fixture completion callback failed')):
            with self.subTest(scenario=scenario):
                code, output, _ = self.execute(scenario)
                self.assertEqual(code, 1, output)
                failure = output[output.index('FAIL: Neovim preparation:'):]
                self.assertIn(reason, failure)

    def test_warning_notification_does_not_fail_preparation(self):
        code, output, _ = self.execute('treesitter_notify_warning')
        self.assertEqual(code, 0, output)
        self.assertIn('fixture config warning: nvim-treesitter', output)
        self.assertNotIn('FAIL: Neovim preparation:', output)

    def test_parser_failure_and_timeout_keep_reason_and_clear_timer(self):
        code, output, _ = self.execute('install_failure', 'WAIT: Neovim / Treesitter / install;')
        self.assertEqual(code, 1, output)
        self.assertIn('PARSER FAIL: Neovim / Treesitter / install / bash;', output)
        self.assertIn('Error during "tree-sitter build": clang: bad grammar', output)
        self.assertIn('FAIL: Neovim / Treesitter / install;', output)
        self.assertNotIn('READY: Neovim / Treesitter / install', output)
        self.assertLess(output.rfind('WAIT:'), output.index('FAIL: Neovim / Treesitter / install;'))

        code, output, timers = self.execute('install_timeout')
        self.assertEqual(code, 1, output)
        self.assertIn('WAIT: Neovim / Treesitter / install;', output)
        self.assertIn('Treesitter install timed out; active parser=bash', output)
        self.assertNotIn('READY: Neovim / Treesitter / install', output)
        self.assertEqual(timers['task_closed'], 1)
        self.assertLess(output.rfind('WAIT:'), output.index('FAIL: Neovim / Treesitter / install;'))

    def test_registry_mason_verify_and_lock_failures(self):
        for scenario, reason, absent in (
                ('setup_failure', 'fixture plugin setup failed', 'READY: Neovim / plugins / setup'),
                ('registry_failure', 'Mason registry refresh failed: registry transport refused',
                 'READY: Neovim / Mason / registry refresh'),
                ('mason_failure', 'Mason installation failed: taplo: archive checksum mismatch',
                 'READY: Neovim / Mason / install / taplo'),
                ('mason_not_installed', 'Mason package is not installed: taplo',
                 'READY: Neovim / Mason / install / taplo'),
                ('verify_failure', 'Treesitter parser cannot be loaded: bash: No parser for language "bash"',
                 'READY: Neovim / Treesitter / verify'),
                ('lock_failure', 'bootstrap changed the lockfile', 'Bootstrap complete.')):
            with self.subTest(scenario=scenario):
                wait = ('WAIT: Neovim / Mason / install / taplo;' if scenario in
                        ('mason_failure', 'mason_not_installed') else None)
                code, output, _ = self.execute(scenario, wait)
                self.assertEqual(code, 1, output)
                self.assertIn(reason, output)
                self.assertNotIn(absent, output)


class Publication(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bootstrap-resources-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / 'home'
        self.home.mkdir()
        # 直接调用资源管理代码会继承当前进程环境；创建或检查测试仓库前，
        # 先隔离 Git 的仓库选择、配置和钩子来源。
        environment = patch.dict(os.environ, {'HOME': str(self.home), 'PATH': os.environ['PATH'],
                                 'TMPDIR': str(self.root), 'LC_ALL': 'C',
                                 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.archive = self.root / 'payload.tar.gz'
        with tarfile.open(self.archive, 'w:gz') as archive:
            info = tarfile.TarInfo('bundle/bin/tool')
            content = b'#!/bin/sh\nexit 0\n'
            info.size = len(content)
            info.mode = 0o755
            archive.addfile(info, io.BytesIO(content))
        self.manifest = {'tool': {'version': 'v1', 'kind': 'archive', 'links': {'.local/bin/tool': 'bin/tool'},
                         'assets': {'linux-arm64': {'url': 'https://example.test/payload', 'sha256': resources.digest(self.archive)}}}}
        self.manager = resources.Resources(self.home, self.manifest)

    def seed_cache(self):
        self.manager.cache.mkdir(parents=True)
        shutil.copyfile(self.archive, self.manager.cache / resources.digest(self.archive))

    def go_release(self, version, stable=True):
        return {'version': version, 'stable': stable, 'files': [
            {'filename': f'{version}.{go_os}-{arch}.tar.gz', 'os': go_os, 'arch': arch,
             'version': version, 'sha256': resources.digest(self.archive), 'kind': 'archive'}
            for go_os, arch in (('darwin', 'arm64'), ('linux', 'arm64'), ('linux', 'amd64'))]}

    def go_archive(self, version):
        with tarfile.open(self.archive, 'w:gz') as archive:
            for name in ('go', 'gofmt'):
                info = tarfile.TarInfo('go/bin/' + name)
                content = f'#!/bin/sh\nprintf "%s\\n" "go version {version} fixture"\n'.encode()
                info.size, info.mode = len(content), 0o755
                archive.addfile(info, io.BytesIO(content))

    def jdk_release(self, key, release_name='jdk-25.0.4.1+1'):
        jdk_os, arch = {'macos-arm64': ('mac', 'aarch64'), 'linux-arm64': ('linux', 'aarch64'),
                        'linux-x86_64': ('linux', 'x64')}[key]
        filename = f'OpenJDK25U-jdk_{arch}_{jdk_os}_hotspot_{release_name[4:].replace("+", "_")}.tar.gz'
        return {'binary': {'architecture': arch, 'image_type': 'jdk', 'jvm_impl': 'hotspot', 'os': jdk_os,
                           'package': {'checksum': resources.digest(self.archive), 'name': filename,
                                       'link': 'https://example.test/' + filename}},
                'release_name': release_name, 'vendor': 'eclipse',
                'version': {'major': 25, 'openjdk_version': release_name[4:]}}

    def jdk_archive(self, release_name, *, mac=False, include_javac=True):
        home = release_name + ('/Contents/Home' if mac else '')
        with tarfile.open(self.archive, 'w:gz') as archive:
            for name in ('java', 'javac') if include_javac else ('java',):
                info = tarfile.TarInfo(home + '/bin/' + name)
                content = b'#!/bin/sh\nexit 0\n'
                info.size, info.mode = len(content), 0o755
                archive.addfile(info, io.BytesIO(content))

    @staticmethod
    def maven_metadata(*versions):
        values = ''.join(f'<version>{version}</version>' for version in versions)
        return '<metadata><versioning><latest>4.0.0-rc-5</latest><versions>' + values + '</versions></versioning></metadata>'

    def maven_archive(self, version):
        with tarfile.open(self.archive, 'w:gz') as archive:
            info = tarfile.TarInfo(f'apache-maven-{version}/bin/mvn')
            content = b'#!/bin/sh\nexit 0\n'
            info.size, info.mode = len(content), 0o755
            archive.addfile(info, io.BytesIO(content))

    def test_go_release_selects_latest_stable_numerically_for_each_platform(self):
        releases = [self.go_release('go1.27.9'), self.go_release('go1.9.9'),
                    self.go_release('go1.28rc1', stable=False), self.go_release('go1.27.10')]
        for key, suffix in (('macos-arm64', 'darwin-arm64'), ('linux-arm64', 'linux-arm64'),
                            ('linux-x86_64', 'linux-amd64')):
            with self.subTest(platform=key), patch.object(resources, 'run', return_value=json.dumps(releases)):
                item = resources.latest_go_release(key)
                self.assertEqual(item['version'], 'go1.27.10')
                self.assertEqual(item['assets'][key], {
                    'url': f'https://go.dev/dl/go1.27.10.{suffix}.tar.gz',
                    'sha256': resources.digest(self.archive)})

    def test_go_release_rejects_unusable_metadata_without_falling_back(self):
        missing = self.go_release('go1.27.1')
        missing['files'] = []
        bad_hash = self.go_release('go1.27.1')
        bad_hash['files'][0]['sha256'] = 'invalid'
        bad_path = self.go_release('go1.27.1')
        bad_path['files'][0]['filename'] = '../../other.tar.gz'
        duplicate = self.go_release('go1.27.1')
        duplicate['files'].append(duplicate['files'][0])
        cases = [('not a list', {}), ('no stable release', [self.go_release('go1.28rc1', stable=False)]),
                 ('latest has no archive', [self.go_release('go1.26.9'), missing]),
                 ('invalid checksum', [bad_hash]), ('invalid archive path', [bad_path]),
                 ('ambiguous archive', [duplicate])]
        for label, releases in cases:
            with self.subTest(case=label), patch.object(resources, 'run', return_value=json.dumps(releases)):
                with self.assertRaisesRegex(RuntimeError, 'Go'):
                    resources.latest_go_release('macos-arm64')
        with patch.object(resources, 'run') as lookup:
            with self.assertRaisesRegex(RuntimeError, 'unsupported Go platform'):
                resources.latest_go_release('linux-riscv64')
            lookup.assert_not_called()

    def test_jdk_release_validates_official_latest_schema_and_platform_layout(self):
        self.jdk_archive('jdk-25.0.4.1+1', mac=True)
        for key, expected in (('macos-arm64', ('os=mac', 'architecture=aarch64', '/Contents/Home')),
                              ('linux-arm64', ('os=linux', 'architecture=aarch64', 'jdk-25.0.4.1+1')),
                              ('linux-x86_64', ('os=linux', 'architecture=x64', 'jdk-25.0.4.1+1'))):
            release = self.jdk_release(key)
            with self.subTest(platform=key), patch.object(resources, 'run', return_value=json.dumps([release])) as lookup:
                item = resources.latest_jdk_release(key)
                self.assertEqual(item['version'], 'jdk-25.0.4.1+1')
                self.assertTrue(all(part in lookup.call_args.args[0][-1] for part in expected[:2]))
                self.assertTrue(next(iter(item['links'].values())).endswith(expected[2]))
                self.assertEqual(len(item['required_executables']), 2)

    def test_jdk_release_rejects_early_access_incomplete_and_ambiguous_metadata(self):
        self.jdk_archive('jdk-25.0.4.1+1')
        valid = self.jdk_release('linux-arm64')
        cases = []
        early = dict(valid, release_name='jdk-25-ea+20')
        cases.append(('early access', [early]))
        jre = json.loads(json.dumps(valid))
        jre['binary']['image_type'] = 'jre'
        cases.append(('not a JDK', [jre]))
        bad_hash = json.loads(json.dumps(valid))
        bad_hash['binary']['package']['checksum'] = 'invalid'
        cases.append(('bad checksum', [bad_hash]))
        cases.append(('ambiguous', [valid, valid]))
        for label, releases in cases:
            with self.subTest(case=label), patch.object(resources, 'run', return_value=json.dumps(releases)):
                with self.assertRaisesRegex(RuntimeError, 'JDK'):
                    resources.latest_jdk_release('linux-arm64')
        with patch.object(resources, 'run') as lookup:
            with self.assertRaisesRegex(RuntimeError, 'unsupported JDK platform'):
                resources.latest_jdk_release('linux-riscv64')
            lookup.assert_not_called()

    def test_maven_release_selects_latest_3_9_and_uses_sha512(self):
        metadata = self.maven_metadata('3.9.9', '4.0.0-rc-5', '3.9.16', '3.10.0-beta-1', '3.9.15')
        checksum = 'ab' * 64
        with patch.object(resources, 'run', side_effect=[metadata, checksum]):
            item = resources.latest_maven_release('linux-x86_64')
        self.assertEqual(item['version'], '3.9.16')
        self.assertEqual(item['assets']['linux-x86_64']['sha512'], checksum)
        self.assertTrue(item['assets']['linux-x86_64']['url'].endswith('/3.9.16/apache-maven-3.9.16-bin.tar.gz'))
        for metadata, checksum in ((self.maven_metadata('4.0.0-rc-5'), 'ab' * 64),
                                   (self.maven_metadata('3.9.16'), 'invalid')):
            with self.subTest(metadata=metadata, checksum=checksum), patch.object(
                    resources, 'run', side_effect=[metadata, checksum]):
                with self.assertRaisesRegex(RuntimeError, 'Maven'):
                    resources.latest_maven_release('macos-arm64')

    def test_jdk_publication_requires_complete_executable_payload_and_keeps_current(self):
        self.jdk_archive('jdk-25.0.4.1+1')
        release = self.jdk_release('linux-arm64')
        def download(args, **kwargs):
            if args[-1].startswith(resources.ADOPTIUM_RELEASES_URL):
                return json.dumps([release])
            shutil.copyfile(self.archive, args[args.index('--output') + 1])
            return ''
        with patch.object(resources, 'run', side_effect=download):
            self.manager.install_jdk('linux-arm64')
        current = self.manager.root / 'jdk/current'
        previous = current.resolve()
        self.assertTrue((current / 'bin/java').is_file())
        self.assertTrue((current / 'bin/javac').is_file())
        self.jdk_archive('jdk-25.0.5+2', include_javac=False)
        release = self.jdk_release('linux-arm64', 'jdk-25.0.5+2')
        with patch.object(resources, 'run', side_effect=download):
            with self.assertRaisesRegex(RuntimeError, 'bin/javac'):
                self.manager.install_jdk('linux-arm64')
        self.assertEqual(current.resolve(), previous)
        self.assertTrue(previous.exists())
        cached = self.manager.root / 'jdk/jdk-25.0.5+2-linux-arm64'
        java = cached / 'jdk-25.0.5+2/bin/java'
        java.parent.mkdir(parents=True)
        java.write_text('#!/bin/sh\nexit 0\n')
        java.chmod(0o755)
        (cached / '.ready.json').write_text(json.dumps({'sha256': resources.digest(self.archive)}) + '\n')
        with patch.object(resources, 'run', return_value=json.dumps([release])):
            with self.assertRaisesRegex(RuntimeError, 'bin/javac'):
                self.manager.install_jdk('linux-arm64')
        self.assertEqual(current.resolve(), previous, 'a damaged cached JDK must not replace current')

    def test_maven_sha512_archive_is_verified_published_and_reused(self):
        version = '3.9.16'
        self.maven_archive(version)
        metadata = self.maven_metadata('3.9.9', version)
        checksum = resources.digest(self.archive, 'sha512')
        def download(args, **kwargs):
            if args[-1] == resources.MAVEN_METADATA_URL:
                return metadata
            if args[-1].endswith('.sha512'):
                return checksum + '\n'
            shutil.copyfile(self.archive, args[args.index('--output') + 1])
            return ''
        with patch.object(resources, 'run', side_effect=download) as downloads:
            self.manager.install_maven('macos-arm64')
            command = self.home / '.local/bin/mvn'
            self.assertTrue(command.is_symlink())
            self.assertEqual(run([str(command)]).returncode, 0)
            receipt = self.manager.root / f'maven/{version}-macos-arm64/.ready.json'
            self.assertEqual(json.loads(receipt.read_text()), {'sha512': checksum})
            before = snapshot(self.home)
            self.manager.install_maven('macos-arm64')
            self.assertEqual(snapshot(self.home), before)
            self.assertEqual(downloads.call_count, 5)  # 两次元数据/校验和解析，只下载一次归档。

    def test_latest_go_archive_is_verified_installed_and_reused(self):
        self.go_archive('go1.27.1')
        release = self.go_release('go1.27.1')
        def download(args, **kwargs):
            if args[-1] == resources.GO_RELEASES_URL:
                return json.dumps([release])
            self.assertEqual(args[-1], 'https://go.dev/dl/go1.27.1.darwin-arm64.tar.gz')
            shutil.copyfile(self.archive, args[args.index('--output') + 1])
            return ''
        with patch.object(resources, 'run', side_effect=download) as downloads:
            self.manager.install_go('macos-arm64')
            for name in ('go', 'gofmt'):
                target = self.home / '.local/bin' / name
                self.assertEqual(target.resolve(), self.manager.root / 'go/go1.27.1-macos-arm64/go/bin' / name)
                self.assertIn('go1.27.1', run([str(target), 'version'], check=True).stdout)
            receipt = self.manager.root / 'go/go1.27.1-macos-arm64/.ready.json'
            self.assertEqual(json.loads(receipt.read_text()), {'sha256': resources.digest(self.archive)})
            before = snapshot(self.home)
            self.manager.install_go('macos-arm64')
            self.assertEqual(snapshot(self.home), before)
            self.assertEqual(downloads.call_count, 3)  # 两次版本解析，只下载一次归档。

    def test_latest_go_corrupt_download_and_lookup_failure_keep_previous_installation(self):
        self.go_archive('go1.27.0')
        release = self.go_release('go1.27.0')
        def download(args, **kwargs):
            if args[-1] == resources.GO_RELEASES_URL:
                return json.dumps([release])
            shutil.copyfile(self.archive, args[args.index('--output') + 1])
            return ''
        with patch.object(resources, 'run', side_effect=download):
            self.manager.install_go('linux-arm64')
        targets = {name: (self.home / '.local/bin' / name).resolve() for name in ('go', 'gofmt')}
        before = snapshot(self.home)
        with patch.object(resources, 'run', side_effect=RuntimeError('Go release lookup failed')):
            with self.assertRaisesRegex(RuntimeError, 'lookup failed'):
                self.manager.install_go('linux-arm64')
        self.assertEqual(snapshot(self.home), before)
        self.go_archive('go1.27.1')
        release = self.go_release('go1.27.1')
        def corrupt_download(args, **kwargs):
            if args[-1] == resources.GO_RELEASES_URL:
                return json.dumps([release])
            Path(args[args.index('--output') + 1]).write_bytes(b'corrupted')
            return ''
        with patch.object(resources, 'run', side_effect=corrupt_download):
            with self.assertRaisesRegex(RuntimeError, 'SHA256 mismatch'):
                self.manager.install_go('linux-arm64')
        for name, previous in targets.items():
            self.assertEqual((self.home / '.local/bin' / name).resolve(), previous)
            self.assertTrue(previous.exists())
        self.assertFalse((self.manager.root / 'go/go1.27.1-linux-arm64').exists())
        self.assertEqual(list(self.manager.cache.glob('.download-*')), [])

    def test_verified_archive_installs_atomically_and_reuses(self):
        self.seed_cache()
        self.manager.install('tool', 'linux-arm64')
        target = self.home / '.local/bin/tool'
        self.assertTrue(target.is_symlink())
        self.assertEqual(run([str(target)]).returncode, 0)
        before = snapshot(self.home)
        self.manager.install('tool', 'linux-arm64')
        self.assertEqual(snapshot(self.home), before)

    def test_unmanaged_file_and_symlink_preserved(self):
        target = self.home / '.local/bin/tool'
        target.parent.mkdir(parents=True)
        target.write_text('keep')
        with self.assertRaisesRegex(RuntimeError, 'manual review'):
            self.manager.install('tool', 'linux-arm64')
        self.assertEqual(target.read_text(), 'keep')
        target.unlink()
        target.symlink_to('/usr/bin/true')
        with self.assertRaisesRegex(RuntimeError, 'manual review'):
            self.manager.install('tool', 'linux-arm64')
        self.assertEqual(os.readlink(target), '/usr/bin/true')

    def test_parent_symlink_cannot_redirect_publication(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (self.home / '.local').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'symbolic link'):
            self.manager.install('tool', 'linux-arm64')
        self.assertEqual(list(outside.iterdir()), [])

    def test_download_hash_failure_does_not_publish(self):
        def fake_download(args, **kwargs):
            Path(args[args.index('--output') + 1]).write_bytes(b'corrupted')
            return ''
        with patch.object(resources, 'run', side_effect=fake_download):
            with self.assertRaisesRegex(RuntimeError, 'SHA256 mismatch'):
                self.manager.install('tool', 'linux-arm64')
        self.assertFalse((self.home / '.local/bin/tool').exists())
        self.assertFalse(list(self.manager.cache.glob('.download-*')))
        self.seed_cache_after_failure()
        self.manager.install('tool', 'linux-arm64')

    def seed_cache_after_failure(self):
        self.manager.cache.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.archive, self.manager.cache / resources.digest(self.archive))

    def test_path_traversal_and_escaping_links_are_rejected(self):
        for name, link in [('../escaped', None), ('/absolute', None), ('escape', '../../escaped')]:
            archive_path = self.root / 'bad.tar'
            with tarfile.open(archive_path, 'w') as archive:
                info = tarfile.TarInfo(name)
                if link:
                    info.type = tarfile.SYMTYPE
                    info.linkname = link
                archive.addfile(info)
            with tempfile.TemporaryDirectory(dir=self.root) as destination:
                with self.assertRaises(RuntimeError):
                    resources.extract(archive_path, Path(destination), 'archive', 'unused')
            self.assertFalse((self.root / 'escaped').exists())

    def test_internal_symlink_payload_supported(self):
        archive_path = self.root / 'links.tar'
        with tarfile.open(archive_path, 'w') as archive:
            info = tarfile.TarInfo('bundle/bin/alias')
            info.type = tarfile.SYMTYPE
            info.linkname = '../lib/tool'
            archive.addfile(info)
            info = tarfile.TarInfo('bundle/lib/tool')
            info.size = 2
            archive.addfile(info, io.BytesIO(b'ok'))
        destination = self.root / 'unpacked'
        destination.mkdir()
        resources.extract(archive_path, destination, 'archive', '')
        self.assertEqual((destination / 'bundle/bin/alias').read_bytes(), b'ok')

    def test_node_entry_prefers_top_level_npm_over_nested_package_file(self):
        unpacked = self.root / 'node-layout'
        top = unpacked / 'node-v24/bin/npm'
        nested = unpacked / 'node-v24/lib/node_modules/npm/bin/npm'
        nested.parent.mkdir(parents=True)
        nested.write_text('nested npm command')
        top.parent.mkdir(parents=True)
        top.symlink_to('../lib/node_modules/npm/bin/npm')
        self.assertEqual(resources.Resources.locate(unpacked, 'bin/npm'), top)

    def test_requirements_and_platform_manifests_agree(self):
        rows = [line.split('\t') for line in (REPO / 'scripts/bootstrap/requirements.tsv').read_text().splitlines()
                if line and not line.startswith('#')]
        requirements = {}
        for row in rows:
            self.assertEqual(len(row), 4, row)
            name, minimum, scope, platform = row
            self.assertNotIn(name, requirements, name)
            self.assertRegex(minimum, r'^(0|[0-9]+\.[0-9]+\.[0-9]+)$')
            self.assertIn(scope, ('base', 'desktop'))
            self.assertIn(platform, ('any', 'linux', 'macos'))
            requirements[name] = (scope, platform)
        # 共享入口解析官方 Go、JDK 与 Maven，独立于各平台包清单。
        for name in ('go', 'java', 'javac', 'mvn'):
            self.assertEqual(requirements.pop(name), ('base', 'any'))
        manifest = json.loads((REPO / 'scripts/bootstrap/releases.json').read_text())
        for scope in ('base', 'desktop'):
            for release in ('24.04', '26.04'):
                command = r'''
script_dir=$1; os_version=$2
source "$script_dir/bootstrap/ubuntu/install.bash"
ubuntu_load_packages "$3"
for name in "${release_tools[@]}" "${apt_fallbacks[@]}" "${deb_releases[@]}"; do printf 'RESOURCE:%s\n' "$name"; done
for entry in "${apt_packages[@]}" "${release_tools[@]}" "${apt_fallbacks[@]}" "${deb_releases[@]}"; do
  commands=$entry
  for mapping in "${apt_commands[@]}" "${release_commands[@]}"; do
    if [[ ${mapping%%|*} == "$entry" ]]; then commands=${mapping#*|}; break; fi
  done
  for name in $commands; do printf 'COMMAND:%s\n' "$name"; done
done
'''
                result = run([BASH, '-c', command, 'manifest-check', str(REPO / 'scripts'), release, scope],
                             check=True)
                provided = set()
                for entry in result.stdout.splitlines():
                    kind, name = entry.split(':', 1)
                    if kind == 'RESOURCE':
                        self.assertIn(name, manifest, (release, scope, name))
                    else:
                        self.assertIn(name, requirements, (release, scope, name))
                        provided.add(name)
                expected = {name for name, (required_scope, platform) in requirements.items()
                            if required_scope == scope and platform in ('any', 'linux')}
                self.assertEqual(provided, expected, (release, scope))
        brewfiles = {scope: (REPO / 'scripts/bootstrap/macos' / filename).read_text()
                     for scope, filename in (('base', 'Brewfile'), ('desktop', 'Brewfile.desktop'))}
        for name, (scope, platform) in requirements.items():
            if platform == 'linux' or name in ('cc', 'tar', 'unzip'):
                continue
            command = 'source "$1/bootstrap/macos/install.bash"; macos_package_for "$2"'
            result = run([BASH, '-c', command, 'manifest-check', str(REPO / 'scripts'), name],
                         check=True)
            self.assertIn('"' + result.stdout.strip() + '"', brewfiles[scope], (scope, name))

    def test_all_release_assets_have_https_and_sha256(self):
        manifest = json.loads((REPO / 'scripts/bootstrap/releases.json').read_text())
        for name, item in manifest.items():
            self.assertTrue(item['version'], name)
            for asset in item['assets'].values():
                self.assertTrue(asset['url'].startswith('https://'), name)
                self.assertRegex(asset['sha256'], r'^[0-9a-f]{64}$', name)

    def font_fixture(self, name, family):
        def ttf(family):
            # 最小 SFNT 夹具仅含一个 name 表和一条 UTF-16BE 族名记录。
            # 18 = 6 字节表头 + 12 字节记录；28 = 12 字节文件头 + 16 字节表目录。
            text = family.encode('utf-16-be')
            names = struct.pack('>HHH', 0, 1, 18) + struct.pack('>HHHHHH', 3, 1, 0x409, 1, len(text), 0) + text
            return b'\0\1\0\0' + struct.pack('>HHHH', 1, 0, 0, 0) + struct.pack('>4sIII', b'name', 0, 28, len(names)) + names
        archive = self.root / 'fonts.zip'
        # 归档包含两个不同族名，验证只发布清单指定的字体。
        with zipfile.ZipFile(archive, 'w') as zipped:
            zipped.writestr('selected.ttf', ttf(family))
            zipped.writestr('unrelated.ttf', ttf('Sarasa Term TC'))
        sha = resources.digest(archive)
        manifest = {name: {'version': 'v1', 'kind': 'archive', 'family': family,
                    'assets': {'universal': {'url': 'https://example.test/fonts.zip', 'sha256': sha}}}}
        manager = resources.Resources(self.home, manifest)
        manager.cache.mkdir(parents=True)
        shutil.copyfile(archive, manager.cache / sha)
        return manager

    def test_macos_font_outside_default_list_installs_and_reuses(self):
        family = 'IosevkaTerm Nerd Font'
        manager = self.font_fixture('iosevka', family)
        destination = self.home / 'Library/Fonts/dotfiles-bootstrap/iosevka'

        def enumerate_fonts(args, **kwargs):
            # 默认列表只显示等宽字体；按族名查询才能看到已发布的目标字体。
            if args[1:] == ['+list-fonts']:
                return 'Menlo\n  Menlo Regular\n'
            self.assertEqual(args[1:], ['+list-fonts', '--family=' + family])
            if (destination / 'selected.ttf').is_file():
                return family + '\n  IosevkaTerm NF\n'
            return ''

        with patch.object(resources.shutil, 'which', return_value='/fixture bin/ghostty'), \
                patch.object(resources, 'run', side_effect=enumerate_fonts), \
                patch.object(manager, 'fetch', wraps=manager.fetch) as fetch:
            manager.font('iosevka', 'macos')
            self.assertTrue((destination / 'selected.ttf').is_file())
            self.assertFalse((destination / 'unrelated.ttf').exists())
            installed = snapshot(self.home)
            manager.font('iosevka', 'macos')
            self.assertEqual(snapshot(self.home), installed)
            fetch.assert_called_once_with('iosevka', 'universal')

    def test_macos_font_rejects_fallback_and_style_names(self):
        family = 'IosevkaTerm Nerd Font'
        manager = self.font_fixture('iosevka', family)
        destination = self.home / 'Library/Fonts/dotfiles-bootstrap/iosevka'
        destination.mkdir(parents=True)
        (destination / 'existing.ttf').write_bytes(b'preserve existing font')
        before = snapshot(self.home)
        for output in ('', family + ' Mono\n  ' + family + '\n', 'Other Family\n  ' + family + '\n'):
            with self.subTest(output=output), \
                    patch.object(resources.shutil, 'which', return_value='/fixture bin/ghostty'), \
                    patch.object(resources, 'run', return_value=output), \
                    patch.object(manager, 'fetch') as fetch:
                with self.assertRaisesRegex(RuntimeError, 'font directory exists but family is unavailable'):
                    manager.font('iosevka', 'macos')
                self.assertEqual(snapshot(self.home), before)
                fetch.assert_not_called()

    def test_macos_font_waits_for_discovery_after_install(self):
        family = 'Sarasa Term SC'
        manager = self.font_fixture('sarasa', family)
        destination = self.home / 'Library/Fonts/dotfiles-bootstrap/sarasa'
        elapsed = 0

        def advance(seconds):
            nonlocal elapsed
            elapsed += seconds

        def families(platform, requested, **kwargs):
            if (destination / 'selected.ttf').is_file() and elapsed >= 2:
                return {family}
            return set()

        with patch.object(resources, 'time') as clock, \
                patch.object(manager, 'font_families', side_effect=families):
            clock.monotonic.side_effect = lambda: elapsed
            clock.sleep.side_effect = advance
            manager.font('sarasa', 'macos')
            self.assertGreaterEqual(elapsed, 2)
            self.assertTrue((destination / '.ready.json').is_file())

    def test_macos_font_discovery_timeout_preserves_install_and_retry_recovers(self):
        family = 'Sarasa Term SC'
        manager = self.font_fixture('sarasa', family)
        destination = self.home / 'Library/Fonts/dotfiles-bootstrap/sarasa'
        elapsed = 0

        def advance(seconds):
            nonlocal elapsed
            elapsed += seconds

        with patch.object(resources, 'time') as clock:
            clock.monotonic.side_effect = lambda: elapsed
            clock.sleep.side_effect = advance
            with patch.object(manager, 'font_families', return_value=set()):
                with self.assertRaisesRegex(RuntimeError, 'font files installed but Sarasa Term SC is not enumerated'):
                    manager.font('sarasa', 'macos')
            self.assertEqual(elapsed, 30)
            self.assertTrue((destination / 'selected.ttf').is_file())
            self.assertEqual(json.loads((destination / '.ready.json').read_text())['family'], family)
            installed = snapshot(self.home)
            with patch.object(manager, 'font_families', side_effect=lambda *args, **kwargs: {family} if elapsed >= 32 else set()), \
                    patch.object(manager, 'fetch') as fetch:
                manager.font('sarasa', 'macos')
                self.assertEqual(snapshot(self.home), installed)
                fetch.assert_not_called()

    def test_macos_font_discovery_query_failure_is_not_retried(self):
        manager = self.font_fixture('sarasa', 'Sarasa Term SC')
        with patch.object(resources, 'time') as clock, \
                patch.object(manager, 'font_families', side_effect=[set(), RuntimeError('font query failed')]) as query:
            clock.monotonic.return_value = 0
            with self.assertRaisesRegex(RuntimeError, 'font query failed'):
                manager.font('sarasa', 'macos')
            self.assertEqual(query.call_count, 2)
            clock.sleep.assert_not_called()
            self.assertTrue((self.home / 'Library/Fonts/dotfiles-bootstrap/sarasa/selected.ttf').is_file())

    def test_font_family_selection_and_cache_failure_recovery(self):
        manager = self.font_fixture('sarasa', 'Sarasa Term SC')
        with patch.object(manager, 'font_families', return_value=set()), patch.object(resources, 'run', side_effect=RuntimeError('cache failure')):
            with self.assertRaisesRegex(RuntimeError, 'cache failure'):
                manager.font('sarasa', 'linux')
        destination = self.home / '.local/share/fonts/dotfiles-bootstrap/sarasa'
        self.assertTrue((destination / 'selected.ttf').exists())
        self.assertFalse((destination / 'unrelated.ttf').exists())
        with patch.object(manager, 'font_families', return_value=set()), \
                patch.object(resources, 'run', return_value=''), patch.object(resources, 'time') as clock:
            with self.assertRaisesRegex(RuntimeError, 'font directory exists but family is unavailable'):
                manager.font('sarasa', 'linux')
            clock.sleep.assert_not_called()
        with patch.object(manager, 'font_families', side_effect=[set(), {'Sarasa Term SC'}]), patch.object(resources, 'run', return_value='') as run:
            manager.font('sarasa', 'linux')
            self.assertEqual(run.call_args.args[0][0], 'fc-cache')

    def test_dirty_plugin_checkout_is_preserved(self):
        repository = self.root / 'config-repo'
        config = repository / 'nvim/.config/nvim'
        config.mkdir(parents=True)
        (config / 'lazy-lock.json').write_text(json.dumps({'sample': {'commit': 'a' * 40}}))
        plugin = self.home / '.local/share/nvim/lazy/sample'
        plugin.mkdir(parents=True)
        run(['git', 'init', '-q', str(plugin)], check=True)
        (plugin / 'file').write_text('original')
        run(['git', '-C', str(plugin), 'add', 'file'], check=True)
        run(['git', '-C', str(plugin), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
             '-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture'], check=True)
        (plugin / 'file').write_text('keep edits')
        with self.assertRaisesRegex(RuntimeError, 'dirty plugin checkout'):
            resources.stage_nvim(self.manager, repository, self.root / 'staged-config')
        self.assertEqual((plugin / 'file').read_text(), 'keep edits')


if __name__ == '__main__':
    with cleanup_on_exit():
        unittest.main(verbosity=2, buffer=True)
