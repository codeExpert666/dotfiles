#!/usr/bin/env python3
"""bootstrap 的离线行为测试：安装编排、应用准备与资源发布。"""
import importlib.util
import io
import json
import os
import shlex
from pathlib import Path
import shutil
import signal
import struct
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'support'))
from harness import REPO, Process, cleanup_on_exit, run, running, snapshot
BASH = os.environ.get('DOTFILES_TEST_BASH') or shutil.which('bash')
REAL_TOOLS = {name: shutil.which(name) for name in ('git', 'stow', 'rm', 'rmdir', 'mktemp', 'zsh', 'nvim', 'starship', 'cc', 'npm')}
spec = importlib.util.spec_from_file_location('bootstrap_resources', REPO / 'scripts/bootstrap/resources.py')
resources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resources)


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
        for path in Path('/usr/bin').iterdir():
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
        self.assertFalse(any(event[:2] == ['brew', 'upgrade'] for event in self.events()))
        self.assertFalse(any(event[0] == 'apt-get' or event[:2] == ['resources', 'font'] for event in self.events()))
        before = len(self.events())
        self.invoke('--apply', '--profile', 'server')
        self.assertEqual(self.brew_installs(self.events()[before:]), [])
        self.assertFalse(any(event[:2] == ['brew', 'upgrade'] for event in self.events()[before:]))

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
        self.env['BOOTSTRAP_TEST_PACKAGES'] = 'ca-certificates,lesspipe'
        self.invoke('--apply', '--profile', 'server')
        self.assertIn(['apt-get', 'install', '-y', '--no-install-recommends',
                       'ca-certificates', 'lesspipe'], self.events())
        before = len(self.events())
        self.invoke('--apply', '--profile', 'server')
        self.assertFalse(any(event[0] == 'apt-get' for event in self.events()[before:]))

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
        helper.write_text('#!' + sys.executable + '\n' +
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

    def zsh_fixture(self):
        manager = self.home / '.local/share/antidote'
        manager.mkdir(parents=True)
        # 仅替换 Antidote 提供插件的边界；准备脚本、Zsh 语法检查
        # 和缓存发布均使用实际实现。
        (manager / 'antidote.zsh').write_text(
            'antidote() {\ncase $1 in\nbundle) cat "$TEST_BUNDLE" ;;\n'
            'path) print -r -- "$ANTIDOTE_HOME/$2" ;;\nesac\n}\n')
        cache = self.home / '.cache/antidote'
        plugin = cache / 'zsh-users/zsh-autosuggestions'
        plugin.mkdir(parents=True)
        (plugin / 'zsh-autosuggestions.zsh').write_text('typeset -g prepared=yes\n')
        manifest = self.root / 'plugins.txt'
        manifest.write_text('zsh-users/zsh-autosuggestions\n')
        os.utime(manifest, (1, 1))
        bundle = self.root / 'bundle.zsh'
        bundle.write_text('source "$ANTIDOTE_HOME/zsh-users/zsh-autosuggestions/zsh-autosuggestions.zsh"\n')
        self.env['TEST_BUNDLE'] = str(bundle)
        return manifest, bundle, cache

    def prepare_zsh(self, manifest):
        return run([REAL_TOOLS['zsh'], '-d', '-f', str(REPO / 'scripts/bootstrap/zsh.zsh'), str(manifest)],
                   env=self.env, cwd=self.root, timeout=10)

    @unittest.skipUnless(REAL_TOOLS['zsh'], 'native Zsh is required')
    def test_zsh_cache_publication_and_reuse(self):
        manifest, bundle, cache = self.zsh_fixture()
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
        manifest, bundle, cache = self.zsh_fixture()
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
        child = run([REAL_TOOLS['nvim'], '--headless', '-u', 'NONE', '-n', '-i', 'NONE',
                     '-l', str(REPO / 'scripts/bootstrap/nvim.lua')], env=self.env, cwd=self.root, timeout=90)
        stdout, stderr = child.stdout, child.stderr
        self.assertEqual(child.returncode, 0, stdout + stderr)
        for stage in ('all plugin checkouts match', 'Mason', 'Treesitter parsers installed', 'completion resources'):
            self.assertIn('READY: ' + stage if stage != 'Treesitter parsers installed' else stage, stdout)
        self.assertEqual((config / 'lazy-lock.json').read_bytes(), lock)


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

    def test_font_family_selection_and_cache_failure_recovery(self):
        def ttf(family):
            # 最小 SFNT 夹具仅含一个 name 表和一条 UTF-16BE 族名记录。
            # 18 = 6 字节表头 + 12 字节记录；28 = 12 字节文件头 + 16 字节表目录。
            text = family.encode('utf-16-be')
            names = struct.pack('>HHH', 0, 1, 18) + struct.pack('>HHHHHH', 3, 1, 0x409, 1, len(text), 0) + text
            return b'\0\1\0\0' + struct.pack('>HHHH', 1, 0, 0, 0) + struct.pack('>4sIII', b'name', 0, 28, len(names)) + names
        archive = self.root / 'fonts.zip'
        # 归档包含两个不同族名，验证只发布清单指定的字体。
        with zipfile.ZipFile(archive, 'w') as zipped:
            zipped.writestr('selected.ttf', ttf('Sarasa Term SC'))
            zipped.writestr('unrelated.ttf', ttf('Sarasa Term TC'))
        sha = resources.digest(archive)
        manifest = {'sarasa': {'version': 'v1', 'kind': 'archive', 'family': 'Sarasa Term SC',
                    'assets': {'universal': {'url': 'https://example.test/fonts.zip', 'sha256': sha}}}}
        manager = resources.Resources(self.home, manifest)
        manager.cache.mkdir(parents=True)
        shutil.copyfile(archive, manager.cache / sha)
        with patch.object(manager, 'font_families', return_value=set()), patch.object(resources, 'run', side_effect=RuntimeError('cache failure')):
            with self.assertRaisesRegex(RuntimeError, 'cache failure'):
                manager.font('sarasa', 'linux')
        destination = self.home / '.local/share/fonts/dotfiles-bootstrap/sarasa'
        self.assertTrue((destination / 'selected.ttf').exists())
        self.assertFalse((destination / 'unrelated.ttf').exists())
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
