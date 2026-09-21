"""doctor 的行为测试：独立故障、原生命令查询与只读保证。"""

import errno
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'support'))
import harness
from harness import REPO, Process, cleanup_on_exit, run, running, snapshot
BASH = os.environ.get("DOTFILES_TEST_BASH") or shutil.which("bash")
PACKAGES = ("scripts", "atuin", "codex", "ghostty", "ghostty-macos", "git", "lazygit", "nvim", "shuck", "skills", "starship", "vim", "zsh")


class DoctorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.suite = tempfile.TemporaryDirectory(prefix="dotfiles-doctor-tests-")
        cls.addClassCleanup(cls.suite.cleanup)
        cls.repo = Path(cls.suite.name).resolve() / "repo with spaces"
        cls.repo.mkdir()
        for name in PACKAGES:
            shutil.copytree(str(REPO / name), str(cls.repo / name), symlinks=True)
        cls.script = cls.repo / "scripts/doctor.sh"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="case-", dir=self.suite.name)
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.target = self.base / "home with spaces"
        self.target.mkdir()
        self.work = self.base / "work"
        self.work.mkdir()
        self.temp = self.base / "tmp"
        self.temp.mkdir()
        self.bindir = self.base / "bin"
        self.bindir.mkdir()
        self.env = {"HOME": str(self.target), "PATH": os.environ["PATH"], "TERM": "xterm-256color",
                    "TMPDIR": str(self.temp), "GIT_CONFIG_NOSYSTEM": "1"}

    def deploy(self):
        result = run([BASH, str(self.repo / "scripts/deploy.sh"), "--apply"],
                     env=self.env, cwd=self.work)
        self.assertEqual(result.returncode, 0, result.stderr)

    def doctor(self, *args, code=0, unchanged=True, env=None):
        before = snapshot(self.target)
        source_before = snapshot(self.repo)
        result = run([BASH, str(self.script), *args], env=env or self.env,
                     cwd=self.work, timeout=50)
        self.assertEqual(result.returncode, code, result.stderr)
        self.assertEqual(result.stdout, "", "reports must use stderr")
        if unchanged:
            self.assertEqual(snapshot(self.target), before, "doctor changed the target HOME")
            self.assertEqual(snapshot(self.repo), source_before, "doctor changed the repository")
        self.assertEqual(list(self.temp.iterdir()), [], "temporary files were not cleaned")
        return result.stderr

    def mock(self, name, body):
        path = self.bindir / name
        if path.is_symlink():
            path.unlink()
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)
        self.env["PATH"] = str(self.bindir) + os.pathsep + os.environ["PATH"]
        return path

    def java_dependencies_fixture(self, *, java_version="25.0.4.1", javac_version=None,
                                  maven_version="3.9.16", reported_home=None,
                                  maven_runtime=None, java_home=True):
        jdk = self.base / "fixture jdk"
        binary = jdk / "bin"
        binary.mkdir(parents=True, exist_ok=True)
        reported = Path(reported_home) if reported_home is not None else jdk
        runtime = Path(maven_runtime) if maven_runtime is not None else reported
        java = binary / "java"
        java.write_text("#!/bin/sh\n"
                        "printf '%s\\n' " + shlex.quote("    java.home = " + str(reported)) + " >&2\n"
                        "printf '%s\\n' " + shlex.quote('openjdk version "' + java_version + '" fixture') + " >&2\n")
        java.chmod(0o755)
        javac = binary / "javac"
        javac.write_text("#!/bin/sh\nprintf '%s\\n' "
                         + shlex.quote("javac " + (javac_version or java_version)) + "\n")
        javac.chmod(0o755)
        self.mock("mvn", "printf '%s\\n' " + shlex.quote("Apache Maven " + maven_version + " (fixture)")
                  + "\nprintf '%s\\n' " + shlex.quote("Java version: " + java_version
                                                         + ", vendor: Fixture, runtime: " + str(runtime)) + "\n")
        self.env["PATH"] = str(binary) + os.pathsep + str(self.bindir) + os.pathsep + os.environ["PATH"]
        if java_home:
            self.env["JAVA_HOME"] = str(jdk)
        else:
            self.env.pop("JAVA_HOME", None)
        return jdk

    def minimal_path(self, *extra):
        for name in ("uname", "mkdir", "mktemp", "rm", "sleep", "env", "cp", "cmp", "cat") + extra:
            path = shutil.which(name)
            if path:
                (self.bindir / name).symlink_to(path)
        self.env["PATH"] = str(self.bindir)

    def test_help_with_empty_path_and_invalid_home(self):
        result = run([BASH, str(self.script), "--help"],
                     env={"PATH": "", "HOME": "/nonexistent"})
        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_argument_errors(self):
        for args in (("--only",), ("--only", "unknown"), ("--help", "--verbose"), ("--apply",)):
            with self.subTest(args=args):
                self.doctor(*args, code=2)

    def test_fresh_home_reports_all_missing_entries_without_writes(self):
        log = self.doctor("--only", "deployment", code=1)
        self.assertIn("deployment..zshenv: not deployed", log)
        self.assertIn("deployment..config/nvim/init.lua: not deployed", log)
        self.assertIn("deployment.git-entry", log)
        self.assertIn("result:", log)

    def test_deployed_layout_and_uninitialized_state(self):
        self.deploy()
        log = self.doctor("--only", "deployment", "--only", "state")
        self.assertIn("PASS deployment..config/nvim/init.lua", log)
        self.assertIn("PASS deployment..agents/skills/coding-mentor", log)
        self.assertIn("PASS deployment..claude/skills/coding-mentor", log)
        self.assertIn("PASS deployment.skill-source.coding-mentor", log)
        self.assertIn("PASS deployment.skill-source.astra-sol", log)
        self.assertIn("PASS deployment..agents/skills/astra-sol", log)
        self.assertIn("PASS deployment..codex/agents/sol_worker.toml", log)
        self.assertNotIn(".claude/skills/astra-sol", log)
        self.assertNotIn("deployment.src", log)
        self.assertNotIn("WARN deployment.ignore.skills", log)
        self.assertIn("SKIP state..cache/zsh", log)
        self.assertNotIn("FAIL", log.replace("FAIL=0", ""))

    def test_multiple_independent_link_faults(self):
        self.deploy()
        root = self.target / ".zshenv"
        root.unlink()
        root.symlink_to("missing")
        vim = self.target / ".vimrc"
        content = vim.read_bytes()
        vim.unlink()
        vim.write_bytes(content)
        log = self.doctor("--only", "deployment", code=1)
        self.assertIn("deployment..zshenv: broken symbolic link", log)
        self.assertIn("deployment..vimrc: entry is not a symbolic link", log)
        self.assertIn("PASS deployment..config/git/config.shared", log)

    def test_shared_skill_and_claude_alias_faults(self):
        self.deploy()
        skill = self.target / '.agents/skills/coding-mentor'
        skill.unlink()
        skill.symlink_to('missing')
        alias = self.target / '.claude/skills/shell-script-review'
        alias.unlink()
        alias.symlink_to(self.repo / 'skills/src/coding-mentor', target_is_directory=True)
        log = self.doctor('--only', 'deployment', code=1)
        self.assertIn('deployment..agents/skills/coding-mentor: broken symbolic link', log)
        self.assertIn('deployment..claude/skills/shell-script-review: entry is not a symbolic link', log)
        self.assertIn('PASS deployment..agents/skills/shell-script-review', log)

    def test_skill_source_missing_or_symlinked_entrypoint(self):
        self.repo = self.base / 'private repo'
        shutil.copytree(type(self).repo, self.repo, symlinks=True)
        self.script = self.repo / 'scripts/doctor.sh'
        self.deploy()
        entry = self.repo / 'skills/src/coding-mentor/SKILL.md'
        moved = self.work / 'SKILL.md'
        entry.rename(moved)
        for fault in ('missing', 'symlink'):
            with self.subTest(fault=fault):
                if fault == 'symlink':
                    entry.symlink_to(moved)
                log = self.doctor('--only', 'deployment', code=1)
                self.assertIn('FAIL deployment.skill-source.coding-mentor', log)
                self.assertIn('PASS deployment.skill-source.shell-script-review', log)

    def test_skill_alias_points_to_wrong_source_in_package(self):
        self.repo = self.base / 'private repo'
        shutil.copytree(type(self).repo, self.repo, symlinks=True)
        self.script = self.repo / 'scripts/doctor.sh'
        self.deploy()
        alias = self.repo / 'skills/.claude/skills/coding-mentor'
        alias.unlink()
        alias.symlink_to('../../src/shell-script-review', target_is_directory=True)
        log = self.doctor('--only', 'deployment', code=1)
        self.assertIn('FAIL deployment.skill-alias..claude/skills/coding-mentor', log)
        self.assertIn('PASS deployment..agents/skills/coding-mentor', log)

    def test_skill_source_directory_missing(self):
        self.repo = self.base / 'private repo'
        shutil.copytree(type(self).repo, self.repo, symlinks=True)
        self.script = self.repo / 'scripts/doctor.sh'
        self.deploy()
        (self.repo / 'skills/src').rename(self.work / 'skill sources')
        log = self.doctor('--only', 'deployment', code=1)
        self.assertIn('FAIL deployment.skill-source: skills/src must be a real directory', log)
        self.assertIn('deployment..agents/skills/coding-mentor: broken symbolic link', log)

    def test_declared_skill_missing_source_and_alias(self):
        self.repo = self.base / 'private repo'
        shutil.copytree(type(self).repo, self.repo, symlinks=True)
        self.script = self.repo / 'scripts/doctor.sh'
        self.deploy()
        (self.repo / 'skills/src/astra-sol').rename(self.work / 'astra-sol')
        (self.repo / 'skills/.agents/skills/astra-sol').unlink()
        log = self.doctor('--only', 'deployment', code=1)
        self.assertIn('FAIL deployment.skill-source.astra-sol', log)
        self.assertIn('FAIL deployment.skill-alias..agents/skills/astra-sol', log)
        self.assertNotIn('.claude/skills/astra-sol', log)

    def test_required_shared_skill_alias_missing(self):
        self.repo = self.base / 'private repo'
        shutil.copytree(type(self).repo, self.repo, symlinks=True)
        self.script = self.repo / 'scripts/doctor.sh'
        self.deploy()
        (self.repo / 'skills/.claude/skills/coding-mentor').unlink()
        log = self.doctor('--only', 'deployment', code=1)
        self.assertIn('FAIL deployment.skill-alias..claude/skills/coding-mentor', log)

    def test_codex_agent_copy_faults(self):
        self.deploy()
        entry = self.target / '.codex/agents/sol_worker.toml'
        for fault in ('missing', 'broken', 'symlink', 'different'):
            with self.subTest(fault=fault):
                if entry.exists() or entry.is_symlink():
                    entry.unlink()
                if fault == 'broken':
                    entry.symlink_to('missing')
                elif fault == 'symlink':
                    entry.symlink_to(self.repo / 'codex/agents/sol_worker.toml')
                elif fault == 'different':
                    entry.write_text('name = "personal"\n')
                log = self.doctor('--only', 'deployment', code=1)
                self.assertIn('FAIL deployment..codex/agents/sol_worker.toml', log)
                self.assertIn('PASS deployment.skill-source.astra-sol', log)

    def test_skill_source_exclusion_missing(self):
        self.repo = self.base / 'private repo'
        shutil.copytree(type(self).repo, self.repo, symlinks=True)
        self.script = self.repo / 'scripts/doctor.sh'
        self.deploy()
        ignore = self.repo / 'skills/.stow-local-ignore'
        ignore.write_text(ignore.read_text().replace('^/src$\n', ''))
        log = self.doctor('--only', 'deployment', code=1)
        self.assertIn('FAIL deployment.ignore.skills', log)

    def test_wrong_checkout_and_legacy_entries(self):
        self.deploy()
        source = self.target / ".config/git/config.shared"
        other = self.work / "config.shared"
        other.write_bytes(source.read_bytes())
        source.unlink()
        source.symlink_to(other)
        (self.target / ".gitconfig").write_text("[core]\n editor = vim\n")
        log = self.doctor("--only", "deployment", code=1)
        self.assertIn("FAIL deployment..config/git/config.shared", log)
        self.assertIn("FAIL deployment.git-legacy", log)

    def test_simulated_macos_package_selection(self):
        self.mock("uname", "printf 'Darwin\\n'\n")
        self.deploy()
        log = self.doctor("--only", "deployment")
        self.assertIn("PASS deployment..config/ghostty/platform.ghostty", log)
        self.assertIn("PASS deployment..config/ghostty/shaders/ripple_cursor.glsl", log)

    def test_simulated_linux_rejects_macos_platform_entry(self):
        self.mock("uname", "printf 'Darwin\\n'\n")
        self.deploy()
        self.mock("uname", "printf 'Linux\\n'\n")
        log = self.doctor("--only", "deployment", code=1)
        self.assertIn("FAIL deployment.ghostty-platform", log)

    def test_environment_overrides_and_directory_alias(self):
        (self.target / ".config").mkdir()
        alias = self.work / "config-alias"
        alias.symlink_to(self.target / ".config", target_is_directory=True)
        env = dict(self.env, XDG_CONFIG_HOME=str(alias), ZDOTDIR="", STARSHIP_CONFIG="personal.toml")
        log = self.doctor("--only", "environment", env=env, code=1)
        self.assertIn("PASS environment.XDG_CONFIG_HOME", log)
        self.assertIn("FAIL environment.ZDOTDIR", log)
        self.assertIn("WARN environment.STARSHIP_CONFIG", log)

    def test_nondefault_xdg_and_external_stow_configuration(self):
        (self.work / ".stowrc").write_text("--ignore=.*\n")
        env = dict(self.env, XDG_CONFIG_HOME=".config")
        log = self.doctor("--only", "environment", env=env, code=1)
        self.assertIn("FAIL environment.XDG_CONFIG_HOME", log)
        self.assertIn("FAIL deployment.stow-config", log)

    def test_state_path_occupied_by_file(self):
        (self.target / ".cache").write_text("do not replace me")
        log = self.doctor("--only", "state", code=1)
        self.assertIn("FAIL state..cache/zsh", log)
        self.assertIn("FAIL state..cache/antidote", log)

    def test_missing_optional_application(self):
        self.minimal_path()
        # 选择仅从 PATH 查找的应用；Ghostty 还可能来自宿主的 /Applications。
        log = self.doctor("--only", "starship")
        self.assertIn("WARN starship.command", log)
        self.assertIn("FAIL=0", log)

    def test_ghostty_validation_uses_explicit_config_without_unsupported_flags(self):
        self.deploy()
        config = self.target / '.config/ghostty/config.ghostty'
        self.mock('ghostty', 'test "$#" -eq 2 && test "$1" = +validate-config && test "$2" = '
                  + shlex.quote('--config-file=' + str(config)) + '\n')
        log = self.doctor('--only', 'ghostty')
        self.assertIn('PASS ghostty.parse', log)

    def test_missing_required_application_continues_other_modules(self):
        self.minimal_path()
        log = self.doctor("--only", "zsh", "--only", "state", code=1)
        self.assertIn("FAIL zsh.command", log)
        self.assertIn("SKIP state..cache/zsh", log)

    def test_native_stow_capability_uses_a_valid_simulation(self):
        self.minimal_path("stow")
        log = self.doctor("--only", "dependencies", code=1)
        self.assertIn("PASS dependencies.stow-capability", log)

    @unittest.skipUnless(shutil.which("delta"), "native delta is required")
    def test_delta_capability_with_a_controlling_terminal(self):
        self.deploy()
        before, source_before = snapshot(self.target), snapshot(self.repo)
        output = harness.terminal([BASH, str(self.script), "--only", "git", "--verbose"],
                                  env=self.env, cwd=self.work, timeout=20)
        self.assertIn(b"PASS git.delta-capability", output)
        self.assertEqual(snapshot(self.target), before)
        self.assertEqual(snapshot(self.repo), source_before)
        self.assertEqual(list(self.temp.iterdir()), [])

    def test_java_and_maven_dependencies_require_a_complete_consistent_toolchain(self):
        jdk = self.java_dependencies_fixture()
        self.mock("delta", "exit 7\n")
        self.env["PATH"] = str(jdk / "bin") + os.pathsep + str(self.bindir) + os.pathsep + os.environ["PATH"]
        log = self.doctor("--only", "dependencies", code=1)
        self.assertIn("PASS dependencies.java: complete JDK 25.0.4.1 selected", log)
        self.assertIn("PASS dependencies.javac", log)
        self.assertIn("PASS dependencies.maven: Maven 3.9.16 uses the selected JDK", log)

    def test_java_dependency_accepts_complete_jdk_21_and_24(self):
        for version in ("21", "24.0.2"):
            with self.subTest(version=version):
                jdk = self.java_dependencies_fixture(java_version=version)
                self.mock("delta", "exit 7\n")
                self.env["PATH"] = (str(jdk / "bin") + os.pathsep + str(self.bindir)
                                    + os.pathsep + os.environ["PATH"])
                log = self.doctor("--only", "dependencies", code=1)
                self.assertIn("PASS dependencies.java: complete JDK " + version + " selected", log)
                self.assertIn("PASS dependencies.maven: Maven 3.9.16 uses the selected JDK", log)

    def test_java_dependency_reports_missing_old_and_incomplete_jdks(self):
        self.minimal_path()
        self.env.pop("JAVA_HOME", None)
        log = self.doctor("--only", "dependencies", code=1)
        self.assertIn("FAIL dependencies.java: java is unavailable", log)

        for version in ("17.0.12", "20.0.2"):
            jdk = self.java_dependencies_fixture(java_version=version)
            log = self.doctor("--only", "dependencies", code=1)
            self.assertIn("FAIL dependencies.java: Java " + version + " is older", log)

        (jdk / "bin/javac").unlink()
        log = self.doctor("--only", "dependencies", code=1)
        self.assertIn("FAIL dependencies.java-home: JAVA_HOME is not a complete JDK", log)

    def test_java_dependency_reports_java_home_and_path_disagreement(self):
        other = self.base / "reported jdk"
        (other / "bin").mkdir(parents=True)
        for name in ("java", "javac"):
            path = other / "bin" / name
            path.write_text("#!/bin/sh\nexit 0\n")
            path.chmod(0o755)
        self.java_dependencies_fixture(reported_home=other)
        log = self.doctor("--only", "dependencies", code=1)
        self.assertIn("FAIL dependencies.java-home: JAVA_HOME and the selected Java runtime disagree", log)

        jdk = self.java_dependencies_fixture()
        shadow = self.bindir / "java"
        shadow.write_text("#!/bin/sh\nexit 0\n")
        shadow.chmod(0o755)
        self.env["PATH"] = str(self.bindir) + os.pathsep + str(jdk / "bin") + os.pathsep + os.environ["PATH"]
        log = self.doctor("--only", "dependencies", code=1)
        self.assertIn("FAIL dependencies.java-path: PATH does not select java and javac from JAVA_HOME", log)

    def test_maven_dependency_rejects_prerelease_and_wrong_runtime(self):
        self.java_dependencies_fixture(maven_version="3.9.16-rc-1")
        log = self.doctor("--only", "dependencies", code=1)
        self.assertIn("FAIL dependencies.maven:", log)
        self.assertIn("not a stable Maven 3.9 release", log)

        other = self.base / "other runtime"
        other.mkdir()
        self.java_dependencies_fixture(maven_runtime=other)
        log = self.doctor("--only", "dependencies", code=1)
        self.assertIn("FAIL dependencies.maven-runtime: Maven runtime does not match", log)

    def test_relative_path_and_config_keep_calling_directory_meaning(self):
        self.deploy()
        observed = self.work / "selected-config"
        self.mock("starship", 'printf "%s\\n" "$STARSHIP_CONFIG" > ' + shlex.quote(str(observed))
                  + "\nprintf 'format = \"[░▒▓](#a3aed2)\"\\n'\n")
        selected = self.work / "personal.toml"
        selected.write_bytes((self.target / ".config/starship.toml").read_bytes())
        env = dict(self.env, PATH=os.path.relpath(self.bindir, self.work) + ":" + os.environ["PATH"],
                   STARSHIP_CONFIG="personal.toml")
        log = self.doctor("--only", "starship", env=env)
        self.assertIn("PASS starship.parse", log)
        self.assertIn(str(selected), log)
        self.assertEqual(observed.read_text().strip(), str(selected))

    def test_ghostty_font_query_finds_families_outside_default_list(self):
        self.mock("ghostty", r'''
case "$#:$1:${2-}" in
    '1:+list-fonts:') printf 'Menlo\n  Menlo Regular\n' ;;
    '2:+list-fonts:--family=IosevkaTerm Nerd Font') printf 'IosevkaTerm Nerd Font\n  IosevkaTerm NF\n' ;;
    '2:+list-fonts:--family=Sarasa Term SC') printf 'Sarasa Term SC\n  Sarasa Term SC Regular\n' ;;
    *) exit 2 ;;
esac
''')
        for platform in ('Darwin', 'Linux'):
            with self.subTest(platform=platform):
                self.mock('uname', 'printf "%s\\n" ' + shlex.quote(platform) + '\n')
                log = self.doctor("--only", "terminal", env=dict(self.env, TERM="dumb"))
                self.assertIn("PASS terminal.font.IosevkaTerm Nerd Font", log)
                self.assertIn("PASS terminal.font.Sarasa Term SC", log)
                self.assertNotIn("WARN terminal.font.", log)

    def test_ghostty_font_query_rejects_missing_family_and_style_matches(self):
        for output in ('', 'IosevkaTerm Nerd Font Mono\n  IosevkaTerm Nerd Font\n',
                       'Other Family\n  IosevkaTerm Nerd Font\n'):
            with self.subTest(output=output):
                self.mock("ghostty", 'case "$#:$1:${2-}" in\n'
                          "'2:+list-fonts:--family=IosevkaTerm Nerd Font') printf '%s' " + shlex.quote(output) + ' ;;\n'
                          "'2:+list-fonts:--family=Sarasa Term SC') printf 'Sarasa Term SC\\n' ;;\n"
                          '*) exit 2 ;;\nesac\n')
                log = self.doctor("--only", "terminal", env=dict(self.env, TERM="dumb"))
                self.assertIn("WARN terminal.font.IosevkaTerm Nerd Font", log)
                self.assertIn("PASS terminal.font.Sarasa Term SC", log)

    def test_ghostty_font_query_failure_does_not_skip_other_family(self):
        self.mock("ghostty", r'''
case "$#:$1:${2-}" in
    '2:+list-fonts:--family=IosevkaTerm Nerd Font') echo 'font query failed' >&2; exit 7 ;;
    '2:+list-fonts:--family=Sarasa Term SC') printf 'Sarasa Term SC\n' ;;
    *) exit 2 ;;
esac
''')
        log = self.doctor("--only", "terminal", "--verbose", code=1, env=dict(self.env, TERM="dumb"))
        self.assertIn("FAIL terminal.font.IosevkaTerm Nerd Font: font enumeration failed; command exit 7", log)
        self.assertIn("native: font query failed", log)
        self.assertIn("PASS terminal.font.Sarasa Term SC", log)

    def test_font_fallback_family_does_not_count_as_exact_match(self):
        self.minimal_path()
        self.mock("uname", "printf 'Linux\\n'\n")
        self.mock("fc-list", "printf 'IosevkaTerm Nerd Font Mono,Sarasa Term SC\\n'\n")
        self.env["PATH"] = str(self.bindir)
        log = self.doctor("--only", "terminal", env=dict(self.env, TERM="dumb"))
        self.assertIn("WARN terminal.font.IosevkaTerm Nerd Font", log)
        self.assertIn("PASS terminal.font.Sarasa Term SC", log)

    def test_git_reads_shared_and_accepts_personal_override(self):
        self.deploy()
        self.mock("nvim", "exit 0\n")
        self.mock("delta", "exit 0\n")
        with (self.target / ".config/git/config").open("a") as config:
            config.write("[core]\n editor = vim\n[user]\n name = PRIVATE_IDENTITY\n")
        log = self.doctor("--only", "git")
        self.assertIn("WARN git.core.editor", log)
        self.assertIn("PASS git.core.pager", log)
        self.assertNotIn("PRIVATE_IDENTITY", log)

    def test_git_override_cannot_hide_unread_shared_file(self):
        self.deploy()
        self.mock("nvim", "exit 0\n")
        self.mock("delta", "exit 0\n")
        alternate = self.work / "git-config"
        alternate.write_text("[core]\n editor = nvim\n pager = delta\n")
        log = self.doctor("--only", "git", env=dict(self.env, GIT_CONFIG_GLOBAL=str(alternate)), code=1)
        self.assertIn("FAIL git.core.editor.source", log)

    def test_native_config_queries_preserve_home(self):
        self.deploy()
        modules = [name for name in ("starship", "atuin", "shuck", "lazygit") if shutil.which(name)]
        if not modules:
            self.skipTest("no optional native configuration clients are installed")
        args = [argument for name in modules for argument in ("--only", name)]
        log = self.doctor(*args)
        self.assertIn("FAIL=0", log)

    @unittest.skipUnless(shutil.which("nvim"), "native Neovim is not installed")
    def test_nvim_syntax_is_checked_without_bootstrap(self):
        self.deploy()
        entry = self.target / ".config/nvim/lua/config/options.lua"
        entry.unlink()
        entry.write_text("this is invalid Lua !!!\n")
        log = self.doctor("--only", "nvim", code=1)
        self.assertIn("FAIL nvim.syntax", log)
        self.assertIn("WARN nvim.plugin.lazy.nvim", log)
        self.assertFalse((self.target / ".local/share/nvim").exists())

    @unittest.skipUnless(shutil.which("nvim"), "native Neovim is not installed")
    def test_nvim_bad_json_is_a_health_failure(self):
        self.deploy()
        entry = self.target / ".config/nvim/lazy-lock.json"
        entry.unlink()
        entry.write_text("{broken\n")
        log = self.doctor("--only", "nvim", code=1)
        self.assertIn("FAIL nvim.lazy-lock.json", log)

    @unittest.skipUnless(shutil.which("nvim"), "native Neovim is not installed")
    def test_nvim_runtime_rejects_extra_startup_files(self):
        self.deploy()
        marker = self.work / "must-not-exist"
        for relative in ("plugin/local.vim", "after/plugin/local.vim", "ftplugin/sh.vim", "lua/plugins/local.lua"):
            with self.subTest(relative=relative):
                entry = self.target / ".config/nvim" / relative
                entry.parent.mkdir(parents=True, exist_ok=True)
                if entry.suffix == ".vim":
                    entry.write_text("call writefile(['unexpected'], '" + str(marker) + "')\n")
                else:
                    entry.write_text("vim.fn.writefile({'unexpected'}, " + json.dumps(str(marker)) + ")\n")
                try:
                    log = self.doctor("--only", "nvim", "--runtime")
                    self.assertIn("SKIP nvim.runtime: unmanaged startup configuration: " + str(entry), log)
                    self.assertNotIn("PASS nvim.runtime.scope", log)
                    self.assertFalse(marker.exists())
                finally:
                    entry.unlink()

    @unittest.skipUnless(shutil.which("nvim"), "native Neovim is not installed")
    def test_nvim_runtime_rejects_directory_links(self):
        self.deploy()
        external = self.work / "external-config"
        external.mkdir()
        marker = self.work / "must-not-exist"
        (external / "local.lua").write_text("vim.fn.writefile({'unexpected'}, " + json.dumps(str(marker)) + ")\n")
        before = snapshot(external)
        entry = self.target / ".config/nvim/lua/plugins/external"
        entry.symlink_to(external, target_is_directory=True)
        log = self.doctor("--only", "nvim", "--runtime")
        self.assertIn("SKIP nvim.runtime: configuration directory is a symbolic link: " + str(entry), log)
        self.assertNotIn("PASS nvim.runtime.scope", log)
        self.assertFalse(marker.exists())
        self.assertEqual(snapshot(external), before)

    def prepare_nvim_runtime_stub(self, records="", completed=False):
        # 空插件目录满足暂存前提；仅替换编辑器命令，Bash/Python 的
        # 暂存和报告流程仍使用实际实现。
        lock = json.loads((self.target / ".config/nvim/lazy-lock.json").read_text())
        for name in lock:
            (self.target / ".local/share/nvim/lazy" / name).mkdir(parents=True, exist_ok=True)
        started = self.work / "nvim-started"
        if started.exists():
            started.unlink()
        body = ('case " $* " in\n*" -l "*) printf "PASS\\tnvim.probe\\toffline fixture\\n";;\n*)\n'
                + "printf 'started\\n' > " + shlex.quote(str(started)) + "\n")
        if records:
            body += "printf '%s' " + shlex.quote(records) + ' > "$DOTFILES_DOCTOR_REPORT"\n'
        if completed:
            body += 'printf "nvim\\n" > "${DOTFILES_DOCTOR_COMPLETE:-${DOTFILES_DOCTOR_REPORT}.complete}"\n'
        self.mock("nvim", body + ";;\nesac\n")
        return started

    def test_nvim_runtime_requires_completion_after_zero_exit(self):
        self.deploy()
        for records in ("", "PASS\tnvim.runtime.options\tpartial checks\n"):
            with self.subTest(records=records):
                started = self.prepare_nvim_runtime_stub(records)
                log = self.doctor("--only", "nvim", "--runtime", "--verbose", code=1)
                self.assertEqual(started.read_text(), "started\n")
                self.assertIn("nvim session did not complete its checks", log)
                self.assertNotIn("PASS nvim.runtime.scope", log)
                if records:
                    self.assertIn("PASS nvim.runtime.options", log)
                started.unlink()

    def test_completed_nvim_runtime_preserves_health_failures(self):
        self.deploy()
        started = self.prepare_nvim_runtime_stub("FAIL\tnvim.runtime.options\tcontrolled failure\n", completed=True)
        log = self.doctor("--only", "nvim", "--runtime", code=1)
        self.assertEqual(started.read_text(), "started\n")
        self.assertIn("FAIL nvim.runtime.options: controlled failure", log)
        self.assertIn("PASS nvim.runtime.scope", log)
        self.assertIn("FAIL=1", log)

    def test_zsh_runtime_requires_completion_from_each_session(self):
        self.deploy()
        manager = self.target / ".local/share/antidote"
        manager.mkdir(parents=True)
        (manager / "antidote.zsh").touch()
        cache = self.target / ".cache/antidote"
        for name in ("zsh-autosuggestions", "zsh-syntax-highlighting"):
            plugin = cache / "github.com/zsh-users" / name
            plugin.mkdir(parents=True)
            (plugin / (name + ".zsh")).touch()
        (cache / "zsh_plugins.zsh").touch()
        modes = ("noninteractive", "login", "interactive")
        visited = self.work / "zsh-sessions"
        for index, mode in enumerate(modes):
            with self.subTest(mode=mode):
                visited.write_text("")
                # 后续故障会话留下部分报告，并保留上一会话的完成标记；
                # 两者都不能代表当前会话已经完成。
                body = ('case "$1" in\n-c) mode=noninteractive;;\n-lc) mode=login;;\n'
                        '-ic) mode=interactive;;\n*) exit 0;;\nesac\n'
                        + 'printf "%s\\n" "$mode" >> ' + shlex.quote(str(visited)) + '\n'
                        + 'if [ "$mode" != noninteractive ] || [ "$mode" != ' + shlex.quote(mode) + ' ]; then\n'
                        + 'printf "PASS\\tzsh.runtime.root\\tpartial checks\\n" >> "$DOTFILES_DOCTOR_REPORT"\nfi\n'
                        + 'if [ "$mode" != ' + shlex.quote(mode) + ' ]; then\n'
                        + 'printf "zsh.%s\\n" "$mode" > "${DOTFILES_DOCTOR_COMPLETE:-${DOTFILES_DOCTOR_REPORT}.complete}"\nfi\n')
                self.mock("zsh", body)
                log = self.doctor("--only", "zsh", "--runtime", "--verbose", code=1)
                self.assertEqual(visited.read_text().splitlines(), list(modes[:index + 1]))
                self.assertIn("zsh." + mode + " session did not complete its checks", log)
                self.assertNotIn("PASS zsh.runtime.scope", log)
                visited.unlink()

    @unittest.skipUnless(shutil.which("zsh"), "native Zsh is not installed")
    def test_zsh_syntax_and_local_override_are_not_executed(self):
        self.deploy()
        entry = self.target / ".config/zsh/local.zsh"
        marker = self.work / "must-not-exist"
        entry.write_text("touch " + shlex.quote(str(marker)) + "\n")
        log = self.doctor("--only", "zsh", "--runtime")
        self.assertIn("SKIP zsh.runtime: local executable override", log)
        self.assertFalse(marker.exists())

    @unittest.skipUnless(shutil.which("vim"), "native Vim is not installed")
    def test_vim_controlled_session_preserves_real_files(self):
        self.deploy()
        log = self.doctor("--only", "vim", "--runtime")
        self.assertIn("PASS vim.runtime", log)

    @unittest.skipUnless(shutil.which("lazygit") and shutil.which("delta"), "native Lazygit and delta are required")
    def test_lazygit_controlled_session_preserves_real_files(self):
        self.deploy()
        log = self.doctor("--only", "lazygit", "--runtime")
        self.assertIn("PASS lazygit.runtime: TUI loaded copied YAML", log)

    def lazygit_terminal_fixture(self, mode, render_status=0):
        self.deploy()
        fixture = REPO / "tests/doctor/lazygit-fixture.py"
        self.mock("lazygit", "exec " + shlex.join([sys.executable, "-B", str(fixture), mode]) + ' "$@"\n')
        self.mock("delta", "cat > /dev/null\nexit " + str(render_status) + "\n")
        self.mock("nvim", "exit 0\n")

    def test_lazygit_retries_quit_after_editor_handoff_discards_input(self):
        self.lazygit_terminal_fixture("lost-quit")
        log = self.doctor("--only", "lazygit", "--runtime", "--verbose")
        self.assertIn("PASS lazygit.runtime", log)

    def test_lazygit_rejects_failed_renderer_even_when_tui_can_exit_successfully(self):
        self.lazygit_terminal_fixture("normal", render_status=7)
        log = self.doctor("--only", "lazygit", "--runtime", "--verbose", code=1)
        self.assertIn("configured delta renderer exited 7", log)
        self.assertNotIn("PASS lazygit.runtime", log)

    @unittest.skipUnless(shutil.which("zsh"), "native Zsh is not installed")
    def test_invalid_fzf_initialization_is_a_health_failure(self):
        self.deploy()
        self.mock("fzf", "printf 'if then\\n'\n")
        log = self.doctor("--only", "zsh", code=1)
        self.assertIn("FAIL zsh.fzf: generated Zsh initialization is invalid", log)
        self.assertNotIn("PASS zsh.fzf", log)

    def test_cleanup_failure_is_in_final_counts_and_preserves_health_failure(self):
        self.mock("rm", "exit 72\n")
        before = snapshot(self.target)
        for broken, code, count in ((False, 2, 1), (True, 1, 2)):
            with self.subTest(broken=broken):
                env = dict(self.env)
                if broken:
                    env["ZDOTDIR"] = ""
                result = run([BASH, str(self.script), "--only", "environment"], env=env,
                             cwd=self.work, timeout=10)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertIn("FAIL doctor.cleanup", result.stderr)
                self.assertIn("FAIL=" + str(count), result.stderr)
                self.assertLess(result.stderr.index("FAIL doctor.cleanup"), result.stderr.index("result:"))
                self.assertEqual(result.stderr.count("result:"), 1)
                self.assertEqual(snapshot(self.target), before)
                retained = list(self.temp.iterdir())
                self.assertEqual(len(retained), 1)
                shutil.rmtree(retained[0])

    def test_signal_with_cleanup_failure_keeps_signal_exit_code(self):
        marker = self.hanging_application()
        self.mock("rm", "exit 72\n")
        with Process([BASH, str(self.script), "--only", "starship"], env=self.env,
                     cwd=self.work, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(marker.exists())
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 143, stderr)
            self.assertEqual(stdout, b"")
            self.assertIn(b"FAIL doctor.cleanup", stderr)
            self.assertIn(b"FAIL=1", stderr)
            self.assertFalse(running(int(marker.read_text())))
            self.assertEqual(len(list(self.temp.iterdir())), 1)

    @unittest.skipUnless(os.environ.get("DOTFILES_TEST_PREPARED_HOME") and shutil.which("nvim"),
                         "set DOTFILES_TEST_PREPARED_HOME for cached Neovim runtime integration")
    def test_cached_neovim_runtime_uses_copied_plugins(self):
        self.deploy()
        original = Path(os.environ["DOTFILES_TEST_PREPARED_HOME"]) / ".local/share/nvim"
        self.assertTrue(original.is_dir(), original)
        before = snapshot(original)
        self.addCleanup(lambda: self.assertEqual(snapshot(original), before,
                                                'runtime integration changed its source baseline'))
        target = self.target / ".local/share/nvim"
        target.parent.mkdir(parents=True, exist_ok=True)
        private = self.base / 'cached-nvim'
        shutil.copytree(original, private, symlinks=True)
        target.symlink_to(private, target_is_directory=True)
        log = self.doctor("--only", "nvim", "--runtime", "--verbose")
        for check in ("scope", "bashls", "shuck", "shfmt", "shuck-format", "zsh-lint", "startup"):
            self.assertIn("PASS nvim.runtime." + check, log)

    @unittest.skipUnless(os.environ.get("DOTFILES_TEST_PREPARED_HOME") and shutil.which("zsh"),
                         "set DOTFILES_TEST_PREPARED_HOME for cached Zsh runtime integration")
    def test_cached_zsh_runtime_uses_copied_plugins(self):
        self.deploy()
        original = Path(os.environ["DOTFILES_TEST_PREPARED_HOME"])
        relatives = (".local/share/antidote", ".cache/antidote")
        before = {name: snapshot(original / name) for name in relatives}
        self.addCleanup(lambda: self.assertEqual(
            {name: snapshot(original / name) for name in relatives}, before,
            'runtime integration changed its source baseline'))
        for name in relatives:
            self.assertTrue((original / name).is_dir(), name)
            shutil.copytree(original / name, self.target / name, symlinks=True)
        cache = self.target / ".cache/antidote/zsh_plugins.zsh"
        cache.write_text(cache.read_text().replace(str(original), str(self.target)))
        log = self.doctor("--only", "zsh", "--runtime", "--verbose")
        for check in ("scope", "root", "child", "plugins", "history"):
            self.assertIn("PASS zsh.runtime." + check, log)

    def hanging_application(self):
        self.deploy()
        marker = self.work / "child.pid"
        self.mock("starship", "trap '' TERM\nsleep 120 &\nprintf '%s\\n' \"$!\" > "
                  + shlex.quote(str(marker)) + "\nwait\n")
        return marker

    def test_timeout_terminates_descendants_and_cleans_temporary_files(self):
        marker = self.hanging_application()
        started = time.monotonic()
        log = self.doctor("--only", "starship", code=1)
        self.assertLess(time.monotonic() - started, 13)
        self.assertIn("timed out", log)
        self.assertFalse(running(int(marker.read_text())))

    def test_signal_preserves_exit_status_and_cleans_children(self):
        marker = self.hanging_application()
        with Process([BASH, str(self.script), "--only", "starship"], env=self.env,
                     cwd=self.work, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
            end = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < end:
                time.sleep(.02)
            self.assertTrue(marker.exists())
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 143, stderr)
            self.assertEqual(stdout, b"")
            self.assertFalse(running(int(marker.read_text())))
            self.assertEqual(list(self.temp.iterdir()), [])


