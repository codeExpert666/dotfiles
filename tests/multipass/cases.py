"""Offline behavior tests for the Multipass orchestrator and guest helper."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts/multipass"))
import guest
import host
import runtime
import ssh_config
import ssh_proxy


PUB = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIG3Z+/i5KijOrBwCDnV+BYuzxq76WhXpuN4V7UZckucA"
REF = "a" * 40


def args(**overrides):
    values = dict(action="create", name=None, config=None, dry_run=True, apply=False, ref=REF,
                  ssh_public_key=None, image=None, cpus=None, memory=None, disk=None, repo_url=None)
    values.update(overrides)
    return SimpleNamespace(**values)


class Inputs(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.key = self.home / "key.pub"
        self.key.write_text(PUB + " comment with quotes ' \"\n")

    def test_defaults_and_both_images_render_without_secret(self):
        for image in ("24.04", "26.04"):
            data = runtime.declaration_from(args(image=image, ssh_public_key=self.key), {}, None,
                                            PUB, "SHA256:test")
            self.assertEqual(data["image"], image)
            self.assertEqual(data["memory"], "8G")
            cloud = runtime.render_cloud(data, PUB)
            self.assertIn("  - default", cloud)
            self.assertIn("package_reboot_if_required: false", cloud)
            self.assertIn("chmod 0600 /var/lib/dotfiles-multipass/instance.json", cloud)
            self.assertIn("Defaults:ubuntu !authenticate", cloud)
            self.assertIn(data["uuid"], cloud)
            self.assertNotIn("comment with quotes", cloud)
            self.assertNotIn("PRIVATE", cloud)

    def test_invalid_resource_ref_url_and_name(self):
        mutations = (("cpus", "0"), ("memory", "1G"), ("disk", "5G"),
                     ("image", "latest"), ("ref", "main"),
                     ("repo_url", "https://user:token@example.com/x.git"),
                     ("name", "primary"))
        for field, bad in mutations:
            with self.subTest(field=field), self.assertRaises(runtime.Failure):
                runtime.declaration_from(args(ssh_public_key=self.key, **{field: bad}), {},
                                         None, PUB, "SHA256:test")

    def test_public_key_rejects_private_and_multiple_keys(self):
        runner = runtime.Runner()
        canonical, fingerprint = runtime.public_key(self.key, runner)
        self.assertEqual(canonical, PUB)
        self.assertTrue(fingerprint.startswith("SHA256:"))
        for text in ("-----BEGIN OPENSSH PRIVATE KEY-----\n", PUB + "\n" + PUB + "\n"):
            self.key.write_text(text)
            with self.assertRaises(runtime.Failure):
                runtime.public_key(self.key, runner)

    def test_cloud_status_json_survives_multipass_restart_progress(self):
        output = "\x1b[2KStarting instance / -\\ |\n" + json.dumps(
            {"status": "done", "extended_status": "done", "errors": []}) + "\n"
        self.assertEqual(runtime.parse_json_output(output, "cloud-init")["status"], "done")

    def test_existing_declaration_rejects_changed_resources_key_and_create_ref(self):
        saved = runtime.declaration_from(args(ssh_public_key=self.key), {}, None, PUB,
                                         "SHA256:test")
        with self.assertRaisesRegex(runtime.Failure, "existing memory"):
            runtime.declaration_from(args(memory="16G"), {}, saved, PUB, "SHA256:test")
        with self.assertRaisesRegex(runtime.Failure, "fingerprint"):
            runtime.declaration_from(args(), {}, saved, PUB, "SHA256:other")
        with self.assertRaisesRegex(runtime.Failure, "use provision"):
            runtime.declaration_from(args(ref="b" * 40), {}, saved, PUB, "SHA256:test")

    def test_preview_does_not_write_state_or_ssh_files(self):
        (self.home / ".ssh").mkdir()
        (self.home / ".ssh/config").write_text("Host github.com\n    User git\n")
        before = sorted(str(path.relative_to(self.home)) for path in self.home.rglob("*"))
        with mock.patch.object(runtime, "host_preflight"), \
                mock.patch.object(runtime, "public_key", return_value=(PUB, "SHA256:test")), \
                mock.patch.object(runtime, "require_agent"), \
                mock.patch.object(host.Host, "probe", return_value={"qualified": True,
                                                                    "client": "1.16.4"}), \
                mock.patch.object(host.Host, "instances", return_value={}), \
                mock.patch.object(host.Host, "verify_service"):
            runtime.create_or_provision(args(ssh_public_key=self.key), {}, self.home,
                                        runtime.Runner())
        after = sorted(str(path.relative_to(self.home)) for path in self.home.rglob("*"))
        self.assertEqual(before, after)

    def test_unmanaged_same_name_instance_is_a_conflict(self):
        (self.home / ".ssh").mkdir()
        with mock.patch.object(runtime, "host_preflight"), \
                mock.patch.object(runtime, "public_key", return_value=(PUB, "SHA256:test")), \
                mock.patch.object(runtime, "require_agent"), \
                mock.patch.object(host.Host, "probe", return_value={"qualified": True,
                                                                    "client": "1.16.4"}), \
                mock.patch.object(host.Host, "instances", return_value={"ubuntu-dev":
                                                                         {"state": "Running"}}):
            with self.assertRaisesRegex(runtime.Failure, "not managed"):
                runtime.create_or_provision(args(ssh_public_key=self.key), {}, self.home,
                                            runtime.Runner())

    def test_agent_must_offer_the_selected_public_key(self):
        fake = lambda *_args, **_kwargs: SimpleNamespace(stdout=PUB.replace("ucA", "ucB"),
                                                         returncode=0)
        with self.assertRaisesRegex(runtime.Failure, "not loaded"):
            runtime.require_agent(PUB, fake)


class HostPolicy(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_qualified_version_does_not_call_installer(self):
        calls = []

        def run(argv, **_kwargs):
            calls.append(argv)
            if argv[1] == "get":
                return SimpleNamespace(stdout="qemu", returncode=0)
            return SimpleNamespace(stdout='{"list": []}', returncode=0)

        machine = host.Host(run, runtime.RELEASES, Path(self.temp.name))
        machine.cli = "/usr/local/bin/multipass"
        with mock.patch.object(machine, "install_pkg", side_effect=AssertionError("installer called")):
            machine.ensure({"qualified": True, "client": "1.16.4"})
        self.assertFalse(any("brew" in str(part) or "installer" in str(part)
                             for argv in calls for part in argv))

    def test_version_mismatch_fails(self):
        def run(_argv, **_kwargs):
            return SimpleNamespace(stdout='{"multipass":"1.16.4","multipassd":"1.16.3"}',
                                   returncode=0)
        machine = host.Host(run, runtime.RELEASES, Path(self.temp.name))
        with mock.patch.object(host.shutil, "which", return_value="/usr/local/bin/multipass"):
            with self.assertRaisesRegex(ValueError, "mismatch"):
                machine.probe()

    def test_path_shadow_or_missing_cli_receipt_is_not_treated_as_fresh_install(self):
        standard = Path(self.temp.name) / "multipass"
        standard.write_text("stub")
        machine = host.Host(lambda *_args, **_kwargs: None, runtime.RELEASES,
                            Path(self.temp.name))
        with mock.patch.object(host, "STANDARD_CLI", standard), \
                mock.patch.object(host.shutil, "which", return_value=None):
            with self.assertRaisesRegex(ValueError, "missing from PATH"):
                machine.probe()
        def receipt(_argv, **_kwargs):
            return SimpleNamespace(stdout="com.canonical.multipass.multipass", returncode=0)
        machine.run = receipt
        with self.assertRaisesRegex(ValueError, "receipts exist"):
            machine.ensure(None)

    def test_old_homebrew_install_uses_only_targeted_cask_upgrade(self):
        calls = []
        def run(argv, **_kwargs):
            calls.append(argv)
            return SimpleNamespace(stdout="multipass 1.15.0", returncode=0)
        machine = host.Host(run, runtime.RELEASES, Path(self.temp.name))
        machine.cli = "/opt/homebrew/bin/multipass"
        with mock.patch.object(host.shutil, "which", return_value="/opt/homebrew/bin/brew"), \
                mock.patch.object(machine, "instances", return_value={}), \
                mock.patch.object(machine, "probe", return_value={"qualified": True}), \
                mock.patch.object(machine, "wait_service"):
            machine.ensure({"qualified": False, "source": "unknown", "client": "1.15.0"})
        self.assertIn(["/opt/homebrew/bin/brew", "upgrade", "--cask", "multipass"], calls)
        self.assertFalse(any("--zap" in argv or "uninstall" in argv for argv in calls))

    def test_old_official_pkg_upgrade_does_not_use_homebrew(self):
        machine = host.Host(lambda *_args, **_kwargs: None, runtime.RELEASES,
                            Path(self.temp.name))
        with mock.patch.object(host.shutil, "which", return_value=None), \
                mock.patch.object(machine, "instances", return_value={}), \
                mock.patch.object(machine, "install_pkg") as installer, \
                mock.patch.object(machine, "probe", return_value={"qualified": True}), \
                mock.patch.object(machine, "wait_service"):
            machine.ensure({"qualified": False, "source": "official-pkg", "client": "1.15.0"})
        installer.assert_called_once()

    def test_pkg_hash_failure_stops_before_sudo(self):
        calls = []

        def run(argv, **_kwargs):
            calls.append(argv)
            if argv[0] == "curl":
                Path(argv[argv.index("--output") + 1]).write_bytes(b"corrupt pkg")
            return SimpleNamespace(stdout="", returncode=0)

        machine = host.Host(run, runtime.RELEASES, Path(self.temp.name))
        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            machine.install_pkg()
        self.assertFalse(any(argv[0] in ("sudo", "installer") for argv in calls))

    def test_size_parser_and_proxy_address_change(self):
        self.assertEqual(host.size_bytes("8.0GiB"), 8 * 1024 ** 3)
        for ip in ("192.168.64.2", "192.168.64.9"):
            self.assertEqual(ssh_proxy.address({"info": {"test": {"state": "Running",
                                                               "ipv4": [ip]}}}, "test"), ip)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            ssh_proxy.address({"info": {"test": {"state": "Running",
                                                   "ipv4": ["10.0.0.2", "10.0.0.3"]}}}, "test")
        with self.assertRaisesRegex(ValueError, "not running"):
            ssh_proxy.address({"info": {"test": {"state": "Stopped"}}}, "test")

    def test_cloud_init_degraded_is_not_accepted_as_done(self):
        machine = runtime.Machine(Path(self.temp.name), "test", runtime.Runner())
        machine.m = lambda *_args, **_kwargs: SimpleNamespace(
            stdout=json.dumps({"status": "done", "extended_status": "degraded done",
                               "errors": []}))
        with self.assertRaisesRegex(runtime.Failure, "did not finish cleanly"):
            machine.cloud_wait()

    def test_cloud_init_exit_two_collects_failure_log(self):
        machine = runtime.Machine(Path(self.temp.name), "test", runtime.Runner())
        calls = []
        def call(*argv, **_kwargs):
            calls.append(argv)
            if "status" in argv:
                raise runtime.CommandFailure(["multipass", "exec", "cloud-init"], 2,
                                             "degraded done")
            return SimpleNamespace(stdout="cloud-init error")
        machine.m = call
        with self.assertRaises(runtime.CommandFailure):
            machine.cloud_wait()
        self.assertEqual(len(calls), 2)
        self.assertTrue(any(part.endswith("cloud-init-output.log") for part in calls[1]))

    def test_instance_lock_rejects_concurrent_run(self):
        path = Path(self.temp.name) / "lock"
        with runtime.lock(path):
            with self.assertRaisesRegex(runtime.Failure, "lock exists"):
                with runtime.lock(path):
                    pass


class SSHConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        (self.home / ".ssh").mkdir()
        (self.home / ".ssh/config").write_text("Host github.com\n    User git\n")
        self.pub = self.home / "key.pub"
        self.pub.write_text(PUB + "\n")
        self.setting = ssh_config.SSHConfig(self.home, "ubuntu-dev", "test-uuid", self.pub,
                                            REPO / "scripts/multipass/ssh_proxy.py",
                                            "/usr/local/bin/multipass")

    def test_include_idempotence_and_existing_host_semantics(self):
        first = self.setting.publish(PUB)
        content = (self.home / ".ssh/config").read_text()
        second = self.setting.publish(PUB, first)
        self.assertEqual(first, second)
        self.assertEqual((self.home / ".ssh/config").read_text(), content)
        self.assertEqual(content.count(ssh_config.SENTINEL), 1)
        old = subprocess.run(["ssh", "-G", "-F", str(self.home / ".ssh/config"), "github.com"],
                             capture_output=True, text=True, check=True)
        self.assertIn("user git\n", old.stdout)
        self.assertIn("stricthostkeychecking true\n",
                      subprocess.run(["ssh", "-G", "-F", str(self.home / ".ssh/config"),
                                      "ubuntu-dev"], capture_output=True, text=True, check=True).stdout)

    def test_collision_and_host_key_change_preserve_existing_files(self):
        (self.home / ".ssh/config").write_text("Host ubuntu-*\n    User alice\n")
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.setting.preflight()
        (self.home / ".ssh/config").write_text("Host github.com\n    User git\n")
        first = self.setting.publish(PUB)
        with self.assertRaisesRegex(ValueError, "host key changed"):
            self.setting.publish(PUB.replace("ucA", "ucB"), first)
        self.assertEqual((self.home / ".ssh/dotfiles-multipass/known_hosts/ubuntu-dev").read_text(),
                         "dotfiles-multipass-test-uuid " + PUB + "\n")

    def test_modified_managed_host_is_not_overwritten(self):
        hashes = self.setting.publish(PUB)
        self.setting.host.write_text(self.setting.host.read_text() + "# user change\n")
        with self.assertRaisesRegex(ValueError, "changed outside"):
            self.setting.publish(PUB, hashes)

    def test_another_managed_host_can_update_aggregate(self):
        first = self.setting.publish(PUB)
        other = ssh_config.SSHConfig(self.home, "ubuntu-dev-2", "other-uuid", self.pub,
                                     REPO / "scripts/multipass/ssh_proxy.py",
                                     "/usr/local/bin/multipass")
        other.publish(PUB)
        self.setting.publish(PUB, first)
        self.assertEqual((self.home / ".ssh/config").read_text().count(ssh_config.SENTINEL), 1)


class GuestGit(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        subprocess.run(["git", "init", "-q", str(self.source)], check=True)
        for item in guest.REQUIRED:
            path = self.source / item
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("#!/bin/sh\n")
        subprocess.run(["git", "-C", str(self.source), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.source), "-c", "user.name=Test", "-c",
                        "user.email=test@example.invalid", "commit", "-qm", "first"], check=True)
        self.ref = subprocess.check_output(["git", "-C", str(self.source), "rev-parse", "HEAD"],
                                           text=True).strip()
        home = self.root / "home"
        home.mkdir()
        self.patchers = [mock.patch.object(guest, "HOME", home),
                         mock.patch.object(guest, "REPO", home / ".dotfiles"),
                         mock.patch.object(guest, "WORKSPACE", home / "workspace"),
                         mock.patch.object(guest, "require_ubuntu"),
                         mock.patch.object(guest, "ensure_owned_directory",
                                           side_effect=lambda path, create=False:
                                           path.mkdir(exist_ok=True) if create else None)]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_pinned_history_dirty_tree_and_workspace_preserved(self):
        guest.repository(str(self.source), self.ref)
        sentinel = guest.WORKSPACE / "sentinel"
        sentinel.write_text("keep")
        guest.repository(str(self.source), self.ref)
        self.assertEqual(sentinel.read_text(), "keep")
        (guest.REPO / "user-file").write_text("change")
        with self.assertRaisesRegex(ValueError, "user changes"):
            guest.repository(str(self.source), self.ref)
        self.assertEqual(sentinel.read_text(), "keep")


class GuestEnvironment(unittest.TestCase):
    @unittest.skipUnless(shutil.which("zsh"), "native Zsh is not installed")
    def test_version_receipt_uses_jdk_from_zsh_startup(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            binary = home / "jdk/bin"
            binary.mkdir(parents=True)
            for name in ("java", "javac", "mvn"):
                tool = binary / name
                tool.write_text("#!/bin/sh\nprintf '%s:%s\\n' " + name + ' "$JAVA_HOME"\n')
                tool.chmod(0o755)
            (home / ".zshenv").write_text(
                f"export JAVA_HOME={shlex.quote(str(binary.parent))}\n"
                f"export PATH={shlex.quote(str(binary))}:$PATH\n")
            with mock.patch.object(guest, "HOME", home), \
                    mock.patch.object(guest, "require_ubuntu"), \
                    mock.patch.object(guest, "git", return_value=REF), \
                    mock.patch("builtins.print") as output:
                guest.versions()
            tools = json.loads(output.call_args.args[0])["tools"]
            for name in ("java", "javac", "maven"):
                executable = "mvn" if name == "maven" else name
                self.assertEqual(tools[name], [f"{executable}:{binary.parent}"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