class HarnessProcessTests(unittest.TestCase):
    """测试运行器回归：在外层夹具兜底清理前断言，避免掩盖资源残留。"""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='test-harness-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.marker = self.root / 'pids'
        self.worker = self.root / 'worker.py'
        self.worker.write_text(
            'import os, signal, subprocess, sys, time\nfrom pathlib import Path\n'
            'child = subprocess.Popen([sys.executable, "-c", '
            '"import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)"], '
            'start_new_session=sys.argv[2] == "detached", '
            'stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n'
            'Path(sys.argv[1]).write_text(f"{os.getpid()} {child.pid}")\n'
            'if sys.argv[2] != "exit": time.sleep(120)\n')
        self.addCleanup(self.rescue_children)

    def rescue_children(self):
        # 最终兜底清理由外层夹具执行，断言前不代为终止进程。
        if self.marker.exists():
            for pid in map(int, self.marker.read_text().split()):
                if running(pid):
                    os.kill(pid, signal.SIGKILL)

    def assert_children_stopped(self):
        self.assertTrue(self.marker.exists(), 'worker did not reach the descendant checkpoint')
        for pid in map(int, self.marker.read_text().split()):
            self.assertFalse(running(pid), f'test runner left process {pid} running')

    def test_timeout_reaps_descendants_in_both_session_types(self):
        for mode in ('same-session', 'detached'):
            with self.subTest(mode=mode):
                with self.assertRaises(subprocess.TimeoutExpired):
                    run([sys.executable, '-B', str(self.worker), str(self.marker), mode], timeout=.4)
                self.assert_children_stopped()

    def test_normal_exit_also_reaps_descendants(self):
        result = run([sys.executable, '-B', str(self.worker), str(self.marker), 'exit'])
        self.assertEqual(result.returncode, 0)
        self.assert_children_stopped()

    def test_signals_reap_children_before_runner_exit(self):
        for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=signum):
                self.marker.unlink(missing_ok=True)
                args = [sys.executable, '-B', str(REPO / 'tests/support/harness.py'), 'run', '10',
                        sys.executable, '-B', str(self.worker), str(self.marker), 'same-session']
                with Process(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
                    deadline = time.monotonic() + 5
                    while not self.marker.exists() and time.monotonic() < deadline:
                        self.assertIsNone(process.poll())
                        time.sleep(.02)
                    self.assertTrue(self.marker.exists())
                    process.send_signal(signum)
                    stdout, stderr = process.communicate(timeout=5)
                    self.assertEqual(process.returncode, 128 + signum, stdout + stderr)
                    self.assert_children_stopped()

    def test_pty_timeout_reaps_children_and_closes_descriptors(self):
        descriptors = []
        real_openpty = harness.pty.openpty

        def openpty():
            pair = real_openpty()
            descriptors.extend(pair)
            return pair

        with mock.patch.object(harness.pty, 'openpty', side_effect=openpty):
            with self.assertRaises(subprocess.TimeoutExpired):
                harness.terminal([sys.executable, '-B', str(self.worker), str(self.marker), 'same-session'], timeout=.4)
        self.assert_children_stopped()
        self.assertEqual(len(descriptors), 2)
        for descriptor in descriptors:
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_bash_signal_diagnostics_survive_application_redirection(self):
        fixture_path = self.root / 'fixture-path'
        script = self.root / 'runner.sh'
        script.write_text(
            'set -eE\nset -o pipefail\n'
            + 'source ' + shlex.quote(str(REPO / 'tests/support/harness.bash')) + '\n'
            + 'initialize_test_state\ninstall_test_traps\n'
            + 'prepare_suite ' + shlex.quote(str(REPO)) + ' ' + shlex.quote(str(self.root)) + '\n'
            + 'new_fixture "redirected application"\n'
            + 'printf "%s\\n" "$test_root" > ' + shlex.quote(str(fixture_path)) + '\n'
            + 'run_test_command run 10 ' + shlex.join(
                [sys.executable, '-B', str(self.worker), str(self.marker), 'same-session'])
            + ' > "$case_log" 2>&1\nsuite_complete=yes\n')
        with Process([BASH, str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
            deadline = time.monotonic() + 5
            while not self.marker.exists() and time.monotonic() < deadline:
                self.assertIsNone(process.poll())
                time.sleep(.02)
            self.assertTrue(self.marker.exists())
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 143, stdout + stderr)
            self.assertIn('FAIL: redirected application', stderr)
            self.assertIn('exited 143', stderr)
            self.assert_children_stopped()
            self.assertFalse(Path(fixture_path.read_text().strip()).exists())


class RuntimeProcessTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("doctor_runtime", REPO / "scripts/doctor/runtime.py")
        self.runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.runtime)
        self.temporary = tempfile.TemporaryDirectory(prefix="doctor-process-test-")
        self.addCleanup(self.temporary.cleanup)
        self.env = {"HOME": self.temporary.name, "PATH": os.environ["PATH"]}
        self.runtime.DEADLINE = time.monotonic() + 10

    def test_cleanup_confirms_group_disappearance_after_permission_error(self):
        with self.runtime.session([sys.executable, "-c", "pass"],
                                  self.env, self.temporary.name) as (child, _master):
            child.communicate(timeout=5)
            denied = PermissionError(errno.EPERM, "process group is exiting")
            gone = ProcessLookupError(errno.ESRCH, "process group is gone")
            with mock.patch.object(self.runtime.os, "killpg", side_effect=[denied, denied, gone]) as killpg:
                self.runtime.stop(child)
            self.assertEqual(killpg.call_args_list,
                             [mock.call(child.pid, signal.SIGKILL), mock.call(child.pid, 0), mock.call(child.pid, 0)])
            self.assertNotIn(child, self.runtime.CHILDREN)
            self.assertTrue(child.stdout.closed)
            self.assertTrue(child.stderr.closed)

    def test_cleanup_preserves_permission_errors_for_groups_not_confirmed_gone(self):
        for state in ("live", "exited-visible", "exited-denied"):
            with self.subTest(state=state):
                command = "import time; time.sleep(120)" if state == "live" else "pass"
                with self.runtime.session([sys.executable, "-c", command],
                                          self.env, self.temporary.name) as (child, _master):
                    if state != "live":
                        child.communicate(timeout=5)
                    denied = PermissionError(errno.EPERM, "cannot signal process group")
                    failures = [denied, None] if state == "exited-visible" else denied
                    with mock.patch.object(self.runtime.os, "killpg", side_effect=failures):
                        with self.assertRaises(PermissionError):
                            self.runtime.stop(child)
                    self.assertIn(child, self.runtime.CHILDREN)
                    if state == "live":
                        self.assertIsNone(child.poll())
                self.assertIsNotNone(child.returncode)
                self.assertNotIn(child, self.runtime.CHILDREN)

    def test_cleanup_reaps_descendants_after_group_leader_exits(self):
        command = ('import subprocess, sys\n'
                   'child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], '
                   'stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n'
                   'print(child.pid, flush=True)\n')
        descendant = None
        try:
            with self.runtime.session([sys.executable, "-c", command],
                                      self.env, self.temporary.name) as (child, _master):
                stdout, _stderr = child.communicate(timeout=5)
                descendant = int(stdout)
                self.assertEqual(child.returncode, 0)
                self.assertTrue(running(descendant))
            deadline = time.monotonic() + 2
            while running(descendant) and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertFalse(running(descendant), "cleanup left a descendant running")
        finally:
            if descendant is not None and running(descendant):
                os.kill(descendant, signal.SIGKILL)

    def assert_spawn_interrupt_cleanup(self, terminal):
        real_popen, real_openpty = subprocess.Popen, self.runtime.pty.openpty
        for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            with self.subTest(terminal=terminal, signal=signum):
                children, descriptors = [], []

                def openpty():
                    pair = real_openpty()
                    descriptors.extend(pair)
                    return pair

                def spawn(*args, **kwargs):
                    child = real_popen(*args, **kwargs)
                    children.append(child)
                    self.assertIsNone(child.poll(), "fixture must reach a live child")
                    # 在 Popen 创建进程后、调用方收到句柄前发送真实信号，
                    # 用确定的注入时机覆盖交接窗口，不依赖 sleep 猜测时序。
                    os.kill(os.getpid(), signum)
                    return child

                previous = signal.signal(signum, self.runtime.interrupted)
                try:
                    with mock.patch.object(subprocess, "Popen", side_effect=spawn), \
                            mock.patch.object(self.runtime.pty, "openpty", side_effect=openpty):
                        entry = self.runtime.terminal if terminal else self.runtime.run
                        with self.assertRaises(SystemExit) as caught:
                            entry([sys.executable, "-c", "import time; time.sleep(120)"],
                                  self.env, self.temporary.name)
                    self.assertEqual(caught.exception.code, 128 + signum)
                    self.assertEqual(len(children), 1)
                    self.assertIsNotNone(children[0].poll(), "interrupted spawn left its child running")
                    for stream in (children[0].stdout, children[0].stderr):
                        if stream:
                            self.assertTrue(stream.closed, "interrupted spawn left a pipe open")
                    for descriptor in descriptors:
                        with self.assertRaises(OSError):
                            os.fstat(descriptor)
                finally:
                    signal.signal(signum, previous)
                    # 即使被测清理逻辑回归，夹具仍负责回收这些句柄。
                    for child in children:
                        if child.poll() is None:
                            os.killpg(child.pid, signal.SIGKILL)
                        child.wait(timeout=5)
                        for stream in (child.stdout, child.stderr):
                            if stream:
                                stream.close()
                    for descriptor in descriptors:
                        try:
                            os.close(descriptor)
                        except OSError:
                            pass

    def test_pipe_spawn_interrupt_reaps_child(self):
        self.assert_spawn_interrupt_cleanup(terminal=False)

    def test_pty_spawn_interrupt_reaps_child_and_closes_descriptors(self):
        self.assert_spawn_interrupt_cleanup(terminal=True)

    def test_signal_during_cleanup_still_reaps_the_child(self):
        real_stop = self.runtime.stop
        children = []

        def stop(child):
            children.append(child)
            os.kill(os.getpid(), signal.SIGTERM)
            real_stop(child)

        previous = signal.signal(signal.SIGTERM, self.runtime.interrupted)
        try:
            with mock.patch.object(self.runtime, "stop", side_effect=stop):
                with self.assertRaises(SystemExit) as caught:
                    with self.runtime.session([sys.executable, "-c", "import time; time.sleep(120)"],
                                              self.env, self.temporary.name) as (child, _master):
                        self.assertIsNone(child.poll())
            self.assertEqual(caught.exception.code, 143)
            self.assertTrue(children, "fixture did not reach session cleanup")
            self.assertIsNotNone(children[0].returncode)
            self.assertTrue(children[0].stdout.closed)
            self.assertTrue(children[0].stderr.closed)
        finally:
            signal.signal(signal.SIGTERM, previous)
            for child in children:
                real_stop(child)

    def test_pty_setup_failure_closes_descriptors(self):
        real_openpty = self.runtime.pty.openpty
        descriptors = []

        def openpty():
            pair = real_openpty()
            descriptors.extend(pair)
            return pair

        try:
            with mock.patch.object(self.runtime.pty, "openpty", side_effect=openpty), \
                    mock.patch.object(self.runtime.fcntl, "ioctl", side_effect=OSError("PTY setup failed")):
                with self.assertRaisesRegex(OSError, "PTY setup failed"):
                    self.runtime.terminal([sys.executable, "-c", "pass"], self.env, self.temporary.name)
            self.assertEqual(len(descriptors), 2)
            for descriptor in descriptors:
                with self.assertRaises(OSError):
                    os.fstat(descriptor)
        finally:
            for descriptor in descriptors:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


if __name__ == "__main__":
    with cleanup_on_exit():
        unittest.main(verbosity=2, buffer=True)
